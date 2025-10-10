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
   - /clubstats: View overall club statistics
   - /playerstats: View individual player stats
   - /lastmatches: View recent match history
   - /leaderboard: View player leaderboards
   - /achievements: View a player's earned achievements
   - /listachievements: List all available achievements

LOGGING:
--------
All operations are logged with descriptive prefixes:
- [Command: <name>] - Slash command execution
- [Guild <id>] - Match polling per guild
- [EA API] - API requests and responses
- [Database] - Database operations

Use these prefixes to quickly identify issues in the console logs.
"""
import os
import re
import logging
import asyncio
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
    update_player_match_history
)
from milestones import check_milestones, announce_milestones
from achievements import check_achievements, announce_achievements
from utils.ea_api import (
    platform_from_choice, parse_club_id_from_any, warmup_session,
    fetch_club_info, fetch_latest_match, fetch_json, HTTP_TIMEOUT,
    fetch_all_matches, calculate_player_wld
)
from utils.embeds import build_match_embed, utc_to_str

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


# ---------- Bot Class ----------
class ProClubsBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

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
                if not (club_id and platform and channel_id and autopost):
                    logger.warning(f"Guild {guild_id} missing required settings (club_id={club_id}, platform={platform}, channel_id={channel_id}, autopost={autopost})")
                    continue
                
                try:
                    # Step 1: Fetch club info to get club name
                    logger.debug(f"[Guild {guild_id}] Fetching club info for club {club_id}...")
                    info, used_platform = await fetch_club_info(session, platform, club_id)
                    
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
                    
                    if not match or not mt:
                        logger.debug(f"[Guild {guild_id}] No matches found or no match data available")
                        continue

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
                        match_id = f"{match.get('timestamp', 0)}:{match.get('homeScore', '?')}-{match.get('awayScore', '?')}"
                        logger.debug(f"[Guild {guild_id}] Using fallback match ID: {match_id}")
                    
                    logger.info(f"[Guild {guild_id}] Latest match ID: {match_id}")

                    # Step 4: Check if we've already posted this match
                    if str(match_id) == str(last_match_id):
                        logger.debug(f"[Guild {guild_id}] Match {match_id} already posted, skipping")
                        continue  # already posted

                    # Step 5: Get the Discord channel to post to
                    logger.debug(f"[Guild {guild_id}] New match detected! Fetching Discord channel {channel_id}...")
                    channel = self.get_channel(int(channel_id))
                    if channel is None:
                        logger.error(f"[Guild {guild_id}] Could not find channel {channel_id} - bot may not have access")
                        continue

                    # Step 6: Build the match embed and post it
                    logger.debug(f"[Guild {guild_id}] Building match embed...")
                    embed = build_match_embed(
                        club_id,
                        used_platform,
                        match,
                        mt,
                        club_name_hint=club_name,
                    )
                    
                    logger.info(f"[Guild {guild_id}] Posting new match {match_id} to channel {channel.name} ({channel_id})")
                    await channel.send(embed=embed)
                    
                    # Step 7: Update database with new match ID
                    set_last_match_id(guild_id, str(match_id))
                    logger.info(f"✅ [Guild {guild_id}] Successfully posted match {match_id} and updated database")
                    
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
                            for pid, pdata in club_players.items():
                                if isinstance(pdata, dict) and pdata.get("playername", "").lower() == player_name.lower():
                                    match_goals = int(pdata.get("goals", 0) or 0)
                                    match_assists = int(pdata.get("assists", 0) or 0)
                                    update_player_match_history(
                                        guild_id, player_name, str(match_id),
                                        match_goals, match_assists, clean_sheet
                                    )
                                    break
                    except Exception as milestone_error:
                        logger.error(f"[Guild {guild_id}] Error checking milestones/achievements: {milestone_error}", exc_info=True)
                    
                except Exception as e:  # noqa: BLE001
                    logger.error(f"❌ [Guild {guild_id}] Error polling guild: {e}", exc_info=True)


client = ProClubsBot()

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
                
                # Check milestones and achievements for all players
                for member in members:
                    player_name = member.get("name", "Unknown")
                    
                    # Check milestones
                    new_milestones = check_milestones(interaction.guild_id, player_name, member)
                    if new_milestones:
                        await announce_milestones(client, interaction.guild_id, player_name, new_milestones)
                    
                    # Check achievements (without match data, only stat-based achievements will be checked)
                    new_achievements = check_achievements(interaction.guild_id, player_name, member)
                    if new_achievements:
                        await announce_achievements(client, interaction.guild_id, player_name, new_achievements)

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

            # Build player stats embed
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

            embed = discord.Embed(
                title=f"⚽ {name}",
                description=f"**{club_name}** | Position: {position}",
                color=discord.Color.green(),
            )

            # Just show matches played and win rate
            embed.add_field(name="🎮 Matches", value=str(matches_played), inline=True)
            embed.add_field(name="📈 Win %", value=f"{win_rate}%", inline=True)
            embed.add_field(name="⭐ Avg Rating", value=f"{rating:.1f}" if rating else "N/A", inline=True)
            
            embed.add_field(name="⚽ Goals", value=str(goals), inline=True)
            embed.add_field(name="🅰️ Assists", value=str(assists), inline=True)
            embed.add_field(name="⭐ MOTM", value=str(motm), inline=True)
            
            embed.add_field(name="📊 Goals/Game", value=f"{goals_per_game:.2f}", inline=True)
            embed.add_field(name="📊 Assists/Game", value=f"{assists_per_game:.2f}", inline=True)
            embed.add_field(name="🎯 Pass Accuracy", value=f"{pass_success_rate}%", inline=True)
            
            embed.add_field(name="🥅 Shot Accuracy", value=f"{shot_success_rate}%", inline=True)
            embed.add_field(name="🛡️ Tackles", value=f"{tackles_made}", inline=True)
            embed.add_field(name="🛡️ Tackle Success", value=f"{tackle_success_rate}%", inline=True)
            
            if clean_sheets_def > 0 or clean_sheets_gk > 0:
                clean_sheets = clean_sheets_gk if clean_sheets_gk > 0 else clean_sheets_def
                embed.add_field(name="🧤 Clean Sheets", value=str(clean_sheets), inline=True)
            
            if red_cards > 0:
                embed.add_field(name="🟥 Red Cards", value=str(red_cards), inline=True)

            embed.set_footer(text=f"Platform: {used_platform}")

            await interaction.followup.send(embed=embed)
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error fetching player stats: {e}", exc_info=True)
        await interaction.followup.send(
            f"Could not fetch player stats right now. Error: {e}", ephemeral=True
        )


@client.tree.command(name="lastmatches", description="Show the last 5 matches played by the club")
async def lastmatches(interaction: discord.Interaction):
    """Display recent match history."""
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
            
            # Fetch club name
            info, used_platform = await fetch_club_info(session, platform, club_id)
            if isinstance(info, dict):
                club_info = info.get(str(club_id), {})
            else:
                club_info = {}
            club_name = club_info.get("name", "Unknown Club")
            
            # Fetch last 5 matches
            matches = await fetch_all_matches(session, used_platform, club_id, max_count=5)
            
            if not matches:
                await interaction.followup.send("No recent matches found.", ephemeral=True)
                return
            
            embed = discord.Embed(
                title=f"📋 Recent Matches - {club_name}",
                description=f"Last {len(matches)} league matches",
                color=discord.Color.blue(),
            )
            
            for i, match in enumerate(matches, 1):
                # Get our club's data
                clubs = match.get("clubs", {})
                our_club = clubs.get(str(club_id), {})
                
                # Find opponent
                opponent_id = [cid for cid in clubs.keys() if str(cid) != str(club_id)]
                opponent_club = clubs.get(opponent_id[0], {}) if opponent_id else {}
                opponent_name = opponent_club.get("details", {}).get("name", "Unknown")
                
                # Scores
                our_score = our_club.get("score", "?")
                opp_score = opponent_club.get("score", "?")
                
                # Result
                result = our_club.get("result", "")
                if result == "1":
                    result_emoji = "✅ Win"
                    result_color = "🟢"
                elif result == "2":
                    result_emoji = "❌ Loss"
                    result_color = "🔴"
                elif result == "3":
                    result_emoji = "🤝 Draw"
                    result_color = "🟡"
                else:
                    result_emoji = "❓"
                    result_color = "⚪"
                
                # Timestamp
                timestamp = match.get("timestamp", 0)
                time_ago = match.get("timeAgo", {})
                time_str = f"{time_ago.get('number', '?')} {time_ago.get('unit', 'ago')}" if time_ago else "?"
                
                # Find highest rated player (MOTM or highest rating)
                # Players are at top level: match.players[clubId][playerId]
                all_players = match.get("players", {})
                club_players = all_players.get(str(club_id), {})
                best_player = None
                best_rating = 0.0
                
                for player_id, player_data in club_players.items():
                    if isinstance(player_data, dict):
                        rating = float(player_data.get("rating", 0) or 0)
                        # Check if MOTM
                        if int(player_data.get("mom", 0) or 0) == 1:
                            best_player = player_data.get("playername", "Unknown")
                            best_rating = rating
                            break  # MOTM is always best
                        elif rating > best_rating:
                            best_rating = rating
                            best_player = player_data.get("playername", "Unknown")
                
                match_info = f"{result_emoji}: **{our_score}-{opp_score}** vs {opponent_name}"
                if best_player:
                    match_info += f"\n⭐ **{best_player}** ({best_rating:.1f} rating)"
                
                embed.add_field(
                    name=f"{result_color} Match {i} - {time_str} ago",
                    value=match_info,
                    inline=False
                )
            
            embed.set_footer(text=f"Platform: {used_platform}")
            await interaction.followup.send(embed=embed)
            
    except Exception as e:
        logger.error(f"Error fetching matches: {e}", exc_info=True)
        await interaction.followup.send(
            f"Could not fetch matches right now. Error: {e}", ephemeral=True
        )


@client.tree.command(name="leaderboard", description="Show club leaderboard for various stats")
@app_commands.describe(category="The stat category to rank by")
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
    ],
)
async def leaderboard(interaction: discord.Interaction, category: app_commands.Choice[str]):
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
            
            if not members:
                await interaction.followup.send("No player data available.", ephemeral=True)
                return

            # Calculate derived stats for each player using correct EA API field names
            for m in members:
                matches = int(m.get("gamesPlayed", 0))
                m["_matches"] = matches
                
                # Core stats
                goals = int(m.get("goals", 0))
                assists = int(m.get("assists", 0))
                m["_goals"] = goals
                m["_assists"] = assists
                m["_goals_per_game"] = goals / matches if matches else 0
                m["_assists_per_game"] = assists / matches if matches else 0
                
                # Pass accuracy (already a percentage)
                m["_pass_accuracy"] = int(m.get("passSuccessRate", 0))
                
                # Other stats
                m["_motm"] = int(m.get("manOfTheMatch", 0))
                m["_rating"] = float(m.get("ratingAve", 0))

            # Sort based on category
            cat_value = category.value
            if cat_value == "goals":
                sorted_members = sorted(members, key=lambda m: m["_goals"], reverse=True)
                title = "⚽ Goals Leaderboard"
                format_fn = lambda m: f"{m['_goals']} goals"
            elif cat_value == "assists":
                sorted_members = sorted(members, key=lambda m: m["_assists"], reverse=True)
                title = "🅰️ Assists Leaderboard"
                format_fn = lambda m: f"{m['_assists']} assists"
            elif cat_value == "matches":
                sorted_members = sorted(members, key=lambda m: m["_matches"], reverse=True)
                title = "🎮 Matches Played Leaderboard"
                format_fn = lambda m: f"{m['_matches']} matches"
            elif cat_value == "motm":
                sorted_members = sorted(members, key=lambda m: m["_motm"], reverse=True)
                title = "⭐ Man of the Match Leaderboard"
                format_fn = lambda m: f"{m['_motm']} MOTM"
            elif cat_value == "rating":
                sorted_members = sorted(members, key=lambda m: m["_rating"], reverse=True)
                title = "📊 Average Rating Leaderboard"
                format_fn = lambda m: f"{m['_rating']:.1f} rating"
            elif cat_value == "pass_accuracy":
                sorted_members = sorted(members, key=lambda m: m["_pass_accuracy"], reverse=True)
                title = "🎯 Pass Accuracy Leaderboard"
                format_fn = lambda m: f"{m['_pass_accuracy']}% accuracy"
            elif cat_value == "goals_per_game":
                sorted_members = sorted(members, key=lambda m: m["_goals_per_game"], reverse=True)
                title = "📈 Goals Per Game Leaderboard"
                format_fn = lambda m: f"{m['_goals_per_game']:.2f} goals/game"
            elif cat_value == "assists_per_game":
                sorted_members = sorted(members, key=lambda m: m["_assists_per_game"], reverse=True)
                title = "📈 Assists Per Game Leaderboard"
                format_fn = lambda m: f"{m['_assists_per_game']:.2f} assists/game"
            else:
                sorted_members = members
                title = "Leaderboard"
                format_fn = lambda m: ""

            # Build leaderboard embed
            embed = discord.Embed(
                title=f"{title}",
                description=f"**{club_name}**",
                color=discord.Color.gold(),
            )

            # Show top 10
            medals = ["🥇", "🥈", "🥉"]
            for i, m in enumerate(sorted_members[:10]):
                rank = medals[i] if i < 3 else f"{i + 1}."
                name = m.get("name", "Unknown")
                stat_text = format_fn(m)
                embed.add_field(
                    name=f"{rank} {name}",
                    value=stat_text,
                    inline=False
                )

            embed.set_footer(text=f"Platform: {used_platform} | Showing top {min(10, len(sorted_members))} of {len(sorted_members)} players")

            await interaction.followup.send(embed=embed)
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
    """Display all available achievements that can be earned."""
    await interaction.response.defer(thinking=True)
    logger.info(f"[Command: listachievements] User {interaction.user} requesting achievement list")
    
    try:
        from achievements import get_all_achievements_list
        
        categorized = get_all_achievements_list()
        
        embed = discord.Embed(
            title="🏆 All Available Achievements",
            description=f"Earn these special achievements through exceptional performance!",
            color=discord.Color.gold(),
        )
        
        # Add fields for each category
        for category, achievements_list in categorized.items():
            ach_text = "\n".join([
                f"{ach['emoji']} **{ach['name']}** - {ach['description']}"
                for ach in achievements_list
            ])
            embed.add_field(name=category, value=ach_text, inline=False)
        
        total_count = sum(len(achievements_list) for achievements_list in categorized.values())
        embed.set_footer(text=f"Total: {total_count} achievements available")
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        logger.error(f"Error listing achievements: {e}", exc_info=True)
        await interaction.followup.send(
            f"Could not list achievements right now. Error: {e}", ephemeral=True
        )


# ---------- run ----------
if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set DISCORD_TOKEN in your .env")
    init_db()
    logger.info("Database initialized")
    logger.info("Starting bot...")
    client.run(TOKEN)




