# Telegram Wallpaper Sync

Android app + Telegram bot that lets you send images to a bot which become your phone's wallpaper.

**Flow:** Android app → generate Telegram deep link → send photos to the bot → they appear in the app → set as home/lock wallpaper → bot confirms back in Telegram.

This project is built to be deployed on **Railway** from **GitHub** (webhook + persistent volume recommended).

---

## Deploy in 5 minutes (GitHub + Railway)

1. Push this folder to a GitHub repo.
2. In Railway: New Project → Deploy from GitHub → select repo.
3. **Set Root Directory to `server`** (important).
4. Add these Variables:
   - `TELEGRAM_BOT_TOKEN` = (from @BotFather)
   - `PUBLIC_BASE_URL` = `https://your-app.up.railway.app` (Railway gives you this)
5. Add a Volume mounted at `/data` (for DB + images to survive deploys).
6. Deploy.
7. In the Android sources (`android/...`), edit two lines in `MainActivity.kt`:
   - `BOT_USERNAME`
   - `BASE_URL`
8. Build the APK in Android Studio and install.

Full details below. Local testing is possible but not required — this is production-oriented.

---

## Is This Possible?

**Yes, it is possible.** But **not with a Telegram bot alone**.

### Why a bot by itself is not enough
- Telegram bots run on Telegram's servers.
- They have **zero** access to your Android device's settings, `WallpaperManager`, home screen, or files.
- Bots can **receive** photos and **send** messages/photos back perfectly.
- Your phone must run native Android code (`WallpaperManager.setBitmap(...)`) to actually change the system wallpaper.

### The working architecture (this project)
```
[You or friend] --send photo--> [Telegram Bot]
                                    |
                                    v
                              [Backend Server]  (maps Telegram chat <-> your Android device)
                                    |
                        (stores image + marks "pending")
                                    |
                      (push or manual sync) ----> [Your Android App]
                                                        |
                                              WallpaperManager.setBitmap()
                                                        |
                                              (notify backend "done")
                                    |
                                    v
                              [Bot sends confirmation photo + "✅ Set!" back to Telegram]
```

**Pairing flow (one-time):**
1. App generates a unique `device_id` (stored locally).
2. App shows a link: `https://t.me/YourBot?start=DEVICE_ID`
3. You tap it → Telegram opens the bot with a deep-link start parameter.
4. Bot tells the backend "this Telegram `chat_id` now owns this `device_id`".
5. Done. Future photos from that chat target your phone.

**Image flow:**
- Bot receives photo → downloads the highest-res version via Bot API (`getFile` + `https://api.telegram.org/file/bot...`).
- Saves it locally and records a "pending wallpaper" record for your `device_id`.
- Your app (button or future push) asks the backend for pending items.
- App downloads the image, shows preview, "Set as Wallpaper" button.
- App calls `WallpaperManager` (supports home screen + lock screen on modern Android).
- App tells backend it was applied → bot sends the image (or a note) back to you in Telegram as confirmation.

### "Send a screenshot back" — important limitation
- The app **can** send the wallpaper image itself back to the bot/chat ("here is what we set").
- **True home-screen screenshot** (wallpaper + your app icons, widgets, status bar, clock, etc.) is **not easy or reliable**:
  - `WallpaperManager.getDrawable()` gives you only the wallpaper layer.
  - Full screen capture requires either:
    - `MediaProjection` (user sees a scary "cast / screen capture" dialog every time), or
    - AccessibilityService (user must manually enable it in system settings, privacy implications).
  - Many OEMs (Samsung, Xiaomi, etc.) make this even harder.

**Practical compromise (implemented):** After applying, the app can upload/send back the wallpaper bitmap. The Telegram user sees the exact image that is now their wallpaper + a success message. This is what most similar tools do.

---

## Tech Stack (Railway + Production Oriented)

