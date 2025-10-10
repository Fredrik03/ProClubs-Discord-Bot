"""
Achievement tracking and announcement logic.

This module handles fun achievements that players can earn through exceptional
performance or specific accomplishments (separate from milestones).

ACHIEVEMENT CATEGORIES:
-----------------------
1. Match Performance: Hat tricks, assists, perfect ratings, etc.
2. Statistical Excellence: Long-term statistical achievements
3. Streak & Consistency: Consecutive match achievements
4. Team Performance: Team-based achievements

Total: 14 unique achievements available.
Each achievement is only announced once per player.
"""
import logging
import discord
from datetime import datetime, timezone
from database import (
    has_achievement_been_earned, record_achievement, get_settings,
    get_player_achievement_history, get_player_match_history,
    update_player_match_history
)

logger = logging.getLogger('ProClubsBot.Achievements')

# Achievement definitions
ACHIEVEMENTS = {
    # Match Performance
    "hat_trick_hero": {
        "name": "Hat Trick Hero",
        "emoji": "⚽",
        "description": "Score 3+ goals in a single match",
        "category": "Match Performance"
    },
    "assist_king": {
        "name": "Assist King",
        "emoji": "🎯",
        "description": "Get 3+ assists in a single match",
        "category": "Match Performance"
    },
    "perfect_10": {
        "name": "Perfect 10",
        "emoji": "💯",
        "description": "Achieve a 10.0 match rating",
        "category": "Match Performance"
    },
    "man_of_match": {
        "name": "Man of the Match",
        "emoji": "⭐",
        "description": "Earn your first MOTM award",
        "category": "Match Performance"
    },
    
    # Statistical Excellence
    "sharpshooter": {
        "name": "Sharpshooter",
        "emoji": "🔫",
        "description": "Maintain 70%+ shot accuracy (min 50 matches)",
        "category": "Statistical Excellence"
    },
    "playmaker": {
        "name": "Playmaker",
        "emoji": "🎭",
        "description": "More assists than goals (min 50 of each)",
        "category": "Statistical Excellence"
    },
    "goal_machine": {
        "name": "Goal Machine",
        "emoji": "🤖",
        "description": "Average 2+ goals per game (min 25 matches)",
        "category": "Statistical Excellence"
    },
    "midfield_maestro": {
        "name": "Midfield Maestro",
        "emoji": "🎼",
        "description": "90%+ pass accuracy (min 100 matches)",
        "category": "Statistical Excellence"
    },
    "the_wall": {
        "name": "The Wall",
        "emoji": "🧱",
        "description": "80%+ tackle success rate (min 500 tackles)",
        "category": "Statistical Excellence"
    },
    
    # Streak & Consistency
    "on_fire": {
        "name": "On Fire",
        "emoji": "🔥",
        "description": "Score in 5 consecutive matches",
        "category": "Streak & Consistency"
    },
    "mr_reliable": {
        "name": "Mr. Reliable",
        "emoji": "💪",
        "description": "Play 20 consecutive matches",
        "category": "Streak & Consistency"
    },
    "clean_sheet_specialist": {
        "name": "Clean Sheet Specialist",
        "emoji": "🧤",
        "description": "5 clean sheets in a row (GK/Defender)",
        "category": "Streak & Consistency"
    },
    
    # Team Performance
    "demolition": {
        "name": "Demolition",
        "emoji": "💥",
        "description": "Win a match 10-0 or better",
        "category": "Team Performance"
    },
    "giant_killer": {
        "name": "Giant Killer",
        "emoji": "⚔️",
        "description": "Beat a team with 500+ skill rating difference",
        "category": "Team Performance"
    },
}


