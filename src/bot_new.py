"""
EA Sports FC Pro Clubs Discord Bot
Refactored for better maintainability

ARCHITECTURE OVERVIEW:
----------------------
This bot automatically posts Pro Clubs match results to Discord and provides
slash commands for viewing club and player statistics.

KEY COMPONENTS:
1. Match Polling System (poll_once_all_guilds):
   - Runs every 60 seconds in the background
   - Fetches latest match from EA API for each configured guild
   - Compares match ID with last posted match
   - Posts new matches to configured Discord channel
   - Updates database with new match ID to prevent duplicates
   - Checks for player milestones and achievements

2. Database (database.py):
   - SQLite database storing guild settings (club_id, platform, channel_id, etc.)
   - Tracks last posted match ID per guild
   - Caches player names for autocomplete
   - Stores player milestone achievements
   - Stores player achievement unlocks
   - Tracks match history for streak-based achievements

3. Milestones & Achievements:
   - Milestones (milestones.py): Career stat thresholds (10 goals, 50 assists, etc.)
   - Achievements (achievements.py): Special accomplishments (hat tricks, perfect 10s, etc.)
   - Both auto-detected after matches and when using /clubstats

4. EA API Client (utils/ea_api.py):
   - Handles all communication with EA's Pro Clubs API
   - Includes retry logic and error handling
   - Session warmup to bypass Cloudflare/WAF protection
   - Auto-fallback between gen5/gen4 platforms

5. Slash Commands:
   - /setclub: Configure which club to track
   - /setmatchchannel: Set where matches are posted
   - /setmilestonechannel: Set where milestones are announced
   - /setachievementchannel: Set where achievements are announced
   - /setmonthlychannel: Set where Player of the Month announcements go
   - /potm: View current Player of the Month standings
   - /clubstats: View overall club statistics
   - /playerstats: View individual player stats
   - /lastmatches: View recent match history
   - /leaderboard: View player leaderboards
   - /achievements: View a player's earned achievements
   - /listachievements: List all available achievements
   - /lastperformance: View a player's stats over their last 10 matches
   - /statsovertime: Visualize goals/assists rates over time as a chart

LOGGING:
--------
All operations are logged with descriptive prefixes:
- [Command: <name>] - Slash command execution
- [Guild <id>] - Match polling per guild
- [EA API] - API requests and responses
- [Database] - Database operations

Use these prefixes to quickly identify issues in the console logs.
"""
import io
import os
import re
import logging
import asyncio
import time
import aiohttp
import discord
from discord import app_commands
from dotenv import load_dotenv
from pathlib import Path
from datetime import datetime, timezone

# Import our modules
from database import (
    init_db, get_settings, upsert_settings, set_last_match_id,
    get_all_guild_settings, cache_club_members, get_cached_club_members,
    update_player_match_history, is_player_initialized, mark_player_initialized,
    get_player_hat_trick_count, get_player_assist_hat_trick_count,
    get_all_players_hat_trick_stats, set_last_playoff_match_id,
    get_monthly_stats, get_player_dominant_position,
    get_potm_history, get_player_recent_goals_assists,
)
from milestones import check_milestones, announce_milestones
from achievements import (
    check_achievements, announce_achievements,
    check_historical_achievements, announce_historical_achievements
)
from playoffs import is_playoff_match, process_playoff_match
from monthly import (
    process_league_match_monthly, check_month_rollover, detect_month_period
)
from utils.ea_api import (
    platform_from_choice, parse_club_id_from_any, warmup_session,
    fetch_club_info, fetch_latest_match, fetch_latest_playoff_match,
    fetch_json, HTTP_TIMEOUT, EAApiForbiddenError,
    fetch_all_matches, calculate_player_wld
)
from utils.embeds import build_match_embed, utc_to_str, PaginatedEmbedView

# Set Matplotlib backend before any pyplot import so it works correctly
# in a headless server environment and across repeated command invocations.
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

# ---------- logging ----------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('ProClubsBot')

# ---------- config ----------
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")  # optional for fast guild sync

POLL_INTERVAL_SECONDS = 60
EA_FORBIDDEN_COOLDOWN_SECONDS = 600
MIN_CHART_DATA_POINTS = 2  # minimum match-history entries needed to render a chart


