"""
Telegram Wallpaper Sync - Server + Bot (single process)

Runs great locally and on Railway (GitHub deploy).

Startup modes:
  - Local dev: polling (simple)
  - Railway / production: webhook (recommended, set PUBLIC_BASE_URL)

It starts:
- FastAPI (your Android app + Telegram webhook talk here)
- Telegram bot (either long polling or webhook)

Important env vars (set these in Railway dashboard or .env):
    TELEGRAM_BOT_TOKEN   (required - from @BotFather)
    PUBLIC_BASE_URL      (https://your-app.up.railway.app) - used for image URLs + webhook
    DATA_DIR             (recommended on Railway: /data  + attach a Volume)
    PORT                 (Railway sets this automatically)
"""

import os
import asyncio
import logging
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from typing import Optional
from contextlib import asynccontextmanager

import httpx
import firebase_admin
from firebase_admin import credentials, messaging
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from database import (
    init_db,
    link_device_to_chat,
    get_device_for_chat,
    add_pending_wallpaper,
    get_pending_for_device,
    get_pending_by_id,
    mark_wallpaper_applied,
    get_history_for_device,
    get_chat_id_for_pending,
    set_push_token,
    get_push_token,
    IMAGES_DIR,
    DATA_DIR,
    DB_PATH,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
FIREBASE_SERVICE_ACCOUNT_JSON = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")

# PUBLIC_BASE_URL is critical for both image links the Android app downloads
# and for setting the Telegram webhook.
def normalize_public_base_url(raw_url: str) -> str:
    """Return an absolute base URL suitable for webhooks and image links."""
    url = raw_url.strip().rstrip("/")
    if not url:
        return "http://localhost:8000"
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    if url.startswith("http://") and "localhost" not in url and "127.0.0.1" not in url:
        url = url.replace("http://", "https://", 1)
    return url


PUBLIC_BASE_URL = normalize_public_base_url(
    os.getenv("PUBLIC_BASE_URL")
    or os.getenv("RAILWAY_PUBLIC_DOMAIN")  # Often just the domain, with no scheme.
    or os.getenv("RAILWAY_STATIC_URL")
    or "http://localhost:8000"
)

PORT = int(os.getenv("PORT", "8000"))

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is required. Set it in .env or Railway Variables.")

# Ensure image dir exists (database.py already creates it, but be defensive)
Path(IMAGES_DIR).mkdir(parents=True, exist_ok=True)

# Global bot application reference
ptb_app: Optional[Application] = None
_use_webhook = False
_webhook_path = "/webhook"
_firebase_ready = False


def init_firebase():
    global _firebase_ready
    if _firebase_ready:
        return True
    if firebase_admin._apps:
        _firebase_ready = True
        return True
    if not FIREBASE_SERVICE_ACCOUNT_JSON:
        logger.info("FIREBASE_SERVICE_ACCOUNT_JSON not set; FCM push delivery is disabled")
        return False

    try:
        service_account = json.loads(FIREBASE_SERVICE_ACCOUNT_JSON)
        firebase_admin.initialize_app(credentials.Certificate(service_account))
        _firebase_ready = True
        logger.info("Firebase Admin initialized")
        return True
    except Exception as e:
        logger.exception(f"Failed to initialize Firebase Admin: {e}")
        return False


async def send_wallpaper_push(device_id: str, pending_id: int):
    token = get_push_token(device_id)
    if not token:
        logger.info(f"No FCM token registered for device {device_id}; skipping push")
        return
    if not init_firebase():
        return

    try:
        message = messaging.Message(
            token=token,
            data={
                "type": "wallpaper_pending",
                "device_id": device_id,
                "pending_id": str(pending_id),
            },
            android=messaging.AndroidConfig(priority="high"),
        )
        message_id = await asyncio.to_thread(messaging.send, message)
        logger.info(f"Sent FCM wallpaper push to device {device_id}: {message_id}")
    except Exception as e:
        logger.warning(f"Failed to send FCM push to device {device_id}: {e}")


# --------------------------- Pydantic models ---------------------------

class ApplyRequest(BaseModel):
    device_id: str
    pending_id: int
    screen: str = "both"   # "home", "lock", or "both"


class RegisterPushRequest(BaseModel):
    device_id: str
    fcm_token: str


# --------------------------- FastAPI app ---------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global ptb_app, _use_webhook
    init_db()
    init_firebase()
    logger.info(f"Database initialized (DB_PATH={DB_PATH}, IMAGES_DIR={IMAGES_DIR}, DATA_DIR={DATA_DIR})")

    ptb_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Register handlers
    ptb_app.add_handler(CommandHandler("start", start_command))
    ptb_app.add_handler(CommandHandler("help", help_command))
    ptb_app.add_handler(CommandHandler("status", status_command))
    ptb_app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_fallback))

    await ptb_app.initialize()
    await ptb_app.start()

    # Decide webhook vs polling
    _use_webhook = bool(PUBLIC_BASE_URL and PUBLIC_BASE_URL.startswith("https://") and "localhost" not in PUBLIC_BASE_URL)

    if _use_webhook:
        webhook_url = f"{PUBLIC_BASE_URL}{_webhook_path}"
        try:
            await ptb_app.bot.set_webhook(
                url=webhook_url,
                # You can add a secret_token for extra security:
                # secret_token=os.getenv("TELEGRAM_WEBHOOK_SECRET")
            )
            logger.info(f"Telegram webhook set to: {webhook_url}")
        except Exception as e:
            logger.error(f"Failed to set webhook: {e}. Falling back to polling.")
            _use_webhook = False

    if not _use_webhook:
        # Local dev / fallback: long polling
        logger.info("Starting bot in long-polling mode")
        # We run polling in a background task
        asyncio.create_task(run_polling())

    logger.info(f"Server ready. PUBLIC_BASE_URL={PUBLIC_BASE_URL}  (webhook={'yes' if _use_webhook else 'no'})")

    yield

    # Shutdown
    if ptb_app:
        if not _use_webhook and ptb_app.updater:
            try:
                await ptb_app.updater.stop()
            except Exception:
                pass
        await ptb_app.stop()
        await ptb_app.shutdown()

        # Remove webhook on clean shutdown (optional but tidy)
        if _use_webhook:
            try:
                await ptb_app.bot.delete_webhook()
            except Exception:
                pass
    logger.info("Shutdown complete")


