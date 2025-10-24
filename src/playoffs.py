"""
Playoff tracking and Player of the Playoffs announcement logic.

This module handles:
1. Detecting playoff periods (monthly tracking)
2. Checking if 15 playoff matches have been completed
3. Calculating Player of the Playoffs using the scoring algorithm
4. Announcing the results to the configured channel
"""
import logging
import discord
from datetime import datetime, timezone
from database import (
    get_playoff_stats, has_playoff_been_announced, mark_playoff_announced,
    count_playoff_matches, get_settings, record_playoff_match, get_playoff_club_stats,
    update_playoff_stats
)

logger = logging.getLogger('ProClubsBot.Playoffs')


def detect_playoff_period() -> str:
    """
    Determine the current playoff period (month) in YYYY-MM format.
    Playoffs typically occur at the end of each month.
    """
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m")


def check_playoff_completion(guild_id: int, playoff_period: str) -> bool:
    """
    Check if 15 playoff matches have been played in the current period.
    Returns True if playoffs are complete and ready for announcement.
    """
    try:
        total_matches = count_playoff_matches(guild_id, playoff_period)
        logger.debug(f"[Playoffs] Guild {guild_id} playoff period {playoff_period}: {total_matches}/15 matches played")
        return total_matches >= 15
    except Exception as e:
        logger.error(f"[Playoffs] Failed to check playoff completion: {e}", exc_info=True)
        return False


def calculate_player_of_playoffs(guild_id: int, playoff_period: str) -> dict | None:
    """
    Calculate the Player of the Playoffs using the scoring algorithm.
    Returns the best player's stats or None if no players found.
    
    Scoring Algorithm:
    Playoff Score = (Goals × 10) + (Assists × 7) + (Average Rating × 5) + (Matches Played × 2)
    """
    try:
        stats = get_playoff_stats(guild_id, playoff_period)
        if not stats:
            logger.debug(f"[Playoffs] No playoff stats found for guild {guild_id}, period {playoff_period}")
            return None
        
        # Stats are already sorted by playoff_score DESC from the database
        best_player = stats[0]
        logger.info(f"[Playoffs] Player of the Playoffs: {best_player['player_name']} with score {best_player['playoff_score']:.1f}")
        return best_player
        
    except Exception as e:
        logger.error(f"[Playoffs] Failed to calculate Player of the Playoffs: {e}", exc_info=True)
        return None


async def announce_player_of_playoffs(client, guild_id: int, playoff_period: str):
    """
    Announce the Player of the Playoffs to the configured channel.
    Only announces if not already announced for this period.
    """
    try:
        # Check if already announced
        if has_playoff_been_announced(guild_id, playoff_period):
            logger.debug(f"[Playoffs] Playoff summary already announced for guild {guild_id}, period {playoff_period}")
            return
        
        # Get settings to find the playoff summary channel
        settings = get_settings(guild_id)
        if not settings or not settings.get("playoff_summary_channel_id"):
            logger.warning(f"[Playoffs] No playoff summary channel configured for guild {guild_id}")
            return
        
        # Get the best player
        best_player = calculate_player_of_playoffs(guild_id, playoff_period)
        if not best_player:
            logger.warning(f"[Playoffs] No playoff stats found for guild {guild_id}, period {playoff_period}")
            return
        
        # Get the channel
        channel = client.get_channel(settings["playoff_summary_channel_id"])
        if not channel:
            logger.error(f"[Playoffs] Could not find playoff summary channel {settings['playoff_summary_channel_id']}")
            return
        
        # Get club statistics
        club_stats = get_playoff_club_stats(guild_id, playoff_period)
        
        # Build the announcement embed
        embed = discord.Embed(
            title="🏆 Player of the Playoffs",
            description=f"**{playoff_period}** Playoff Summary",
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc)
        )
        
        # Add club performance stats if available
        if club_stats:
            club_performance = (
                f"**{club_stats['wins']}W - {club_stats['losses']}L - {club_stats['draws']}D** "
                f"({club_stats['win_rate']:.1f}% win rate)\n"
                f"⚽ Goals: **{club_stats['goals_for']} - {club_stats['goals_against']}** "
                f"(GD: **{club_stats['goal_difference']:+d}**)\n"
                f"🧤 **{club_stats['clean_sheets']}** clean sheets"
            )
            embed.add_field(
                name=f"📊 Club Performance ({club_stats['total_matches']} matches)",
                value=club_performance,
                inline=False
            )
        
        # Add separator
        embed.add_field(name="\u200b", value="\u200b", inline=False)
        
        # Add the winner
        embed.add_field(
            name="🥇 Player of the Playoffs",
            value=f"**{best_player['player_name']}**",
            inline=False
        )
        
        # Add detailed stats
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
            name="⭐ Playoff Score",
            value=f"**{best_player['playoff_score']:.1f}**",
            inline=True
        )
        
        # Add top 3 if there are multiple players
        all_stats = get_playoff_stats(guild_id, playoff_period)
        if len(all_stats) > 1:
            top_3_text = ""
            medals = ["🥇", "🥈", "🥉"]
            for i, player in enumerate(all_stats[:3]):
                medal = medals[i] if i < 3 else f"{i+1}."
                top_3_text += f"{medal} **{player['player_name']}** ({player['playoff_score']:.1f})\n"
            
            embed.add_field(
                name="🏅 Top Performers",
                value=top_3_text,
                inline=False
            )
        
        embed.set_footer(text="Playoff performance calculated using: Goals×10 + Assists×7 + Avg Rating×5 + Matches×2")
        
        # Post the announcement
        await channel.send(embed=embed)
        
        # Mark as announced
        mark_playoff_announced(guild_id, playoff_period)
        
        logger.info(f"[Playoffs] ✅ Player of the Playoffs announced for guild {guild_id}, period {playoff_period}")
        
    except Exception as e:
        logger.error(f"[Playoffs] Failed to announce Player of the Playoffs: {e}", exc_info=True)


