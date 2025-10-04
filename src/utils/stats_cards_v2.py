"""
Stats Cards V2 - Completely redesigned for a cleaner, more modern look
Inspired by FIFA/FC25 UI with better spacing, shadows, and polish
"""

import logging
from pathlib import Path
from io import BytesIO
from datetime import datetime
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger('ProClubsBot.StatsCards')

# Paths
DATA_DIR = Path(__file__).parent.parent.parent / "data"
CARD_CACHE_DIR = DATA_DIR / "card_cache"
CARD_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Card dimensions
CARD_WIDTH = 1400
CARD_HEIGHT = 900

# Modern FC25-inspired color palette
COLORS = {
    'bg': '#0d1117',                    # GitHub dark bg
    'card_bg': '#161b22',               # Card background
    'border': '#30363d',                # Subtle borders
    
    # Result colors with gradients
    'win_primary': '#238636',           # GitHub green
    'win_secondary': '#2ea043',
    'loss_primary': '#da3633',          # GitHub red
    'loss_secondary': '#f85149',
    'draw_primary': '#9e6a03',          # GitHub yellow/orange
    'draw_secondary': '#d29922',
    
    # Text
    'text_white': '#f0f6fc',
    'text_gray': '#8b949e',
    'text_muted': '#6e7681',
    
    # Stats
    'stat_goals': '#3fb950',            # Bright green
    'stat_assists': '#58a6ff',          # Bright blue
    'stat_rating': '#f2cc60',           # Gold
    
    # Accent
    'accent': '#58a6ff',                # Blue accent
    'motm_gold': '#d29922',
}


