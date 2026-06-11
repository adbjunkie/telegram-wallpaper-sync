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
import re
import time
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
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from bs4 import BeautifulSoup
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
    get_connected_users,
    unlink_device_from_chat,
    add_batch_pending_wallpapers,
    add_wallpaper_upload,
    get_all_devices_for_chat,
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


class BatchUploadRequest(BaseModel):
    device_id: str
    device_ids: list[str]
    screen: str = "both"


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


@app.get("/push_status")
async def push_status(device_id: str = Query(..., min_length=8)):
    token = get_push_token(device_id)
    return {
        "device_id": device_id,
        "has_fcm_token": bool(token),
        "firebase_ready": _firebase_ready or bool(firebase_admin._apps),
    }


@app.get("/connected")
async def get_connected(device_id: str = Query(..., min_length=8)):
    """Return all Telegram users linked to this device."""
    users = get_connected_users(device_id)
    bot_username = (await ptb_app.bot.get_me()).username if ptb_app and ptb_app.bot else "bot"
    return {
        "device_id": device_id,
        "connected": [
            {
                "chat_id": u["chat_id"],
                "username": u.get("username"),
                "first_name": u.get("first_name"),
                "linked_at": u.get("linked_at"),
                "avatar_url": f"https://t.me/i/userpic/320/{u['username'] or u['chat_id']}.jpg",
                "talk_link": f"https://t.me/{bot_username}?start={device_id}",
            }
            for u in users
        ],
        "share_link": f"https://t.me/{bot_username}?start={device_id}" if bot_username else None,
    }


@app.post("/unlink")
async def unlink_user(chat_id: int = Query(...), device_id: str = Query(..., min_length=8)):
    """Remove a connected user from a device (device owner can kick people)."""
    unlink_device_from_chat(device_id, chat_id)
    return {"ok": True, "chat_id": chat_id, "device_id": device_id}


@app.post("/batch_send")
async def batch_send(device_id: str = Query(...), device_ids: str = Query(...), screen: str = Query("both")):
    """
    Placeholder for batch send. Real implementation requires the image file.
    This will be extended with multipart upload from the Android app.
    """
    target_ids = [d.strip() for d in device_ids.split(",") if d.strip()]
    if not target_ids:
        raise HTTPException(status_code=400, detail="No device_ids provided")
    return {
        "ok": True,
        "device_id": device_id,
        "target_devices": target_ids,
        "screen": screen,
        "note": "Image upload to be implemented via multipart"
    }


