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
from datetime import datetime
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager

import httpx
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

# PUBLIC_BASE_URL is critical for both image links the Android app downloads
# and for setting the Telegram webhook.
PUBLIC_BASE_URL = (
    os.getenv("PUBLIC_BASE_URL")
    or os.getenv("RAILWAY_PUBLIC_DOMAIN")  # Railway injects this in some contexts
    or os.getenv("RAILWAY_STATIC_URL")
    or "http://localhost:8000"
).rstrip("/")

# Ensure https for production (Railway domains are https)
if PUBLIC_BASE_URL.startswith("http://") and "localhost" not in PUBLIC_BASE_URL and "127.0.0.1" not in PUBLIC_BASE_URL:
    PUBLIC_BASE_URL = PUBLIC_BASE_URL.replace("http://", "https://", 1)

PORT = int(os.getenv("PORT", "8000"))

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is required. Set it in .env or Railway Variables.")

# Ensure image dir exists (database.py already creates it, but be defensive)
Path(IMAGES_DIR).mkdir(parents=True, exist_ok=True)

# Global bot application reference
ptb_app: Optional[Application] = None
_use_webhook = False
_webhook_path = "/webhook"


# --------------------------- Pydantic models ---------------------------

class ApplyRequest(BaseModel):
    device_id: str
    pending_id: int
    screen: str = "both"   # "home", "lock", or "both"


# --------------------------- FastAPI app ---------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global ptb_app, _use_webhook
    init_db()
    logger.info(f"Database initialized (DB_PATH={DB_PATH}, IMAGES_DIR={IMAGES_DIR}, DATA_DIR={DATA_DIR})")

    # Build PTB application **without** the built-in updater.
    # We will either run polling manually or use webhooks via FastAPI.
    ptb_app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .updater(None)   # Important for webhook or custom polling
        .build()
    )

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
            "To connect me to your Android app:\n"
            "1. Open the Wallpaper Sync app on your phone\n"
            "2. Tap 'Connect to Telegram'\n"
            "3. Send me the link (or tap it)\n\n"
            "Once connected, just send me photos and they can become your phone's wallpaper."
        )
        return

    device_id = args[0].strip()
    if not device_id or len(device_id) < 8:
        await update.message.reply_text("Invalid connect code. Please generate a fresh link from the Android app.")
        return

    username = chat.username
    first_name = chat.first_name

    link_device_to_chat(device_id, chat.id, username=username, first_name=first_name)

    await update.message.reply_text(
        f"✅ Connected!\n\n"
        f"Your Telegram chat is now linked to device <code>{device_id}</code>.\n\n"
        "Send me any photo (as a normal message) and I'll make it available for your Android app to set as wallpaper.\n\n"
        "Tip: You can also forward photos here from other chats."
    )
    logger.info(f"Linked device {device_id} <-> chat {chat.id} (@{username})")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Wallpaper Sync Bot\n\n"
        "• Send me a photo → it becomes available in your Android app\n"
        "• The app can set it as your home or lock screen wallpaper\n"
        "• After the app applies it, I'll send you a confirmation here\n\n"
        "Generate the connect link from inside the Android app."
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    device_id = get_device_for_chat(chat_id)
    if device_id:
        await update.message.reply_text(f"✅ You are connected.\nDevice ID: <code>{device_id}</code>")
    else:
        await update.message.reply_text("Not connected yet. Generate a link from the Android app and send it here.")


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive a photo, download the best quality version, register it as pending for the linked device."""
    chat = update.effective_chat
    message = update.message

    device_id = get_device_for_chat(chat.id)
    if not device_id:
        await message.reply_text(
            "You're not connected to any Android device yet.\n"
            "Open the Wallpaper Sync app on your phone, generate a connect link, and send it to me first."
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
        # file.file_path is something like 'photos/file_123.jpg'

        # Create a nice local filename
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        ext = Path(file.file_path).suffix or ".jpg"
        filename = f"{timestamp}_{chat.id}_{file_id[-8:]}{ext}"
        dest_path = IMAGES_DIR / filename

        # Download using httpx for full control (or use file.download_to_drive)
        # PTB's download_to_drive also works:
        # await file.download_to_drive(custom_path=str(dest_path))
        async with httpx.AsyncClient(timeout=60) as client:
            url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file.file_path}"
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

        await message.reply_text(
            f"📸 Photo received!\n"
            f"Open the Wallpaper Sync app on your phone and tap 'Sync' to see it.\n"
            f"(Pending ID: {pending_id})"
        )

    except Exception as e:
        logger.exception("Failed to download or register photo")
        await message.reply_text("Sorry, I couldn't process that photo. Please try again.")


async def text_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Send me a photo to make it available as a wallpaper for your connected Android device.\n"
        "Use /help for more info."
    )


# --------------------------- Entrypoint ---------------------------

if __name__ == "__main__":
    import uvicorn
    print("Starting Telegram Wallpaper Sync server + bot...")
    print(f"Public base URL for images: {PUBLIC_BASE_URL}")
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)
