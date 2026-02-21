"""
Player of the Month tracking and announcement logic.

This module handles:
1. Tracking player performance across all league matches each calendar month
2. Detecting month rollovers during the polling loop
3. Calculating Player of the Month using the scoring algorithm
4. Announcing the results to the configured monthly channel

Scoring Algorithm (same as playoffs):
Score = (Goals x 10) + (Assists x 7) + (Avg Rating x 5) + (Matches Played x 2)
"""
import logging
import discord
from datetime import datetime, timezone
from database import (
    get_monthly_stats, has_monthly_been_announced, mark_monthly_announced,
    get_settings, update_monthly_stats
)

logger = logging.getLogger('ProClubsBot.Monthly')

# Track the last known month per guild to detect rollovers
_last_known_month = {}

# Minimum matches required to be eligible for Player of the Month
MIN_MATCHES_FOR_POTM = 3


def detect_month_period() -> str:
    """Return the current month period in YYYY-MM format."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m")


def previous_month_period() -> str:
    """Return the previous month period in YYYY-MM format."""
    now = datetime.now(timezone.utc)
    if now.month == 1:
        return f"{now.year - 1}-12"
    return f"{now.year}-{now.month - 1:02d}"


def process_league_match_monthly(guild_id: int, match_data: dict, club_id: int):
    """
    Update monthly stats for all players after a league match.
    Called from the polling loop after a new league match is detected.
    Uses the match's own timestamp to assign stats to the correct month,
    so matches played in a previous month are attributed correctly even if
    the bot was offline at the time.
    """
    try:
        # Use the match's own timestamp to determine the correct month
        timestamp = match_data.get("timestamp")
        if timestamp:
            match_dt = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
            month_period = match_dt.strftime("%Y-%m")
        else:
            month_period = detect_month_period()

        players = match_data.get("players", {})
        club_players = players.get(str(club_id), {})

        for player_id, player_data in club_players.items():
            if isinstance(player_data, dict):
                player_name = player_data.get("playername", "Unknown")
                goals = int(player_data.get("goals", 0) or 0)
                assists = int(player_data.get("assists", 0) or 0)
                rating = float(player_data.get("rating", 0) or 0)

                update_monthly_stats(guild_id, player_name, month_period, goals, assists, rating)

        logger.info(f"[Monthly] Updated monthly stats for guild {guild_id}, period {month_period}")
    except Exception as e:
        logger.error(f"[Monthly] Failed to process league match for monthly stats: {e}", exc_info=True)


async def announce_player_of_month(client, guild_id: int, month_period: str):
    """
    Announce the Player of the Month to the configured channel.
    Only announces if not already announced for this period.
    Requires at least MIN_MATCHES_FOR_POTM matches to be eligible.
    """
    try:
        if has_monthly_been_announced(guild_id, month_period):
            logger.debug(f"[Monthly] POTM already announced for guild {guild_id}, period {month_period}")
            return

        settings = get_settings(guild_id)
        if not settings or not settings.get("monthly_channel_id"):
            logger.warning(f"[Monthly] No monthly channel configured for guild {guild_id}")
            return

        all_stats = get_monthly_stats(guild_id, month_period)
        if not all_stats:
            logger.warning(f"[Monthly] No monthly stats found for guild {guild_id}, period {month_period}")
            return

        # Filter for players with enough matches to be eligible
        eligible = [p for p in all_stats if p["matches_played"] >= MIN_MATCHES_FOR_POTM]
        if not eligible:
            logger.info(f"[Monthly] No players with {MIN_MATCHES_FOR_POTM}+ matches for {month_period}, using all players as fallback")
            eligible = all_stats

        best_player = eligible[0]

        channel = client.get_channel(settings["monthly_channel_id"])
        if not channel:
            logger.error(f"[Monthly] Could not find monthly channel {settings['monthly_channel_id']}")
            return

        embed = discord.Embed(
            title="🏅 Player of the Month",
            description=f"**{month_period}** Monthly Summary",
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc)
        )

        embed.add_field(
            name="🥇 Player of the Month",
            value=f"**{best_player['player_name']}**",
            inline=False
        )

        embed.add_field(
            name="Individual Performance",
            value=f"⚽ **{best_player['goals']}** goals • 🅰️ **{best_player['assists']}** assists • ⭐ **{best_player['avg_rating']:.1f}** avg rating",
            inline=False
        )

        embed.add_field(
            name="🎮 Matches",
            value=f"**{best_player['matches_played']}** matches played",
            inline=True
        )

        embed.add_field(
            name="⭐ Monthly Score",
            value=f"**{best_player['monthly_score']:.1f}**",
            inline=True
        )

        # Runners-up: 2nd and 3rd place only (winner already shown above)
        runners_up = eligible[1:3]
        if runners_up:
            medals = ["🥈", "🥉"]
            runners_up_text = ""
            for i, player in enumerate(runners_up):
                runners_up_text += f"{medals[i]} **{player['player_name']}** ({player['monthly_score']:.1f})\n"

            embed.add_field(
                name="🏅 Runners-up",
                value=runners_up_text,
                inline=False
            )

        embed.set_footer(text="Score = Goals×10 + Assists×7 + Avg Rating×5 + Matches×2")

        await channel.send(embed=embed)
        mark_monthly_announced(guild_id, month_period)
        logger.info(f"[Monthly] ✅ Player of the Month announced for guild {guild_id}, period {month_period}")

    except Exception as e:
        logger.error(f"[Monthly] Failed to announce Player of the Month: {e}", exc_info=True)


async def check_month_rollover(client, guild_id: int):
    """
    Check if the month has changed since last poll cycle.
    If so, announce POTM for the previous month (if any matches were played).
    Called each poll cycle from bot_new.py.

    On the first poll cycle after a bot restart, also checks whether the
    previous month has unannounced POTM stats (which could have been missed
    if the bot was down during the month rollover).
    """
    current_month = detect_month_period()
    last_month = _last_known_month.get(guild_id)

    if last_month is None:
        # First poll cycle after bot start - record the current month
        _last_known_month[guild_id] = current_month

        # Check if the previous month had stats that were never announced
        # (e.g., bot was down when the month rolled over)
        prev_month = previous_month_period()
        if not has_monthly_been_announced(guild_id, prev_month):
            stats = get_monthly_stats(guild_id, prev_month)
            if stats:
                logger.info(f"[Monthly] Found unannounced POTM for {prev_month} after bot start, announcing now")
                await announce_player_of_month(client, guild_id, prev_month)
        return

    if current_month != last_month:
        # Month has changed! Announce POTM for the previous month
        logger.info(f"[Monthly] Month rollover detected for guild {guild_id}: {last_month} -> {current_month}")
        _last_known_month[guild_id] = current_month

        prev_month = last_month
        stats = get_monthly_stats(guild_id, prev_month)
        if stats:
            logger.info(f"[Monthly] Found {len(stats)} players with stats for {prev_month}, announcing POTM")
            await announce_player_of_month(client, guild_id, prev_month)
        else:
            logger.debug(f"[Monthly] No stats found for previous month {prev_month}, skipping POTM announcement")