- **Backend + Bot:** Python + FastAPI + Uvicorn + python-telegram-bot (webhook mode on Railway)
- **Persistence:** SQLite + images on Railway Volume (mounted at `/data`)
- **Android client:** Kotlin + Jetpack Compose + Coil + Retrofit/OkHttp
- **Sync:** Manual "Sync now" in v1 (easy to add Firebase Cloud Messaging push later)

---

## Project Structure

```
telegram-wallpaper-sync/
├── README.md
├── QUICKSTART.md               # The "just push it" guide
├── railway.json
├── server/                     # This is what you deploy to Railway
│   ├── main.py                 # FastAPI + bot (webhook aware)
│   ├── database.py
│   ├── requirements.txt
│   ├── Dockerfile
│   ├── Procfile
│   └── .env.example
└── android/                    # Drop these sources into Android Studio
    └── ... (Manifest, MainActivity.kt with Compose UI, etc.)
```
            ├── java/.../wallpaper/
            └── res/
```

---

## Development / Local Testing (Optional)

The project is designed so you **don't need** to run anything locally.

If you really want to test changes before pushing:
- Use the code in `server/` with `python main.py` + ngrok for `PUBLIC_BASE_URL`.
- See the old "local" notes in git history or ask for help.

For the normal flow, follow [QUICKSTART.md](./QUICKSTART.md) → push to GitHub → Railway.

---

## API the Android App Uses (for reference)

All calls include `device_id` (the long UUID the app generated).

- `GET /pending?device_id=...` → list of pending wallpapers with `image_url`
- `POST /apply` → `{device_id, pending_id, success: true, screen: "home" | "lock" | "both"}`
- `GET /history?device_id=...` (optional)

The bot side uses internal DB calls (no public endpoints for Telegram).

---

## Security & Privacy Notes (Important)

- The `device_id` acts as a capability token. Treat the connect link as private.
- Anyone who has your connect link (or guesses the ID) can send images that target your phone.
- For a real public app you would add proper user accounts, rate limiting, image size limits, approval step ("approve this wallpaper?"), HTTPS everywhere, etc.
- Images are stored locally on the server machine. Add cleanup (delete after apply or after N days).
- On production, put images behind signed URLs or auth, not public static files forever.

---

## Future Enhancements (easy to add)

- Firebase Cloud Messaging (FCM) for instant "new wallpaper available" push (no button needed).
- Multiple devices per user (different device_ids).
- Preview + crop/ blur / dim before setting (Android supports wallpaper offsets too).
- Lock screen only vs home only (WallpaperManager.FLAG_LOCK / FLAG_SYSTEM).
- Scheduled / daily wallpaper from a Telegram channel or album.
- "Send current wallpaper back to Telegram" button.
- Support for live wallpapers (more complex).
- Proper auth (magic link or Firebase Auth + Telegram login widget).

---

## Limitations & Gotchas

- Some Chinese OEMs and heavy battery optimization can kill background sync (if you add polling). Use FCM + "high priority" or a foreground service for reliability.
- Very large images: resize before setting (wallpaper doesn't need 4K usually).
- Android 12+ has "Themed icons" and wallpaper colors that affect the UI — the bitmap is still set.
- Emulator: wallpaper setting may not be visually obvious (use a real device for testing).
- Telegram file URLs from `getFile` are only valid for ~1 hour. We download immediately in the bot.

---

## License / Use

This is a starter scaffold for personal/educational use. Adapt it, harden the auth, add your branding, etc.

Enjoy sending photos from Telegram straight onto your phone wallpaper!

If you want push notifications (FCM), nicer UI, image cleanup jobs, or anything else — just ask.

---

## Push it

See [PUSH_NOW.md](./PUSH_NOW.md) + [QUICKSTART.md](./QUICKSTART.md) for the exact commands.

Everything is already set up for Railway (webhooks, volume support, Dockerfile + Procfile + railway.json).

---

## Deploying to Railway + GitHub (Recommended for Production)

This project is set up to deploy easily with Railway (which can pull directly from GitHub).

### 1. Prepare the GitHub repo

```bash
cd telegram-wallpaper-sync

