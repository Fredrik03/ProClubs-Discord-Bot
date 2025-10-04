"""
Stats Cards Generator - Beautiful image-based stat cards for Pro Clubs

This module generates stunning visual stat cards using Pillow (PIL).
Cards are designed with modern aesthetics, gradients, and clean typography.

CARD TYPES:
-----------
1. Match Result Cards - Shows match score, players, and stats
2. Player Stat Cards - Individual player performance
3. Club Stat Cards - Overall club statistics

DESIGN PRINCIPLES:
------------------
- Dark theme with vibrant accents
- Gradient backgrounds
- Clean typography hierarchy
- Color-coded results (green=win, red=loss, yellow=draw)
- Icons/emojis for quick stat recognition
- Professional esports-style layout

OUTPUT:
-------
All cards are saved to data/card_cache/ and return Discord File objects
ready to be sent as attachments.
"""

import logging
from pathlib import Path
from io import BytesIO
from datetime import datetime
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageFilter

logger = logging.getLogger('ProClubsBot.StatsCards')

# Paths
DATA_DIR = Path(__file__).parent.parent.parent / "data"
CARD_CACHE_DIR = DATA_DIR / "card_cache"
CARD_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Card dimensions
MATCH_CARD_WIDTH = 1200
MATCH_CARD_HEIGHT = 800
PLAYER_CARD_WIDTH = 800
PLAYER_CARD_HEIGHT = 600

# Color palette - Ultra modern FIFA/FC style
COLORS = {
    # Base colors
    'bg_dark': '#0f1419',           # Almost black background
    'bg_card': '#1a1f26',           # Card background
    'bg_secondary': '#252b33',      # Secondary elements
    'bg_player_card': '#1e242b',    # Player card bg
    
    # Accent colors - More vibrant
    'win': '#00f260',               # Electric green (gradient start)
    'win_end': '#0bcea3',           # Teal (gradient end)
    'loss': '#f54242',              # Bright red
    'loss_end': '#c71010',          # Dark red
    'draw': '#f2994a',              # Orange
    'draw_end': '#f2c94c',          # Yellow
    'primary': '#00d9ff',           # Electric cyan
    'secondary': '#a855f7',         # Purple accent
    
    # Text colors
    'text_primary': '#ffffff',      # Pure white
    'text_secondary': '#94a3b8',    # Slate gray
    'text_muted': '#64748b',        # Muted slate
    
    # Stat colors - More vibrant
    'goals': '#22c55e',             # Green
    'assists': '#3b82f6',           # Blue
    'rating': '#fbbf24',            # Amber/Gold
    'motm': '#f97316',              # Orange
    
    # Shadows and effects
    'shadow': '#000000',
    'glow': '#ffffff',
}