async def run_polling():
    """Background task for long polling (used when webhook is not active)."""
    if ptb_app and ptb_app.updater:
        await ptb_app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        logger.info("Bot polling task started")
    else:
        logger.error("Cannot start polling because the Telegram updater is unavailable")


app = FastAPI(title="Telegram Wallpaper Sync", lifespan=lifespan)

# In production you should restrict this (e.g. your Android app's package or known IPs).
# For now we keep it open so the companion app works easily from anywhere.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve downloaded images. On Railway use a Volume so files survive restarts.
app.mount("/images", StaticFiles(directory=str(IMAGES_DIR)), name="images")


@app.get("/")
async def root():
    return {
        "status": "ok",
        "service": "telegram-wallpaper-sync",
        "mode": "webhook" if _use_webhook else "polling",
        "images_base": f"{PUBLIC_BASE_URL}/images",
        "note": "Android app uses /pending and /apply. Telegram uses the webhook (or polling).",
    }


@app.get("/healthz")
async def healthz():
    """Simple health check for Railway / load balancers."""
    return {"status": "healthy", "db": DB_PATH, "images": str(IMAGES_DIR)}


@app.post(_webhook_path)
async def telegram_webhook(request: Request):
    """
    Telegram sends updates here when webhook mode is active.
    This is the recommended way to run on Railway.
    """
    if not ptb_app:
        raise HTTPException(status_code=503, detail="Bot not initialized")

    try:
        data = await request.json()
        update = Update.de_json(data, ptb_app.bot)
        await ptb_app.process_update(update)
    except Exception as e:
        logger.exception(f"Error processing webhook update: {e}")
        # Return 200 anyway so Telegram doesn't keep retrying bad updates forever
    return {"ok": True}


@app.get("/pending")
async def get_pending(device_id: str = Query(..., min_length=8)):
    """Android app calls this to discover new wallpapers sent via the bot."""
    pending = get_pending_for_device(device_id)
    results = []
    for p in pending:
        results.append({
            "id": p["id"],
            "image_url": f"{PUBLIC_BASE_URL}/images/{p['filename']}",
            "received_at": p["received_at"],
            "screen_hint": p.get("screen"),
        })
    return {"device_id": device_id, "pending": results}


@app.post("/apply")
async def apply_wallpaper(req: ApplyRequest):
    """
    Called by Android after the user taps "Set as wallpaper".
    Marks the item applied in DB and tells the Telegram bot to send a confirmation.
    """
    pending = get_pending_by_id(req.pending_id)
    if not pending or pending["device_id"] != req.device_id:
        raise HTTPException(status_code=404, detail="Pending wallpaper not found for this device")

    if pending["applied"]:
        return {"ok": True, "already_applied": True}

    success = mark_wallpaper_applied(req.pending_id, screen=req.screen)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to mark as applied")

    # Send confirmation back to Telegram (best effort)
    chat_id = get_chat_id_for_pending(req.pending_id)
    if chat_id and ptb_app and ptb_app.bot:
        try:
            filename = pending["filename"]
            image_path = Path(IMAGES_DIR) / filename
            caption = f"✅ Wallpaper applied on your Android device!\nScreen: {req.screen}"
            if image_path.exists():
                with open(image_path, "rb") as f:
                    await ptb_app.bot.send_photo(
                        chat_id=chat_id,
                        photo=f,
                        caption=caption,
                        parse_mode="HTML"
                    )
            else:
                await ptb_app.bot.send_message(
                    chat_id=chat_id,
                    text=caption
                )
        except Exception as e:
            logger.warning(f"Failed to send confirmation photo to chat {chat_id}: {e}")

    return {"ok": True, "pending_id": req.pending_id, "screen": req.screen}