# ---------- Bot Class ----------
class ProClubsBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self._ea_forbidden_until: dict[int, float] = {}

    async def on_ready(self):
        logger.info(f"Logged in as {self.user} (id: {self.user.id})")
        
        # Sync commands
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info(f"✓ Synced {len(synced)} commands to guild {GUILD_ID}")
            for cmd in synced:
                logger.info(f"  - {cmd.name} (guild {GUILD_ID})")
        else:
            synced = await self.tree.sync()
            logger.info(f"✓ Synced {len(synced)} commands globally")

        # Start background match polling
        self.loop.create_task(self.match_watch())
        logger.info("Started match watch loop")

    async def match_watch(self):
        """Background task to poll for new matches."""
        await self.wait_until_ready()
        logger.info("Match watch loop ready")
        
        while not self.is_closed():
            try:
                await self.poll_once_all_guilds()
            except Exception as e:
                logger.error(f"Error in match watch loop: {e}", exc_info=True)
            
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    async def poll_once_all_guilds(self):
        """
        Poll all configured guilds for new matches.
        This runs every POLL_INTERVAL_SECONDS (60s by default).
        """
        # Fetch all guild settings from database
        rows = get_all_guild_settings()
        
        if not rows:
            logger.info("No guild settings found for match polling")
            return
        
        logger.info(f"Polling {len(rows)} guild(s) for new matches")

        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
            # Warm up session: visit EA's site to get cookies/pass Cloudflare
            # This helps prevent 403 errors when making API calls
            logger.debug("Warming up session for EA API...")
            await warmup_session(session)
            
            # Process each guild's settings
            for (guild_id, club_id, platform, channel_id, last_match_id, autopost) in rows:
                logger.info(f"Checking guild {guild_id}: club_id={club_id}, platform={platform}, channel_id={channel_id}, autopost={autopost}, last_match_id={last_match_id}")
                
                # Verify all required settings are present
                # Check if autopost is enabled (explicitly check for 1, not just truthy)
                if not club_id or not platform or not channel_id:
                    logger.warning(f"Guild {guild_id} missing required settings (club_id={club_id}, platform={platform}, channel_id={channel_id})")
                    continue
                
                if autopost != 1:
                    logger.debug(f"Guild {guild_id} autopost is disabled (autopost={autopost}), skipping")
                    continue

                blocked_until = self._ea_forbidden_until.get(int(guild_id), 0.0)
                now_ts = time.time()
                if blocked_until > now_ts:
                    remaining = int(blocked_until - now_ts)
                    logger.warning(
                        f"[Guild {guild_id}] Skipping EA poll due to recent 403 block "
                        f"(cooldown remaining: {remaining}s)"
                    )
                    continue
                
                try:
                    # Step 1: Fetch club info to get club name
                    logger.debug(f"[Guild {guild_id}] Fetching club info for club {club_id}...")
                    info, used_platform = await fetch_club_info(session, platform, club_id)
                    self._ea_forbidden_until.pop(int(guild_id), None)
                    
                    # EA API returns different formats, normalize to dict
                    if isinstance(info, list):
                        club_info = next(
                            (entry for entry in info if str(entry.get("clubId")) == str(club_id)),
                            {},
                        )
                    elif isinstance(info, dict):
                        club_info = info.get(str(club_id), {})
                    else:
                        club_info = {}
                    club_name = club_info.get("name", f"Club {club_id}")
                    logger.debug(f"[Guild {guild_id}] Found club: {club_name}")

                    # Step 2: Fetch the latest match from EA API
                    logger.debug(f"[Guild {guild_id}] Fetching latest match...")
                    match, mt = await fetch_latest_match(session, used_platform, club_id)
                    
                    if not match:
                        logger.debug(f"[Guild {guild_id}] No matches found for club {club_id}")
                        continue
                    
                    if not mt:
                        logger.warning(f"[Guild {guild_id}] Match found but match_type is None/empty, defaulting to 'league'")
                        mt = "league"
                    
                    logger.info(f"[Guild {guild_id}] Found match: type={mt}, timestamp={match.get('timestamp', 'unknown')}")

                    # Step 3: Extract match ID (EA API format is inconsistent)
                    match_id = match.get("matchJson")
                    if isinstance(match_id, str) and '"matchId":"' in match_id:
                        try:
                            match_id = re.search(r'"matchId":"(\d+)"', match_id).group(1)
                            logger.debug(f"[Guild {guild_id}] Extracted match ID from JSON string: {match_id}")
                        except Exception:
                            match_id = None
                    elif isinstance(match_id, dict):
                        match_id = match_id.get("matchId")
                        logger.debug(f"[Guild {guild_id}] Extracted match ID from dict: {match_id}")
                    
                    # Fallback to direct matchId field
                    match_id = match.get("matchId", match_id)
                    
                    # Last resort: create composite ID from timestamp and score
                    if not match_id:
                        # Get scores from clubs structure (correct field names)
                        clubs = match.get("clubs", {})
                        our_club = clubs.get(str(club_id), {})
                        opponent_ids = [cid for cid in clubs.keys() if str(cid) != str(club_id)]
                        opponent_club = clubs.get(opponent_ids[0], {}) if opponent_ids else {}
                        our_score = our_club.get("score", "?")
                        opp_score = opponent_club.get("score", "?")
                        match_id = f"{match.get('timestamp', 0)}:{our_score}-{opp_score}"
                        logger.debug(f"[Guild {guild_id}] Using fallback match ID: {match_id}")
                    
                    logger.info(f"[Guild {guild_id}] Latest match ID: {match_id}, Last posted match ID: {last_match_id or 'None (no matches posted yet)'}")

                    # Step 4: Check if we've already posted this match
                    # Handle None last_match_id (first time posting)
                    if last_match_id is not None and str(match_id) == str(last_match_id):
                        logger.info(f"[Guild {guild_id}] Match {match_id} already posted (matches last_match_id {last_match_id}), skipping")
                        continue  # already posted
                    
                    logger.info(f"[Guild {guild_id}] NEW match detected! Match ID {match_id} differs from last posted {last_match_id or '(none)'}")

                    # Step 5: Get the Discord channel to post to
                    logger.debug(f"[Guild {guild_id}] New match detected! Fetching Discord channel {channel_id}...")
                    try:
                        # Try get_channel first (fast, but requires channel in cache)
                        channel = self.get_channel(int(channel_id))
                        # If not in cache, fetch it from Discord
                        if channel is None:
                            logger.debug(f"[Guild {guild_id}] Channel {channel_id} not in cache, fetching from Discord...")
                            channel = await self.fetch_channel(int(channel_id))
                        if channel is None:
                            logger.error(f"[Guild {guild_id}] Could not find channel {channel_id} - bot may not have access")
                            continue
                        logger.debug(f"[Guild {guild_id}] Found channel: {channel.name} (ID: {channel_id})")
                    except discord.Forbidden:
                        logger.error(f"[Guild {guild_id}] Bot does not have access to channel {channel_id}")
                        continue
                    except discord.NotFound:
                        logger.error(f"[Guild {guild_id}] Channel {channel_id} not found")
                        continue
                    except Exception as channel_error:
                        logger.error(f"[Guild {guild_id}] Error fetching channel {channel_id}: {channel_error}", exc_info=True)
                        continue

                    # Step 6: Build the match embed and post it
                    logger.debug(f"[Guild {guild_id}] Building match embed...")
                    try:
                        embed = build_match_embed(
                            club_id,
                            used_platform,
                            match,
                            mt,
                            club_name_hint=club_name,
                        )
                        logger.debug(f"[Guild {guild_id}] Match embed built successfully")
                    except Exception as embed_error:
                        logger.error(f"[Guild {guild_id}] Failed to build match embed: {embed_error}", exc_info=True)
                        continue
                    
                    logger.info(f"[Guild {guild_id}] Posting new match {match_id} to channel {channel.name} ({channel_id})")
                    try:
                        await channel.send(embed=embed)
                        logger.info(f"✅ [Guild {guild_id}] Successfully sent match {match_id} to Discord channel")
                    except discord.Forbidden as perm_error:
                        logger.error(f"[Guild {guild_id}] Permission denied posting to channel {channel_id}: {perm_error}")
                        continue
                    except discord.HTTPException as http_error:
                        logger.error(f"[Guild {guild_id}] HTTP error posting to channel {channel_id}: {http_error}")
                        continue
                    except Exception as send_error:
                        logger.error(f"[Guild {guild_id}] Unexpected error posting to channel {channel_id}: {send_error}", exc_info=True)
                        continue
                    
                    # Step 7: Update database with new match ID (only if send succeeded)
                    try:
                        set_last_match_id(guild_id, str(match_id))
                        logger.info(f"✅ [Guild {guild_id}] Successfully posted match {match_id} and updated database")
                    except Exception as db_error:
                        logger.error(f"[Guild {guild_id}] Failed to update last_match_id in database: {db_error}", exc_info=True)
                        # Don't continue here - match was posted, just DB update failed
                    
                    # Track monthly stats for league matches (not playoffs)
                    if not is_playoff_match(mt):
                        process_league_match_monthly(guild_id, match, club_id)
                    
                    # Step 8: Check for milestones and achievements
                    logger.debug(f"[Guild {guild_id}] Checking for player milestones and achievements...")
                    try:
                        members_data = await fetch_json(
                            session,
                            "/members/stats",
                            {"clubId": str(club_id), "platform": used_platform},
                        )
                        
                        if isinstance(members_data, list):
                            members_list = members_data
                        else:
                            members_list = members_data.get("members") if isinstance(members_data, dict) else []
                        
                        members = [m for m in members_list if isinstance(m, dict)]
                        
                        # Get club data from match for team-based achievements
                        clubs = match.get("clubs", {})
                        our_club = clubs.get(str(club_id), {})
                        opponent_id = [cid for cid in clubs.keys() if str(cid) != str(club_id)]
                        opponent_club = clubs.get(opponent_id[0], {}) if opponent_id else {}
                        our_score = int(our_club.get("score", 0) or 0)
                        opp_score = int(opponent_club.get("score", 0) or 0)
                        clean_sheet = (opp_score == 0)
                        
                        # Check milestones and achievements for all players
                        for member in members:
                            player_name = member.get("name", "Unknown")
                            
                            # Check if player needs initialization (first time seeing them)
                            if not is_player_initialized(guild_id, player_name):
                                logger.info(f"[Guild {guild_id}] New player detected: {player_name} - checking historical achievements")
                                historical_achievements = check_historical_achievements(guild_id, player_name, member)
                                if historical_achievements:
                                    logger.info(f"[Guild {guild_id}] Found {len(historical_achievements)} historical achievement(s) for {player_name}")
                                    await announce_historical_achievements(self, guild_id, player_name, historical_achievements)
                                mark_player_initialized(guild_id, player_name)
                            
                            # Check milestones
                            new_milestones = check_milestones(guild_id, player_name, member)
                            if new_milestones:
                                logger.info(f"[Guild {guild_id}] New milestones detected for {player_name}: {len(new_milestones)} milestone(s)")
                                await announce_milestones(self, guild_id, player_name, new_milestones)
                            
                            # Check achievements (pass match data for match-specific achievements)
                            new_achievements = check_achievements(guild_id, player_name, member, match_data=match)
                            if new_achievements:
                                logger.info(f"[Guild {guild_id}] New achievements detected for {player_name}: {len(new_achievements)} achievement(s)")
                                await announce_achievements(self, guild_id, player_name, new_achievements)
                            
                            # Update match history for streak tracking
                            # Find player's match stats
                            players = match.get("players", {})
                            club_players = players.get(str(club_id), {})

                            # Determine match result for team-streak tracking
                            result_code = our_club.get("result", "")
                            if result_code == "1":
                                match_result = "W"
                            elif result_code == "2":
                                match_result = "L"
                            elif result_code == "3":
                                match_result = "D"
                            else:
                                match_result = "L"

                            for pid, pdata in club_players.items():
                                if isinstance(pdata, dict) and pdata.get("playername", "").lower() == player_name.lower():
                                    match_goals = int(pdata.get("goals", 0) or 0)
                                    match_assists = int(pdata.get("assists", 0) or 0)
                                    match_rating = float(pdata.get("rating", 0) or 0)

                                    # Extract position played in this match
                                    # Check various possible field names for position
                                    position = (pdata.get("pos") or pdata.get("position") or
                                               pdata.get("posSorted") or pdata.get("positionSorted") or
                                               member.get("favoritePosition") or "Unknown")

                                    # Debug logging for ANY position investigation
                                    if str(position).upper() == "ANY" or str(position) == "28":
                                        logger.info(f"[ANY Position Debug] Player: {player_name}, Position: {position}, Goals: {match_goals}, Assists: {match_assists}")
                                        logger.debug(f"[ANY Position Debug] Full player data: {pdata}")
                                        vproattr = pdata.get("vproattr")
                                        if vproattr:
                                            logger.debug(f"[ANY Position Debug] vproattr present: {vproattr}")

                                    update_player_match_history(
                                        guild_id, player_name, str(match_id),
                                        match_goals, match_assists, clean_sheet, position, match_result,
                                        rating=match_rating,
                                    )
                                    break
                    except Exception as milestone_error:
                        logger.error(f"[Guild {guild_id}] Error checking milestones/achievements: {milestone_error}", exc_info=True)
                    
                except EAApiForbiddenError as e:
                    self._ea_forbidden_until[int(guild_id)] = time.time() + EA_FORBIDDEN_COOLDOWN_SECONDS
                    logger.error(
                        f"[Guild {guild_id}] EA API returned 403 ({e.path}). "
                        f"Pausing this guild for {EA_FORBIDDEN_COOLDOWN_SECONDS}s before retry."
                    )
                except Exception as e:  # noqa: BLE001
                    logger.error(f"❌ [Guild {guild_id}] Error polling guild: {e}", exc_info=True)

                # These checks run every poll cycle regardless of new league match
                try:
                    # Playoff match check (separate from league match)
                    settings = get_settings(guild_id)
                    last_playoff_id = settings.get("last_playoff_match_id") if settings else None

                    playoff_match, playoff_mt = await fetch_latest_playoff_match(session, platform, club_id)
                    if playoff_match:
                        playoff_match_id = playoff_match.get("matchId", str(playoff_match.get("timestamp", 0)))
                        if last_playoff_id is None or str(playoff_match_id) != str(last_playoff_id):
                            logger.info(f"[Guild {guild_id}] [Playoffs] Playoff match detected: {playoff_match_id}")

                            # Get channel to post playoff match
                            try:
                                po_channel = self.get_channel(int(channel_id))
                                if po_channel is None:
                                    po_channel = await self.fetch_channel(int(channel_id))

                                if po_channel:
                                    # Get club name for embed
                                    po_info, po_platform = await fetch_club_info(session, platform, club_id)
                                    if isinstance(po_info, dict):
                                        po_club_info = po_info.get(str(club_id), {})
                                    elif isinstance(po_info, list):
                                        po_club_info = next((e for e in po_info if str(e.get("clubId")) == str(club_id)), {})
                                    else:
                                        po_club_info = {}
                                    po_club_name = po_club_info.get("name", f"Club {club_id}")

                                    playoff_embed = build_match_embed(
                                        club_id, po_platform, playoff_match, playoff_mt,
                                        club_name_hint=po_club_name,
                                    )
                                    await po_channel.send(embed=playoff_embed)
                                    logger.info(f"✅ [Guild {guild_id}] [Playoffs] Posted playoff match {playoff_match_id}")
                            except Exception as playoff_post_err:
                                logger.error(f"[Guild {guild_id}] [Playoffs] Failed to post playoff match: {playoff_post_err}", exc_info=True)

                            # Update last playoff match ID
                            set_last_playoff_match_id(guild_id, str(playoff_match_id))

                            # Process playoff stats
                            await process_playoff_match(self, guild_id, playoff_match, playoff_mt, club_id)
                except EAApiForbiddenError as e:
                    self._ea_forbidden_until[int(guild_id)] = time.time() + EA_FORBIDDEN_COOLDOWN_SECONDS
                    logger.error(
                        f"[Guild {guild_id}] [Playoffs] EA API returned 403 ({e.path}). "
                        f"Pausing this guild for {EA_FORBIDDEN_COOLDOWN_SECONDS}s before retry."
                    )
                except Exception as playoff_err:
                    logger.error(f"[Guild {guild_id}] [Playoffs] Error checking playoff matches: {playoff_err}", exc_info=True)

                # Month rollover check (POTM announcement)
                try:
                    await check_month_rollover(self, guild_id)
                except Exception as monthly_err:
                    logger.error(f"[Guild {guild_id}] [Monthly] Error checking month rollover: {monthly_err}", exc_info=True)


client = ProClubsBot()


def _generate_player_chart(player_name: str, history: list) -> tuple | None:
    """
    Render a goals/assists/rating-over-time chart for *player_name* using *history*.

    Returns a ``(discord.File, filename)`` tuple, or ``None`` when there are
    fewer than ``MIN_CHART_DATA_POINTS`` data-points.
    """
    if len(history) < MIN_CHART_DATA_POINTS:
        return None

    match_nums = list(range(1, len(history) + 1))
    goals = [m["goals"] for m in history]
    assists = [m["assists"] for m in history]
    ratings = [m.get("rating", 0.0) for m in history]
    cum_gpg = [sum(goals[:i + 1]) / (i + 1) for i in range(len(goals))]
    cum_apg = [sum(assists[:i + 1]) / (i + 1) for i in range(len(assists))]

    has_ratings = any(r > 0 for r in ratings)
    nrows = 3 if has_ratings else 2
    fig, axes = plt.subplots(nrows, 1, figsize=(10, 4 * nrows), sharex=True)
    ax1, ax2 = axes[0], axes[1]
    ax3 = axes[2] if has_ratings else None

    fig.patch.set_facecolor("#2f3136")
    for ax in axes:
        ax.set_facecolor("#36393f")
        ax.tick_params(colors="white")
        ax.spines[:].set_color("#555")
        ax.yaxis.label.set_color("white")
        ax.xaxis.label.set_color("white")
        ax.title.set_color("white")

    ax1.bar(match_nums, goals, color="#e74c3c", alpha=0.7, label="Goals (match)")
    ax1.plot(match_nums, cum_gpg, color="#ff9966", linewidth=2, marker="o",
             markersize=4, label="Goals/game (avg)")
    ax1.set_ylabel("Goals", color="white")
    ax1.set_title(f"Goals Over Time — {player_name}", color="white")
    ax1.legend(facecolor="#2f3136", labelcolor="white")
    ax1.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))

    ax2.bar(match_nums, assists, color="#3498db", alpha=0.7, label="Assists (match)")
    ax2.plot(match_nums, cum_apg, color="#66ccff", linewidth=2, marker="o",
             markersize=4, label="Assists/game (avg)")
    ax2.set_ylabel("Assists", color="white")
    ax2.set_title(f"Assists Over Time — {player_name}", color="white")
    ax2.legend(facecolor="#2f3136", labelcolor="white")
    ax2.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))

    if ax3 is not None:
        valid_ratings = [r for r in ratings if r > 0]
        avg_r = sum(valid_ratings) / len(valid_ratings) if valid_ratings else 0
        ax3.plot(match_nums, ratings, color="#f1c40f", linewidth=2, marker="o",
                 markersize=4, label="Rating (match)")
        ax3.axhline(avg_r, color="#f39c12", linewidth=1.5, linestyle="--",
                    label=f"Avg {avg_r:.2f}")
        ax3.set_xlabel("Match #", color="white")
        ax3.set_ylabel("Rating", color="white")
        ax3.set_title(f"Rating Over Time — {player_name}", color="white")
        ax3.legend(facecolor="#2f3136", labelcolor="white")
        ax3.set_ylim(0, 10.5)
    else:
        ax2.set_xlabel("Match #", color="white")

    plt.tight_layout(pad=2.0)

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=120, facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)

    filename = f"{player_name}_stats.png"
    return discord.File(buf, filename=filename), filename


# ---------- Slash commands ----------

