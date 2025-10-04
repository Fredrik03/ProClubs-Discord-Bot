"""
Database functions for guild settings and player milestones.

This module handles all SQLite database operations for the bot.

TABLES:
-------
1. settings:
   - Stores guild configuration (club_id, platform, channel_id, etc.)
   - Tracks last_match_id to prevent duplicate match posts
   - One row per Discord guild/server

2. player_milestones:
   - Records when players achieve milestones (goals, assists, matches, MOTM)
   - Prevents duplicate milestone announcements
   - Primary key: (guild_id, player_name, milestone_type, milestone_value)

3. club_members_cache:
   - Caches player names for autocomplete in slash commands
   - Updated when /clubstats or /playerstats is run
   - Improves user experience by showing current roster

DATABASE LOCATION:
------------------
data/guild_settings.sqlite3 (relative to project root)
This directory is used for Docker volume mounting.

LOGGING:
--------
All database operations are logged with [Database] prefix for easy debugging.
"""
import sqlite3
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger('ProClubsBot.Database')

# Use data directory for persistence in Docker
DATA_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DATA_DIR / "guild_settings.sqlite3"


def init_db():
    """Initialize database tables."""
    try:
        # Ensure data directory exists
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        logger.info(f"Initializing database at: {DB_PATH.absolute()}")
        with sqlite3.connect(DB_PATH) as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    guild_id    INTEGER PRIMARY KEY,
                    club_id     INTEGER,
                    platform    TEXT,           -- common-gen5 / common-gen4
                    channel_id  INTEGER,        -- where new matches get posted
                    milestone_channel_id INTEGER, -- where milestones get posted
                    last_match_id TEXT,         -- last posted matchId
                    autopost    INTEGER DEFAULT 1,
                    updated_at  TEXT NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS player_milestones (
                    guild_id    INTEGER,
                    player_name TEXT,
                    milestone_type TEXT,        -- 'goals', 'assists', 'matches', 'motm'
                    milestone_value INTEGER,    -- e.g. 50 for "50 goals"
                    achieved_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, player_name, milestone_type, milestone_value)
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS club_members_cache (
                    guild_id    INTEGER,
                    player_name TEXT,
                    cached_at   TEXT NOT NULL,
                    PRIMARY KEY (guild_id, player_name)
                )
            """)
            
            # Migration: Add milestone_channel_id column if it doesn't exist
            cursor = db.execute("PRAGMA table_info(settings)")
            columns = [row[1] for row in cursor.fetchall()]
            if "milestone_channel_id" not in columns:
                db.execute("ALTER TABLE settings ADD COLUMN milestone_channel_id INTEGER")
        return True
    except Exception as e:
        raise RuntimeError(f"Failed to initialize database: {e}") from e


def upsert_settings(guild_id: int, **fields):
    """
    Update or insert guild settings in the database.
    Uses SQLite's UPSERT functionality to update existing records or insert new ones.
    
    Args:
        guild_id: Discord guild ID
        **fields: Any settings fields to update (club_id, platform, channel_id, etc.)
    """
    fields["updated_at"] = datetime.utcnow().isoformat()
    cols = ", ".join(fields.keys())
    qmarks = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{k}=excluded.{k}" for k in fields)
    
    logger.info(f"[Database] Upserting settings for guild {guild_id}: {fields}")
    
    try:
        with sqlite3.connect(DB_PATH) as db:
            db.execute(
                f"""
                INSERT INTO settings (guild_id, {cols})
                VALUES (?, {qmarks})
                ON CONFLICT(guild_id) DO UPDATE SET {updates}
                """,
                (guild_id, *fields.values()),
            )
            db.commit()
        
        logger.info(f"[Database] ✅ Successfully saved settings for guild {guild_id}")
    except Exception as e:
        logger.error(f"[Database] ❌ Failed to upsert settings for guild {guild_id}: {e}", exc_info=True)
        raise


def get_settings(guild_id: int):
    """
    Retrieve guild settings from the database.
    
    Args:
        guild_id: Discord guild ID
    
    Returns:
        Dictionary with guild settings or None if not found
    """
    logger.debug(f"[Database] Fetching settings for guild {guild_id}")
    try:
        with sqlite3.connect(DB_PATH) as db:
            cur = db.execute(
                "SELECT guild_id, club_id, platform, channel_id, last_match_id, autopost, milestone_channel_id FROM settings WHERE guild_id=?",
                (guild_id,),
            )
            row = cur.fetchone()
            if not row:
                logger.debug(f"[Database] No settings found for guild {guild_id}")
                return None
            keys = ["guild_id", "club_id", "platform", "channel_id", "last_match_id", "autopost", "milestone_channel_id"]
            result = dict(zip(keys, row))
            logger.debug(f"[Database] Retrieved settings for guild {guild_id}: club_id={result.get('club_id')}, platform={result.get('platform')}, autopost={result.get('autopost')}")
            return result
    except Exception as e:
        logger.error(f"[Database] ❌ Failed to get settings for guild {guild_id}: {e}", exc_info=True)
        raise


def set_last_match_id(guild_id: int, match_id: str):
    """
    Update the last posted match ID for a guild.
    This prevents the same match from being posted multiple times.
    
    Args:
        guild_id: Discord guild ID
        match_id: Match ID from EA API to store
    """
    logger.debug(f"[Database] Updating last_match_id for guild {guild_id} to {match_id}")
    try:
        with sqlite3.connect(DB_PATH) as db:
            db.execute(
                "UPDATE settings SET last_match_id=?, updated_at=? WHERE guild_id=?",
                (match_id, datetime.utcnow().isoformat(), guild_id),
            )
            db.commit()
        logger.info(f"[Database] ✅ Updated last_match_id for guild {guild_id} to {match_id}")
    except Exception as e:
        logger.error(f"[Database] ❌ Failed to update last_match_id for guild {guild_id}: {e}", exc_info=True)
        raise


def get_all_guild_settings():
    """
    Get settings for all guilds configured in the database.
    Used by the match polling loop to check all guilds for new matches.
    
    Returns:
        List of tuples: (guild_id, club_id, platform, channel_id, last_match_id, autopost)
    """
    logger.debug("[Database] Fetching settings for all guilds")
    try:
        with sqlite3.connect(DB_PATH) as db:
            cur = db.execute(
                "SELECT guild_id, club_id, platform, channel_id, last_match_id, autopost FROM settings"
            )
            rows = cur.fetchall()
        logger.debug(f"[Database] Found {len(rows)} guild(s) in database")
        return rows
    except Exception as e:
        logger.error(f"[Database] ❌ Failed to get all guild settings: {e}", exc_info=True)
        raise


def has_milestone_been_announced(guild_id: int, player_name: str, milestone_type: str, milestone_value: int) -> bool:
    """Check if a milestone has already been announced."""
    with sqlite3.connect(DB_PATH) as db:
        cur = db.execute(
            "SELECT 1 FROM player_milestones WHERE guild_id=? AND player_name=? AND milestone_type=? AND milestone_value=?",
            (guild_id, player_name, milestone_type, milestone_value),
        )
        return cur.fetchone() is not None


def record_milestone(guild_id: int, player_name: str, milestone_type: str, milestone_value: int):
    """Record that a milestone has been announced."""
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            """
            INSERT OR IGNORE INTO player_milestones (guild_id, player_name, milestone_type, milestone_value, achieved_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (guild_id, player_name, milestone_type, milestone_value, datetime.utcnow().isoformat()),
        )