@app.post("/register_push")
async def register_push(req: RegisterPushRequest):
    if not req.device_id or len(req.device_id) < 8:
        raise HTTPException(status_code=400, detail="Invalid device_id")
    if not req.fcm_token or len(req.fcm_token) < 20:
        raise HTTPException(status_code=400, detail="Invalid fcm_token")

    set_push_token(req.device_id, req.fcm_token)
    return {"ok": True, "device_id": req.device_id}


@app.get("/history")
async def get_history(device_id: str = Query(...), limit: int = 20):
    items = get_history_for_device(device_id, limit=limit)
    for item in items:
        item["image_url"] = f"{PUBLIC_BASE_URL}/images/{item['filename']}"
    return {"device_id": device_id, "history": items}


# --------------------------- Telegram Bot Handlers ---------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start and the deep link parameter."""
    chat = update.effective_chat
    args = context.args or []

    if not args:
        await update.message.reply_text(
            "Hi! I'm the Wallpaper Sync bot.\n\n"
            "Ask the phone owner for their share link. Once you open it, any photo you send here will be pushed to that phone's wallpaper automatically."
        )
        return

    device_id = args[0].strip()
    if not device_id or len(device_id) < 8:
        await update.message.reply_text("Invalid connect code. Please generate a fresh link from the Android app.")
        return

    username = chat.username
    first_name = chat.first_name

    link_device_to_chat(device_id, chat.id, username=username, first_name=first_name)
    bot_username = (await context.bot.get_me()).username
    share_link = f"https://t.me/{bot_username}?start={device_id}"

    await update.message.reply_text(
        f"✅ Connected to this wallpaper link.\n\n"
        f"Target device: <code>{device_id}</code>\n\n"
        "Send or forward any photo here and it will be queued for that phone to apply automatically.\n\n"
        f"Share this link with anyone you want to let change this wallpaper:\n{share_link}"
    )
    logger.info(f"Linked device {device_id} <-> chat {chat.id} (@{username})")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Wallpaper Sync Bot\n\n"
        "• Open a phone owner's share link once\n"
        "• Send me a photo any time\n"
        "• The phone app auto-syncs and applies it as wallpaper\n"
        "• I send a confirmation after the phone applies it"
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    device_id = get_device_for_chat(chat_id)
    if device_id:
        await update.message.reply_text(f"✅ You can send wallpapers to:\n<code>{device_id}</code>")
    else:
        await update.message.reply_text("Not connected yet. Ask the phone owner for their wallpaper share link.")


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive a photo, download the best quality version, register it as pending for the linked device."""
    chat = update.effective_chat
    message = update.message

    device_id = get_device_for_chat(chat.id)
    if not device_id:
        await message.reply_text(
            "You're not connected to any phone yet.\n"
            "Ask the phone owner for their wallpaper share link, open it once, then send photos here."
        )
        return

    if not message.photo:
        return

    # Telegram sends multiple sizes. Last one is the largest.
    photo = message.photo[-1]
    file_id = photo.file_id

    # Download the file
    try:
        file = await context.bot.get_file(file_id)
        # Depending on python-telegram-bot version, file_path may be either
        # 'photos/file_123.jpg' or a full Telegram file URL.

        # Create a nice local filename
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        file_path = file.file_path or ""
        parsed_file_path = urlparse(file_path)
        ext = Path(parsed_file_path.path).suffix or ".jpg"
        filename = f"{timestamp}_{chat.id}_{file_id[-8:]}{ext}"
        dest_path = IMAGES_DIR / filename

        # Download using httpx for full control (or use file.download_to_drive)
        # PTB's download_to_drive also works:
        # await file.download_to_drive(custom_path=str(dest_path))
        async with httpx.AsyncClient(timeout=60) as client:
            if file_path.startswith(("http://", "https://")):
                url = file_path
            else:
                url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
            resp = await client.get(url)
            resp.raise_for_status()
            dest_path.write_bytes(resp.content)

        pending_id = add_pending_wallpaper(
            device_id=device_id,
            filename=filename,
            original_file_id=file_id,
            chat_id=chat.id,
        )

        logger.info(f"Received photo for device {device_id} (pending #{pending_id}, file {filename})")
        await send_wallpaper_push(device_id, pending_id)

        await message.reply_text(
            f"📸 Photo received!\n"
            f"The phone will auto-sync and apply it as wallpaper.\n"
            f"(Pending ID: {pending_id})"
        )

    except Exception as e:
        logger.exception("Failed to download or register photo")
        await message.reply_text("Sorry, I couldn't process that photo. Please try again.")


async def text_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Send me a photo to push it to the connected phone's wallpaper.\n"
        "Use /help for more info."
    )


# --------------------------- Entrypoint ---------------------------

if __name__ == "__main__":
    import uvicorn
    print("Starting Telegram Wallpaper Sync server + bot...")
    print(f"Public base URL for images: {PUBLIC_BASE_URL}")
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)
