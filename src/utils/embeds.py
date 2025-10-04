"""
Discord embed builders for match results and stats.
"""
import discord
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger('ProClubsBot.Embeds')


def utc_to_str(ts: int) -> str:
    """Convert UTC timestamp to readable string."""
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return str(ts)


def build_match_embed(club_id: int, platform: str, match: dict, match_type: str, club_name_hint: str | None = None):
    """
    Build a Discord embed for a match result with detailed stats.
    
    New API structure:
    - clubs: { "clubId": { "score": "5", "result": "1/2/3", "details": { "name": "..." } } }
    - players: { "clubId": { "playerId": { "playername": "...", stats... } } }
    """
    # Get clubs data from new API structure
    clubs = match.get("clubs", {})
    
    # Find our club and opponent
    our_club = clubs.get(str(club_id), {})
    opponent_ids = [cid for cid in clubs.keys() if str(cid) != str(club_id)]
    opponent_club = clubs.get(opponent_ids[0], {}) if opponent_ids else {}
    
    # Get club names
    our_name = club_name_hint or our_club.get("details", {}).get("name", f"Club {club_id}")
    opponent_name = opponent_club.get("details", {}).get("name", "Unknown")
    
    # Get scores
    our_score = our_club.get("score", "?")
    opp_score = opponent_club.get("score", "?")
    
    # Determine result
    result = our_club.get("result", "")
    if result == "1":
        res = "✅ Win"
        color = 0x2ecc71  # Green
    elif result == "2":
        res = "❌ Loss"
        color = 0xe74c3c  # Red
    elif result == "3":
        res = "🤝 Draw"
        color = 0xf1c40f  # Yellow
    else:
        res = "❓"
        color = 0x95a5a6  # Gray
    
    # Time
    when = utc_to_str(match.get("timestamp", 0))
    time_ago = match.get("timeAgo", {})
    time_ago_str = f"{time_ago.get('number', '?')} {time_ago.get('unit', 'ago')}" if time_ago else ""
    
    # Build embed
    title = f"{res} {our_score}–{opp_score}"
    desc_lines = [
        f"**{our_name}** vs **{opponent_name}**",
        f"📅 {when}",
    ]
    if time_ago_str:
        desc_lines.append(f"🕐 {time_ago_str} ago")
    
    embed = discord.Embed(
        title=title,
        description="\n".join(desc_lines),
        color=color
    )
    
    # Get player stats from top-level players structure
    all_players = match.get("players", {})
    club_players = all_players.get(str(club_id), {})
    
    if club_players:
        # Collect all player stats
        player_stats = []
        total_goals = 0
        total_assists = 0
        motm_player = None
        
        for player_id, player_data in club_players.items():
            if isinstance(player_data, dict):
                name = player_data.get("playername", "Unknown")
                goals = int(player_data.get("goals", 0) or 0)
                assists = int(player_data.get("assists", 0) or 0)
                rating = float(player_data.get("rating", 0) or 0)
                mom = int(player_data.get("mom", 0) or 0)
                
                total_goals += goals
                total_assists += assists
                
                if mom == 1:
                    motm_player = (name, rating)
                
                player_stats.append({
                    "name": name,
                    "goals": goals,
                    "assists": assists,
                    "rating": rating,
                    "mom": mom
                })
        
        # Sort by goals, then assists, then rating
        player_stats.sort(key=lambda x: (x["goals"], x["assists"], x["rating"]), reverse=True)
        
        # Goal scorers
        scorers = [p for p in player_stats if p["goals"] > 0]
        if scorers:
            scorers_text = "\n".join([
                f"⚽ **{p['name']}** - {p['goals']}G" + (f" {p['assists']}A" if p['assists'] > 0 else "")
                for p in scorers[:5]
            ])
            embed.add_field(name="⚽ Goal Scorers", value=scorers_text, inline=False)
        
        # Man of the Match
        if motm_player:
            embed.add_field(
                name="⭐ Man of the Match",
                value=f"**{motm_player[0]}** ({motm_player[1]:.1f} rating)",
                inline=False
            )
        
        # Top rated players (show top 3)
        player_stats.sort(key=lambda x: x["rating"], reverse=True)
        top_rated_text = "\n".join([
            f"{'⭐' if p['mom'] == 1 else '📊'} **{p['name']}**: {p['rating']:.1f}"
            for p in player_stats[:3] if p['rating'] > 0
        ])
        if top_rated_text:
            embed.add_field(name="📊 Top Ratings", value=top_rated_text, inline=True)
        
        # Aggregate stats from match data
        aggregate = match.get("aggregate", {})
        our_aggregate = aggregate.get(str(club_id), {})
        
        if our_aggregate:
            # Calculate some team stats
            passes_made = int(our_aggregate.get("passesmade", 0) or 0)
            pass_attempts = int(our_aggregate.get("passattempts", 0) or 0)
            pass_pct = int((passes_made / pass_attempts * 100)) if pass_attempts > 0 else 0
            
            shots = int(our_aggregate.get("shots", 0) or 0)
            tackles = int(our_aggregate.get("tacklesmade", 0) or 0)
            
            team_stats_text = f"🎯 Pass: {pass_pct}% ({passes_made}/{pass_attempts})\n"
            team_stats_text += f"🥅 Shots: {shots}\n"
            team_stats_text += f"🛡️ Tackles: {tackles}"
            
            embed.add_field(name="📈 Team Stats", value=team_stats_text, inline=True)
    
    embed.set_footer(text=f"Platform: {platform} | Match Type: {match_type}")
    return embed


