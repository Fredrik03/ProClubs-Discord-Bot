"""
Milestone tracking and announcement logic.
"""
import logging
import discord
from datetime import datetime, timezone
from database import has_milestone_been_announced, record_milestone, get_settings

logger = logging.getLogger('ProClubsBot.Milestones')

# Milestone thresholds
MILESTONE_THRESHOLDS = {
    "goals": [1, 10, 25, 50, 100, 250, 500],
    "assists": [1, 10, 25, 50, 100, 250, 500],
    "matches": [1, 10, 25, 50, 100, 250, 500],
    "motm": [1, 5, 10, 25, 50, 100],
}


def check_milestones(guild_id: int, player_name: str, stats: dict) -> list[dict]:
    """
    Check if player has reached any new milestones.
    Returns list of milestone dicts: [{"type": "goals", "value": 50, "emoji": "⚽", "label": "Goals"}, ...]
    """
    milestones = []
    
    # Goals
    goals = int(stats.get("goals", 0) or 0)
    for threshold in MILESTONE_THRESHOLDS["goals"]:
        if goals >= threshold and not has_milestone_been_announced(guild_id, player_name, "goals", threshold):
            milestones.append({"type": "goals", "value": threshold, "emoji": "⚽", "label": "Goals"})
    
    # Assists
    assists = int(stats.get("assists", 0) or 0)
    for threshold in MILESTONE_THRESHOLDS["assists"]:
        if assists >= threshold and not has_milestone_been_announced(guild_id, player_name, "assists", threshold):
            milestones.append({"type": "assists", "value": threshold, "emoji": "🅰️", "label": "Assists"})
    
    # Matches
    matches = int(stats.get("gamesPlayed", 0) or 0)
    for threshold in MILESTONE_THRESHOLDS["matches"]:
        if matches >= threshold and not has_milestone_been_announced(guild_id, player_name, "matches", threshold):
            milestones.append({"type": "matches", "value": threshold, "emoji": "🎮", "label": "Matches Played"})
    
    # Man of the Match
    motm = int(stats.get("manOfTheMatch", 0) or 0)
    for threshold in MILESTONE_THRESHOLDS["motm"]:
        if motm >= threshold and not has_milestone_been_announced(guild_id, player_name, "motm", threshold):
            milestones.append({"type": "motm", "value": threshold, "emoji": "⭐", "label": "Man of the Match"})
    
    return milestones


async def announce_milestones(client, guild_id: int, player_name: str, milestones: list[dict]):
    """Post milestone announcements to the configured channel."""
    if not milestones:
        return
    
    settings = get_settings(guild_id)
    if not settings or not settings.get("milestone_channel_id"):
        return
    
    try:
        channel = client.get_channel(settings["milestone_channel_id"])
        if not channel:
            return
        
        for milestone in milestones:
            # Record milestone FIRST to prevent duplicates
            record_milestone(guild_id, player_name, milestone["type"], milestone["value"])

            # Pick flavour text based on milestone size
            value = milestone["value"]
            if value >= 500:
                tier_bar = "🔶🔶🔶🔶🔶"
                tier_label = "LEGENDARY"
            elif value >= 250:
                tier_bar = "🟣🟣🟣🟣🟣"
                tier_label = "EPIC"
            elif value >= 100:
                tier_bar = "🟡🟡🟡🟡🟡"
                tier_label = "ELITE"
            elif value >= 50:
                tier_bar = "🔵🔵🔵🔵⬜"
                tier_label = "IMPRESSIVE"
            elif value >= 25:
                tier_bar = "🔵🔵🔵⬜⬜"
                tier_label = "SOLID"
            elif value >= 10:
                tier_bar = "🔵🔵⬜⬜⬜"
                tier_label = "GROWING"
            else:
                tier_bar = "🔵⬜⬜⬜⬜"
                tier_label = "FIRST STEP"

            embed = discord.Embed(
                title=f"🏆 MILESTONE UNLOCKED — {tier_label}",
                description=(
                    f"## {milestone['emoji']} {value} {milestone['label'].upper()}\n"
                    f"**{player_name}** has reached this milestone!\n\n"
                    f"{tier_bar}"
                ),
                color=discord.Color.gold(),
                timestamp=datetime.now(timezone.utc),
            )
            embed.set_footer(text="Keep grinding — the next milestone awaits!")

            await channel.send(embed=embed)
            
            logger.info(f"Announced milestone: {player_name} - {milestone['value']} {milestone['type']}")
    
    except Exception as e:
        logger.error(f"Failed to announce milestone: {e}", exc_info=True)