def check_achievements(guild_id: int, player_name: str, stats: dict, match_data: dict = None) -> list[dict]:
    """
    Check if player has earned any new achievements.
    
    Args:
        guild_id: Discord guild ID
        player_name: Player name
        stats: Overall player stats from EA API
        match_data: Latest match data (optional, for match-specific achievements)
    
    Returns:
        List of achievement dicts with keys: id, name, emoji, description
    """
    achievements = []
    
    # Extract stats
    matches_played = int(stats.get("gamesPlayed", 0) or 0)
    goals = int(stats.get("goals", 0) or 0)
    assists = int(stats.get("assists", 0) or 0)
    motm = int(stats.get("manOfTheMatch", 0) or 0)
    shot_accuracy = int(stats.get("shotSuccessRate", 0) or 0)
    pass_accuracy = int(stats.get("passSuccessRate", 0) or 0)
    tackle_success = int(stats.get("tackleSuccessRate", 0) or 0)
    tackles_made = int(stats.get("tacklesMade", 0) or 0)
    clean_sheets_gk = int(stats.get("cleanSheetsGK", 0) or 0)
    clean_sheets_def = int(stats.get("cleanSheetsDef", 0) or 0)
    
    # Man of the Match (first one)
    if motm >= 1 and not has_achievement_been_earned(guild_id, player_name, "man_of_match"):
        achievements.append({
            "id": "man_of_match",
            **ACHIEVEMENTS["man_of_match"]
        })
    
    # Statistical Excellence Achievements
    if matches_played >= 50 and shot_accuracy >= 70:
        if not has_achievement_been_earned(guild_id, player_name, "sharpshooter"):
            achievements.append({
                "id": "sharpshooter",
                **ACHIEVEMENTS["sharpshooter"]
            })
    
    if assists >= 50 and goals >= 50 and assists > goals:
        if not has_achievement_been_earned(guild_id, player_name, "playmaker"):
            achievements.append({
                "id": "playmaker",
                **ACHIEVEMENTS["playmaker"]
            })
    
    goals_per_game = goals / matches_played if matches_played > 0 else 0
    if matches_played >= 25 and goals_per_game >= 2.0:
        if not has_achievement_been_earned(guild_id, player_name, "goal_machine"):
            achievements.append({
                "id": "goal_machine",
                **ACHIEVEMENTS["goal_machine"]
            })
    
    if matches_played >= 100 and pass_accuracy >= 90:
        if not has_achievement_been_earned(guild_id, player_name, "midfield_maestro"):
            achievements.append({
                "id": "midfield_maestro",
                **ACHIEVEMENTS["midfield_maestro"]
            })
    
    if tackles_made >= 500 and tackle_success >= 80:
        if not has_achievement_been_earned(guild_id, player_name, "the_wall"):
            achievements.append({
                "id": "the_wall",
                **ACHIEVEMENTS["the_wall"]
            })
    
    # Match-specific achievements (if match data provided)
    if match_data:
        achievements.extend(check_match_achievements(guild_id, player_name, match_data))
    
    # Streak achievements (requires match history)
    achievements.extend(check_streak_achievements(guild_id, player_name, stats))
    
    return achievements


def check_match_achievements(guild_id: int, player_name: str, match_data: dict) -> list[dict]:
    """Check achievements based on a single match performance."""
    achievements = []
    
    # Extract player's match stats
    player_match_stats = None
    club_id = None
    
    # Find the player in the match data
    players = match_data.get("players", {})
    for cid, club_players in players.items():
        for pid, pdata in club_players.items():
            if isinstance(pdata, dict) and pdata.get("playername", "").lower() == player_name.lower():
                player_match_stats = pdata
                club_id = cid
                break
        if player_match_stats:
            break
    
    if not player_match_stats:
        return achievements
    
    # Extract match stats
    match_goals = int(player_match_stats.get("goals", 0) or 0)
    match_assists = int(player_match_stats.get("assists", 0) or 0)
    match_rating = float(player_match_stats.get("rating", 0) or 0)
    is_motm = int(player_match_stats.get("mom", 0) or 0) == 1
    
    # Hat Trick Hero
    if match_goals >= 3 and not has_achievement_been_earned(guild_id, player_name, "hat_trick_hero"):
        achievements.append({
            "id": "hat_trick_hero",
            **ACHIEVEMENTS["hat_trick_hero"]
        })
    
    # Assist King
    if match_assists >= 3 and not has_achievement_been_earned(guild_id, player_name, "assist_king"):
        achievements.append({
            "id": "assist_king",
            **ACHIEVEMENTS["assist_king"]
        })
    
    # Perfect 10
    if match_rating >= 10.0 and not has_achievement_been_earned(guild_id, player_name, "perfect_10"):
        achievements.append({
            "id": "perfect_10",
            **ACHIEVEMENTS["perfect_10"]
        })
    
    # Team Performance Achievements
    if club_id:
        clubs = match_data.get("clubs", {})
        our_club = clubs.get(str(club_id), {})
        
        # Find opponent
        opponent_id = [cid for cid in clubs.keys() if str(cid) != str(club_id)]
        opponent_club = clubs.get(opponent_id[0], {}) if opponent_id else {}
        
        our_score = int(our_club.get("score", 0) or 0)
        opp_score = int(opponent_club.get("score", 0) or 0)
        result = our_club.get("result", "")
        
        # Demolition - Win 10-0 or better
        if result == "1" and our_score >= 10 and opp_score == 0:
            if not has_achievement_been_earned(guild_id, player_name, "demolition"):
                achievements.append({
                    "id": "demolition",
                    **ACHIEVEMENTS["demolition"]
                })
        
        # Giant Killer - Beat team with 500+ skill rating difference
        our_rating = int(our_club.get("skillrating", 0) or 0)
        opp_rating = int(opponent_club.get("skillrating", 0) or 0)
        rating_diff = opp_rating - our_rating
        
        if result == "1" and rating_diff >= 500:
            if not has_achievement_been_earned(guild_id, player_name, "giant_killer"):
                achievements.append({
                    "id": "giant_killer",
                    **ACHIEVEMENTS["giant_killer"]
                })
    
    return achievements


