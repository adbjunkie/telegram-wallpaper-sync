import sqlite3
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

# Railway-friendly configuration.
# Set DATA_DIR=/data (and attach a Railway Volume at /data) for persistence across deploys/restarts.
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
DB_PATH = Path(os.getenv("DB_PATH")) if os.getenv("DB_PATH") else DATA_DIR / "wallpaper_sync.db"
IMAGES_DIR = Path(os.getenv("IMAGES_DIR")) if os.getenv("IMAGES_DIR") else DATA_DIR / "received_images"

def init_db():
    print(f"[DB] Initializing with DATA_DIR={DATA_DIR}, DB_PATH={DB_PATH}, IMAGES_DIR={IMAGES_DIR}")
    db_dir = DB_PATH.parent
    db_dir.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    # Ensure writable for volume mounts (e.g. Railway). Ignore if not permitted.
    try:
        os.chmod(db_dir, 0o777)
        os.chmod(IMAGES_DIR, 0o777)
    except PermissionError:
        pass
    # Explicitly create the DB file if it doesn't exist to ensure the volume is writable
    if not DB_PATH.exists():
        try:
            with DB_PATH.open('w') as f:
                f.write('')
            print(f"[DB] Pre-created DB file at {DB_PATH}")
        except Exception as e:
            print(f"[DB] Failed to pre-create DB file {DB_PATH}: {e}")
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS links (
                device_id TEXT PRIMARY KEY,
                chat_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                linked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_wallpapers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                original_file_id TEXT,
                chat_id INTEGER NOT NULL,
                received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                applied INTEGER DEFAULT 0,
                applied_at TIMESTAMP,
                screen TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS applied_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                screen TEXT,
                chat_id INTEGER
            )
        """)
        conn.commit()

@contextmanager
def get_conn():
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def link_device_to_chat(device_id: str, chat_id: int, username: Optional[str] = None, first_name: Optional[str] = None) -> bool:
    """Link (or re-link) a device to a Telegram chat. Returns True if newly created or updated."""
    with get_conn() as conn:
        # Upsert style
        conn.execute("""
            INSERT INTO links (device_id, chat_id, username, first_name, linked_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(device_id) DO UPDATE SET
                chat_id = excluded.chat_id,
                username = excluded.username,
                first_name = excluded.first_name,
                linked_at = CURRENT_TIMESTAMP
        """, (device_id, chat_id, username, first_name))
        conn.commit()
    return True

def get_chat_for_device(device_id: str) -> Optional[int]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT chat_id FROM links WHERE device_id = ?", (device_id,)
        ).fetchone()
        return row["chat_id"] if row else None

def get_device_for_chat(chat_id: int) -> Optional[str]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT device_id FROM links WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return row["device_id"] if row else None

def add_pending_wallpaper(device_id: str, filename: str, original_file_id: str, chat_id: int) -> int:
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO pending_wallpapers (device_id, filename, original_file_id, chat_id)
            VALUES (?, ?, ?, ?)
        """, (device_id, filename, original_file_id, chat_id))
        conn.commit()
        return cur.lastrowid

def get_pending_for_device(device_id: str) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, device_id, filename, original_file_id, chat_id, received_at, applied, screen
            FROM pending_wallpapers
            WHERE device_id = ? AND applied = 0
            ORDER BY received_at DESC
        """, (device_id,)).fetchall()
        return [dict(r) for r in rows]

def get_pending_by_id(pending_id: int) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("""
            SELECT id, device_id, filename, original_file_id, chat_id, received_at, applied, screen
            FROM pending_wallpapers
            WHERE id = ?
        """, (pending_id,)).fetchone()
        return dict(row) if row else None

def mark_wallpaper_applied(pending_id: int, screen: str = "both") -> bool:
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        row = conn.execute("""
            SELECT device_id, filename, chat_id FROM pending_wallpapers WHERE id = ?
        """, (pending_id,)).fetchone()
        if not row:
            return False

        conn.execute("""
            UPDATE pending_wallpapers
            SET applied = 1, applied_at = ?, screen = ?
            WHERE id = ?
        """, (now, screen, pending_id))

        # Also write to history
        conn.execute("""
            INSERT INTO applied_history (device_id, filename, applied_at, screen, chat_id)
            VALUES (?, ?, ?, ?, ?)
        """, (row["device_id"], row["filename"], now, screen, row["chat_id"]))
        conn.commit()
        return True

def get_history_for_device(device_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, device_id, filename, applied_at, screen
            FROM applied_history
            WHERE device_id = ?
            ORDER BY applied_at DESC
            LIMIT ?
        """, (device_id, limit)).fetchall()
        return [dict(r) for r in rows]

def get_chat_id_for_pending(pending_id: int) -> Optional[int]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT chat_id FROM pending_wallpapers WHERE id = ?", (pending_id,)
        ).fetchone()
        return row["chat_id"] if row else None