@client.tree.command(name="setclub", description="Set the club to track (ID or EA URL) and generation.")
@app_commands.describe(
    club="Enter club ID or paste the EA URL (contains clubId=...)",
    gen="Game generation/platform"
)
@app_commands.choices(
    gen=[
        app_commands.Choice(name="gen5 (PS5/XSX/PC)", value="gen5"),
        app_commands.Choice(name="gen4 (PS4/XB1)", value="gen4"),
    ],
)
async def setclub(interaction: discord.Interaction, club: str, gen: app_commands.Choice[str]):
    """
    Command: /setclub
    Sets the Pro Club to track for this Discord server.
    Accepts either a numeric club ID or an EA URL containing clubId=...
    """
    await interaction.response.defer(ephemeral=True)
    logger.info(f"[Command: setclub] User {interaction.user} (ID: {interaction.user.id}) in guild {interaction.guild_id} executing /setclub with club='{club}' gen='{gen.value}'")
    
    # Parse the club ID from input (handles both numeric IDs and EA URLs)
    parsed_id = parse_club_id_from_any(club)
    if not parsed_id:
        logger.warning(f"[Command: setclub] Invalid club input from user {interaction.user}: '{club}'")
        await interaction.followup.send("Invalid input. Provide a number (clubId) or an EA URL containing `clubId=...`.", ephemeral=True)
        return

    # Convert generation choice to platform string
    platform = platform_from_choice(gen.value)
    logger.debug(f"[Command: setclub] Parsed club ID: {parsed_id}, platform: {platform}")

    try:
        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
            # Warm up session before making API calls to reduce 403 errors
            logger.debug(f"[Command: setclub] Warming up session for EA API...")
            await warmup_session(session)
            
            # Verify the club exists by fetching its info from EA API
            logger.debug(f"[Command: setclub] Fetching club info for club {parsed_id}...")
            info, used_platform = await fetch_club_info(session, platform, parsed_id)
            
            # EA API returns different formats, normalize to dict
            if isinstance(info, list):
                club_info = next(
                    (entry for entry in info if str(entry.get("clubId")) == str(parsed_id)),
                    {},
                )
            elif isinstance(info, dict):
                club_info = info.get(str(parsed_id), {})
            else:
                club_info = {}
            name = club_info.get("name", f"Club {parsed_id}")
            logger.info(f"[Command: setclub] Successfully verified club: {name} (ID: {parsed_id})")
    except Exception as e:
        logger.error(f"[Command: setclub] Failed to verify club {parsed_id}: {e}", exc_info=True)
        await interaction.followup.send(f"Could not verify club: `{e}`", ephemeral=True)
        return

    # Save club settings to database
    logger.debug(f"[Command: setclub] Saving settings to database: guild_id={interaction.guild_id}, club_id={parsed_id}, platform={used_platform}")
    upsert_settings(interaction.guild_id, club_id=parsed_id, platform=used_platform)
    logger.info(f"✅ [Command: setclub] Guild {interaction.guild_id} set club to {name} (ID: {parsed_id}, platform: {used_platform})")
    await interaction.followup.send(f"✅ Club set to **{name}** (ID `{parsed_id}`) on `{used_platform}`.", ephemeral=True)


@client.tree.command(name="setmatchchannel", description="Choose the channel where new matches will be posted.")
@app_commands.describe(channel="Channel to receive new match posts")
async def setmatchchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    """
    Command: /setmatchchannel
    Sets the Discord channel where new match results will be automatically posted.
    Requires that /setclub has been run first.
    """
    await interaction.response.defer(ephemeral=True)
    logger.info(f"[Command: setmatchchannel] User {interaction.user} (ID: {interaction.user.id}) in guild {interaction.guild_id} setting match channel to #{channel.name} (ID: {channel.id})")
    
    # Check if club has been configured first
    st = get_settings(interaction.guild_id)
    if not st or not st.get("club_id"):
        logger.warning(f"[Command: setmatchchannel] Guild {interaction.guild_id} tried to set match channel without setting club first")
        await interaction.followup.send("Set a club first with `/setclub`.", ephemeral=True)
        return

    # Save channel settings and enable autopost
    logger.debug(f"[Command: setmatchchannel] Saving match channel to database: guild_id={interaction.guild_id}, channel_id={channel.id}, autopost=1")
    upsert_settings(interaction.guild_id, channel_id=channel.id, autopost=1)
    logger.info(f"✅ [Command: setmatchchannel] Guild {interaction.guild_id} set match channel to #{channel.name} (ID: {channel.id}), autopost enabled")
    await interaction.followup.send(f"✅ New matches will be posted in {channel.mention}.", ephemeral=True)


@client.tree.command(name="setmilestonechannel", description="Set the channel for milestone announcements.")
@app_commands.describe(channel="Channel to receive milestone notifications")
async def setmilestonechannel(interaction: discord.Interaction, channel: discord.TextChannel):
    """
    Command: /setmilestonechannel
    Sets the Discord channel where player milestone achievements will be posted.
    Milestones include goals, assists, matches played, and MOTM awards.
    """
    await interaction.response.defer(ephemeral=True)
    logger.info(f"[Command: setmilestonechannel] User {interaction.user} in guild {interaction.guild_id} setting milestone channel to #{channel.name} (ID: {channel.id})")
    
    # Check if club has been configured first
    st = get_settings(interaction.guild_id)
    if not st or not st.get("club_id"):
        logger.warning(f"[Command: setmilestonechannel] Guild {interaction.guild_id} tried to set milestone channel without setting club first")
        await interaction.followup.send("Set a club first with `/setclub`.", ephemeral=True)
        return

    # Save milestone channel settings
    logger.debug(f"[Command: setmilestonechannel] Saving milestone channel to database: guild_id={interaction.guild_id}, milestone_channel_id={channel.id}")
    upsert_settings(interaction.guild_id, milestone_channel_id=channel.id)
    logger.info(f"✅ [Command: setmilestonechannel] Guild {interaction.guild_id} set milestone channel to #{channel.name} (ID: {channel.id})")
    await interaction.followup.send(
        f"✅ Milestone notifications will be posted in {channel.mention}.\n\n"
        f"**Milestones tracked:**\n"
        f"⚽ Goals: 1, 10, 25, 50, 100, 250, 500\n"
        f"🅰️ Assists: 1, 10, 25, 50, 100, 250, 500\n"
        f"🎮 Matches: 1, 10, 25, 50, 100, 250, 500\n"
        f"⭐ Man of the Match: 1, 5, 10, 25, 50, 100",
        ephemeral=True
    )


@client.tree.command(name="setachievementchannel", description="Set the channel for achievement announcements.")
@app_commands.describe(channel="Channel to receive achievement notifications")
async def setachievementchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    """
    Command: /setachievementchannel
    Sets the Discord channel where player achievements will be posted.
    Achievements are special accomplishments like hat tricks, perfect ratings, etc.
    """
    await interaction.response.defer(ephemeral=True)
    logger.info(f"[Command: setachievementchannel] User {interaction.user} in guild {interaction.guild_id} setting achievement channel to #{channel.name} (ID: {channel.id})")
    
    # Check if club has been configured first
    st = get_settings(interaction.guild_id)
    if not st or not st.get("club_id"):
        logger.warning(f"[Command: setachievementchannel] Guild {interaction.guild_id} tried to set achievement channel without setting club first")
        await interaction.followup.send("Set a club first with `/setclub`.", ephemeral=True)
        return

    # Save achievement channel settings
    logger.debug(f"[Command: setachievementchannel] Saving achievement channel to database: guild_id={interaction.guild_id}, achievement_channel_id={channel.id}")
    upsert_settings(interaction.guild_id, achievement_channel_id=channel.id)
    logger.info(f"✅ [Command: setachievementchannel] Guild {interaction.guild_id} set achievement channel to #{channel.name} (ID: {channel.id})")
    await interaction.followup.send(
        f"✅ Achievement notifications will be posted in {channel.mention}.\n\n"
        f"Use `/listachievements` to see all available achievements!",
        ephemeral=True
    )


@client.tree.command(name="setmonthlychannel", description="Set the channel for Player of the Month announcements.")
@app_commands.describe(channel="Channel to receive monthly POTM announcements")
async def setmonthlychannel(interaction: discord.Interaction, channel: discord.TextChannel):
    """
    Command: /setmonthlychannel
    Sets the Discord channel where Player of the Month announcements will be posted.
    """
    await interaction.response.defer(ephemeral=True)
    logger.info(f"[Command: setmonthlychannel] User {interaction.user} in guild {interaction.guild_id} setting monthly channel to #{channel.name} (ID: {channel.id})")

    st = get_settings(interaction.guild_id)
    if not st or not st.get("club_id"):
        await interaction.followup.send("Set a club first with `/setclub`.", ephemeral=True)
        return

    upsert_settings(interaction.guild_id, monthly_channel_id=channel.id)
    logger.info(f"✅ [Command: setmonthlychannel] Guild {interaction.guild_id} set monthly channel to #{channel.name} (ID: {channel.id})")
    await interaction.followup.send(
        f"✅ Player of the Month announcements will be posted in {channel.mention}.\n\n"
        f"**How it works:**\n"
        f"📊 All league matches are tracked throughout each month\n"
        f"🏅 When a new month begins, the best performer is announced\n"
        f"📈 Use `/potm` to see current month standings",
        ephemeral=True
    )


@client.tree.command(name="potm", description="View current Player of the Month standings")
async def potm(interaction: discord.Interaction):
    """
    Command: /potm
    Displays current month's Player of the Month standings.
    Shows top players, their scores, and matches played.
    """
    await interaction.response.defer(thinking=True)
    logger.info(f"[Command: potm] User {interaction.user} in guild {interaction.guild_id} requesting POTM standings")

    st = get_settings(interaction.guild_id)
    if not st or not st.get("club_id"):
        await interaction.followup.send("Set a club first with `/setclub`.", ephemeral=True)
        return

    current_month = detect_month_period()
    stats = get_monthly_stats(interaction.guild_id, current_month)

    if not stats:
        await interaction.followup.send(
            f"📊 No matches tracked yet for **{current_month}**.\n"
            f"Stats are recorded automatically after each league match.",
            ephemeral=True
        )
        return

    # Build weekly score for each player from match history
    weekly_scores: dict[str, float] = {}
    for player in stats:
        pname = player["player_name"]
        recent = get_player_recent_goals_assists(interaction.guild_id, pname, days=7)
        weekly_scores[pname] = recent["goals"] * 10 + recent["assists"] * 7

    embed = discord.Embed(
        title="🏅 Player of the Month Standings",
        description=f"**{current_month}** — Current Standings",
        color=discord.Color.gold(),
    )

    medals = ["🥇", "🥈", "🥉"]
    for i, player in enumerate(stats[:10]):
        rank = medals[i] if i < 3 else f"{i + 1}."
        avg_rating = player["avg_rating"]
        # Weekly trend arrow
        w = weekly_scores.get(player["player_name"], 0)
        if w > 15:
            trend = " 🔥"
        elif w > 0:
            trend = " 📈"
        else:
            trend = ""
        embed.add_field(
            name=f"{rank} {player['player_name']}{trend}",
            value=(
                f"Score: **{player['monthly_score']:.1f}** | "
                f"⚽ {player['goals']} | 🅰️ {player['assists']} | "
                f"⭐ {avg_rating:.1f} | 🎮 {player['matches_played']}"
            ),
            inline=False,
        )

    # Historical POTM winners
    history = get_potm_history(interaction.guild_id, limit=6)
    # Filter out current month
    past = [h for h in history if h["month_period"] != current_month]
    if past:
        history_lines = []
        for h in past[:5]:
            history_lines.append(
                f"**{h['month_period']}** — 🏅 **{h['player_name']}** "
                f"({h['goals']}G {h['assists']}A ⭐{h['avg_rating']:.1f})"
            )
        embed.add_field(
            name="📜 Past Winners",
            value="\n".join(history_lines),
            inline=False,
        )

    embed.set_footer(text="Score = Goals×10 + Assists×7 + Avg Rating×5 + Matches×2 | 🔥 = active this week")
    await interaction.followup.send(embed=embed)


