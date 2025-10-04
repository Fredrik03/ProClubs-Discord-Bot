# EA FC 26 Pro Clubs Discord Bot

A feature-rich Discord bot that tracks EA FC 26 Pro Clubs matches and displays comprehensive team & player statistics with **stunning visual stat cards** and detailed embeds.

## ✨ Features

### 🎨 **NEW: Beautiful Stat Cards!**

Match results are now posted as **gorgeous image-based stat cards** featuring:
- 🎯 Modern dark theme with gradient backgrounds
- 🏆 Color-coded results (🟢 Win / 🔴 Loss / 🟡 Draw)
- 👥 Top 3 performers with 🥇🥈🥉 medals
- ⚽ Goals, 🅰️ Assists, ⭐ Ratings for each player
- 🏅 Special MOTM (Man of the Match) badge
- 📱 1200x800px resolution, perfect for Discord

**Preview**: Use `/testcard` to see a demo!
**Documentation**: See [STAT_CARDS.md](docs/STAT_CARDS.md) for details

### 📊 Statistics Commands

**`/clubstats`** — Overall club statistics including:
- Win/Loss/Draw record and win percentage
- Skill rating, promotions, relegations
- Recent form (last 5 matches: W/L/D)
- Top scorer and top assister
- Goals for/against, win streaks
- Milestone tracking integration

**`/playerstats <player>`** — Detailed individual player stats:
- Matches played and win percentage
- Goals, assists, and per-game averages
- Average match rating and MOTM awards
- Pass accuracy with completion stats
- Shot accuracy percentage
- Tackles made and success rate
- Clean sheets (for defenders/GKs)
- Disciplinary record (red cards)

**`/leaderboard <category>`** — Top 10 players ranked by:
- ⚽ Goals
- 🅰️ Assists
- 🎮 Matches Played
- ⭐ Man of the Match
- 📊 Average Rating
- 🎯 Pass Accuracy
- 📈 Goals Per Game
- 📈 Assists Per Game

**`/lastmatches`** — Recent match history showing:
- Last 5 league matches
- Scores and results (✅ Win / ❌ Loss / 🤝 Draw)
- Opponent names
- Time ago
- ⭐ Highest rated player per match

### ⚙️ Setup Commands

- **`/setclub <club_id> <generation>`** — Set the club to track (accepts club ID or EA URL)
- **`/setmatchchannel <channel>`** — Configure automatic match result posting
- **`/setmilestonechannel <channel>`** — Set channel for milestone announcements
- **`/testcard`** — 🎨 Preview a demo stat card to see the design

### 🔄 Auto Match Posting

Automatically polls for new matches every 60 seconds and posts **beautiful stat cards** with:
- 🎨 **Stunning visual cards** with gradient backgrounds and modern design
- 🎯 Final score and result (color-coded: 🟢 Win / 🔴 Loss / 🟡 Draw)
- 👥 Top 3 performers with medals 🥇🥈🥉
- ⚽ Goals, 🅰️ Assists, ⭐ Ratings for each player
- 🏅 Man of the Match badge
- 🕐 Match timestamp and platform
- ✅ **Automatic fallback** to text embeds if card generation fails

### 🏆 Milestone Tracking

Automatically tracks and announces player milestones:
- ⚽ Goals: 1, 10, 25, 50, 100, 250, 500
- 🅰️ Assists: 1, 10, 25, 50, 100, 250, 500
- 🎮 Matches: 1, 10, 25, 50, 100, 250, 500
- ⭐ Man of the Match: 1, 5, 10, 25, 50, 100

### 📝 Comprehensive Logging

- Detailed logs for all API requests, retries, and errors
- Easy debugging with timestamped, structured log messages
- Match posting events and milestone achievements

## 🚀 Quick Start

### Option 1: Docker (Recommended)