def get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """
    Get a font with fallback support.
    Tries to use modern fonts, falls back to default if unavailable.
    
    Args:
        size: Font size in points
        bold: Whether to use bold weight
    
    Returns:
        ImageFont object
    """
    # Try modern fonts first (Windows, macOS, Linux)
    font_names = [
        # Windows
        'C:/Windows/Fonts/segoeui.ttf' if not bold else 'C:/Windows/Fonts/segoeuib.ttf',
        # macOS
        '/System/Library/Fonts/SFNS.ttf',
        # Linux
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf' if not bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    ]
    
    for font_path in font_names:
        try:
            return ImageFont.truetype(font_path, size)
        except Exception:
            continue
    
    # Fallback to default PIL font
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        logger.warning(f"Could not load custom font, using default")
        return ImageFont.load_default()


def hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    """Convert hex color to RGB tuple."""
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def create_gradient_background(width: int, height: int, color1: str, color2: str) -> Image.Image:
    """
    Create a gradient background image.
    
    Args:
        width: Image width
        height: Image height
        color1: Start color (hex)
        color2: End color (hex)
    
    Returns:
        PIL Image with gradient
    """
    base = Image.new('RGB', (width, height), hex_to_rgb(color1))
    top = Image.new('RGB', (width, height), hex_to_rgb(color2))
    mask = Image.new('L', (width, height))
    mask_data = []
    for y in range(height):
        for x in range(width):
            mask_data.append(int(255 * (y / height)))
    mask.putdata(mask_data)
    base.paste(top, (0, 0), mask)
    return base


def draw_rounded_rectangle(
    draw: ImageDraw.ImageDraw,
    xy: Tuple[int, int, int, int],
    radius: int,
    fill: Optional[str] = None,
    outline: Optional[str] = None,
    width: int = 1
):
    """Draw a rounded rectangle."""
    x1, y1, x2, y2 = xy
    
    # Draw rectangles
    draw.rectangle([x1 + radius, y1, x2 - radius, y2], fill=fill, outline=outline, width=width)
    draw.rectangle([x1, y1 + radius, x2, y2 - radius], fill=fill, outline=outline, width=width)
    
    # Draw corners
    draw.pieslice([x1, y1, x1 + radius * 2, y1 + radius * 2], 180, 270, fill=fill, outline=outline)
    draw.pieslice([x2 - radius * 2, y1, x2, y1 + radius * 2], 270, 360, fill=fill, outline=outline)
    draw.pieslice([x1, y2 - radius * 2, x1 + radius * 2, y2], 90, 180, fill=fill, outline=outline)
    draw.pieslice([x2 - radius * 2, y2 - radius * 2, x2, y2], 0, 90, fill=fill, outline=outline)


def add_shadow_text(draw, position, text, font, fill, shadow_color='#000000', offset=2):
    """Add text with shadow for depth."""
    x, y = position
    # Draw shadow
    draw.text((x + offset, y + offset), text, fill=shadow_color, font=font)
    # Draw main text
    draw.text((x, y), text, fill=fill, font=font)


def create_gradient_horizontal(width: int, height: int, color1: str, color2: str) -> Image.Image:
    """Create a horizontal gradient."""
    base = Image.new('RGB', (width, height), hex_to_rgb(color1))
    top = Image.new('RGB', (width, height), hex_to_rgb(color2))
    mask = Image.new('L', (width, height))
    mask_data = []
    for y in range(height):
        for x in range(width):
            mask_data.append(int(255 * (x / width)))
    mask.putdata(mask_data)
    base.paste(top, (0, 0), mask)
    return base


def create_match_card(
    club_name: str,
    opponent_name: str,
    club_score: int,
    opponent_score: int,
    result: str,  # "win", "loss", "draw"
    players: list,  # List of player dicts with stats
    match_time: str,
    platform: str = "gen5"
) -> BytesIO:
    """
    Create a beautiful match result card.
    
    Args:
        club_name: Your club's name
        opponent_name: Opponent club name
        club_score: Your score
        opponent_score: Opponent's score
        result: "win", "loss", or "draw"
        players: List of player dicts with stats (goals, assists, rating)
        match_time: Time string (e.g., "2 hours ago")
        platform: Platform string
    
    Returns:
        BytesIO object containing PNG image
    """
    logger.info(f"[Stats Card] Creating match card: {club_name} {club_score}-{opponent_score} {opponent_name}")
    
    # Create base image - solid dark background
    img = Image.new('RGB', (MATCH_CARD_WIDTH, MATCH_CARD_HEIGHT), hex_to_rgb(COLORS['bg_dark']))
    draw = ImageDraw.Draw(img)
    
    # Fonts
    font_title = get_font(48, bold=True)
    font_score = get_font(120, bold=True)
    font_club = get_font(36, bold=True)
    font_stat_label = get_font(20)
    font_stat_value = get_font(28, bold=True)
    font_player = get_font(24, bold=True)
    font_player_stats = get_font(18)
    font_small = get_font(16)
    
    # Result color
    result_color = COLORS.get(result, COLORS['draw'])
    
    # --- HEADER SECTION ---
    # Result badge
    badge_y = 30
    badge_height = 50
    result_text = result.upper()
    
    # Draw result badge
    draw_rounded_rectangle(
        draw,
        (50, badge_y, 230, badge_y + badge_height),
        radius=25,
        fill=result_color
    )
    
    # Result text
    bbox = draw.textbbox((0, 0), result_text, font=font_title)
    text_width = bbox[2] - bbox[0]
    draw.text(
        (140 - text_width // 2, badge_y + 5),
        result_text,
        fill=COLORS['bg_dark'],
        font=font_title
    )
    
    # Match time
    draw.text(
        (MATCH_CARD_WIDTH - 220, badge_y + 10),
        match_time,
        fill=COLORS['text_secondary'],
        font=font_small
    )
    
    # Platform
    draw.text(
        (MATCH_CARD_WIDTH - 220, badge_y + 30),
        f"Platform: {platform}",
        fill=COLORS['text_muted'],
        font=font_small
    )
    
    # --- SCORE SECTION ---
    score_y = 140
    
    # Your club (left side)
    club_bbox = draw.textbbox((0, 0), club_name, font=font_club)
    club_width = club_bbox[2] - club_bbox[0]
    draw.text(
        (MATCH_CARD_WIDTH // 2 - club_width - 120, score_y - 50),
        club_name,
        fill=result_color,
        font=font_club
    )
    
    # Your score
    score_text = str(club_score)
    score_bbox = draw.textbbox((0, 0), score_text, font=font_score)
    score_width = score_bbox[2] - score_bbox[0]
    draw.text(
        (MATCH_CARD_WIDTH // 2 - score_width - 100, score_y),
        score_text,
        fill=result_color,
        font=font_score
    )
    
    # VS
    vs_font = get_font(40)
    draw.text(
        (MATCH_CARD_WIDTH // 2 - 20, score_y + 35),
        "VS",
        fill=COLORS['text_secondary'],
        font=vs_font
    )
    
    # Opponent club (right side)
    opp_bbox = draw.textbbox((0, 0), opponent_name, font=font_club)
    draw.text(
        (MATCH_CARD_WIDTH // 2 + 120, score_y - 50),
        opponent_name,
        fill=COLORS['text_primary'],
        font=font_club
    )
    
    # Opponent score
    opp_score_text = str(opponent_score)
    draw.text(
        (MATCH_CARD_WIDTH // 2 + 100, score_y),
        opp_score_text,
        fill=COLORS['text_primary'],
        font=font_score
    )
    
    # --- DIVIDER LINE ---
    line_y = 320
    draw.line(
        [(80, line_y), (MATCH_CARD_WIDTH - 80, line_y)],
        fill=COLORS['bg_secondary'],
        width=2
    )
    
    # --- PLAYERS SECTION ---
    players_y = 360
    draw.text(
        (50, players_y),
        "⭐ TOP PERFORMERS",
        fill=COLORS['text_secondary'],
        font=font_club
    )
    
    # Show top 3 players
    player_y = players_y + 60
    for i, player in enumerate(players[:3]):
        if i >= 3:
            break
        
        # Player card background
        card_x = 50
        card_y = player_y + (i * 100)
        card_width = MATCH_CARD_WIDTH - 100
        card_height = 80
        
        draw_rounded_rectangle(
            draw,
            (card_x, card_y, card_x + card_width, card_y + card_height),
            radius=15,
            fill=COLORS['bg_secondary']
        )
        
        # Rank number
        rank_color = ['#FFD700', '#C0C0C0', '#CD7F32'][i]  # Gold, Silver, Bronze
        draw.text(
            (card_x + 20, card_y + 20),
            f"#{i+1}",
            fill=rank_color,
            font=font_player
        )
        
        # Player name
        player_name = player.get('name', 'Unknown')
        draw.text(
            (card_x + 80, card_y + 15),
            player_name,
            fill=COLORS['text_primary'],
            font=font_player
        )
        
        # Position
        position = player.get('position', 'ST')
        draw.text(
            (card_x + 80, card_y + 45),
            position,
            fill=COLORS['text_muted'],
            font=font_small
        )
        
        # Stats
        goals = player.get('goals', 0)
        assists = player.get('assists', 0)
        rating = player.get('rating', 0)
        
        stats_x = card_x + 400
        
        # Goals
        draw.text(
            (stats_x, card_y + 15),
            f"⚽ {goals}",
            fill=COLORS['goals'],
            font=font_player_stats
        )
        
        # Assists
        draw.text(
            (stats_x + 100, card_y + 15),
            f"🅰️ {assists}",
            fill=COLORS['assists'],
            font=font_player_stats
        )
        
        # Rating
        draw.text(
            (stats_x + 200, card_y + 15),
            f"⭐ {rating:.1f}",
            fill=COLORS['rating'],
            font=font_player_stats
        )
        
        # MOTM badge if applicable
        if player.get('motm'):
            draw_rounded_rectangle(
                draw,
                (stats_x + 320, card_y + 10, stats_x + 420, card_y + 50),
                radius=10,
                fill=COLORS['motm']
            )
            draw.text(
                (stats_x + 330, card_y + 15),
                "MOTM",
                fill='#ffffff',
                font=font_player_stats
            )
    
    # --- FOOTER ---
    footer_text = "EA Sports FC Pro Clubs • Discord Bot"
    draw.text(
        (MATCH_CARD_WIDTH // 2 - 200, MATCH_CARD_HEIGHT - 40),
        footer_text,
        fill=COLORS['text_muted'],
        font=font_small
    )
    
    # Convert to BytesIO
    buffer = BytesIO()
    img.save(buffer, format='PNG', optimize=True)
    buffer.seek(0)
    
    # Optionally save to cache
    try:
        cache_path = CARD_CACHE_DIR / f"match_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        img.save(cache_path, format='PNG')
        logger.debug(f"[Stats Card] Saved to cache: {cache_path}")
    except Exception as e:
        logger.warning(f"[Stats Card] Could not save to cache: {e}")
    
    logger.info(f"[Stats Card] ✅ Match card created successfully")
    return buffer


def create_player_card(
    player_name: str,
    club_name: str,
    position: str,
    stats: dict,
    platform: str = "gen5"
) -> BytesIO:
    """
    Create a player stat card.
    
    Args:
        player_name: Player name
        club_name: Club name
        position: Player position
        stats: Dictionary of player stats
        platform: Platform string
    
    Returns:
        BytesIO object containing PNG image
    """
    logger.info(f"[Stats Card] Creating player card for {player_name}")
    
    # Create base image
    img = create_gradient_background(
        PLAYER_CARD_WIDTH,
        PLAYER_CARD_HEIGHT,
        COLORS['bg_dark'],
        COLORS['bg_card']
    )
    draw = ImageDraw.Draw(img)
    
    # Fonts
    font_name = get_font(48, bold=True)
    font_club = get_font(24)
    font_stat_label = get_font(18)
    font_stat_value = get_font(32, bold=True)
    font_small = get_font(16)
    
    # --- HEADER ---
    # Player name
    draw.text(
        (50, 50),
        player_name,
        fill=COLORS['primary'],
        font=font_name
    )
    
    # Club name
    draw.text(
        (50, 110),
        club_name,
        fill=COLORS['text_secondary'],
        font=font_club
    )
    
    # Position badge
    draw_rounded_rectangle(
        draw,
        (PLAYER_CARD_WIDTH - 150, 60, PLAYER_CARD_WIDTH - 50, 110),
        radius=15,
        fill=COLORS['secondary']
    )
    draw.text(
        (PLAYER_CARD_WIDTH - 120, 65),
        position,
        fill=COLORS['text_primary'],
        font=font_club
    )
    
    # --- STATS GRID ---
    stats_y = 200
    stats_per_row = 3
    stat_width = (PLAYER_CARD_WIDTH - 100) // stats_per_row
    stat_height = 120
    
    stat_items = [
        ("⚽ Goals", stats.get('goals', 0), COLORS['goals']),
        ("🅰️ Assists", stats.get('assists', 0), COLORS['assists']),
        ("⭐ Rating", f"{stats.get('rating', 0):.1f}", COLORS['rating']),
        ("🎮 Matches", stats.get('matches', 0), COLORS['primary']),
        ("📈 Win %", f"{stats.get('win_rate', 0)}%", COLORS['win']),
        ("🥇 MOTM", stats.get('motm', 0), COLORS['motm']),
    ]
    
    for i, (label, value, color) in enumerate(stat_items):
        row = i // stats_per_row
        col = i % stats_per_row
        
        x = 50 + (col * stat_width)
        y = stats_y + (row * stat_height)
        
        # Stat card
        draw_rounded_rectangle(
            draw,
            (x, y, x + stat_width - 20, y + stat_height - 20),
            radius=15,
            fill=COLORS['bg_secondary']
        )
        
        # Label
        draw.text(
            (x + 20, y + 20),
            label,
            fill=COLORS['text_secondary'],
            font=font_stat_label
        )
        
        # Value
        draw.text(
            (x + 20, y + 50),
            str(value),
            fill=color,
            font=font_stat_value
        )
    
    # --- FOOTER ---
    draw.text(
        (50, PLAYER_CARD_HEIGHT - 40),
        f"Platform: {platform} • EA Sports FC Pro Clubs",
        fill=COLORS['text_muted'],
        font=font_small
    )
    
    # Convert to BytesIO
    buffer = BytesIO()
    img.save(buffer, format='PNG', optimize=True)
    buffer.seek(0)
    
    logger.info(f"[Stats Card] ✅ Player card created successfully")
    return buffer


# Export main functions
__all__ = ['create_match_card', 'create_player_card']
