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
            db.execute("""
                CREATE TABLE IF NOT EXISTS player_achievements (
                    guild_id    INTEGER,
                    player_name TEXT,
                    achievement_id TEXT,        -- e.g. 'hat_trick_hero', 'perfect_10'
                    achieved_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, player_name, achievement_id)
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS player_match_history (
                    guild_id    INTEGER,
                    player_name TEXT,
                    match_id    TEXT,
                    goals       INTEGER DEFAULT 0,
                    assists     INTEGER DEFAULT 0,
                    clean_sheet INTEGER DEFAULT 0,  -- 1 if clean sheet, 0 otherwise
                    hat_trick   INTEGER DEFAULT 0,  -- 1 if 3+ goals in match, 0 otherwise
                    assist_hat_trick INTEGER DEFAULT 0,  -- 1 if 3+ assists in match, 0 otherwise
                    position    TEXT,               -- position played in that match (e.g. ST, CAM, ANY)
                    played_at   TEXT NOT NULL,
                    PRIMARY KEY (guild_id, player_name, match_id)
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS player_initialization (
                    guild_id    INTEGER,
                    player_name TEXT,
                    initialized_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, player_name)
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS playoff_stats (
                    guild_id    INTEGER,
                    player_name TEXT,
                    playoff_period TEXT,  -- YYYY-MM format for monthly tracking
                    goals       INTEGER DEFAULT 0,
                    assists     INTEGER DEFAULT 0,
                    total_rating REAL DEFAULT 0.0,
                    matches_played INTEGER DEFAULT 0,
                    playoff_score REAL DEFAULT 0.0,
                    updated_at  TEXT NOT NULL,
                    PRIMARY KEY (guild_id, player_name, playoff_period)
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS playoff_announcements (
                    guild_id    INTEGER,
                    playoff_period TEXT,  -- YYYY-MM format
                    announced_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, playoff_period)
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS playoff_club_stats (
                    guild_id    INTEGER,
                    playoff_period TEXT,  -- YYYY-MM format
                    match_id    TEXT,
                    result      TEXT,  -- W, L, D
                    goals_for   INTEGER DEFAULT 0,
                    goals_against INTEGER DEFAULT 0,
                    clean_sheet INTEGER DEFAULT 0,
                    played_at   TEXT NOT NULL,
                    PRIMARY KEY (guild_id, playoff_period, match_id)
                )
            """)
            
            # Migration: Add milestone_channel_id column if it doesn't exist
            cursor = db.execute("PRAGMA table_info(settings)")
            columns = [row[1] for row in cursor.fetchall()]
            if "milestone_channel_id" not in columns:
                db.execute("ALTER TABLE settings ADD COLUMN milestone_channel_id INTEGER")
            
            # Migration: Add achievement_channel_id column if it doesn't exist
            if "achievement_channel_id" not in columns:
                db.execute("ALTER TABLE settings ADD COLUMN achievement_channel_id INTEGER")
            
            # Migration: Add playoff_summary_channel_id column if it doesn't exist
            if "playoff_summary_channel_id" not in columns:
                db.execute("ALTER TABLE settings ADD COLUMN playoff_summary_channel_id INTEGER")
            
            # Migration: Add hat-trick columns to player_match_history if they don't exist
            cursor = db.execute("PRAGMA table_info(player_match_history)")
            match_history_columns = [row[1] for row in cursor.fetchall()]
            if "hat_trick" not in match_history_columns:
                db.execute("ALTER TABLE player_match_history ADD COLUMN hat_trick INTEGER DEFAULT 0")
            if "assist_hat_trick" not in match_history_columns:
                db.execute("ALTER TABLE player_match_history ADD COLUMN assist_hat_trick INTEGER DEFAULT 0")
            if "position" not in match_history_columns:
                db.execute("ALTER TABLE player_match_history ADD COLUMN position TEXT")
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
                "SELECT guild_id, club_id, platform, channel_id, last_match_id, autopost, milestone_channel_id, achievement_channel_id, playoff_summary_channel_id FROM settings WHERE guild_id=?",
                (guild_id,),
            )
            row = cur.fetchone()
            if not row:
                logger.debug(f"[Database] No settings found for guild {guild_id}")
                return None
            keys = ["guild_id", "club_id", "platform", "channel_id", "last_match_id", "autopost", "milestone_channel_id", "achievement_channel_id", "playoff_summary_channel_id"]
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
    try:
        with sqlite3.connect(DB_PATH) as db:
            cur = db.execute(
                "SELECT 1 FROM player_milestones WHERE guild_id=? AND player_name=? AND milestone_type=? AND milestone_value=?",
                (guild_id, player_name, milestone_type, milestone_value),
            )
            exists = cur.fetchone() is not None
            logger.debug(f"[Database] Milestone check: {player_name} {milestone_value} {milestone_type} = {'already announced' if exists else 'NEW'}")
            return exists
    except Exception as e:
        logger.error(f"[Database] ❌ Failed to check milestone: {e}", exc_info=True)
        raise