def is_playoff_match(match_type: str) -> bool:
    """
    Determine if a match is a playoff match based on the match type.
    This may need to be updated based on how EA API identifies playoff matches.
    """
    # Common playoff match type identifiers (may need adjustment based on actual API response)
    playoff_indicators = ["playoff", "playoffMatch", "cup", "tournament"]
    
    if not match_type:
        return False
    
    match_type_lower = match_type.lower()
    return any(indicator in match_type_lower for indicator in playoff_indicators)


async def process_playoff_match(client, guild_id: int, match_data: dict, match_type: str, club_id: int):
    """
    Process a playoff match: update stats and check for completion.
    This should be called after a playoff match is detected and posted.
    """
    try:
        playoff_period = detect_playoff_period()
        
        # Extract club and player data from the match
        clubs = match_data.get("clubs", {})
        players = match_data.get("players", {})
        
        # Get our club's data
        our_club = clubs.get(str(club_id), {})
        if not our_club:
            logger.warning(f"[Playoffs] Could not find club {club_id} in playoff match")
            return
        
        # Extract match result and scores
        result_code = our_club.get("result", "")
        our_score = int(our_club.get("score", 0) or 0)
        
        # Find opponent's score
        opponent_ids = [cid for cid in clubs.keys() if str(cid) != str(club_id)]
        opponent_club = clubs.get(opponent_ids[0], {}) if opponent_ids else {}
        opp_score = int(opponent_club.get("score", 0) or 0)
        
        # Convert result code to W/L/D
        if result_code == "1":
            result = "W"
        elif result_code == "2":
            result = "L"
        elif result_code == "3":
            result = "D"
        else:
            result = "L"  # Default to loss for unknown results
        
        # Check for clean sheet
        clean_sheet = (opp_score == 0)
        
        # Get match ID
        match_id = match_data.get("matchId", str(match_data.get("timestamp", 0)))
        
        # Record club stats
        record_playoff_match(guild_id, playoff_period, str(match_id), result, our_score, opp_score, clean_sheet)
        logger.info(f"[Playoffs] Recorded playoff match: {result} {our_score}-{opp_score}")
        
        # Update stats for each player
        club_players = players.get(str(club_id), {})
        for player_id, player_data in club_players.items():
            if isinstance(player_data, dict):
                player_name = player_data.get("playername", "Unknown")
                goals = int(player_data.get("goals", 0) or 0)
                assists = int(player_data.get("assists", 0) or 0)
                rating = float(player_data.get("rating", 0) or 0)
                
                # Update playoff player stats
                update_playoff_stats(guild_id, player_name, playoff_period, goals, assists, rating)
        
        # Check if playoffs are complete
        if check_playoff_completion(guild_id, playoff_period):
            logger.info(f"[Playoffs] Playoffs complete for guild {guild_id}, period {playoff_period}")
            await announce_player_of_playoffs(client, guild_id, playoff_period)
        
    except Exception as e:
        logger.error(f"[Playoffs] Failed to process playoff match: {e}", exc_info=True)