@client.tree.command(name="setplayoffsummarychannel", description="Set the channel for playoff summary announcements.")
@app_commands.describe(channel="Channel to receive playoff summaries")
async def setplayoffsummarychannel(interaction: discord.Interaction, channel: discord.TextChannel):
    """
    Command: /setplayoffsummarychannel
    Sets the Discord channel where "Player of the Playoffs" announcements will be posted.
    These are posted monthly after 15 playoff matches are completed.
    """
    await interaction.response.defer(ephemeral=True)
    logger.info(f"[Command: setplayoffsummarychannel] User {interaction.user} in guild {interaction.guild_id} setting playoff summary channel to #{channel.name} (ID: {channel.id})")
    
    # Check if club has been configured first
    st = get_settings(interaction.guild_id)
    if not st or not st.get("club_id"):
        logger.warning(f"[Command: setplayoffsummarychannel] Guild {interaction.guild_id} tried to set playoff summary channel without setting club first")
        await interaction.followup.send("Set a club first with `/setclub`.", ephemeral=True)
        return

    # Save playoff summary channel settings
    logger.debug(f"[Command: setplayoffsummarychannel] Saving playoff summary channel to database: guild_id={interaction.guild_id}, playoff_summary_channel_id={channel.id}")
    upsert_settings(interaction.guild_id, playoff_summary_channel_id=channel.id)
    logger.info(f"✅ [Command: setplayoffsummarychannel] Guild {interaction.guild_id} set playoff summary channel to #{channel.name} (ID: {channel.id})")
    await interaction.followup.send(
        f"✅ Playoff summaries will be posted in {channel.mention}.\n\n"
        f"**Playoff Summary includes:**\n"
        f"🏆 Player of the Playoffs winner\n"
        f"📊 Top 3 performers\n"
        f"⭐ Performance scores\n\n"
        f"*Summaries are posted automatically after 15 playoff matches each month.*",
        ephemeral=True
    )


@client.tree.command(name="clubstats", description="Show overall club statistics")
async def clubstats(interaction: discord.Interaction):
    """
    Command: /clubstats
    Displays overall club statistics including record, goals, assists, and top players.
    Also checks for any new player milestones achieved.
    """
    await interaction.response.defer(thinking=True)
    logger.info(f"[Command: clubstats] User {interaction.user} in guild {interaction.guild_id} requesting club stats")
    
    # Check if club has been configured
    st = get_settings(interaction.guild_id)
    if not st or not (st.get("club_id") and st.get("platform")):
        logger.warning(f"[Command: clubstats] Guild {interaction.guild_id} tried to view stats without setting club first")
        await interaction.followup.send("Set a club first with `/setclub`.", ephemeral=True)
        return

    club_id = int(st["club_id"])
    platform = st["platform"]
    logger.debug(f"[Command: clubstats] Fetching stats for club {club_id} on platform {platform}")

    try:
        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
            await warmup_session(session)

            info, used_platform = await fetch_club_info(session, platform, club_id)
            if isinstance(info, list):
                club_info = next(
                    (entry for entry in info if str(entry.get("clubId")) == str(club_id)),
                    {},
                )
            elif isinstance(info, dict):
                club_info = info.get(str(club_id), {})
            else:
                club_info = {}
            name = club_info.get("name", "Unknown Club")

            overall_data = await fetch_json(
                session,
                "/clubs/overallStats",
                {"clubIds": str(club_id), "platform": used_platform},
            )

            if isinstance(overall_data, list):
                stats = overall_data[0] if overall_data else {}
            else:
                stats = overall_data.get("value") if isinstance(overall_data, dict) else {}
                if isinstance(stats, list) and stats:
                    stats = stats[0]
                elif not isinstance(stats, dict):
                    stats = {}

            wins = int(stats.get("wins", 0) or 0)
            losses = int(stats.get("losses", 0) or 0)
            ties = int(stats.get("ties", 0) or 0)
            total_matches = int(stats.get("gamesPlayed", 0) or 0)
            
            # Use CLUB overall stats for consistency (not player stats which exclude former members)
            goals_for = int(stats.get("goals", 0) or 0)
            goals_against = int(stats.get("goalsAgainst", 0) or 0)
            
            skill_rating = int(stats.get("skillRating", 0) or 0)
            promotions = int(stats.get("promotions", 0) or 0)
            relegations = int(stats.get("relegations", 0) or 0)
            win_streak = int(stats.get("wstreak", 0) or 0)
            unbeaten_streak = int(stats.get("unbeatenstreak", 0) or 0)

            win_pct = (wins / total_matches * 100) if total_matches else 0

            form_map = {"-1": "", "1": "W", "2": "L", "3": "D"}
            recent_form = "".join(form_map.get(str(stats.get(f"lastMatch{i}", "-1")), "") for i in range(5))

            members_data = await fetch_json(
                session,
                "/members/stats",
                {"clubId": str(club_id), "platform": used_platform},
            )

            if isinstance(members_data, list):
                members_list = members_data
            else:
                members_list = (
                    members_data.get("members") if isinstance(members_data, dict) else []
                )

            members = [m for m in members_list if isinstance(m, dict)]
            
            # Cache player names for autocomplete
            player_names = [m.get("name", "") for m in members if m.get("name")]
            if player_names:
                cache_club_members(interaction.guild_id, player_names)
                logger.debug(f"[Command: clubstats] Cached {len(player_names)} player names for autocomplete")
            
            # Note: We use club overall stats for goals/assists, not player sum
            # This ensures consistency with GA and includes former members
            logger.debug(f"[Command: clubstats] Found {len(members)} members, club goals: {goals_for}, club GA: {goals_against}")

            # Calculate assists from current players (EA doesn't provide club-wide assists)
            assists = sum(int(m.get("assists", 0) or 0) for m in members)
            
            embed = discord.Embed(
                title=f"📊 {name}",
                description=f"Skill Rating: **{skill_rating}** | Platform: {used_platform}",
                color=discord.Color.blue(),
            )

            embed.add_field(name="Record", value=f"{wins}W - {losses}L - {ties}D", inline=True)
            embed.add_field(name="Matches", value=str(total_matches), inline=True)
            embed.add_field(name="Win %", value=f"{win_pct:.1f}%", inline=True)
            embed.add_field(name="Goals", value=str(goals_for), inline=True)
            embed.add_field(name="Assists", value=str(assists), inline=True)
            embed.add_field(name="GA", value=str(goals_against), inline=True)
            embed.add_field(name="Promotions", value=f"↗️ {promotions}", inline=True)
            embed.add_field(name="Relegations", value=f"↘️ {relegations}", inline=True)
            embed.add_field(name="Form (Last 5)", value=recent_form or "N/A", inline=True)

            if win_streak > 0:
                embed.add_field(name="🔥 Win Streak", value=str(win_streak), inline=True)
            if unbeaten_streak > 0:
                embed.add_field(name="🛡️ Unbeaten", value=str(unbeaten_streak), inline=True)

            if members:
                top_scorer = max(members, key=lambda m: int(m.get("goals", 0) or 0))
                top_assister = max(members, key=lambda m: int(m.get("assists", 0) or 0))

                embed.add_field(
                    name="🥇 Top Scorer",
                    value=f"{top_scorer.get('name', 'Unknown')} ({top_scorer.get('goals', 0)} goals)",
                    inline=False,
                )
                embed.add_field(
                    name="🎯 Top Assister",
                    value=f"{top_assister.get('name', 'Unknown')} ({top_assister.get('assists', 0)} assists)",
                    inline=False,
                )
                
                # Check for new players and initialize them (but don't announce milestones/achievements here)
                # Milestones and achievements are already handled automatically when new matches are detected
                for member in members:
                    player_name = member.get("name", "Unknown")
                    
                    # Check if player needs initialization (first time seeing them)
                    if not is_player_initialized(interaction.guild_id, player_name):
                        logger.info(f"New player detected in /clubstats: {player_name} - checking historical achievements")
                        historical_achievements = check_historical_achievements(interaction.guild_id, player_name, member)
                        if historical_achievements:
                            logger.info(f"Found {len(historical_achievements)} historical achievement(s) for {player_name}")
                            await announce_historical_achievements(client, interaction.guild_id, player_name, historical_achievements)
                        mark_player_initialized(interaction.guild_id, player_name)
                    
                    # Note: Milestones and achievements are checked automatically when new matches are detected
                    # We don't check them here to avoid duplicate announcements

        await interaction.followup.send(embed=embed)
    except Exception as e:  # noqa: BLE001
        await interaction.followup.send(
            f"Could not fetch club stats right now. Error: {e}", ephemeral=True
        )


