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
5. Career Milestones: Long-term career accomplishments

<<<<<<< HEAD
Total: 33 unique achievements available.
=======
Total: 19 unique achievements available.
>>>>>>> origin/main
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
    "brace": {
        "name": "Brace",
        "emoji": "✌️",
        "description": "Score exactly 2 goals in a single match",
        "category": "Match Performance"
    },
    "double_threat": {
        "name": "Double Threat",
        "emoji": "🔄",
        "description": "Score 1+ goal AND 1+ assist in the same match",
        "category": "Match Performance"
    },
    "poker": {
        "name": "Poker",
        "emoji": "🃏",
        "description": "Score 4+ goals in a single match",
        "category": "Match Performance"
    },
    "ghost": {
        "name": "Ghost",
        "emoji": "👻",
        "description": "0 goals, 0 assists, rating below 5.0",
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
    "century": {
        "name": "Century",
        "emoji": "💯",
        "description": "Score 100 career goals",
        "category": "Statistical Excellence"
    },
    "provider": {
        "name": "Provider",
        "emoji": "🤝",
        "description": "Provide 100 career assists",
        "category": "Statistical Excellence"
    },
    "veteran": {
        "name": "Veteran",
        "emoji": "🎖️",
        "description": "Play 200+ career matches",
        "category": "Statistical Excellence"
    },
    "red_mist": {
        "name": "Red Mist",
        "emoji": "🟥",
        "description": "Receive 10+ career red cards",
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
    "assist_streak": {
        "name": "Assist Streak",
        "emoji": "🅰️",
        "description": "Assist in 5 consecutive matches",
        "category": "Streak & Consistency"
    },
    "involvement": {
        "name": "Involvement",
        "emoji": "🌟",
        "description": "Goal or assist in 10 consecutive matches",
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
<<<<<<< HEAD
    "nil_nil": {
        "name": "Nil-Nil Nightmare",
        "emoji": "😴",
        "description": "0-0 draw",
        "category": "Team Performance"
    },
    "comeback_kings": {
        "name": "Comeback Kings",
        "emoji": "👑",
        "description": "Win a match where opponent also scored",
        "category": "Team Performance"
    },
    "fortress": {
        "name": "Fortress",
        "emoji": "🏰",
        "description": "Win 5 matches in a row",
        "category": "Streak & Consistency"
    },
    "draw_specialists": {
        "name": "Draw Specialists",
        "emoji": "🤝",
        "description": "Draw 3 matches in a row",
        "category": "Streak & Consistency"
    },
=======
>>>>>>> origin/main

    # Match Performance (additional)
    "double_trouble": {
        "name": "Double Trouble",
        "emoji": "💫",
        "description": "Score 2+ goals AND 2+ assists in a single match",
        "category": "Match Performance"
    },
    "sniper": {
        "name": "Sniper",
        "emoji": "🏹",
        "description": "Score 5+ goals in a single match",
        "category": "Match Performance"
    },
    "unsung_hero": {
        "name": "Unsung Hero",
        "emoji": "🦸",
        "description": "Win Man of the Match without scoring or assisting",
        "category": "Match Performance"
    },

    # Career Milestones
    "iron_man": {
        "name": "Iron Man",
        "emoji": "🦾",
        "description": "Play 50+ matches",
        "category": "Career Milestones"
    },
    "consistent_performer": {
        "name": "Consistent Performer",
        "emoji": "📈",
        "description": "Maintain 7.5+ average rating over 20+ matches",
        "category": "Career Milestones"
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
    rating = float(stats.get("ratingAve", 0) or 0)
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
    
<<<<<<< HEAD
    # Century - 100 career goals
    if goals >= 100:
        if not has_achievement_been_earned(guild_id, player_name, "century"):
            achievements.append({
                "id": "century",
                **ACHIEVEMENTS["century"]
            })

    # Provider - 100 career assists
    if assists >= 100:
        if not has_achievement_been_earned(guild_id, player_name, "provider"):
            achievements.append({
                "id": "provider",
                **ACHIEVEMENTS["provider"]
            })

    # Veteran - 200+ career matches
    if matches_played >= 200:
        if not has_achievement_been_earned(guild_id, player_name, "veteran"):
            achievements.append({
                "id": "veteran",
                **ACHIEVEMENTS["veteran"]
            })

    # Red Mist - 10+ career red cards
    red_cards = int(stats.get("redCards", 0) or 0)
    if red_cards >= 10:
        if not has_achievement_been_earned(guild_id, player_name, "red_mist"):
            achievements.append({
                "id": "red_mist",
                **ACHIEVEMENTS["red_mist"]
            })

=======
>>>>>>> origin/main
    # Career Milestone Achievements
    if matches_played >= 50 and not has_achievement_been_earned(guild_id, player_name, "iron_man"):
        achievements.append({
            "id": "iron_man",
            **ACHIEVEMENTS["iron_man"]
        })
    
    if matches_played >= 20 and rating >= 7.5:
        if not has_achievement_been_earned(guild_id, player_name, "consistent_performer"):
            achievements.append({
                "id": "consistent_performer",
                **ACHIEVEMENTS["consistent_performer"]
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
    
<<<<<<< HEAD
    # Brace - exactly 2 goals
    if match_goals == 2 and not has_achievement_been_earned(guild_id, player_name, "brace"):
        achievements.append({
            "id": "brace",
            **ACHIEVEMENTS["brace"]
        })

    # Double Threat - 1+ goal AND 1+ assist
    if match_goals >= 1 and match_assists >= 1:
        if not has_achievement_been_earned(guild_id, player_name, "double_threat"):
            achievements.append({
                "id": "double_threat",
                **ACHIEVEMENTS["double_threat"]
            })

    # Poker - 4+ goals
    if match_goals >= 4 and not has_achievement_been_earned(guild_id, player_name, "poker"):
        achievements.append({
            "id": "poker",
            **ACHIEVEMENTS["poker"]
        })

    # Ghost - 0 goals, 0 assists, rating below 5.0
    if match_goals == 0 and match_assists == 0 and match_rating < 5.0 and match_rating > 0:
        if not has_achievement_been_earned(guild_id, player_name, "ghost"):
            achievements.append({
                "id": "ghost",
                **ACHIEVEMENTS["ghost"]
            })

=======
>>>>>>> origin/main
    # Sniper - 5+ goals in a single match
    if match_goals >= 5 and not has_achievement_been_earned(guild_id, player_name, "sniper"):
        achievements.append({
            "id": "sniper",
            **ACHIEVEMENTS["sniper"]
        })
    
    # Double Trouble - 2+ goals AND 2+ assists in a single match
    if match_goals >= 2 and match_assists >= 2 and not has_achievement_been_earned(guild_id, player_name, "double_trouble"):
        achievements.append({
            "id": "double_trouble",
            **ACHIEVEMENTS["double_trouble"]
        })
    
    # Unsung Hero - Win MOTM without scoring or assisting
    if is_motm and match_goals == 0 and match_assists == 0 and not has_achievement_been_earned(guild_id, player_name, "unsung_hero"):
        achievements.append({
            "id": "unsung_hero",
            **ACHIEVEMENTS["unsung_hero"]
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
        
        # Nil-Nil Nightmare - 0-0 draw
        if result == "3" and our_score == 0 and opp_score == 0:
            if not has_achievement_been_earned(guild_id, player_name, "nil_nil"):
                achievements.append({
                    "id": "nil_nil",
                    **ACHIEVEMENTS["nil_nil"]
                })

        # Comeback Kings - Win a match where opponent also scored
        if result == "1" and opp_score > 0:
            if not has_achievement_been_earned(guild_id, player_name, "comeback_kings"):
                achievements.append({
                    "id": "comeback_kings",
                    **ACHIEVEMENTS["comeback_kings"]
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
    
    # Check for "Assist Streak" - assist in 5 consecutive matches
    if len(match_history) >= 5:
        recent_5 = match_history[-5:]
        if all(m.get("assists", 0) > 0 for m in recent_5):
            if not has_achievement_been_earned(guild_id, player_name, "assist_streak"):
                achievements.append({
                    "id": "assist_streak",
                    **ACHIEVEMENTS["assist_streak"]
                })

    # Check for "Involvement" - goal or assist in 10 consecutive matches
    if len(match_history) >= 10:
        recent_10 = match_history[-10:]
        if all((m.get("goals", 0) > 0 or m.get("assists", 0) > 0) for m in recent_10):
            if not has_achievement_been_earned(guild_id, player_name, "involvement"):
                achievements.append({
                    "id": "involvement",
                    **ACHIEVEMENTS["involvement"]
                })

    # Check for "Fortress" - win 5 matches in a row
    if len(match_history) >= 5:
        recent_5 = match_history[-5:]
        if all(m.get("result") == "W" for m in recent_5):
            if not has_achievement_been_earned(guild_id, player_name, "fortress"):
                achievements.append({
                    "id": "fortress",
                    **ACHIEVEMENTS["fortress"]
                })

    # Check for "Draw Specialists" - draw 3 matches in a row
    if len(match_history) >= 3:
        recent_3 = match_history[-3:]
        if all(m.get("result") == "D" for m in recent_3):
            if not has_achievement_been_earned(guild_id, player_name, "draw_specialists"):
                achievements.append({
                    "id": "draw_specialists",
                    **ACHIEVEMENTS["draw_specialists"]
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
            # Record achievement FIRST to prevent duplicates
            record_achievement(guild_id, player_name, achievement["id"])
            
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


def check_historical_achievements(guild_id: int, player_name: str, stats: dict) -> list[dict]:
    """
    Check for stat-based achievements only (for historical backfill).
    This is used when a player is first seen to award historical achievements.
    
    Only checks achievements that can be determined from career stats:
    - Man of the Match (first one)
    - Sharpshooter (70%+ shot accuracy)
    - Playmaker (more assists than goals)
    - Goal Machine (2+ goals per game)
    - Midfield Maestro (90%+ pass accuracy)
    - The Wall (80%+ tackle success)
    - Iron Man (50+ matches)
    - Consistent Performer (7.5+ avg rating over 20+ matches)
    
    Skips match-specific achievements (hat tricks, perfect 10s, etc.) and
    streak achievements (on fire, clean sheets, etc.) since we don't have
    historical match data.
    
    Args:
        guild_id: Discord guild ID
        player_name: Player name
        stats: Overall player stats from EA API
    
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
    
    # Man of the Match (first one)
    if motm >= 1 and not has_achievement_been_earned(guild_id, player_name, "man_of_match"):
        achievements.append({
            "id": "man_of_match",
            **ACHIEVEMENTS["man_of_match"]
        })
        # Record it silently (no announcement, that's done in the summary)
        record_achievement(guild_id, player_name, "man_of_match")
    
    # Statistical Excellence Achievements
    if matches_played >= 50 and shot_accuracy >= 70:
        if not has_achievement_been_earned(guild_id, player_name, "sharpshooter"):
            achievements.append({
                "id": "sharpshooter",
                **ACHIEVEMENTS["sharpshooter"]
            })
            record_achievement(guild_id, player_name, "sharpshooter")
    
    if assists >= 50 and goals >= 50 and assists > goals:
        if not has_achievement_been_earned(guild_id, player_name, "playmaker"):
            achievements.append({
                "id": "playmaker",
                **ACHIEVEMENTS["playmaker"]
            })
            record_achievement(guild_id, player_name, "playmaker")
    
    goals_per_game = goals / matches_played if matches_played > 0 else 0
    if matches_played >= 25 and goals_per_game >= 2.0:
        if not has_achievement_been_earned(guild_id, player_name, "goal_machine"):
            achievements.append({
                "id": "goal_machine",
                **ACHIEVEMENTS["goal_machine"]
            })
            record_achievement(guild_id, player_name, "goal_machine")
    
    if matches_played >= 100 and pass_accuracy >= 90:
        if not has_achievement_been_earned(guild_id, player_name, "midfield_maestro"):
            achievements.append({
                "id": "midfield_maestro",
                **ACHIEVEMENTS["midfield_maestro"]
            })
            record_achievement(guild_id, player_name, "midfield_maestro")
    
    if tackles_made >= 500 and tackle_success >= 80:
        if not has_achievement_been_earned(guild_id, player_name, "the_wall"):
            achievements.append({
                "id": "the_wall",
                **ACHIEVEMENTS["the_wall"]
            })
            record_achievement(guild_id, player_name, "the_wall")
    
<<<<<<< HEAD
    # Century - 100 career goals
    if goals >= 100:
        if not has_achievement_been_earned(guild_id, player_name, "century"):
            achievements.append({
                "id": "century",
                **ACHIEVEMENTS["century"]
            })
            record_achievement(guild_id, player_name, "century")

    # Provider - 100 career assists
    if assists >= 100:
        if not has_achievement_been_earned(guild_id, player_name, "provider"):
            achievements.append({
                "id": "provider",
                **ACHIEVEMENTS["provider"]
            })
            record_achievement(guild_id, player_name, "provider")

    # Veteran - 200+ career matches
    if matches_played >= 200:
        if not has_achievement_been_earned(guild_id, player_name, "veteran"):
            achievements.append({
                "id": "veteran",
                **ACHIEVEMENTS["veteran"]
            })
            record_achievement(guild_id, player_name, "veteran")

    # Red Mist - 10+ career red cards
    red_cards = int(stats.get("redCards", 0) or 0)
    if red_cards >= 10:
        if not has_achievement_been_earned(guild_id, player_name, "red_mist"):
            achievements.append({
                "id": "red_mist",
                **ACHIEVEMENTS["red_mist"]
            })
            record_achievement(guild_id, player_name, "red_mist")

=======
>>>>>>> origin/main
    # Career Milestone Achievements
    if matches_played >= 50 and not has_achievement_been_earned(guild_id, player_name, "iron_man"):
        achievements.append({
            "id": "iron_man",
            **ACHIEVEMENTS["iron_man"]
        })
        record_achievement(guild_id, player_name, "iron_man")
    
    rating = float(stats.get("ratingAve", 0) or 0)
    if matches_played >= 20 and rating >= 7.5 and not has_achievement_been_earned(guild_id, player_name, "consistent_performer"):
        achievements.append({
            "id": "consistent_performer",
            **ACHIEVEMENTS["consistent_performer"]
        })
        record_achievement(guild_id, player_name, "consistent_performer")
    
    return achievements


async def announce_historical_achievements(client, guild_id: int, player_name: str, achievements_list: list[dict]):
    """
    Post a summary announcement for historical achievements (earned before bot joined).
    This is different from regular achievement announcements - it's a single embed
    with all historical achievements listed.
    
    Args:
        client: Discord client
        guild_id: Discord guild ID
        player_name: Player name
        achievements_list: List of historical achievements
    """
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
        
        # Create summary embed (blue color to differentiate from new achievements)
        embed = discord.Embed(
            title=f"🏆 {player_name}'s Historical Achievements",
            description="Achievements earned before the bot joined:",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc)
        )
        
        # Group by category for better organization
        categorized = {}
        for achievement in achievements_list:
            category = achievement['category']
            if category not in categorized:
                categorized[category] = []
            categorized[category].append(achievement)
        
        # Add fields for each category
        for category, achs in categorized.items():
            ach_text = "\n".join([
                f"{ach['emoji']} **{ach['name']}** - {ach['description']}"
                for ach in achs
            ])
            embed.add_field(name=category, value=ach_text, inline=False)
        
        embed.set_footer(text="Match-specific achievements (hat tricks, perfect ratings, streaks, etc.) will be tracked from now on!")
        
        await channel.send(embed=embed)
        logger.info(f"Announced {len(achievements_list)} historical achievement(s) for {player_name}")
    
    except Exception as e:
        logger.error(f"Failed to announce historical achievements: {e}", exc_info=True)