def cache_club_members(guild_id: int, player_names: list[str]):
    """
    Cache club member names for autocomplete in slash commands.
    This is updated whenever /clubstats or /playerstats is called.
    
    Args:
        guild_id: Discord guild ID
        player_names: List of player names to cache
    """
    logger.debug(f"[Database] Caching {len(player_names)} player names for guild {guild_id}")
    try:
        with sqlite3.connect(DB_PATH) as db:
            # Clear old cache for this guild
            db.execute("DELETE FROM club_members_cache WHERE guild_id=?", (guild_id,))
            
            # Insert new cache
            now = datetime.utcnow().isoformat()
            db.executemany(
                "INSERT INTO club_members_cache (guild_id, player_name, cached_at) VALUES (?, ?, ?)",
                [(guild_id, name, now) for name in player_names],
            )
            db.commit()
        logger.debug(f"[Database] ✅ Cached player names for guild {guild_id}")
    except Exception as e:
        logger.error(f"[Database] ❌ Failed to cache club members for guild {guild_id}: {e}", exc_info=True)
        raise


def get_cached_club_members(guild_id: int) -> list[str]:
    """Get cached club member names for a guild."""
    with sqlite3.connect(DB_PATH) as db:
        cur = db.execute(
            "SELECT player_name FROM club_members_cache WHERE guild_id=? ORDER BY player_name",
            (guild_id,),
        )
        return [row[0] for row in cur.fetchall()]