def check_streak_achievements(guild_id: int, player_name: str, stats: dict) -> list[dict]:
    """Check streak-based achievements using match history."""
    achievements = []
    
    # Get player's match history from database
    match_history = get_player_match_history(guild_id, player_name)
    
    if not match_history:
        return achievements
    
    # Check for "On Fire" - 5 consecutive matches with a goal
    if len(match_history) >= 5:
        recent_5 = match_history[-5:]
        if all(m.get("goals", 0) > 0 for m in recent_5):
            if not has_achievement_been_earned(guild_id, player_name, "on_fire"):
                achievements.append({
                    "id": "on_fire",
                    **ACHIEVEMENTS["on_fire"]
                })
    
    # Check for "Mr. Reliable" - 20 consecutive matches
    if len(match_history) >= 20:
        if not has_achievement_been_earned(guild_id, player_name, "mr_reliable"):
            achievements.append({
                "id": "mr_reliable",
                **ACHIEVEMENTS["mr_reliable"]
            })
    
    # Check for "Clean Sheet Specialist" - 5 clean sheets in a row
    if len(match_history) >= 5:
        recent_5 = match_history[-5:]
        if all(m.get("clean_sheet", False) for m in recent_5):
            if not has_achievement_been_earned(guild_id, player_name, "clean_sheet_specialist"):
                achievements.append({
                    "id": "clean_sheet_specialist",
                    **ACHIEVEMENTS["clean_sheet_specialist"]
                })
    
    return achievements


async def announce_achievements(client, guild_id: int, player_name: str, achievements_list: list[dict]):
    """Post achievement announcements to the configured channel."""
    if not achievements_list:
        return
    
    settings = get_settings(guild_id)
    if not settings or not settings.get("achievement_channel_id"):
        logger.debug(f"No achievement channel configured for guild {guild_id}")
        return
    
    try:
        channel = client.get_channel(settings["achievement_channel_id"])
        if not channel:
            logger.warning(f"Achievement channel {settings['achievement_channel_id']} not found")
            return
        
        for achievement in achievements_list:
            # Create celebratory embed
            embed = discord.Embed(
                title=f"🏆 Achievement Unlocked! 🏆",
                description=f"**{player_name}** earned: **{achievement['emoji']} {achievement['name']}**",
                color=discord.Color.gold(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(name="Description", value=achievement['description'], inline=False)
            embed.add_field(name="Category", value=achievement['category'], inline=True)
            embed.set_footer(text="Keep pushing for more achievements!")
            
            await channel.send(embed=embed)
            
            # Record that we announced this achievement
            record_achievement(guild_id, player_name, achievement["id"])
            logger.info(f"Announced achievement: {player_name} - {achievement['name']}")
    
    except Exception as e:
        logger.error(f"Failed to announce achievement: {e}", exc_info=True)


def get_all_achievements_list() -> list[dict]:
    """Get all available achievements organized by category."""
    categorized = {}
    for ach_id, ach_data in ACHIEVEMENTS.items():
        category = ach_data["category"]
        if category not in categorized:
            categorized[category] = []
        categorized[category].append({
            "id": ach_id,
            **ach_data
        })
    
    return categorized