@app.get("/landing/{device_id}")
async def landing_page(device_id: str):
    """Serve an HTML landing page when someone opens the deep link in a browser (not Telegram)."""
    bot_username = (await ptb_app.bot.get_me()).username if ptb_app and ptb_app.bot else "bot"
    share_link = f"https://t.me/{bot_username}?start={device_id}"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Wallpaper Sync - Connect</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px;
  }}
  .card {{
    background: white; border-radius: 20px; padding: 40px 30px; max-width: 420px; width: 100%;
    text-align: center; box-shadow: 0 20px 60px rgba(0,0,0,0.3);
  }}
  h1 {{ font-size: 28px; margin-bottom: 8px; color: #333; }}
  .emoji {{ font-size: 48px; margin-bottom: 16px; }}
  p {{ color: #666; line-height: 1.6; margin-bottom: 20px; font-size: 15px; }}
  .device-id {{
    background: #f5f5f5; border-radius: 8px; padding: 8px 12px; font-family: monospace;
    font-size: 12px; color: #888; margin-bottom: 20px; word-break: break-all;
  }}
  .btn {{
    display: inline-block; background: #0088cc; color: white; text-decoration: none;
    padding: 14px 32px; border-radius: 12px; font-size: 16px; font-weight: 600;
    transition: transform 0.2s, box-shadow 0.2s; margin: 6px;
  }}
  .btn:hover {{ transform: translateY(-2px); box-shadow: 0 6px 20px rgba(0,136,204,0.4); }}
  .btn-outline {{
    background: white; color: #0088cc; border: 2px solid #0088cc;
  }}
  .steps {{ text-align: left; margin: 20px 0; }}
  .step {{ display: flex; align-items: flex-start; margin-bottom: 12px; }}
  .step-num {{
    background: #667eea; color: white; border-radius: 50%; width: 24px; height: 24px;
    display: flex; align-items: center; justify-content: center; font-size: 12px;
    font-weight: bold; margin-right: 12px; flex-shrink: 0; margin-top: 2px;
  }}
  .step-text {{ color: #555; font-size: 14px; line-height: 1.5; }}
</style>
</head>
<body>
<div class="card">
  <div class="emoji">📱</div>
  <h1>Wallpaper Sync</h1>
  <p>You've been invited to set someone's phone wallpaper. Open this link in Telegram to connect, then send a photo to change their wallpaper instantly.</p>

  <div class="device-id">Device: {device_id[:12]}...</div>

  <a href="{share_link}" class="btn">Open in Telegram</a>
  <a href="https://t.me/{bot_username}" class="btn btn-outline">Go to Bot</a>

  <div class="steps">
    <div class="step">
      <div class="step-num">1</div>
      <div class="step-text">Tap "Open in Telegram" above</div>
    </div>
    <div class="step">
      <div class="step-num">2</div>
      <div class="step-text">Once connected, send any photo to the bot</div>
    </div>
    <div class="step">
      <div class="step-num">3</div>
      <div class="step-text">The phone auto-applies it as wallpaper!</div>
    </div>
  </div>

  <p style="font-size: 12px; color: #aaa; margin-top: 16px;">
    Need Telegram? <a href="https://telegram.org/dl" style="color: #0088cc;">Download it here</a>
  </p>
</div>
</body>
</html>"""
    return HTMLResponse(content=html)


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
    logger.info(f"Registered FCM token for device {req.device_id}")
    return {"ok": True, "device_id": req.device_id}


@app.get("/history")
async def get_history(device_id: str = Query(...), limit: int = 20):
    items = get_history_for_device(device_id, limit=limit)
    for item in items:
        item["image_url"] = f"{PUBLIC_BASE_URL}/images/{item['filename']}"
    return {"device_id": device_id, "history": items}


# --------------------------- PornPics Proxy ---------------------------

PORNPICS_BASE = "https://www.pornpics.com"
PORNPICS_CDN = "https://cdni.pornpics.com"

# Simple in-memory cache: {key: (data, expiry_timestamp)}
_cache = {}

def cache_get(key: str) -> Optional[dict]:
    entry = _cache.get(key)
    if entry and entry[1] > time.time():
        return entry[0]
    return None

def cache_set(key: str, data: dict, ttl: int = 300):
    _cache[key] = (data, time.time() + ttl)

async def _fetch_pornpics_gallery_images(gallery_url: str) -> dict:
    """Scrape a PornPics gallery page for all images + metadata."""
    cached = cache_get(gallery_url)
    if cached:
        return cached

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(gallery_url, headers={
                "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36",
                "Accept": "text/html,*/*",
            })
            resp.raise_for_status()
            html = resp.text

        soup = BeautifulSoup(html, "html.parser")

        title = soup.find("h1").text.strip() if soup.find("h1") else ""
        desc_match = re.search(r'"description":\s*"([^"]+)"', html)
        description = desc_match.group(1) if desc_match else title

        # Extract categories
        categories = []
        cat_container = soup.select_one(".gallery-info .tags:nth-of-type(3) a span, #content .gallery-info .tags a[href*='/categories/'] span")
        if not cat_container:
            cat_links = soup.select("a[href*='/categories/'] span")
            categories = list(set(c.text.strip() for c in cat_links if c.text.strip()))

        # Extract tags
        tags = []
        tag_links = soup.select("a[href*='/tags/'] span")
        tags = list(set(t.text.strip() for t in tag_links if t.text.strip()))

        # Extract models
        models = []
        model_links = soup.select("a[href*='/pornstars/'] span")
        models = list(set(m.text.strip() for m in model_links if m.text.strip()))

        # Extract image URLs from the tiles
        images = []
        tiles = soup.select("#tiles .thumbwook img")
        if not tiles:
            tiles = soup.select(".thumbwook img[data-src]")

        for img in tiles:
            src = img.get("data-src") or img.get("src") or ""
            if src and "pornpics.com" in src:
                thumb = src.strip()
                full = thumb.replace("/460/", "/1280/")
                alt = img.get("alt", "")
                images.append({"thumb": thumb, "full": full, "alt": alt, "width": 1280})

        if not images:
            # Fallback: try regex from inline JS or script tags
            js_pattern = re.findall(r'"(https://cdni\.pornpics\.com/[^"]+\.jpg)"', html)
            for url in set(js_pattern):
                full = url.replace("/460/", "/1280/")
                images.append({"thumb": url, "full": full, "alt": "", "width": 1280})

        result = {
            "title": title or description,
            "description": description,
            "categories": categories,
            "tags": tags,
            "models": models,
            "image_count": len(images),
            "images": images,
        }
        cache_set(gallery_url, result, ttl=600)
        return result

    except Exception as e:
        logger.warning(f"Failed to scrape gallery {gallery_url}: {e}")
        return {"title": "", "categories": [], "tags": [], "models": [], "image_count": 0, "images": []}


@app.get("/browse/search")
async def browse_search(q: str = Query(...), offset: int = Query(0), limit: int = Query(20)):
    """Search PornPics galleries (proxied)."""
    cache_key = f"search:{q}:{offset}:{limit}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    try:
        url = f"{PORNPICS_BASE}/search/srch.php"
        params = {"q": q, "offset": offset, "limit": limit, "lang": "en"}
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params=params, headers={"Accept": "application/json"})
            resp.raise_for_status()
            data = resp.json()

        galleries = []
        for item in data:
            galleries.append({
                "gallery_id": item.get("gid", ""),
                "title": item.get("desc", ""),
                "gallery_url": item.get("g_url", ""),
                "thumbnail": item.get("t_url_460") or item.get("t_url", ""),
                "thumb_small": item.get("t_url", ""),
                "height": item.get("h", 0),
            })

        result = {"query": q, "offset": offset, "limit": limit, "count": len(galleries), "galleries": galleries}
        cache_set(cache_key, result, ttl=120)
        return result

    except Exception as e:
        logger.warning(f"Browse search failed: {e}")
        raise HTTPException(status_code=502, detail=f"PornPics search failed: {str(e)}")


@app.get("/browse/gallery")
async def browse_gallery(url: str = Query(...)):
    """Get all images and metadata for a PornPics gallery."""
    return await _fetch_pornpics_gallery_images(url)


@app.get("/browse/popular")
async def browse_popular(offset: int = Query(0), limit: int = Query(20)):
    """Get popular galleries (empty search query returns popular)."""
    return await browse_search(q="", offset=offset, limit=limit)


@app.get("/browse/image")
async def browse_image_proxy(url: str = Query(...)):
    """Proxy a PornPics image through your server (avoids CDN hotlinking issues)."""
    cache_key = f"img:{url}"
    cached = cache_get(cache_key)
    if cached:
        from fastapi.responses import Response
        return Response(content=cached["body"], media_type=cached["content_type"])

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.pornpics.com/",
            })
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "image/jpeg")
            cache_set(cache_key, {"body": resp.content, "content_type": content_type}, ttl=3600)
            from fastapi.responses import Response
            return Response(content=resp.content, media_type=content_type)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Image proxy failed: {str(e)}")


@app.get("/browse/categories")
async def browse_categories():
    """Return popular search categories for the browser."""
    return {
        "categories": [
            {"id": "milf", "name": "MILF", "icon": "👩"},
            {"id": "teen", "name": "Teen", "icon": "🌸"},
            {"id": "blonde", "name": "Blonde", "icon": "👱"},
            {"id": "brunette", "name": "Brunette", "icon": "👩‍🦰"},
            {"id": "asian", "name": "Asian", "icon": "🎎"},
            {"id": "latina", "name": "Latina", "icon": "💃"},
            {"id": "ebony", "name": "Ebony", "icon": "👩🏿"},
            {"id": "redhead", "name": "Redhead", "icon": "👩‍🦰"},
            {"id": "big tits", "name": "Big Tits", "icon": "🍒"},
            {"id": "big ass", "name": "Big Ass", "icon": "🍑"},
            {"id": "lingerie", "name": "Lingerie", "icon": "👙"},
            {"id": "outdoor", "name": "Outdoor", "icon": "🌳"},
            {"id": "pov", "name": "POV", "icon": "👁️"},
            {"id": "anal", "name": "Anal", "icon": "🎯"},
            {"id": "lesbian", "name": "Lesbian", "icon": "👩‍❤️‍👩"},
            {"id": "solo", "name": "Solo", "icon": "💋"},
            {"id": "hardcore", "name": "Hardcore", "icon": "🔥"},
            {"id": "blowjob", "name": "Blowjob", "icon": "👄"},
        ]
    }



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
    landing_url = f"{PUBLIC_BASE_URL}/landing/{device_id}"

    await update.message.reply_text(
        f"✅ Connected to this wallpaper link.\n\n"
        f"Target device: <code>{device_id}</code>\n\n"
        "Send or forward any photo here and it will be queued for that phone to apply automatically.\n\n"
        f"Share this link with anyone you want to let change this wallpaper:\n{share_link}\n\n"
        f"If they don't have Telegram, send them:\n{landing_url}"
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
