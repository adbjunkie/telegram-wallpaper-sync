# Railway-friendly Dockerfile for Telegram Wallpaper Sync server + bot
#
# Works in two common Railway setups:
#   A) Root directory = server/  (simple)  → Dockerfile is at build root
#   B) Monorepo from repo root       → railway.json points to server/Dockerfile and we copy from server/
FROM python:3.11-slim

WORKDIR /app

# System deps (if you ever need them for Pillow etc. in the future)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better layer caching
COPY server/requirements.txt ./requirements.txt 2>/dev/null || COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the server code (handles both monorepo and subdirectory build contexts)
COPY server/ . 2>/dev/null || COPY . .

# Railway will set PORT. Prepare the persistent data directory (mount a Volume here).
RUN mkdir -p /data

ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/data
# TELEGRAM_BOT_TOKEN and PUBLIC_BASE_URL must be set in Railway Variables

# Railway provides $PORT at runtime
EXPOSE 8080

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