# Make sure .env is NOT committed (it's in .gitignore)
git init
git add .
git commit -m "Initial Telegram Wallpaper Sync scaffold"
git remote add origin https://github.com/YOUR_USERNAME/telegram-wallpaper-sync.git
git branch -M main
git push -u origin main
```

**Important files that should be committed:**
- `server/` (everything except what .gitignore excludes)
- `android/`
- `railway.json`, Dockerfiles, READMEs, etc.

**Do NOT commit:**
- `server/.env`
- `server/*.db`
- `server/received_images/`

### 2. Deploy on Railway

1. Go to [railway.app](https://railway.app) and log in (GitHub login is easiest).
2. **New Project** → **Deploy from GitHub repo**.
3. Select your new repo.
4. Railway should detect the `railway.json` + Dockerfile in the server folder.

   - If it asks for **Root Directory**, you can set it to `server` (simpler), **or** leave it at root and let the `railway.json` point to `server/Dockerfile`.
5. Once the service is created:
   - Go to the service → **Variables** tab:
     - Add `TELEGRAM_BOT_TOKEN` = (your bot token from BotFather)
     - Add `PUBLIC_BASE_URL` = `https://your-generated-domain.up.railway.app`  
       (you'll see the domain in the "Settings" or after the first successful deploy)
   - (Strongly recommended for data to survive deploys)
     - Go to **Settings** → **Volumes** → **Add Volume**
     - Mount path: `/data`
   - Add `DATA_DIR=/data` in Variables (if not already using the volume default in the Dockerfile).

6. Deploy / restart the service.

Railway will automatically set a `PORT`. The code detects `RAILWAY_PUBLIC_DOMAIN` / `PUBLIC_BASE_URL` and will prefer **webhook mode** (much better than polling on PaaS).

After the first successful deploy with a public `PUBLIC_BASE_URL`, the bot should automatically call `setWebhook`.

### 3. Point the Android app at the Railway URL

In `MainActivity.kt` (top of the file):

```kotlin
const val BASE_URL = "https://your-generated-domain.up.railway.app"
```

Rebuild the APK and install on your phone.

### 4. Test the full flow on the live server

- Open Android app → generate link → open in Telegram
- Send photo to bot
- In Android app: Sync → apply
- Bot should reply with the confirmation photo

### 5. Useful Railway tips

- **Logs**: Service → Deployments → click a deployment → View Logs. Great for debugging webhook registration or photo download errors.
- **Custom domain**: You can attach a real domain in Railway if you want (e.g. `wallpaper.yourdomain.com`).
- **Environment separation**: Create a "staging" service if you want.
- **Database / images persistence**: The Volume at `/data` is the key. Without it, every deploy wipes the SQLite DB and all received photos.
- **Scaling**: One worker is fine (`--workers 1` in Procfile). The bot + API are lightweight.
- **Re-setting the webhook**: If you change the domain, just redeploy or restart the service — the lifespan code will call `set_webhook` again.
- **Manual webhook reset**: You can also call the Telegram API directly:
  `https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://your-domain.up.railway.app/webhook`

### Alternative: Deploy only the server folder

If you prefer a clean separation, you can:
- Put the server code in its own GitHub repo later, or
- In Railway, when creating the service, choose the `server` subdirectory as the source.

The current structure works well as a single repo containing both the backend (Railway) and the Android client sources.

---

## Next Steps After Railway Deploy

- Set up a real custom domain (optional but nicer links).
- Add Firebase Cloud Messaging so the Android app gets notified instantly when a new photo arrives (no more "Sync" button).
- Add image cleanup / retention policy.
- Add basic auth or per-user approval step if you ever share the bot with others.
- Build a release Android APK and distribute it (or publish to Play Store).

You're now running a real hosted version of the Telegram → Android wallpaper pipeline! Let me know if you hit any snags during the Railway setup.
