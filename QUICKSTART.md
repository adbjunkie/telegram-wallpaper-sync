# Quickstart — GitHub + Railway (No Local Testing)

This is the direct path. No ngrok. No local server.

## 1. Create the Telegram Bot
1. Message `@BotFather`
2. `/newbot` → name + username (e.g. `mywallpapersyncbot`)
3. Copy the **HTTP API token**

## 2. Push to GitHub
```bash
cd telegram-wallpaper-sync
git init
git add .
git commit -m "Telegram wallpaper sync - ready for Railway"
git remote add origin https://github.com/YOURNAME/telegram-wallpaper-sync.git
git branch -M main
git push -u origin main
```

## 3. Deploy on Railway
1. Go to Railway → New Project → Deploy from GitHub repo.
2. Select this repo.
3. Keep the service **Root Directory** at the repository root. The included `railway.json` points Railway at `server/Dockerfile`.
4. Variables:
   - `TELEGRAM_BOT_TOKEN` = your bot token
   - `PUBLIC_BASE_URL` = `https://your-service.up.railway.app` (Railway shows this after the first deploy)
5. Settings → Volumes → Add a Volume mounted at path `/data` (this is required so the SQLite database and received photos survive deploys and restarts).
6. Deploy the service.

After the deploy succeeds, the server will start in webhook mode automatically (the code detects the https PUBLIC_BASE_URL).

## 4. Configure the Android App
1. Open the `android/` folder contents in Android Studio (New Project → Empty Activity with Compose, then replace files).
2. Edit the two constants at the very top of `MainActivity.kt`:
   ```kotlin
   const val BOT_USERNAME = "mywallpapersyncbot"          // without @
   const val BASE_URL = "https://your-service.up.railway.app"
   ```
3. Add the dependencies listed in `android/README_ANDROID.md`.
4. Build → install the APK on your phone.

## 5. Use it
- Open the Android app.
- Tap "Copy Link" or "Open in Telegram".
- Send photos to the bot.
- In the app, tap "Sync Now" → choose Home / Lock / Both.

The bot will send a confirmation photo back when the wallpaper is applied.

That's it. Everything else (webhook, persistence, image serving) is handled by the Railway setup.

See the main README for more details on volumes, custom domains, etc.

- Wallpaper doesn't visually change on emulator → use a physical phone.
- Bot not responding → confirm `TELEGRAM_BOT_TOKEN` is correct and the python process is alive.

See the main [README.md](./README.md) for architecture, limitations, and how to add real push notifications later.
