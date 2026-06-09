# Railway Dockerfile for Telegram Wallpaper Sync
#
# IMPORTANT: In Railway, when deploying this repo, set the service's
# "Root Directory" to `server`. This makes the build context the server/ folder,
# so all COPY commands below work simply.

FROM python:3.11-slim

WORKDIR /app

# Install build deps (kept minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (better caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the server code
COPY . .

# Prepare persistent data dir (attach a Railway Volume at /data)
RUN mkdir -p /data

ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/data

# Railway sets the $PORT env var. We default to 8080 for local/Docker testing.
EXPOSE 8080

# Use exec form (JSON) to avoid the JSONArgsRecommended warning.
# Railway will route the correct port regardless.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
