# 🎨 Stat Cards - Beautiful Visual Match Results

The bot now generates **stunning image-based stat cards** instead of plain text embeds for match results!

## ✨ Features

### **Match Result Cards**
- 🎯 **Modern Design**: Dark theme with vibrant gradient backgrounds
- 🏆 **Color-Coded Results**: 
  - 🟢 **Green** for wins
  - 🔴 **Red** for losses  
  - 🟡 **Orange** for draws
- 📊 **Top Performers**: Shows top 3 players with their stats
- 🥇 **Rank Medals**: Gold, Silver, Bronze for top players
- ⭐ **MOTM Badge**: Special highlight for Man of the Match
- 📈 **Live Stats**: Goals ⚽, Assists 🅰️, Ratings ⭐

### **Card Details**
- **Resolution**: 1200x800px (perfect for Discord)
- **Format**: PNG with optimization
- **Font**: Uses system fonts (Segoe UI on Windows, SF Pro on macOS)
- **Colors**: Professional esports-style color palette
- **Cache**: Cards are saved to `data/card_cache/` for reference

## 🎨 Design Elements

### Color Palette
```
Background:  #0a0e27 → #141b3b (gradient)
Win:         #00ff87 (bright green)
Loss:        #ff4757 (red)
Draw:        #ffa502 (orange)
Primary:     #00d2ff (cyan)
Gold Rating: #ffd32a
```

### Card Layout
```
┌─────────────────────────────────────────┐
│ [WIN/LOSS/DRAW]        2 hours ago      │
│                                         │
│   YOUR CLUB         VS      OPPONENT    │
│      5                          3       │
│                                         │
├─────────────────────────────────────────┤
│                                         │
│ ⭐ TOP PERFORMERS                        │
│                                         │
│ 🥇 #1  Player Name       ⚽2 🅰️1 ⭐8.5  │
│ 🥈 #2  Player Name       ⚽1 🅰️2 ⭐8.2  │
│ 🥉 #3  Player Name       ⚽0 🅰️1 ⭐7.8  │
│                                         │
└─────────────────────────────────────────┘
```

## 🚀 How It Works

### **Automatic Match Posting**
When you finish a match:
1. Bot detects new match from EA API (within 60 seconds)
2. Extracts player stats and match data
3. Generates beautiful stat card image
4. Posts to your configured Discord channel
5. **Fallback**: If card generation fails, uses text embed instead

### **Manual Testing**
Use the `/lastmatches` command to see recent matches
- Bot will generate stat cards for the most recent matches
- Great way to preview the design!

## 📁 File Structure
```
src/utils/
├── stats_cards.py          # Card generation engine
├── embeds.py               # Data preparation
└── ...

data/
└── card_cache/             # Generated cards saved here
    ├── match_20250104_203000.png
    ├── match_20250104_203100.png
    └── ...
```

## 🎯 Technical Details

### **Image Generation**
- Uses **Pillow (PIL)** library
- Draws shapes, gradients, and text dynamically
- Optimized PNG output
- No external image dependencies needed

### **Performance**
- Card generation: ~100-300ms
- Minimal CPU usage
- Cached to disk automatically
- Discord-optimized file size

### **Fallback System**
If card generation fails (missing fonts, PIL issues, etc.), the bot automatically falls back to the traditional text embed system. This ensures **100% reliability**.

## 🎨 Customization

### **Colors**
Edit `COLORS` dict in `src/utils/stats_cards.py`:
```python
COLORS = {
    'win': '#00ff87',      # Change win color
    'loss': '#ff4757',     # Change loss color
    'draw': '#ffa502',     # Change draw color
    # ... etc
}
```

### **Dimensions**
```python
MATCH_CARD_WIDTH = 1200   # Card width
MATCH_CARD_HEIGHT = 800   # Card height
```

### **Fonts**
The system automatically tries to load:
1. **Windows**: Segoe UI
2. **macOS**: SF Pro
3. **Linux**: DejaVu Sans
4. **Fallback**: Default PIL font

To use custom fonts, place .ttf files in `assets/fonts/` and update the `get_font()` function.

## 🐛 Troubleshooting

### **"Could not load custom font"**
- **Solution**: System will use default font, cards still work fine
- **Fix**: Install better fonts or add custom .ttf files

### **Card generation fails**
- Check logs for PIL/Pillow errors
- Ensure Pillow is installed: `pip install Pillow==10.4.0`
- Bot automatically falls back to embed

### **Cards look pixelated**
- Cards are 1200x800px, should look crisp on all devices
- Discord compresses images, but quality should remain high

## 💡 Future Enhancements

Potential additions (not yet implemented):
- Player avatar images
- Club logos/badges
- Animated GIFs for special achievements
- Weekly/monthly stat summary cards
- Leaderboard cards
- Custom team colors based on club

## 🎉 Examples

The bot will automatically generate cards that look like this:

```
╔═══════════════════════════════════════╗
║  [WIN]                  2 hours ago   ║
║                                       ║
║   MIGHTY FC      5-3      RIVALS FC   ║
║                                       ║
║  ⭐ TOP PERFORMERS                     ║
║                                       ║
║  🥇 #1  fredr       ⚽2 🅰️1 ⭐8.5     ║
║  🥈 #2  player2     ⚽1 🅰️2 ⭐8.2     ║
║  🥉 #3  player3     ⚽0 🅰️1 ⭐7.8     ║
╚═══════════════════════════════════════╝
```

---

**Your matches just got a MAJOR upgrade! 🔥**