def record_milestone(guild_id: int, player_name: str, milestone_type: str, milestone_value: int):
    """Record that a milestone has been announced."""
    logger.debug(f"[Database] Recording milestone: guild={guild_id}, player={player_name}, type={milestone_type}, value={milestone_value}")
    try:
        with sqlite3.connect(DB_PATH) as db:
            db.execute(
                """
                INSERT OR IGNORE INTO player_milestones (guild_id, player_name, milestone_type, milestone_value, achieved_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (guild_id, player_name, milestone_type, milestone_value, datetime.utcnow().isoformat()),
            )
            db.commit()
        logger.debug(f"[Database] ✅ Milestone recorded successfully")
    except Exception as e:
        logger.error(f"[Database] ❌ Failed to record milestone: {e}", exc_info=True)
        raise


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


# ---------- Achievement Functions ----------

def has_achievement_been_earned(guild_id: int, player_name: str, achievement_id: str) -> bool:
    """Check if a player has already earned an achievement."""
    try:
        with sqlite3.connect(DB_PATH) as db:
            cur = db.execute(
                "SELECT 1 FROM player_achievements WHERE guild_id=? AND player_name=? AND achievement_id=?",
                (guild_id, player_name, achievement_id),
            )
            exists = cur.fetchone() is not None
            logger.debug(f"[Database] Achievement check: {player_name} - {achievement_id} = {'already earned' if exists else 'NEW'}")
            return exists
    except Exception as e:
        logger.error(f"[Database] ❌ Failed to check achievement: {e}", exc_info=True)
        raise


def record_achievement(guild_id: int, player_name: str, achievement_id: str):
    """Record that a player has earned an achievement."""
    logger.debug(f"[Database] Recording achievement: guild={guild_id}, player={player_name}, achievement={achievement_id}")
    try:
        with sqlite3.connect(DB_PATH) as db:
            db.execute(
                """
                INSERT OR IGNORE INTO player_achievements (guild_id, player_name, achievement_id, achieved_at)
                VALUES (?, ?, ?, ?)
                """,
                (guild_id, player_name, achievement_id, datetime.utcnow().isoformat()),
            )
            db.commit()
        logger.debug(f"[Database] ✅ Achievement recorded successfully")
    except Exception as e:
        logger.error(f"[Database] ❌ Failed to record achievement: {e}", exc_info=True)
        raise


def get_player_achievement_history(guild_id: int, player_name: str) -> list[dict]:
    """Get all achievements earned by a player."""
    try:
        with sqlite3.connect(DB_PATH) as db:
            cur = db.execute(
                "SELECT achievement_id, achieved_at FROM player_achievements WHERE guild_id=? AND player_name=? ORDER BY achieved_at",
                (guild_id, player_name),
            )
            rows = cur.fetchall()
            return [{"achievement_id": row[0], "achieved_at": row[1]} for row in rows]
    except Exception as e:
        logger.error(f"[Database] ❌ Failed to get player achievements: {e}", exc_info=True)
        raise


def get_player_match_history(guild_id: int, player_name: str, limit: int = 20) -> list[dict]:
    """Get recent match history for a player (for streak tracking)."""
    try:
        with sqlite3.connect(DB_PATH) as db:
            cur = db.execute(
                """
                SELECT match_id, goals, assists, clean_sheet, played_at 
                FROM player_match_history 
                WHERE guild_id=? AND player_name=? 
                ORDER BY played_at DESC 
                LIMIT ?
                """,
                (guild_id, player_name, limit),
            )
            rows = cur.fetchall()
            # Return in chronological order (oldest first)
            return [
                {
                    "match_id": row[0],
                    "goals": row[1],
                    "assists": row[2],
                    "clean_sheet": bool(row[3]),
                    "played_at": row[4]
                }
                for row in reversed(rows)
            ]
    except Exception as e:
        logger.error(f"[Database] ❌ Failed to get player match history: {e}", exc_info=True)
        return []


def update_player_match_history(guild_id: int, player_name: str, match_id: str, goals: int, assists: int, clean_sheet: bool, position: str = None):
    """Add or update a player's match in their history."""
    # Calculate hat-trick flags
    hat_trick = 1 if goals >= 3 else 0
    assist_hat_trick = 1 if assists >= 3 else 0
    
    logger.debug(f"[Database] Updating match history: player={player_name}, match={match_id}, goals={goals}, assists={assists}, position={position}, hat_trick={hat_trick}, assist_hat_trick={assist_hat_trick}")
    try:
        with sqlite3.connect(DB_PATH) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO player_match_history 
                (guild_id, player_name, match_id, goals, assists, clean_sheet, hat_trick, assist_hat_trick, position, played_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (guild_id, player_name, match_id, goals, assists, 1 if clean_sheet else 0, hat_trick, assist_hat_trick, position, datetime.utcnow().isoformat()),
            )
            db.commit()
        logger.debug(f"[Database] ✅ Match history updated successfully")
    except Exception as e:
        logger.error(f"[Database] ❌ Failed to update match history: {e}", exc_info=True)
        raise


# ---------- Player Initialization Functions ----------

def is_player_initialized(guild_id: int, player_name: str) -> bool:
    """Check if a player has been initialized (historical achievements backfilled)."""
    try:
        with sqlite3.connect(DB_PATH) as db:
            cur = db.execute(
                "SELECT 1 FROM player_initialization WHERE guild_id=? AND player_name=?",
                (guild_id, player_name),
            )
            exists = cur.fetchone() is not None
            logger.debug(f"[Database] Player initialization check: {player_name} = {'initialized' if exists else 'NEW'}")
            return exists
    except Exception as e:
        logger.error(f"[Database] ❌ Failed to check player initialization: {e}", exc_info=True)
        raise


def mark_player_initialized(guild_id: int, player_name: str):
    """Mark a player as initialized after historical achievements have been backfilled."""
    logger.debug(f"[Database] Marking player as initialized: guild={guild_id}, player={player_name}")
    try:
        with sqlite3.connect(DB_PATH) as db:
            db.execute(
                """
                INSERT OR IGNORE INTO player_initialization (guild_id, player_name, initialized_at)
                VALUES (?, ?, ?)
                """,
                (guild_id, player_name, datetime.utcnow().isoformat()),
            )
            db.commit()
        logger.debug(f"[Database] ✅ Player marked as initialized")
    except Exception as e:
        logger.error(f"[Database] ❌ Failed to mark player as initialized: {e}", exc_info=True)
        raise


# ---------- Hat-trick Stats Functions ----------

def get_player_hat_trick_count(guild_id: int, player_name: str) -> int:
    """Get total number of hat-tricks (3+ goals in a match) for a player."""
    try:
        with sqlite3.connect(DB_PATH) as db:
            cur = db.execute(
                "SELECT COUNT(*) FROM player_match_history WHERE guild_id=? AND player_name=? AND hat_trick=1",
                (guild_id, player_name),
            )
            count = cur.fetchone()[0]
            return count
    except Exception as e:
        logger.error(f"[Database] ❌ Failed to get hat-trick count: {e}", exc_info=True)
        return 0


def get_player_assist_hat_trick_count(guild_id: int, player_name: str) -> int:
    """Get total number of assist hat-tricks (3+ assists in a match) for a player."""
    try:
        with sqlite3.connect(DB_PATH) as db:
            cur = db.execute(
                "SELECT COUNT(*) FROM player_match_history WHERE guild_id=? AND player_name=? AND assist_hat_trick=1",
                (guild_id, player_name),
            )
            count = cur.fetchone()[0]
            return count
    except Exception as e:
        logger.error(f"[Database] ❌ Failed to get assist hat-trick count: {e}", exc_info=True)
        return 0


def get_all_players_hat_trick_stats(guild_id: int) -> list[dict]:
    """
    Get hat-trick stats for all players in a guild (for leaderboards).
    Returns list of dicts with player_name, hat_tricks, and assist_hat_tricks.
    """
    try:
        with sqlite3.connect(DB_PATH) as db:
            cur = db.execute(
                """
                SELECT 
                    player_name,
                    SUM(hat_trick) as hat_tricks,
                    SUM(assist_hat_trick) as assist_hat_tricks
                FROM player_match_history 
                WHERE guild_id=? 
                GROUP BY player_name
                """,
                (guild_id,),
            )
            rows = cur.fetchall()
            return [
                {
                    "player_name": row[0],
                    "hat_tricks": row[1] or 0,
                    "assist_hat_tricks": row[2] or 0
                }
                for row in rows
            ]
    except Exception as e:
        logger.error(f"[Database] ❌ Failed to get hat-trick stats: {e}", exc_info=True)
        return []


# ---------- Playoff Stats Functions ----------

def update_playoff_stats(guild_id: int, player_name: str, playoff_period: str, goals: int, assists: int, rating: float):
    """Update playoff stats for a player in a specific playoff period."""
    logger.debug(f"[Database] Updating playoff stats: player={player_name}, period={playoff_period}, goals={goals}, assists={assists}, rating={rating}")
    try:
        with sqlite3.connect(DB_PATH) as db:
            # Get current stats
            cur = db.execute(
                "SELECT goals, assists, total_rating, matches_played FROM playoff_stats WHERE guild_id=? AND player_name=? AND playoff_period=?",
                (guild_id, player_name, playoff_period)
            )
            row = cur.fetchone()
            
            if row:
                # Update existing record
                current_goals, current_assists, current_total_rating, current_matches = row
                new_goals = current_goals + goals
                new_assists = current_assists + assists
                new_total_rating = current_total_rating + rating
                new_matches = current_matches + 1
            else:
                # Create new record
                new_goals = goals
                new_assists = assists
                new_total_rating = rating
                new_matches = 1
            
            # Calculate playoff score: (Goals × 10) + (Assists × 7) + (Average Rating × 5) + (Matches Played × 2)
            avg_rating = new_total_rating / new_matches if new_matches > 0 else 0
            playoff_score = (new_goals * 10) + (new_assists * 7) + (avg_rating * 5) + (new_matches * 2)
            
            # Insert or update
            db.execute(
                """
                INSERT OR REPLACE INTO playoff_stats 
                (guild_id, player_name, playoff_period, goals, assists, total_rating, matches_played, playoff_score, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (guild_id, player_name, playoff_period, new_goals, new_assists, new_total_rating, new_matches, playoff_score, datetime.utcnow().isoformat())
            )
            db.commit()
        logger.debug(f"[Database] ✅ Playoff stats updated successfully")
    except Exception as e:
        logger.error(f"[Database] ❌ Failed to update playoff stats: {e}", exc_info=True)
        raise


def get_playoff_stats(guild_id: int, playoff_period: str) -> list[dict]:
    """Get all player playoff stats for a specific period."""
    try:
        with sqlite3.connect(DB_PATH) as db:
            cur = db.execute(
                """
                SELECT player_name, goals, assists, total_rating, matches_played, playoff_score
                FROM playoff_stats 
                WHERE guild_id=? AND playoff_period=?
                ORDER BY playoff_score DESC
                """,
                (guild_id, playoff_period)
            )
            rows = cur.fetchall()
            return [
                {
                    "player_name": row[0],
                    "goals": row[1],
                    "assists": row[2],
                    "total_rating": row[3],
                    "matches_played": row[4],
                    "playoff_score": row[5],
                    "avg_rating": row[3] / row[4] if row[4] > 0 else 0
                }
                for row in rows
            ]
    except Exception as e:
        logger.error(f"[Database] ❌ Failed to get playoff stats: {e}", exc_info=True)
        return []


def has_playoff_been_announced(guild_id: int, playoff_period: str) -> bool:
    """Check if playoff summary has been announced for this period."""
    try:
        with sqlite3.connect(DB_PATH) as db:
            cur = db.execute(
                "SELECT 1 FROM playoff_announcements WHERE guild_id=? AND playoff_period=?",
                (guild_id, playoff_period)
            )
            exists = cur.fetchone() is not None
            logger.debug(f"[Database] Playoff announcement check: period={playoff_period} = {'already announced' if exists else 'NOT announced'}")
            return exists
    except Exception as e:
        logger.error(f"[Database] ❌ Failed to check playoff announcement: {e}", exc_info=True)
        return False


def mark_playoff_announced(guild_id: int, playoff_period: str):
    """Mark that playoff summary has been announced for this period."""
    logger.debug(f"[Database] Marking playoff as announced: guild={guild_id}, period={playoff_period}")
    try:
        with sqlite3.connect(DB_PATH) as db:
            db.execute(
                """
                INSERT OR IGNORE INTO playoff_announcements (guild_id, playoff_period, announced_at)
                VALUES (?, ?, ?)
                """,
                (guild_id, playoff_period, datetime.utcnow().isoformat())
            )
            db.commit()
        logger.debug(f"[Database] ✅ Playoff announcement marked")
    except Exception as e:
        logger.error(f"[Database] ❌ Failed to mark playoff announcement: {e}", exc_info=True)
        raise


def count_playoff_matches(guild_id: int, playoff_period: str) -> int:
    """Count total playoff matches played in a period."""
    try:
        with sqlite3.connect(DB_PATH) as db:
            cur = db.execute(
                "SELECT COUNT(DISTINCT match_id) FROM playoff_club_stats WHERE guild_id=? AND playoff_period=?",
                (guild_id, playoff_period)
            )
            result = cur.fetchone()
            return result[0] if result and result[0] else 0
    except Exception as e:
        logger.error(f"[Database] ❌ Failed to count playoff matches: {e}", exc_info=True)
        return 0


def record_playoff_match(guild_id: int, playoff_period: str, match_id: str, result: str, goals_for: int, goals_against: int, clean_sheet: bool):
    """Record a playoff match result for club statistics."""
    logger.debug(f"[Database] Recording playoff match: guild={guild_id}, period={playoff_period}, match={match_id}, result={result}")
    try:
        with sqlite3.connect(DB_PATH) as db:
            db.execute(
                """
                INSERT OR IGNORE INTO playoff_club_stats 
                (guild_id, playoff_period, match_id, result, goals_for, goals_against, clean_sheet, played_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (guild_id, playoff_period, match_id, result, goals_for, goals_against, 1 if clean_sheet else 0, datetime.utcnow().isoformat())
            )
            db.commit()
        logger.debug(f"[Database] ✅ Playoff match recorded")
    except Exception as e:
        logger.error(f"[Database] ❌ Failed to record playoff match: {e}", exc_info=True)
        raise


def get_playoff_club_stats(guild_id: int, playoff_period: str) -> dict:
    """Get aggregated club statistics for a playoff period."""
    try:
        with sqlite3.connect(DB_PATH) as db:
            cur = db.execute(
                """
                SELECT 
                    COUNT(*) as total_matches,
                    SUM(CASE WHEN result = 'W' THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN result = 'L' THEN 1 ELSE 0 END) as losses,
                    SUM(CASE WHEN result = 'D' THEN 1 ELSE 0 END) as draws,
                    SUM(goals_for) as total_goals_for,
                    SUM(goals_against) as total_goals_against,
                    SUM(clean_sheet) as clean_sheets
                FROM playoff_club_stats
                WHERE guild_id=? AND playoff_period=?
                """,
                (guild_id, playoff_period)
            )
            row = cur.fetchone()
            if row and row[0]:
                wins, losses, draws = row[1] or 0, row[2] or 0, row[3] or 0
                total_matches = row[0]
                win_rate = (wins / total_matches * 100) if total_matches > 0 else 0
                
                return {
                    "total_matches": total_matches,
                    "wins": wins,
                    "losses": losses,
                    "draws": draws,
                    "win_rate": win_rate,
                    "goals_for": row[4] or 0,
                    "goals_against": row[5] or 0,
                    "goal_difference": (row[4] or 0) - (row[5] or 0),
                    "clean_sheets": row[6] or 0
                }
            return None
    except Exception as e:
        logger.error(f"[Database] ❌ Failed to get playoff club stats: {e}", exc_info=True)
        return None


