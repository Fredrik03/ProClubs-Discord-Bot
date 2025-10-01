from __future__ import annotations

import sqlite3
import threading
from typing import Any, Dict


_LOCK = threading.Lock()
_DB_PATH = "guild_settings.sqlite3"


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    with _get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id TEXT PRIMARY KEY,
                club_id TEXT,
                platform TEXT,
                region TEXT,
                pool TEXT,
                channel_id INTEGER
            )
        """)
        conn.commit()


_init_db()


def get_guild_settings(guild_id: int) -> Dict[str, Any]:
    with _LOCK:
        with _get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM guild_settings WHERE guild_id = ?", (str(guild_id),)
            ).fetchone()
            if row:
                return dict(row)
            return {}


def set_guild_settings(guild_id: int, updates: Dict[str, Any]) -> Dict[str, Any]:
    with _LOCK:
        with _get_connection() as conn:
            current = get_guild_settings(guild_id)
            current.update({k: v for k, v in updates.items() if v is not None})
            conn.execute(
                """
                INSERT OR REPLACE INTO guild_settings (guild_id, club_id, platform, region, pool, channel_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(guild_id),
                    current.get("club_id"),
                    current.get("platform"),
                    current.get("region"),
                    current.get("pool"),
                    current.get("channel_id"),
                ),
            )
            conn.commit()
            return current


def all_guild_settings() -> Dict[str, Any]:
    with _LOCK:
        with _get_connection() as conn:
            rows = conn.execute("SELECT * FROM guild_settings").fetchall()
            return {row["guild_id"]: dict(row) for row in rows}

