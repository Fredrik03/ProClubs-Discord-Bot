# EA FC Pro Clubs Discord Bot

A Discord bot that tracks EA FC Pro Clubs matches and displays team & player statistics.

## Features

### 📊 Statistics Commands
- `/clubstats` — Overall club statistics (record, skill rating, promotions, relegations, form, top scorer/assister)
- `/playerleaderboard` — Top 10 players by goals, assists, rating, or matches
- `/playerstats <player>` — Detailed stats for a specific player (goals, assists, rating, pass %, shot %, MOTM, etc.)

### ⚙️ Setup Commands
- `/setclub <club_id> <generation>` — Set the club to track (accepts club ID or EA URL)
- `/setmatchchannel <channel>` — Choose where new match results are posted
- `/postlatest` — Manually post the latest match result

### 🔄 Auto Match Posting
- Automatically polls for new matches every 60 seconds
- Posts match results to the configured channel with:
  - Score, result (W/D/L)
  - Match type (league/playoff)
  - Timestamp and stadium

### 📝 Comprehensive Logging
- Detailed logs for all API requests, retries, and errors
- Easy debugging with timestamped, structured log messages

## Requirements

- **Python 3.10+**
- Discord bot token from [Discord Developer Portal](https://discord.com/developers/applications)

## Setup

### 1. Clone the Repository
```bash
git clone <your-repo-url>
cd ProClubs-Discord-Bot
```

### 2. Create Virtual Environment (Windows PowerShell)
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Create a `.env` file in the project root:
```env
DISCORD_TOKEN=your_bot_token_here
GUILD_ID=your_guild_id_here  # Optional: for faster command sync during development
```

### 5. Run the Bot
```bash
cd src
python bot.py
```

## Usage

1. **Invite the bot** to your Discord server with the required permissions (Send Messages, Embed Links)
2. **Set up your club:**
   ```
   /setclub club: 669174 gen: gen5
   ```
   Or use an EA URL:
   ```
   /setclub club: https://proclubs.ea.com/fc/clubs/overview?clubId=669174 gen: gen5
   ```
3. **Configure match posting:**
   ```
   /setmatchchannel channel: #proclubs-matches
   ```
4. **Check stats:**
   ```
   /clubstats
   /playerleaderboard sort_by: goals
   /playerstats player: YourPlayerName
   ```

## Commands Reference

| Command | Description | Example |
|---------|-------------|---------|
| `/setclub` | Set club to track | `/setclub club: 669174 gen: gen5` |
| `/setmatchchannel` | Set match results channel | `/setmatchchannel channel: #matches` |
| `/postlatest` | Manually post latest match | `/postlatest` |
| `/clubstats` | View overall club stats | `/clubstats` |
| `/playerleaderboard` | Top 10 players by stat | `/playerleaderboard sort_by: goals` |
| `/playerstats` | Individual player stats | `/playerstats player: FredrikSD` |

## Generation/Platform Options

- **gen5** — PS5, Xbox Series X/S, PC (common-gen5)
- **gen4** — PS4, Xbox One (common-gen4)

## Technical Details

### EA API Endpoints Used
- `/api/fc/clubs/info` — Club information
- `/api/fc/clubs/matches` — Match history
- `/api/fc/clubs/overallStats` — Club statistics
- `/api/fc/members/stats` — Player statistics

### Database
- SQLite database (`guild_settings.sqlite3`) stores:
  - Guild settings (club ID, platform, channel)
  - Last posted match ID (prevents duplicates)

### Logging
Logs include:
- API request/response details
- Retry attempts and failures
- Command execution
- Match posting events
- Full stack traces for errors

## Troubleshooting

### Bot isn't posting matches
- Check that `/setclub` and `/setmatchchannel` are configured
- Verify the bot has permissions in the target channel
- Check logs for API errors

### "Could not verify club" error
- Ensure the club ID is correct
- Try the opposite generation (gen4 vs gen5)
- Check EA servers are online

### Commands not appearing
- Make sure `GUILD_ID` is set in `.env` for fast sync
- Wait a few minutes for global command sync
- Check bot has `applications.commands` scope

## Notes

- EA's Pro Clubs API is undocumented and community-discovered
- Endpoints may change without notice
- The bot includes retry logic and session warming to handle 403/429 errors
- Match polling interval: 60 seconds (±5s jitter)

## License

MIT

## Credits

Built with:
- [discord.py](https://github.com/Rapptz/discord.py)
- [aiohttp](https://github.com/aio-libs/aiohttp)
- EA FC Pro Clubs unofficial API