async def player_name_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete callback for player names - uses cached data."""
    try:
        # Get cached player names from database
        player_names = get_cached_club_members(interaction.guild_id)
        
        if not player_names:
            return [app_commands.Choice(name="No players cached - use /clubstats first", value="")]
        
        # Filter based on current input (case-insensitive)
        current_lower = current.lower()
        matching_names = [
            name for name in player_names 
            if current_lower in name.lower()
        ]
        
        # Limit to 25 (Discord's max)
        return [
            app_commands.Choice(name=name, value=name)
            for name in matching_names[:25]
        ]
    except Exception as e:
        logger.warning(f"Autocomplete error: {e}")
        return []


@client.tree.command(name="playerstats", description="Show detailed statistics for a specific player")
@app_commands.describe(player_name="The name of the player to look up")
@app_commands.autocomplete(player_name=player_name_autocomplete)
async def playerstats(interaction: discord.Interaction, player_name: str):
    await interaction.response.defer(thinking=True)
    st = get_settings(interaction.guild_id)
    if not st or not (st.get("club_id") and st.get("platform")):
        await interaction.followup.send("Set a club first with `/setclub`.", ephemeral=True)
        return

    club_id = int(st["club_id"])
    platform = st["platform"]

    try:
        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
            await warmup_session(session)

            # Fetch club info for club name
            info, used_platform = await fetch_club_info(session, platform, club_id)
            if isinstance(info, list):
                club_info = next(
                    (entry for entry in info if str(entry.get("clubId")) == str(club_id)),
                    {},
                )
            elif isinstance(info, dict):
                club_info = info.get(str(club_id), {})
            else:
                club_info = {}
            club_name = club_info.get("name", "Unknown Club")

            # Fetch members data
            members_data = await fetch_json(
                session,
                "/members/stats",
                {"clubId": str(club_id), "platform": used_platform},
            )

            if isinstance(members_data, list):
                members_list = members_data
            else:
                members_list = (
                    members_data.get("members") if isinstance(members_data, dict) else []
                )

            members = [m for m in members_list if isinstance(m, dict)]
            
            # Cache player names for autocomplete
            player_names = [m.get("name", "") for m in members if m.get("name")]
            if player_names:
                cache_club_members(interaction.guild_id, player_names)
            
            # Find the player (case-insensitive search)
            player = None
            for m in members:
                if m.get("name", "").lower() == player_name.lower():
                    player = m
                    break
            
            if not player:
                # Try partial match
                for m in members:
                    if player_name.lower() in m.get("name", "").lower():
                        player = m
                        break
            
            if not player:
                available = ", ".join([m.get("name", "Unknown") for m in members[:10]])
                await interaction.followup.send(
                    f"❌ Player `{player_name}` not found in **{club_name}**.\n\n"
                    f"Available players: {available}{'...' if len(members) > 10 else ''}",
                    ephemeral=True
                )
                return

            # Build player stats embed (page 1)
            name = player.get("name", "Unknown")
            position = player.get("favoritePosition", player.get("proPos", "N/A"))
            
            # Stats - using correct EA API field names
            matches_played = int(player.get("gamesPlayed", 0))
            win_rate = int(player.get("winRate", 0))  # This is already a percentage
            goals = int(player.get("goals", 0))
            assists = int(player.get("assists", 0))
            
            # We don't calculate W/L/D since API only returns last 10 matches
            
            # Pass stats
            passes_made = int(player.get("passesMade", 0))
            pass_success_rate = int(player.get("passSuccessRate", 0))
            # Calculate attempts from success rate
            pass_attempts = int(passes_made / (pass_success_rate / 100)) if pass_success_rate > 0 else passes_made
            
            # Shot stats
            shot_success_rate = int(player.get("shotSuccessRate", 0))
            
            # Tackle stats
            tackles_made = int(player.get("tacklesMade", 0))
            tackle_success_rate = int(player.get("tackleSuccessRate", 0))
            
            # Other stats
            motm = int(player.get("manOfTheMatch", 0))
            rating = float(player.get("ratingAve", 0))
            red_cards = int(player.get("redCards", 0))
            # Yellow cards not in API response, but keep the field
            yellow_cards = 0
            
            clean_sheets_def = int(player.get("cleanSheetsDef", 0))
            clean_sheets_gk = int(player.get("cleanSheetsGK", 0))
            
            goals_per_game = goals / matches_played if matches_played else 0
            assists_per_game = assists / matches_played if matches_played else 0
            
            # Get hat-trick stats from match history
            hat_tricks = get_player_hat_trick_count(interaction.guild_id, name)
            assist_hat_tricks = get_player_assist_hat_trick_count(interaction.guild_id, name)

            stats_embed = discord.Embed(
                title=f"⚽ {name}",
                description=f"**{club_name}** | Position: {position}",
                color=discord.Color.green(),
            )

            # Just show matches played and win rate
            stats_embed.add_field(name="🎮 Matches", value=str(matches_played), inline=True)
            stats_embed.add_field(name="📈 Win %", value=f"{win_rate}%", inline=True)
            stats_embed.add_field(name="⭐ Avg Rating", value=f"{rating:.1f}" if rating else "N/A", inline=True)
            
            stats_embed.add_field(name="⚽ Goals", value=str(goals), inline=True)
            stats_embed.add_field(name="🅰️ Assists", value=str(assists), inline=True)
            stats_embed.add_field(name="⭐ MOTM", value=str(motm), inline=True)
            
            # Hat-trick stats (only show if > 0)
            if hat_tricks > 0:
                stats_embed.add_field(name="🎩 Hat-tricks", value=str(hat_tricks), inline=True)
            if assist_hat_tricks > 0:
                stats_embed.add_field(name="🎯 Assist Hat-tricks", value=str(assist_hat_tricks), inline=True)
            
            stats_embed.add_field(name="📊 Goals/Game", value=f"{goals_per_game:.2f}", inline=True)
            stats_embed.add_field(name="📊 Assists/Game", value=f"{assists_per_game:.2f}", inline=True)
            stats_embed.add_field(name="🎯 Pass Accuracy", value=f"{pass_success_rate}%", inline=True)
            
            stats_embed.add_field(name="🥅 Shot Accuracy", value=f"{shot_success_rate}%", inline=True)
            stats_embed.add_field(name="🛡️ Tackles", value=f"{tackles_made}", inline=True)
            stats_embed.add_field(name="🛡️ Tackle Success", value=f"{tackle_success_rate}%", inline=True)
            
            if clean_sheets_def > 0 or clean_sheets_gk > 0:
                clean_sheets = clean_sheets_gk if clean_sheets_gk > 0 else clean_sheets_def
                stats_embed.add_field(name="🧤 Clean Sheets", value=str(clean_sheets), inline=True)
            
            if red_cards > 0:
                stats_embed.add_field(name="🟥 Red Cards", value=str(red_cards), inline=True)

            # Next milestone progress
            from milestones import MILESTONE_THRESHOLDS
            from database import has_milestone_been_announced as _hm
            milestone_lines = []
            _stat_map = [
                ("goals", goals, "⚽"),
                ("assists", assists, "🅰️"),
                ("matches", matches_played, "🎮"),
                ("motm", motm, "⭐"),
            ]
            for stat_key, current_val, stat_emoji in _stat_map:
                for threshold in MILESTONE_THRESHOLDS[stat_key]:
                    if current_val < threshold:
                        remaining = threshold - current_val
                        milestone_lines.append(
                            f"{stat_emoji} {current_val}/{threshold} — **{remaining}** to go"
                        )
                        break
            if milestone_lines:
                stats_embed.add_field(
                    name="🎯 Next Milestones",
                    value="\n".join(milestone_lines),
                    inline=False,
                )

            stats_embed.set_footer(text=f"Platform: {used_platform} | Page 1/3")

            # Build achievements embed (page 2)
            from database import get_player_achievement_history
            from achievements import ACHIEVEMENTS

            achievement_history = get_player_achievement_history(interaction.guild_id, name)
            earned_ids = {a["achievement_id"] for a in achievement_history}
            ach_embed = discord.Embed(
                title=f"🏆 {name}'s Achievements",
                color=discord.Color.gold(),
            )
            earned_count = len(achievement_history)
            total_count = len(ACHIEVEMENTS)
            ach_embed.description = (
                f"**{earned_count}/{total_count}** achievements earned"
            )

            if achievement_history:
                categorized: dict = {}
                for ach in achievement_history:
                    ach_id = ach["achievement_id"]
                    if ach_id in ACHIEVEMENTS:
                        ach_data = ACHIEVEMENTS[ach_id]
                        cat = ach_data["category"]
                        categorized.setdefault(cat, []).append(ach_data)
                for cat, achs in categorized.items():
                    ach_embed.add_field(
                        name=cat,
                        value="\n".join(
                            f"{a['emoji']} **{a['name']}** — {a['description']}"
                            for a in achs
                        ),
                        inline=False,
                    )

            # Locked achievements with progress hints
            # Map achievement IDs to progress based on current stats
            _progress_hints: dict[str, str] = {
                "hat_trick_hero": f"{hat_tricks}/1 hat trick",
                "assist_king": f"{assist_hat_tricks}/1 assist hat trick",
                "brace": f"{goals} career goals",
                "poker": f"{goals} career goals",
                "century": f"{goals}/100 goals",
                "provider": f"{assists}/100 assists",
                "iron_man": f"{matches_played}/50 matches",
                "sharpshooter": f"{shot_success_rate}% shot acc (need 70%+, 50+ matches)",
                "midfield_maestro": f"{pass_success_rate}% pass acc (need 90%+, 100+ matches)",
                "the_wall": f"{tackle_success_rate}% tackle success (need 80%+, 500+ tackles)",
                "goal_machine": f"{goals_per_game:.2f} goals/game (need 2.0+, 25+ matches)",
                "playmaker": f"{goals}G / {assists}A (need more assists than goals, 50+ each)",
                "man_of_match": f"{motm} MOTM awards",
            }

            locked = [
                (ach_id, data) for ach_id, data in ACHIEVEMENTS.items()
                if ach_id not in earned_ids
            ]
            if locked:
                locked_lines = []
                for ach_id, data in locked[:8]:  # cap at 8 to avoid embed overflow
                    hint = _progress_hints.get(ach_id, "")
                    hint_str = f" `{hint}`" if hint else ""
                    locked_lines.append(f"🔒 **{data['name']}** — {data['description']}{hint_str}")
                remaining = len(locked) - 8
                if remaining > 0:
                    locked_lines.append(f"*…and {remaining} more. Use `/listachievements` to see all.*")
                ach_embed.add_field(
                    name="🔒 Locked Achievements",
                    value="\n".join(locked_lines),
                    inline=False,
                )

            ach_embed.set_footer(text=f"Platform: {used_platform} | Page 2/3")

            # Build stats-over-time embed (page 3) and pre-render the chart
            from database import get_player_match_history as _get_history
            history = _get_history(interaction.guild_id, name, limit=20)
            chart_result = _generate_player_chart(name, history)

            chart_page_files: dict = {}
            if chart_result:
                chart_file, chart_filename = chart_result
                # Read bytes so the file can be recreated on demand (e.g. when
                # the user navigates to the graph page after viewing other pages).
                chart_file.fp.seek(0)
                chart_raw_bytes = chart_file.fp.read()

                def _make_chart_file(raw=chart_raw_bytes, fname=chart_filename):
                    return discord.File(io.BytesIO(raw), filename=fname)

                chart_page_files = {2: _make_chart_file}

                total_g = sum(m["goals"] for m in history)
                total_a = sum(m["assists"] for m in history)
                gpg = total_g / len(history)
                apg = total_a / len(history)
                chart_embed = discord.Embed(
                    title=f"📈 Stats Over Time — {name}",
                    description=(
                        f"**{len(history)} matches tracked** | "
                        f"⚽ {total_g} goals ({gpg:.2f}/game) | "
                        f"🅰️ {total_a} assists ({apg:.2f}/game)"
                    ),
                    color=discord.Color.blurple(),
                )
                chart_embed.set_image(url=f"attachment://{chart_filename}")
                chart_embed.set_footer(
                    text="Match data tracked since the bot was set up | Page 3/3"
                )
            else:
                chart_embed = discord.Embed(
                    title=f"📈 Stats Over Time — {name}",
                    description=(
                        "Not enough match history yet.\n"
                        "The bot needs to track at least 2 matches after setup. "
                        "Play more and the chart will appear here automatically!"
                    ),
                    color=discord.Color.blurple(),
                )
                chart_embed.set_footer(text=f"Platform: {used_platform} | Page 3/3")

            pages = [stats_embed, ach_embed, chart_embed]
            view = PaginatedEmbedView(pages, page_files=chart_page_files)

            msg = await interaction.followup.send(embed=pages[0], view=view, wait=True)
            view.message = msg
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error fetching player stats: {e}", exc_info=True)
        await interaction.followup.send(
            f"Could not fetch player stats right now. Error: {e}", ephemeral=True
        )


@client.tree.command(name="lastmatches", description="Show the last 10 matches played by the club")
@app_commands.describe(match_type="Which type of matches to show (default: League)")
@app_commands.choices(
    match_type=[
        app_commands.Choice(name="League 🏟️", value="leagueMatch"),
        app_commands.Choice(name="Playoff 🏆", value="playoffMatch"),
        app_commands.Choice(name="All match types 🔀", value="all"),
    ]
)
async def lastmatches(interaction: discord.Interaction, match_type: app_commands.Choice[str] = None):
    """Display recent match history, paginated (one match per page)."""
    await interaction.response.defer(thinking=True)
    st = get_settings(interaction.guild_id)
    if not st or not (st.get("club_id") and st.get("platform")):
        await interaction.followup.send("Set a club first with `/setclub`.", ephemeral=True)
        return

    club_id = int(st["club_id"])
    platform = st["platform"]

    # Resolve the EA API match-type string and a human-readable label
    if not match_type or match_type.value == "all":
        ea_match_type = None
        type_label = "All match types"
    else:
        ea_match_type = match_type.value
        type_label = match_type.name

    try:
        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
            await warmup_session(session)
            
            # Fetch club name
            info, used_platform = await fetch_club_info(session, platform, club_id)
            if isinstance(info, dict):
                club_info = info.get(str(club_id), {})
            else:
                club_info = {}
            club_name = club_info.get("name", "Unknown Club")
            
            # Fetch last 10 matches of the requested type
            matches = await fetch_all_matches(
                session, used_platform, club_id, max_count=10, match_type=ea_match_type
            )
            
            if not matches:
                await interaction.followup.send(
                    f"No recent **{type_label}** matches found.", ephemeral=True
                )
                return
            
            # Calculate summary stats across all fetched matches
            total_w = total_d = total_l = total_gf = total_ga = 0
            for match in matches:
                clubs_s = match.get("clubs", {})
                oc = clubs_s.get(str(club_id), {})
                opp_ids = [cid for cid in clubs_s.keys() if str(cid) != str(club_id)]
                opc = clubs_s.get(opp_ids[0], {}) if opp_ids else {}
                r = oc.get("result", "")
                if r == "1": total_w += 1
                elif r == "2": total_l += 1
                elif r == "3": total_d += 1
                try: total_gf += int(oc.get("score", 0) or 0)
                except ValueError: pass
                try: total_ga += int(opc.get("score", 0) or 0)
                except ValueError: pass

            summary_line = f"W{total_w} D{total_d} L{total_l}  |  ⚽ {total_gf} scored, {total_ga} conceded"

            # Build one page per match with full player breakdown
            pages = []
            total_matches = len(matches)
            for i, match in enumerate(matches, 1):
                clubs = match.get("clubs", {})
                our_club = clubs.get(str(club_id), {})

                opponent_id = [cid for cid in clubs.keys() if str(cid) != str(club_id)]
                opponent_club = clubs.get(opponent_id[0], {}) if opponent_id else {}
                opponent_name = opponent_club.get("details", {}).get("name", "Unknown")

                our_score = our_club.get("score", "?")
                opp_score = opponent_club.get("score", "?")

                result = our_club.get("result", "")
                if result == "1":
                    result_emoji = "✅"
                    color = 0x2ecc71
                elif result == "2":
                    result_emoji = "❌"
                    color = 0xe74c3c
                elif result == "3":
                    result_emoji = "🤝"
                    color = 0xf1c40f
                else:
                    result_emoji = "❓"
                    color = 0x95a5a6

                time_ago = match.get("timeAgo", {})
                time_str = (
                    f"{time_ago.get('number', '?')} {time_ago.get('unit', 'ago')}"
                    if time_ago else "?"
                )

                embed = discord.Embed(
                    title=f"{result_emoji} {our_score}–{opp_score} vs {opponent_name}",
                    description=f"📊 `{summary_line}`\n🕐 {time_str} ago",
                    color=color,
                )

                # Player stats for this match
                all_players = match.get("players", {})
                club_players = all_players.get(str(club_id), {})
                player_stats = []
                for player_id, player_data in club_players.items():
                    if isinstance(player_data, dict):
                        player_stats.append({
                            "name": player_data.get("playername", "Unknown"),
                            "goals": int(player_data.get("goals", 0) or 0),
                            "assists": int(player_data.get("assists", 0) or 0),
                            "rating": float(player_data.get("rating", 0) or 0),
                            "mom": int(player_data.get("mom", 0) or 0),
                        })

                # Sort by rating descending
                player_stats.sort(key=lambda p: p["rating"], reverse=True)

                if player_stats:
                    lines = []
                    for p in player_stats:
                        motm_tag = " 🏅" if p["mom"] == 1 else ""
                        g = f"⚽{p['goals']}" if p["goals"] > 0 else ""
                        a = f"🅰️{p['assists']}" if p["assists"] > 0 else ""
                        extras = " ".join(filter(None, [g, a]))
                        line = f"**{p['name']}** — {p['rating']:.1f}{motm_tag}"
                        if extras:
                            line += f"  {extras}"
                        lines.append(line)
                    embed.add_field(
                        name="👥 Player Ratings",
                        value="\n".join(lines),
                        inline=False,
                    )

                embed.set_footer(
                    text=f"Match {i}/{total_matches} | {type_label} | {used_platform}"
                )
                pages.append(embed)
            
            view = PaginatedEmbedView(pages)
            view.message = await interaction.followup.send(embed=pages[0], view=view, wait=True)
            
    except Exception as e:
        logger.error(f"Error fetching matches: {e}", exc_info=True)
        await interaction.followup.send(
            f"Could not fetch matches right now. Error: {e}", ephemeral=True
        )


@client.tree.command(name="leaderboard", description="Show club leaderboard for various stats")
@app_commands.describe(
    category="The stat category to rank by",
    period="Career stats (default) or this month only",
)
@app_commands.choices(
    category=[
        app_commands.Choice(name="Goals ⚽", value="goals"),
        app_commands.Choice(name="Assists 🅰️", value="assists"),
        app_commands.Choice(name="Matches Played 🎮", value="matches"),
        app_commands.Choice(name="Man of the Match ⭐", value="motm"),
        app_commands.Choice(name="Average Rating 📊", value="rating"),
        app_commands.Choice(name="Pass Accuracy 🎯", value="pass_accuracy"),
        app_commands.Choice(name="Goals Per Game 📈", value="goals_per_game"),
        app_commands.Choice(name="Assists Per Game 📈", value="assists_per_game"),
        app_commands.Choice(name="Hat-tricks 🎩", value="hat_tricks"),
        app_commands.Choice(name="Assist Hat-tricks 🎯", value="assist_hat_tricks"),
        app_commands.Choice(name="Combined Score 🏆", value="combined"),
    ],
    period=[
        app_commands.Choice(name="Career 📊", value="career"),
        app_commands.Choice(name="This Month 📅", value="month"),
    ],
)
async def leaderboard(
    interaction: discord.Interaction,
    category: app_commands.Choice[str],
    period: app_commands.Choice[str] = None,
):
    await interaction.response.defer(thinking=True)
    st = get_settings(interaction.guild_id)
    if not st or not (st.get("club_id") and st.get("platform")):
        await interaction.followup.send("Set a club first with `/setclub`.", ephemeral=True)
        return

    club_id = int(st["club_id"])
    platform = st["platform"]
    use_month = period is not None and period.value == "month"

    try:
        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
            await warmup_session(session)

            # Fetch club info
            info, used_platform = await fetch_club_info(session, platform, club_id)
            if isinstance(info, list):
                club_info = next(
                    (entry for entry in info if str(entry.get("clubId")) == str(club_id)),
                    {},
                )
            elif isinstance(info, dict):
                club_info = info.get(str(club_id), {})
            else:
                club_info = {}
            club_name = club_info.get("name", "Unknown Club")

            # Fetch members data (always needed for names + career fallback)
            members_data = await fetch_json(
                session,
                "/members/stats",
                {"clubId": str(club_id), "platform": used_platform},
            )

            if isinstance(members_data, list):
                members_list = members_data
            else:
                members_list = (
                    members_data.get("members") if isinstance(members_data, dict) else []
                )

            members = [m for m in members_list if isinstance(m, dict)]

            # Cache player names for autocomplete
            player_names = [m.get("name", "") for m in members if m.get("name")]
            if player_names:
                cache_club_members(interaction.guild_id, player_names)

            if not members:
                await interaction.followup.send("No player data available.", ephemeral=True)
                return

            # Get hat-trick stats for all players
            hat_trick_stats = get_all_players_hat_trick_stats(interaction.guild_id)
            hat_trick_dict = {stat["player_name"]: stat for stat in hat_trick_stats}

            # Monthly stats lookup when period=month
            month_lookup: dict = {}
            if use_month:
                month_period = detect_month_period()
                for ms in get_monthly_stats(interaction.guild_id, month_period):
                    month_lookup[ms["player_name"]] = ms

            # Calculate derived stats for each player
            for m in members:
                pname = m.get("name", "")
                if use_month and pname in month_lookup:
                    ms = month_lookup[pname]
                    m_matches = ms["matches_played"]
                    m_goals = ms["goals"]
                    m_assists = ms["assists"]
                    m_rating = ms["avg_rating"]
                    m_score = ms["monthly_score"]
                else:
                    m_matches = int(m.get("gamesPlayed", 0))
                    m_goals = int(m.get("goals", 0))
                    m_assists = int(m.get("assists", 0))
                    m_rating = float(m.get("ratingAve", 0))
                    m_score = m_goals * 10 + m_assists * 7 + m_rating * 5 + m_matches * 2

                m["_matches"] = m_matches
                m["_goals"] = m_goals
                m["_assists"] = m_assists
                m["_goals_per_game"] = m_goals / m_matches if m_matches else 0
                m["_assists_per_game"] = m_assists / m_matches if m_matches else 0
                m["_pass_accuracy"] = int(m.get("passSuccessRate", 0))
                m["_motm"] = int(m.get("manOfTheMatch", 0))
                m["_rating"] = m_rating
                m["_combined"] = m_score
                player_ht_stats = hat_trick_dict.get(pname, {"hat_tricks": 0, "assist_hat_tricks": 0})
                m["_hat_tricks"] = player_ht_stats["hat_tricks"]
                m["_assist_hat_tricks"] = player_ht_stats["assist_hat_tricks"]

            period_label = f"This Month ({detect_month_period()})" if use_month else "Career"

            # Sort based on category
            cat_value = category.value
            if cat_value == "goals":
                sorted_members = sorted(members, key=lambda m: m["_goals"], reverse=True)
                title = f"⚽ Goals Leaderboard — {period_label}"
                format_fn = lambda m: f"{m['_goals']} goals"
            elif cat_value == "assists":
                sorted_members = sorted(members, key=lambda m: m["_assists"], reverse=True)
                title = f"🅰️ Assists Leaderboard — {period_label}"
                format_fn = lambda m: f"{m['_assists']} assists"
            elif cat_value == "matches":
                sorted_members = sorted(members, key=lambda m: m["_matches"], reverse=True)
                title = f"🎮 Matches Played Leaderboard — {period_label}"
                format_fn = lambda m: f"{m['_matches']} matches"
            elif cat_value == "motm":
                sorted_members = sorted(members, key=lambda m: m["_motm"], reverse=True)
                title = "⭐ Man of the Match Leaderboard"
                format_fn = lambda m: f"{m['_motm']} MOTM"
            elif cat_value == "rating":
                sorted_members = sorted(members, key=lambda m: m["_rating"], reverse=True)
                title = f"📊 Average Rating Leaderboard — {period_label}"
                format_fn = lambda m: f"{m['_rating']:.1f} rating"
            elif cat_value == "pass_accuracy":
                sorted_members = sorted(members, key=lambda m: m["_pass_accuracy"], reverse=True)
                title = "🎯 Pass Accuracy Leaderboard"
                format_fn = lambda m: f"{m['_pass_accuracy']}% accuracy"
            elif cat_value == "goals_per_game":
                sorted_members = sorted(members, key=lambda m: m["_goals_per_game"], reverse=True)
                title = f"📈 Goals Per Game Leaderboard — {period_label}"
                format_fn = lambda m: f"{m['_goals_per_game']:.2f} goals/game"
            elif cat_value == "assists_per_game":
                sorted_members = sorted(members, key=lambda m: m["_assists_per_game"], reverse=True)
                title = f"📈 Assists Per Game Leaderboard — {period_label}"
                format_fn = lambda m: f"{m['_assists_per_game']:.2f} assists/game"
            elif cat_value == "hat_tricks":
                sorted_members = sorted(members, key=lambda m: m["_hat_tricks"], reverse=True)
                title = "🎩 Hat-tricks Leaderboard"
                format_fn = lambda m: f"{m['_hat_tricks']} hat-trick{'s' if m['_hat_tricks'] != 1 else ''}"
            elif cat_value == "assist_hat_tricks":
                sorted_members = sorted(members, key=lambda m: m["_assist_hat_tricks"], reverse=True)
                title = "🎯 Assist Hat-tricks Leaderboard"
                format_fn = lambda m: f"{m['_assist_hat_tricks']} assist hat-trick{'s' if m['_assist_hat_tricks'] != 1 else ''}"
            elif cat_value == "combined":
                sorted_members = sorted(members, key=lambda m: m["_combined"], reverse=True)
                title = f"🏆 Combined Score Leaderboard — {period_label}"
                format_fn = lambda m: (
                    f"Score: **{m['_combined']:.0f}** | "
                    f"⚽{m['_goals']} 🅰️{m['_assists']} ⭐{m['_rating']:.1f} 🎮{m['_matches']}"
                )
            else:
                sorted_members = members
                title = "Leaderboard"
                format_fn = lambda m: ""

            # Build paginated leaderboard (10 players per page)
            medals = ["🥇", "🥈", "🥉"]
            page_size = 10
            total_players = len(sorted_members)
            total_pages = max(1, (total_players + page_size - 1) // page_size)

            pages = []
            for page_num in range(total_pages):
                start = page_num * page_size
                page_members = sorted_members[start:start + page_size]

                embed = discord.Embed(
                    title=f"{title}",
                    description=f"**{club_name}**",
                    color=discord.Color.gold(),
                )

                for i, m in enumerate(page_members):
                    global_rank = start + i
                    rank = medals[global_rank] if global_rank < 3 else f"{global_rank + 1}."
                    name = m.get("name", "Unknown")
                    stat_text = format_fn(m)
                    embed.add_field(
                        name=f"{rank} {name}",
                        value=stat_text,
                        inline=False
                    )

                embed.set_footer(
                    text=f"Platform: {used_platform} | Page {page_num + 1}/{total_pages} | {total_players} players"
                )
                pages.append(embed)

            view = PaginatedEmbedView(pages)
            view.message = await interaction.followup.send(embed=pages[0], view=view, wait=True)
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error fetching leaderboard: {e}", exc_info=True)
        await interaction.followup.send(
            f"Could not fetch leaderboard right now. Error: {e}", ephemeral=True
        )


@client.tree.command(name="achievements", description="View a player's earned achievements")
@app_commands.describe(player_name="The name of the player to look up")
@app_commands.autocomplete(player_name=player_name_autocomplete)
async def achievements_cmd(interaction: discord.Interaction, player_name: str):
    """Display all achievements earned by a specific player."""
    await interaction.response.defer(thinking=True)
    logger.info(f"[Command: achievements] User {interaction.user} requesting achievements for {player_name}")
    
    st = get_settings(interaction.guild_id)
    if not st or not st.get("club_id"):
        await interaction.followup.send("Set a club first with `/setclub`.", ephemeral=True)
        return
    
    try:
        from database import get_player_achievement_history
        from achievements import ACHIEVEMENTS
        
        # Get player's achievements from database
        achievement_history = get_player_achievement_history(interaction.guild_id, player_name)
        
        if not achievement_history:
            await interaction.followup.send(
                f"🏆 **{player_name}** hasn't earned any achievements yet!\n\n"
                f"Use `/listachievements` to see all available achievements.",
                ephemeral=True
            )
            return
        
        # Build achievements embed
        embed = discord.Embed(
            title=f"🏆 {player_name}'s Achievements",
            description=f"Total: **{len(achievement_history)}** achievement{'s' if len(achievement_history) != 1 else ''}",
            color=discord.Color.gold(),
        )
        
        # Group by category
        categorized = {}
        for ach in achievement_history:
            ach_id = ach["achievement_id"]
            if ach_id in ACHIEVEMENTS:
                ach_data = ACHIEVEMENTS[ach_id]
                category = ach_data["category"]
                if category not in categorized:
                    categorized[category] = []
                categorized[category].append({
                    **ach_data,
                    "achieved_at": ach["achieved_at"]
                })
        
        # Add fields for each category
        for category, achievements_list in categorized.items():
            ach_text = "\n".join([
                f"{ach['emoji']} **{ach['name']}** - {ach['description']}"
                for ach in achievements_list
            ])
            embed.add_field(name=category, value=ach_text, inline=False)
        
        embed.set_footer(text=f"Keep playing to unlock more achievements!")
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        logger.error(f"Error fetching achievements: {e}", exc_info=True)
        await interaction.followup.send(
            f"Could not fetch achievements right now. Error: {e}", ephemeral=True
        )


@client.tree.command(name="listachievements", description="List all available achievements")
async def listachievements(interaction: discord.Interaction):
    """Display all available achievements that can be earned, paginated by category."""
    await interaction.response.defer(thinking=True)
    logger.info(f"[Command: listachievements] User {interaction.user} requesting achievement list")
    
    try:
        from achievements import get_all_achievements_list
        
        categorized = get_all_achievements_list()
        total_count = sum(len(achs) for achs in categorized.values())
        categories = list(categorized.items())

        # Build one embed per category so each page stays focused
        pages = []
        for page_num, (category, achievements_list) in enumerate(categories, 1):
            ach_text = "\n".join([
                f"{ach['emoji']} **{ach['name']}** — {ach['description']}"
                for ach in achievements_list
            ])
            embed = discord.Embed(
                title=f"🏆 Achievements — {category}",
                description=ach_text,
                color=discord.Color.gold(),
            )
            embed.set_footer(
                text=f"Page {page_num}/{len(categories)} | {total_count} achievements total"
            )
            pages.append(embed)

        view = PaginatedEmbedView(pages)
        view.message = await interaction.followup.send(embed=pages[0], view=view, wait=True)
        
    except Exception as e:
        logger.error(f"Error listing achievements: {e}", exc_info=True)
        await interaction.followup.send(
            f"Could not list achievements right now. Error: {e}", ephemeral=True
        )


@client.tree.command(name="lastperformance", description="Show a player's performance over their last 10 matches")
@app_commands.describe(
    player_name="The name of the player to look up",
    match_type="Which type of matches to show (default: League)",
)
@app_commands.choices(
    match_type=[
        app_commands.Choice(name="League 🏟️", value="leagueMatch"),
        app_commands.Choice(name="Playoff 🏆", value="playoffMatch"),
        app_commands.Choice(name="All match types 🔀", value="all"),
    ]
)
@app_commands.autocomplete(player_name=player_name_autocomplete)
async def lastperformance(interaction: discord.Interaction, player_name: str, match_type: app_commands.Choice[str] = None):
    """
    Command: /lastperformance
    Shows detailed per-match stats for a player's last 10 matches fetched from the EA API.
    Displays goals, assists, rating, and result for each match.
    """
    await interaction.response.defer(thinking=True)
    logger.info(f"[Command: lastperformance] User {interaction.user} in guild {interaction.guild_id} requesting last performance for {player_name}")

    st = get_settings(interaction.guild_id)
    if not st or not (st.get("club_id") and st.get("platform")):
        await interaction.followup.send("Set a club first with `/setclub`.", ephemeral=True)
        return

    club_id = int(st["club_id"])
    platform = st["platform"]

    # Resolve the EA API match-type string and a human-readable label
    if not match_type or match_type.value == "all":
        ea_match_type = None
        type_label = "All match types"
    else:
        ea_match_type = match_type.value
        type_label = match_type.name

    try:
        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
            await warmup_session(session)

            info, used_platform = await fetch_club_info(session, platform, club_id)
            if isinstance(info, list):
                club_info = next(
                    (entry for entry in info if str(entry.get("clubId")) == str(club_id)),
                    {},
                )
            elif isinstance(info, dict):
                club_info = info.get(str(club_id), {})
            else:
                club_info = {}
            club_name = club_info.get("name", "Unknown Club")

            matches = await fetch_all_matches(
                session, used_platform, club_id, max_count=10, match_type=ea_match_type
            )

            if not matches:
                await interaction.followup.send(
                    f"No recent **{type_label}** matches found.", ephemeral=True
                )
                return

            # Collect per-match stats for the requested player
            player_match_rows = []
            for match in matches:
                clubs = match.get("clubs", {})
                our_club = clubs.get(str(club_id), {})
                result_code = our_club.get("result", "")
                if result_code == "1":
                    result_emoji = "✅"
                elif result_code == "2":
                    result_emoji = "❌"
                elif result_code == "3":
                    result_emoji = "🤝"
                else:
                    result_emoji = "❓"

                opponent_ids = [cid for cid in clubs.keys() if str(cid) != str(club_id)]
                opponent_club = clubs.get(opponent_ids[0], {}) if opponent_ids else {}
                our_score = our_club.get("score", "?")
                opp_score = opponent_club.get("score", "?")

                all_players = match.get("players", {})
                club_players = all_players.get(str(club_id), {})

                # Find this player in the match
                pdata = None
                for pid, pd in club_players.items():
                    if isinstance(pd, dict) and pd.get("playername", "").lower() == player_name.lower():
                        pdata = pd
                        break

                if pdata is None:
                    continue  # Player didn't play in this match

                goals = int(pdata.get("goals", 0) or 0)
                assists = int(pdata.get("assists", 0) or 0)
                rating = float(pdata.get("rating", 0) or 0)
                is_motm = int(pdata.get("mom", 0) or 0) == 1

                time_ago = match.get("timeAgo", {})
                time_str = f"{time_ago.get('number', '?')} {time_ago.get('unit', '')}" if time_ago else "?"

                player_match_rows.append({
                    "result_emoji": result_emoji,
                    "score": f"{our_score}-{opp_score}",
                    "goals": goals,
                    "assists": assists,
                    "rating": rating,
                    "motm": is_motm,
                    "time_str": time_str,
                })

            if not player_match_rows:
                await interaction.followup.send(
                    f"❌ **{player_name}** didn't appear in the last {len(matches)} **{type_label}** matches.\n"
                    f"Make sure the name is correct or try `/clubstats` first to refresh the player cache.",
                    ephemeral=True
                )
                return

            # Aggregate summary
            total_goals = sum(r["goals"] for r in player_match_rows)
            total_assists = sum(r["assists"] for r in player_match_rows)
            avg_rating = sum(r["rating"] for r in player_match_rows) / len(player_match_rows)
            wins = sum(1 for r in player_match_rows if r["result_emoji"] == "✅")
            losses = sum(1 for r in player_match_rows if r["result_emoji"] == "❌")
            draws = sum(1 for r in player_match_rows if r["result_emoji"] == "🤝")
            motm_count = sum(1 for r in player_match_rows if r["motm"])

            embed = discord.Embed(
                title=f"📊 {player_name} — Last {len(player_match_rows)} Matches",
                description=f"**{club_name}** | {type_label} | Summary: {wins}W {losses}L {draws}D",
                color=discord.Color.green(),
            )

            embed.add_field(name="⚽ Goals", value=str(total_goals), inline=True)
            embed.add_field(name="🅰️ Assists", value=str(total_assists), inline=True)
            embed.add_field(name="⭐ Avg Rating", value=f"{avg_rating:.2f}", inline=True)
            if motm_count:
                embed.add_field(name="🏅 MOTM", value=str(motm_count), inline=True)

            # Per-match breakdown (most recent first, up to 10)
            lines = []
            for i, r in enumerate(player_match_rows, 1):
                motm_tag = " 🏅" if r["motm"] else ""
                lines.append(
                    f"{i}. {r['result_emoji']} `{r['score']}` "
                    f"⚽{r['goals']} 🅰️{r['assists']} ⭐{r['rating']:.1f}{motm_tag} "
                    f"— {r['time_str']} ago"
                )

            embed.add_field(
                name="Match Breakdown (most recent first)",
                value="\n".join(lines),
                inline=False,
            )
            embed.set_footer(text=f"Platform: {used_platform} | {type_label}")
            await interaction.followup.send(embed=embed)

    except Exception as e:
        logger.error(f"Error fetching last performance: {e}", exc_info=True)
        await interaction.followup.send(
            f"Could not fetch performance data right now. Error: {e}", ephemeral=True
        )


@client.tree.command(name="statsovertime", description="Visualize a player's goals and assists per game over recent matches")
@app_commands.describe(player_name="The name of the player to visualize")
@app_commands.autocomplete(player_name=player_name_autocomplete)
async def statsovertime(interaction: discord.Interaction, player_name: str):
    """
    Command: /statsovertime
    Generates a chart showing a player's goals and assists trends over their
    recorded match history. Uses locally stored match data tracked by the bot.
    """
    await interaction.response.defer(thinking=True)
    logger.info(f"[Command: statsovertime] User {interaction.user} in guild {interaction.guild_id} requesting stats over time for {player_name}")

    st = get_settings(interaction.guild_id)
    if not st or not st.get("club_id"):
        await interaction.followup.send("Set a club first with `/setclub`.", ephemeral=True)
        return

    try:
        from database import get_player_match_history

        history = get_player_match_history(interaction.guild_id, player_name, limit=20)

        chart_result = _generate_player_chart(player_name, history)
        if not chart_result:
            await interaction.followup.send(
                f"❌ Not enough match history for **{player_name}** yet.\n"
                f"The bot needs to track at least 2 matches after setup. "
                f"Play more matches and the data will accumulate automatically!",
                ephemeral=True
            )
            return

        chart_file, chart_filename = chart_result
        total_goals = sum(m["goals"] for m in history)
        total_assists = sum(m["assists"] for m in history)
        final_gpg = total_goals / len(history)
        final_apg = total_assists / len(history)

        embed = discord.Embed(
            title=f"📈 Stats Over Time — {player_name}",
            description=(
                f"**{len(history)} matches tracked** | "
                f"⚽ {total_goals} goals ({final_gpg:.2f}/game) | "
                f"🅰️ {total_assists} assists ({final_apg:.2f}/game)"
            ),
            color=discord.Color.blurple(),
        )
        embed.set_image(url=f"attachment://{chart_filename}")
        embed.set_footer(text="Match data tracked since the bot was set up for this server.")
        await interaction.followup.send(embed=embed, file=chart_file)

    except Exception as e:
        logger.error(f"Error generating stats over time chart: {e}", exc_info=True)
        await interaction.followup.send(
            f"Could not generate chart right now. Error: {e}", ephemeral=True
        )


@client.tree.command(name="bestxi", description="Best XI lineup based on player ratings")
@app_commands.describe(period="Which period to base selection on (default: this month)")
@app_commands.choices(period=[
    app_commands.Choice(name="This month 📅", value="month"),
    app_commands.Choice(name="Career 📊",     value="career"),
])
async def bestxi(interaction: discord.Interaction, period: app_commands.Choice[str] = None):
    """Auto-build the best 4-3-3 lineup using avg rating per player, grouped by position."""
    await interaction.response.defer(thinking=True)

    st = get_settings(interaction.guild_id)
    if not st or not (st.get("club_id") and st.get("platform")):
        await interaction.followup.send("Set a club first with `/setclub`.", ephemeral=True)
        return

    club_id = int(st["club_id"])
    platform = st["platform"]
    use_month = period is None or period.value == "month"

    # EA Pro Clubs numeric position code -> short name
    _POS_CODE = {
        "0": "GK",  "1": "SW",  "2": "RWB", "3": "RB",  "4": "RCB",
        "5": "CB",  "6": "LCB", "7": "LB",  "8": "LWB", "9": "RDM",
        "10": "CDM","11": "LDM","12": "RM", "13": "RCM","14": "CM",
        "15": "LCM","16": "LM", "17": "RAM","18": "CAM","19": "LAM",
        "20": "RF", "21": "CF", "22": "LF", "23": "RW", "24": "RS",
        "25": "ST", "26": "LS", "27": "LW", "28": "ANY",
    }
    _GK  = {"GK", "SW"}
    _DEF = {"RWB","RB","RCB","CB","LCB","LB","LWB"}
    _MID = {"RDM","CDM","LDM","RM","RCM","CM","LCM","LM","RAM","CAM","LAM"}
    _ATT = {"RF","CF","LF","RW","RS","ST","LS","LW"}

    def _normalize(raw) -> str:
        s = str(raw).strip()
        return _POS_CODE.get(s, s.upper()) if s.isdigit() else s.upper()

    def _group(pos: str) -> str:
        if pos in _GK:  return "GK"
        if pos in _DEF: return "DEF"
        if pos in _MID: return "MID"
        if pos in _ATT: return "ATT"
        return "ANY"

    try:
        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
            await warmup_session(session)

            info, used_platform = await fetch_club_info(session, platform, club_id)
            if isinstance(info, list):
                club_info = next((e for e in info if str(e.get("clubId")) == str(club_id)), {})
            elif isinstance(info, dict):
                club_info = info.get(str(club_id), {})
            else:
                club_info = {}
            club_name = club_info.get("name", "Unknown Club")

            members_data = await fetch_json(
                session, "/members/stats",
                {"clubId": str(club_id), "platform": used_platform},
            )
            if isinstance(members_data, list):
                members_list = members_data
            else:
                members_list = members_data.get("members", []) if isinstance(members_data, dict) else []
            members = [m for m in members_list if isinstance(m, dict)]

        # Monthly stats lookup (for "month" mode)
        month_period = detect_month_period()
        monthly_lookup = {}
        if use_month:
            for s in get_monthly_stats(interaction.guild_id, month_period):
                monthly_lookup[s["player_name"]] = s

        # Build player list with stats + position group
        min_matches = 3 if use_month else 5
        players = []
        for m in members:
            name = m.get("name", "")
            if not name:
                continue
            # Use most-played position from match history; fall back to EA API favorite
            dominant_raw = get_player_dominant_position(interaction.guild_id, name)
            pos = _normalize(dominant_raw or m.get("favoritePosition") or m.get("proPos") or "ANY")
            group = _group(pos)

            if use_month and name in monthly_lookup:
                ms = monthly_lookup[name]
                rating  = ms["avg_rating"]
                goals   = ms["goals"]
                assists = ms["assists"]
                matches = ms["matches_played"]
            else:
                rating  = float(m.get("ratingAve", 0))
                goals   = int(m.get("goals", 0))
                assists = int(m.get("assists", 0))
                matches = int(m.get("gamesPlayed", 0))

            if matches < min_matches:
                continue

            players.append(dict(
                name=name, pos=pos, group=group,
                rating=rating, goals=goals, assists=assists, matches=matches,
            ))

        # Sort each group by rating desc
        by_group: dict[str, list] = {"GK": [], "DEF": [], "MID": [], "ATT": [], "ANY": []}
        for p in players:
            by_group[p["group"]].append(p)
        for g in by_group:
            by_group[g].sort(key=lambda x: x["rating"], reverse=True)

        # Fill 4-3-3 slots; fall back to ANY-position players if a group is short
        SLOTS = [("GK", 1), ("DEF", 4), ("MID", 3), ("ATT", 3)]
        selected_by_group: dict[str, list] = {}
        used: set[str] = set()
        for group, count in SLOTS:
            pool = [p for p in by_group[group] if p["name"] not in used]
            if len(pool) < count:
                fill = [p for p in by_group["ANY"] if p["name"] not in used]
                pool = pool + fill
            chosen = pool[:count]
            selected_by_group[group] = chosen
            used.update(p["name"] for p in chosen)

        total = sum(len(v) for v in selected_by_group.values())
        if total == 0:
            await interaction.followup.send(
                f"Not enough players with {min_matches}+ matches to build a Best XI.\n"
                "Play more matches or try **Career** mode.",
                ephemeral=True,
            )
            return

        # Build embed
        period_label = f"This month ({month_period})" if use_month else "Career"
        embed = discord.Embed(
            title=f"🏟️ Best XI — {club_name}",
            description=f"📅 **{period_label}** | Formation: 4-3-3 | Ranked by avg rating",
            color=discord.Color.gold(),
        )

        _emoji = {"GK": "🧤", "DEF": "🛡️", "MID": "⚙️", "ATT": "⚽"}
        _label = {"GK": "Goalkeeper", "DEF": "Defenders", "MID": "Midfielders", "ATT": "Attackers"}

        for group, _ in SLOTS:
            group_players = selected_by_group.get(group, [])
            if not group_players:
                continue
            lines = []
            for p in group_players:
                lines.append(
                    f"⭐ **{p['rating']:.2f}** | **{p['name']}** "
                    f"— {p['goals']}G {p['assists']}A ({p['matches']} matches)"
                )
            embed.add_field(
                name=f"{_emoji[group]} {_label[group]}",
                value="\n".join(lines),
                inline=False,
            )

        embed.set_footer(text=f"Min {min_matches} matches required | Platform: {used_platform}")
        await interaction.followup.send(embed=embed)

    except Exception as e:
        logger.error(f"Error in /bestxi: {e}", exc_info=True)
        await interaction.followup.send(f"Could not build Best XI. Error: {e}", ephemeral=True)


@client.tree.command(name="headtohead", description="Compare two players side by side")
@app_commands.describe(
    player1="First player to compare",
    player2="Second player to compare",
)
@app_commands.autocomplete(player1=player_name_autocomplete, player2=player_name_autocomplete)
async def headtohead(interaction: discord.Interaction, player1: str, player2: str):
    """Compare two club members side by side across all key stats."""
    await interaction.response.defer(thinking=True)

    if player1.lower() == player2.lower():
        await interaction.followup.send("Choose two different players to compare.", ephemeral=True)
        return

    st = get_settings(interaction.guild_id)
    if not st or not (st.get("club_id") and st.get("platform")):
        await interaction.followup.send("Set a club first with `/setclub`.", ephemeral=True)
        return

    club_id = int(st["club_id"])
    platform = st["platform"]

    try:
        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
            await warmup_session(session)

            info, used_platform = await fetch_club_info(session, platform, club_id)
            if isinstance(info, list):
                club_info = next(
                    (e for e in info if str(e.get("clubId")) == str(club_id)), {}
                )
            elif isinstance(info, dict):
                club_info = info.get(str(club_id), {})
            else:
                club_info = {}
            club_name = club_info.get("name", "Unknown Club")

            members_data = await fetch_json(
                session,
                "/members/stats",
                {"clubId": str(club_id), "platform": used_platform},
            )
            if isinstance(members_data, list):
                members_list = members_data
            else:
                members_list = members_data.get("members", []) if isinstance(members_data, dict) else []

            members = [m for m in members_list if isinstance(m, dict)]

            def find_player(name: str):
                # exact match first, then partial
                for m in members:
                    if m.get("name", "").lower() == name.lower():
                        return m
                for m in members:
                    if name.lower() in m.get("name", "").lower():
                        return m
                return None

            p1 = find_player(player1)
            p2 = find_player(player2)

            missing = []
            if not p1:
                missing.append(player1)
            if not p2:
                missing.append(player2)
            if missing:
                await interaction.followup.send(
                    f"❌ Could not find: {', '.join(f'`{n}`' for n in missing)} in **{club_name}**.",
                    ephemeral=True,
                )
                return

            def extract(m: dict) -> dict:
                matches = int(m.get("gamesPlayed", 0))
                goals = int(m.get("goals", 0))
                assists = int(m.get("assists", 0))
                rating = float(m.get("ratingAve", 0))
                motm = int(m.get("manOfTheMatch", 0))
                win_rate = int(m.get("winRate", 0))
                pass_acc = int(m.get("passSuccessRate", 0))
                shot_acc = int(m.get("shotSuccessRate", 0))
                tackles = int(m.get("tacklesMade", 0))
                tackle_acc = int(m.get("tackleSuccessRate", 0))
                red_cards = int(m.get("redCards", 0))
                gpg = goals / matches if matches else 0.0
                apg = assists / matches if matches else 0.0
                return dict(
                    name=m.get("name", "Unknown"),
                    matches=matches, goals=goals, assists=assists,
                    rating=rating, motm=motm, win_rate=win_rate,
                    pass_acc=pass_acc, shot_acc=shot_acc,
                    tackles=tackles, tackle_acc=tackle_acc,
                    red_cards=red_cards, gpg=gpg, apg=apg,
                )

            s1, s2 = extract(p1), extract(p2)

            # Build side-by-side field values with 🏆 on the winning side
            def cmp(v1, v2, *, higher_is_better=True, fmt=str):
                if higher_is_better:
                    w1, w2 = v1 > v2, v2 > v1
                else:
                    w1, w2 = v1 < v2, v2 < v1
                t1 = ("🏆 " if w1 else "    ") + fmt(v1)
                t2 = ("🏆 " if w2 else "    ") + fmt(v2)
                return t1, t2

            rows = [
                ("🎮 Matches",       *cmp(s1["matches"],   s2["matches"])),
                ("📈 Win %",         *cmp(s1["win_rate"],  s2["win_rate"],  fmt=lambda x: f"{x}%")),
                ("⭐ Avg Rating",    *cmp(s1["rating"],    s2["rating"],    fmt=lambda x: f"{x:.2f}")),
                ("⚽ Goals",         *cmp(s1["goals"],     s2["goals"])),
                ("🅰️ Assists",       *cmp(s1["assists"],   s2["assists"])),
                ("📊 Goals/Game",    *cmp(s1["gpg"],       s2["gpg"],       fmt=lambda x: f"{x:.2f}")),
                ("📊 Assists/Game",  *cmp(s1["apg"],       s2["apg"],       fmt=lambda x: f"{x:.2f}")),
                ("🏅 MOTM",          *cmp(s1["motm"],      s2["motm"])),
                ("🎯 Pass Acc.",     *cmp(s1["pass_acc"],  s2["pass_acc"],  fmt=lambda x: f"{x}%")),
                ("🥅 Shot Acc.",     *cmp(s1["shot_acc"],  s2["shot_acc"],  fmt=lambda x: f"{x}%")),
                ("🛡️ Tackles",       *cmp(s1["tackles"],   s2["tackles"])),
                ("🛡️ Tackle Acc.",   *cmp(s1["tackle_acc"],s2["tackle_acc"],fmt=lambda x: f"{x}%")),
            ]
            # Only add red cards row if either player has any
            if s1["red_cards"] or s2["red_cards"]:
                rows.append(("🟥 Red Cards", *cmp(s1["red_cards"], s2["red_cards"], higher_is_better=False)))

            labels_col = "\n".join(label for label, _, _ in rows)
            p1_col = "\n".join(v1 for _, v1, _ in rows)
            p2_col = "\n".join(v2 for _, _, v2 in rows)

            embed = discord.Embed(
                title=f"⚔️ {s1['name']} vs {s2['name']}",
                description=f"**{club_name}** — Head to Head",
                color=discord.Color.blue(),
            )
            embed.add_field(name=f"👤 {s1['name']}", value=p1_col, inline=True)
            embed.add_field(name="📊 Stat",            value=labels_col, inline=True)
            embed.add_field(name=f"👤 {s2['name']}", value=p2_col, inline=True)
            embed.set_footer(text=f"Platform: {used_platform} | Career stats")

            await interaction.followup.send(embed=embed)

    except Exception as e:
        logger.error(f"Error in /headtohead: {e}", exc_info=True)
        await interaction.followup.send(f"Could not fetch comparison data. Error: {e}", ephemeral=True)


# ---------- run ----------
if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set DISCORD_TOKEN in your .env")
    init_db()
    logger.info("Database initialized")
    logger.info("Starting bot...")
    client.run(TOKEN)




