# PUSH NOW — GitHub + Railway

Run these commands from inside the `telegram-wallpaper-sync` folder:

```bash
git init
git add .
git commit -m "Telegram Wallpaper Sync - Railway ready"
```

Then create a new repo on GitHub and push:

```bash
git remote add origin https://github.com/YOUR_USERNAME/NAME_OF_REPO.git
git branch -M main
git push -u origin main
```

After push:
- Go to Railway
- Deploy from that GitHub repo
- Keep **Root Directory** at the repository root
- Add the two variables + Volume as described in QUICKSTART.md

That's the entire flow. No local server, no ngrok.
