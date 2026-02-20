"""
Discord embed builders for match results and stats.
"""
import discord
from datetime import datetime, timezone


class PaginatedEmbedView(discord.ui.View):
    """
    A Discord View that adds Previous / Next buttons to navigate
    through a list of pre-built embeds.

    Usage::

        pages = [embed1, embed2, embed3]
        view = PaginatedEmbedView(pages)
        await interaction.followup.send(embed=pages[0], view=view)
    """

    def __init__(self, embeds: list[discord.Embed], *, timeout: float = 180.0):
        super().__init__(timeout=timeout)
        if not embeds:
            raise ValueError("embeds must not be empty")
        self.embeds = embeds
        self.current_page = 0
        self._update_buttons()

    def _update_buttons(self) -> None:
        """Disable navigation buttons at the first / last page."""
        self.prev_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page == len(self.embeds) - 1

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        self._update_buttons()
        await interaction.response.edit_message(
            embed=self.embeds[self.current_page], view=self
        )

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        self._update_buttons()
        await interaction.response.edit_message(
            embed=self.embeds[self.current_page], view=self
        )

    async def on_timeout(self) -> None:
        """Disable all buttons when the view times out (3 minutes by default)."""
        for child in self.children:
            child.disabled = True


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


