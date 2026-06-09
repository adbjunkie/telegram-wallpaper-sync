# Server + Bot (Python)

## Critical Railway Fix for PORT

The server was failing to start with:

`Error: Invalid value for '--port': '$PORT' is not a valid integer.`

**Root cause:** The `startCommand` in `railway.json` was passing `$PORT` literally to uvicorn without shell expansion. Railway's custom startCommand does not run through a shell by default.

### What was fixed:
- Removed the overriding `startCommand` from `railway.json` (so the Dockerfile's CMD is used).
- Updated `Procfile` to use `sh -c "..."` wrapper for safety.
- The `Dockerfile` already uses the correct form: `CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]`

### After pushing these changes:
1. Railway will rebuild using the Dockerfile CMD.
2. The server should start successfully on the correct port.
3. On startup, if `PUBLIC_BASE_URL` is set to your `https://...up.railway.app`, the lifespan will automatically register the Telegram webhook.

## Required Railway Variables (set these!)
- `TELEGRAM_BOT_TOKEN` = your bot token from @BotFather
- `PUBLIC_BASE_URL` = `https://your-service.up.railway.app`  (exact https URL, no trailing slash needed)

## Volume (for persistence)
- Attach a Volume mounted at `/data` (so the SQLite DB and received images survive restarts).

## Root Directory
- When connecting the GitHub repo in Railway, keep **Root Directory** at the repository root. The repo-level `railway.json` points at `server/Dockerfile`.

Once the server is running and the webhook is set, the Telegram bot will respond to messages (including photos for wallpapers).

Check the Railway logs after deploy for lines like:
- "Telegram webhook set to: https://..."
- "Server ready..."

If you see the port error again, double-check that the latest code (with the railway.json change) was deployed.