**Prerequisites:**
- Docker and Docker Compose installed
- Discord bot token from [Discord Developer Portal](https://discord.com/developers/applications)

**Steps:**

1. **Clone the repository:**
```bash
git clone https://github.com/Fredrik03/ProClubs-Discord-Bot.git
cd ProClubs-Discord-Bot
```

2. **Create `.env` file:**
```env
DISCORD_TOKEN=your_bot_token_here
GUILD_ID=your_guild_id_here
```

3. **Start with Docker Compose:**
```bash
docker-compose -f docker-compose.simple.yml up -d
```

4. **View logs:**
```bash
docker-compose logs -f proclubs-bot
```

**To update after pushing to GitHub:**
```bash
docker-compose -f docker-compose.simple.yml up -d --build
```

### Option 2: Python (Local Development)

**Prerequisites:**
- Python 3.10 or higher

**Steps:**

1. **Clone the repository:**
```bash
git clone https://github.com/Fredrik03/ProClubs-Discord-Bot.git
cd ProClubs-Discord-Bot
```

2. **Create virtual environment:**
```bash
# Windows PowerShell
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Linux/Mac
python3 -m venv .venv
source .venv/bin/activate
```

3. **Install dependencies:**
```bash
pip install -r requirements.txt
```

4. **Create `.env` file:**
```env
DISCORD_TOKEN=your_bot_token_here
GUILD_ID=your_guild_id_here
```

5. **Run the bot:**
```bash
python src/bot_new.py
```

## 📖 Usage Guide

### Initial Setup

1. **Invite the bot** to your Discord server with required permissions:
   - Send Messages
   - Embed Links
   - Use Application Commands

2. **Get your Guild ID** (Server ID):
   - Enable Developer Mode in Discord (Settings → Advanced)
   - Right-click your server → Copy Server ID

3. **Set up your club:**
   ```
   /setclub club: 669174 gen: gen5
   ```
   Or paste an EA URL:
   ```
   /setclub club: https://proclubs.ea.com/fc/clubs/overview?clubId=669174 gen: gen5
   ```

4. **Configure match posting:**
   ```
   /setmatchchannel channel: #proclubs-matches
   ```

5. **Optional - Set milestone channel:**
   ```
   /setmilestonechannel channel: #milestones
   ```

### Using Commands

**View club stats:**
```
/clubstats
```

**Check player performance:**
```
/playerstats player: FredrikSD
```

**See leaderboards:**
```
/leaderboard category: Goals ⚽
/leaderboard category: Average Rating 📊
```

**View recent matches:**
```
/lastmatches
```

## 📋 Commands Reference

| Command | Description | Example |
|---------|-------------|---------|
| `/setclub` | Set club to track | `/setclub club: 669174 gen: gen5` |
| `/setmatchchannel` | Set match results channel | `/setmatchchannel channel: #matches` |
| `/setmilestonechannel` | Set milestone channel | `/setmilestonechannel channel: #milestones` |
| `/clubstats` | View overall club stats | `/clubstats` |
| `/playerstats` | Individual player stats | `/playerstats player: FredrikSD` |
| `/leaderboard` | Top 10 players by stat | `/leaderboard category: Goals ⚽` |
| `/lastmatches` | Last 5 matches | `/lastmatches` |

## ⚙️ Configuration

### Generation/Platform Options

- **gen5** — PS5, Xbox Series X/S, PC (common-gen5)
- **gen4** — PS4, Xbox One (common-gen4)

The bot will automatically detect and use the correct platform for your club.

## 🔧 Technical Details

### EA API Endpoints Used

- `/api/fc/clubs/info` — Club information
- `/api/fc/clubs/matches?matchType=leagueMatch` — Match history (league matches)
- `/api/fc/clubs/overallStats` — Club statistics
- `/api/fc/members/stats` — Player statistics
- `/api/fc/members/career/stats` — Career stats

### Database

SQLite database (`guild_settings.sqlite3`) stores:
- Guild settings (club ID, platform, channels)
- Last posted match ID (prevents duplicates)
- Player milestone progress

### Architecture

- **Modular design** with separate utilities for API calls and embeds
- **Async/await** for efficient API requests
- **Session warmup** to reduce 403 errors from EA's WAF
- **Retry logic** with exponential backoff for failed requests
- **Background polling** for automatic match detection

## 🐛 Troubleshooting

### Bot isn't posting matches
- Verify `/setclub` and `/setmatchchannel` are configured
- Check the bot has permissions in the target channel
- View logs for API errors
- Ensure the club is actively playing matches

### "Could not verify club" error
- Double-check the club ID is correct
- Try the opposite generation (gen4 vs gen5)
- Verify EA servers are online at https://proclubs.ea.com

### Commands not appearing in Discord
- Ensure `GUILD_ID` is set correctly for instant sync
- Wait up to 1 hour for global command sync (if GUILD_ID not set)
- Verify bot has `applications.commands` scope
- Check bot logs for command sync confirmation

### Player stats showing 0 for everything
- Ensure the player name matches exactly (case-insensitive)
- Player must be registered with the club
- Check EA API is returning data (view bot logs)

## 📝 Notes

- EA's Pro Clubs API is **undocumented** and **community-discovered**
- Endpoints may change without notice during EA updates
- The bot includes retry logic and session warming to handle 403/429 errors
- Match polling interval: 60 seconds
- Match history limited to last 100 matches by EA API

## 🐳 Docker Deployment

### Using Docker Compose (Recommended)

The bot includes Docker support for easy deployment:

```bash
# Build and start
docker-compose -f docker-compose.simple.yml up -d

# View logs
docker-compose logs -f proclubs-bot

# Stop
docker-compose down

# Update and restart
docker-compose -f docker-compose.simple.yml up -d --build
```

### Environment Variables

Required:
- `DISCORD_TOKEN` — Your Discord bot token
- `GUILD_ID` — Your Discord server ID (for instant command sync)

Optional:
- `TZ` — Timezone (default: Europe/Oslo)

## 📦 Requirements

**Python Dependencies:**
- discord.py >= 2.0
- aiohttp
- python-dotenv

See `requirements.txt` for full list.

## 🤝 Contributing

Contributions are welcome! Feel free to:
- Report bugs
- Suggest new features
- Submit pull requests

## 📄 License

MIT License - See LICENSE file for details

## 🙏 Credits

Built with:
- [discord.py](https://github.com/Rapptz/discord.py) - Discord API wrapper
- [aiohttp](https://github.com/aio-libs/aiohttp) - Async HTTP client
- EA FC Pro Clubs unofficial API

## ⚠️ Disclaimer

This bot uses unofficial EA Sports FC API endpoints that are not publicly documented. EA Sports has not officially endorsed this project, and the API endpoints may change or break at any time. Use at your own risk.