def get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Get font with fallback."""
    fonts_to_try = [
        'C:/Windows/Fonts/segoeui.ttf' if not bold else 'C:/Windows/Fonts/segoeuib.ttf',
        '/System/Library/Fonts/SFNS.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    ]
    
    for font_path in fonts_to_try:
        try:
            return ImageFont.truetype(font_path, size)
        except:
            continue
    
    return ImageFont.load_default()


def hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    """Convert hex to RGB."""
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def draw_rounded_rect(draw, xy, radius, fill=None, outline=None, width=1):
    """Draw a rounded rectangle."""
    x1, y1, x2, y2 = xy
    draw.rectangle([x1 + radius, y1, x2 - radius, y2], fill=fill, outline=outline, width=width)
    draw.rectangle([x1, y1 + radius, x2, y2 - radius], fill=fill, outline=outline, width=width)
    draw.pieslice([x1, y1, x1 + radius * 2, y1 + radius * 2], 180, 270, fill=fill, outline=outline)
    draw.pieslice([x2 - radius * 2, y1, x2, y1 + radius * 2], 270, 360, fill=fill, outline=outline)
    draw.pieslice([x1, y2 - radius * 2, x1 + radius * 2, y2], 90, 180, fill=fill, outline=outline)
    draw.pieslice([x2 - radius * 2, y2 - radius * 2, x2, y2], 0, 90, fill=fill, outline=outline)


def create_match_card_v2(
    club_name: str,
    opponent_name: str,
    club_score: int,
    opponent_score: int,
    result: str,
    players: list,
    match_time: str,
    platform: str = "gen5"
) -> BytesIO:
    """
    Create a clean, modern match card.
    """
    logger.info(f"[Stats Card V2] Creating: {club_name} {club_score}-{opponent_score} {opponent_name}")
    
    # Create base
    img = Image.new('RGB', (CARD_WIDTH, CARD_HEIGHT), hex_to_rgb(COLORS['bg']))
    draw = ImageDraw.Draw(img)
    
    # Fonts
    font_huge = get_font(140, bold=True)
    font_large = get_font(48, bold=True)
    font_medium = get_font(32, bold=True)
    font_normal = get_font(24)
    font_small = get_font(18)
    
    # Result colors
    if result == "win":
        result_color = COLORS['win_primary']
        result_text = "VICTORY"
        result_emoji = "🏆"
    elif result == "loss":
        result_color = COLORS['loss_primary']
        result_text = "DEFEAT"
        result_emoji = "💔"
    else:
        result_color = COLORS['draw_primary']
        result_text = "DRAW"
        result_emoji = "🤝"
    
    # --- TOP BAR ---
    # Result banner
    draw_rounded_rect(draw, (40, 40, 400, 110), 15, fill=result_color)
    
    # Result text
    result_full = f"{result_emoji} {result_text}"
    draw.text((220 - len(result_full) * 8, 55), result_full, fill='#ffffff', font=font_medium)
    
    # Time in top right
    draw.text((CARD_WIDTH - 300, 55), match_time, fill=COLORS['text_gray'], font=font_small)
    draw.text((CARD_WIDTH - 300, 80), f"Platform: {platform.upper()}", fill=COLORS['text_muted'], font=font_small)
    
    # --- SCORE SECTION ---
    score_y = 180
    
    # Your club (left)
    club_text = club_name[:20]  # Truncate if too long
    draw.text((100, score_y - 60), club_text, fill=result_color, font=font_large)
    
    # Your score
    draw.text((150, score_y), str(club_score), fill=COLORS['text_white'], font=font_huge)
    
    # Dash
    draw.text((CARD_WIDTH // 2 - 30, score_y + 20), "-", fill=COLORS['text_gray'], font=font_large)
    
    # Opponent score
    draw.text((CARD_WIDTH - 350, score_y), str(opponent_score), fill=COLORS['text_white'], font=font_huge)
    
    # Opponent name (right)
    opp_text = opponent_name[:20]
    draw.text((CARD_WIDTH - 450, score_y - 60), opp_text, fill=COLORS['text_gray'], font=font_large)
    
    # --- PLAYERS SECTION ---
    players_y = 420
    
    # Section header
    draw.text((60, players_y), "⭐ MATCH STARS", fill=COLORS['text_white'], font=font_large)
    
    # Player cards (horizontal layout, cleaner)
    card_start_y = players_y + 80
    card_width = 400
    card_height = 160
    card_spacing = 30
    
    for i, player in enumerate(players[:3]):
        card_x = 60 + (i * (card_width + card_spacing))
        
        # Player card background
        draw_rounded_rect(
            draw,
            (card_x, card_start_y, card_x + card_width, card_start_y + card_height),
            20,
            fill=COLORS['card_bg'],
            outline=COLORS['border'],
            width=2
        )
        
        # Rank badge (top left corner)
        medal_colors = ['#FFD700', '#C0C0C0', '#CD7F32']  # Gold, Silver, Bronze
        medal_emojis = ['🥇', '🥈', '🥉']
        
        draw_rounded_rect(
            draw,
            (card_x + 20, card_start_y + 20, card_x + 80, card_start_y + 70),
            10,
            fill=medal_colors[i]
        )
        draw.text((card_x + 32, card_start_y + 22), medal_emojis[i], fill='#ffffff', font=font_medium)
        
        # Player name
        p_name = player.get('name', 'Unknown')[:15]
        draw.text((card_x + 100, card_start_y + 25), p_name, fill=COLORS['text_white'], font=font_medium)
        
        # Position
        pos = player.get('position', 'N/A')
        draw.text((card_x + 100, card_start_y + 60), pos, fill=COLORS['text_muted'], font=font_small)
        
        # Stats row (bottom of card)
        stats_y = card_start_y + 105
        
        # Goals
        goals = player.get('goals', 0)
        draw.text((card_x + 30, stats_y), f"⚽ {goals}", fill=COLORS['stat_goals'], font=font_normal)
        
        # Assists  
        assists = player.get('assists', 0)
        draw.text((card_x + 130, stats_y), f"🅰️ {assists}", fill=COLORS['stat_assists'], font=font_normal)
        
        # Rating
        rating = player.get('rating', 0)
        draw.text((card_x + 240, stats_y), f"⭐ {rating:.1f}", fill=COLORS['stat_rating'], font=font_normal)
        
        # MOTM badge if applicable
        if player.get('motm'):
            draw_rounded_rect(
                draw,
                (card_x + card_width - 90, card_start_y + 15, card_x + card_width - 15, card_start_y + 55),
                8,
                fill=COLORS['motm_gold']
            )
            draw.text((card_x + card_width - 75, card_start_y + 20), "MOTM", fill='#000000', font=font_small)
    
    # --- FOOTER ---
    footer_y = CARD_HEIGHT - 50
    footer_text = "EA SPORTS FC • PRO CLUBS"
    draw.text((CARD_WIDTH // 2 - 150, footer_y), footer_text, fill=COLORS['text_muted'], font=font_small)
    
    # Save to buffer
    buffer = BytesIO()
    img.save(buffer, format='PNG', optimize=True, quality=95)
    buffer.seek(0)
    
    # Cache
    try:
        cache_path = CARD_CACHE_DIR / f"match_v2_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        img.save(cache_path, format='PNG')
        logger.debug(f"[Stats Card V2] Saved: {cache_path}")
    except Exception as e:
        logger.warning(f"[Stats Card V2] Cache failed: {e}")
    
    logger.info(f"[Stats Card V2] ✅ Card created")
    return buffer


# Export
__all__ = ['create_match_card_v2']

