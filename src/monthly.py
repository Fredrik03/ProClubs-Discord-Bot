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
    """
    try:
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


def calculate_player_of_month(guild_id: int, month_period: str) -> dict | None:
    """
    Calculate the Player of the Month.
    Returns the best player's stats or None if no players found.
    """
    try:
        stats = get_monthly_stats(guild_id, month_period)
        if not stats:
            logger.debug(f"[Monthly] No monthly stats found for guild {guild_id}, period {month_period}")
            return None

        best_player = stats[0]
        logger.info(f"[Monthly] Player of the Month: {best_player['player_name']} with score {best_player['monthly_score']:.1f}")
        return best_player
    except Exception as e:
        logger.error(f"[Monthly] Failed to calculate Player of the Month: {e}", exc_info=True)
        return None


async def announce_player_of_month(client, guild_id: int, month_period: str):
    """
    Announce the Player of the Month to the configured channel.
    Only announces if not already announced for this period.
    """
    try:
        if has_monthly_been_announced(guild_id, month_period):
            logger.debug(f"[Monthly] POTM already announced for guild {guild_id}, period {month_period}")
            return

        settings = get_settings(guild_id)
        if not settings or not settings.get("monthly_channel_id"):
            logger.warning(f"[Monthly] No monthly channel configured for guild {guild_id}")
            return

        best_player = calculate_player_of_month(guild_id, month_period)
        if not best_player:
            logger.warning(f"[Monthly] No monthly stats found for guild {guild_id}, period {month_period}")
            return

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

        # Top 3 performers
        all_stats = get_monthly_stats(guild_id, month_period)
        if len(all_stats) > 1:
            top_3_text = ""
            medals = ["🥇", "🥈", "🥉"]
            for i, player in enumerate(all_stats[:3]):
                medal = medals[i] if i < 3 else f"{i+1}."
                top_3_text += f"{medal} **{player['player_name']}** ({player['monthly_score']:.1f})\n"

            embed.add_field(
                name="🏅 Top Performers",
                value=top_3_text,
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
    """
    current_month = detect_month_period()
    last_month = _last_known_month.get(guild_id)

    if last_month is None:
        # First time seeing this guild - just record the month
        _last_known_month[guild_id] = current_month
        return

    if current_month != last_month:
        # Month has changed! Announce POTM for the previous month
        logger.info(f"[Monthly] Month rollover detected for guild {guild_id}: {last_month} -> {current_month}")
        _last_known_month[guild_id] = current_month

        prev_month = previous_month_period()
        stats = get_monthly_stats(guild_id, prev_month)
        if stats:
            logger.info(f"[Monthly] Found {len(stats)} players with stats for {prev_month}, announcing POTM")
            await announce_player_of_month(client, guild_id, prev_month)
        else:
            logger.debug(f"[Monthly] No stats found for previous month {prev_month}, skipping POTM announcement")
