## ProClubs Discord Bot (Python)

### Features
- Slash commands: `/topscorer`, `/highestrating`, `/leaderboard`
- Background task posts a brief summary when a new match appears

### Requirements
- Python 3.10+
- A Discord application/bot token

### Setup
1. Clone this repo.
2. Create a virtual environment and activate it (Windows PowerShell):
   - Install Python from `https://www.python.org/downloads/` if not installed.
   - `python -m venv venv`
   - `./venv/Scripts/Activate.ps1`
3. Install dependencies:
   - `pip install -r requirements.txt`
4. Configure environment:
   - Copy `.env.example` to `.env` and fill values:
     - `DISCORD_TOKEN` — bot token from Discord Developer Portal
     - `DISCORD_CHANNEL_ID` — channel ID for match posts
     - `PLATFORM` — e.g. `ps5`, `ps4`, `xboxseriesxs`, `xboxone`, `pc`
     - `CLUB_ID` — your club id (string)
     - `REGION` — `us` or `eu`

### Run
```
cd src
python bot.py
```

Open Discord, in any server where your bot is installed, use:
- `/ping` to test
- `/topscorer`, `/highestrating`, `/leaderboard`

### Notes
- The Pro Clubs endpoints are unofficial and can change; the client uses retries.
- Match watcher checks every 2 minutes; adjust in `src/bot.py` if needed.