def prepare_match_card_data(club_id: int, platform: str, match: dict, club_name_hint: Optional[str] = None) -> dict:
    """
    Prepare match data for stat card generation.
    Extracts and formats all necessary data from the match dict.
    
    Args:
        club_id: Your club ID
        platform: Platform string
        match: Match data from EA API
        club_name_hint: Optional club name override
    
    Returns:
        Dictionary with formatted data for create_match_card()
    """
    logger.debug(f"[Embeds] Preparing match card data for club {club_id}")
    
    # Get clubs data from API structure
    clubs = match.get("clubs", {})
    
    # Find our club and opponent
    our_club = clubs.get(str(club_id), {})
    opponent_ids = [cid for cid in clubs.keys() if str(cid) != str(club_id)]
    opponent_club = clubs.get(opponent_ids[0], {}) if opponent_ids else {}
    
    # Get club names
    our_name = club_name_hint or our_club.get("details", {}).get("name", f"Club {club_id}")
    opponent_name = opponent_club.get("details", {}).get("name", "Unknown Opponent")
    
    # Get scores
    our_score = int(our_club.get("score", 0))
    opp_score = int(opponent_club.get("score", 0))
    
    # Determine result
    result_code = our_club.get("result", "")
    if result_code == "1":
        result = "win"
    elif result_code == "2":
        result = "loss"
    elif result_code == "3":
        result = "draw"
    else:
        result = "draw"  # Default
    
    # Time ago string
    time_ago = match.get("timeAgo", {})
    if time_ago:
        time_str = f"{time_ago.get('number', '?')} {time_ago.get('unit', 'time units')} ago"
    else:
        # Fallback to timestamp
        ts = match.get("timestamp", 0)
        try:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            time_str = dt.strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            time_str = "Recently"
    
    # Get player stats from top-level players structure
    all_players = match.get("players", {})
    club_players = all_players.get(str(club_id), {})
    
    # Collect and format player data
    players_list = []
    
    if club_players:
        for player_id, player_data in club_players.items():
            if isinstance(player_data, dict):
                name = player_data.get("playername", "Unknown")
                goals = int(player_data.get("goals", 0) or 0)
                assists = int(player_data.get("assists", 0) or 0)
                rating = float(player_data.get("rating", 0) or 0)
                mom = int(player_data.get("mom", 0) or 0)
                
                # Get position (try multiple fields)
                position = (
                    player_data.get("pos", "") or
                    player_data.get("position", "") or
                    player_data.get("posSorted", ["ST"])[0] if player_data.get("posSorted") else "ST"
                )
                
                # Clean up position string (remove numbers, limit length)
                if isinstance(position, str):
                    position = position.split()[0][:3].upper()  # Take first 3 chars
                
                players_list.append({
                    "name": name,
                    "position": position,
                    "goals": goals,
                    "assists": assists,
                    "rating": rating,
                    "motm": bool(mom == 1)
                })
        
        # Sort players by contribution: goals > assists > rating
        players_list.sort(
            key=lambda x: (x["goals"], x["assists"], x["rating"]),
            reverse=True
        )
    
    # If no players found, add a placeholder
    if not players_list:
        players_list = [{
            "name": "No player data",
            "position": "N/A",
            "goals": 0,
            "assists": 0,
            "rating": 0.0,
            "motm": False
        }]
    
    logger.debug(f"[Embeds] Prepared data for {our_name} vs {opponent_name}: {our_score}-{opp_score} ({result})")
    logger.debug(f"[Embeds] Found {len(players_list)} players")
    
    return {
        "club_name": our_name,
        "opponent_name": opponent_name,
        "club_score": our_score,
        "opponent_score": opp_score,
        "result": result,
        "players": players_list,
        "match_time": time_str,
        "platform": platform
    }


