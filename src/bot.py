import os
import re
import sqlite3
import asyncio
import aiohttp
import random
import logging
import discord
from discord import app_commands
from dotenv import load_dotenv
from pathlib import Path
from datetime import datetime, timezone

# ---------- logging ----------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('ProClubsBot')

# ---------- config ----------
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")  # optional for fast guild sync
DB_PATH = Path(__file__).parent.parent / "guild_settings.sqlite3"

EA_BASE = "https://proclubs.ea.com/api/fc"   # <-- ONLY fc
SITE_URL = "https://proclubs.ea.com/"
SITE_REFERER = "https://proclubs.ea.com/fc/clubs/overview"

# Browser-like headers improve success rate with EA's edge/WAF
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.ea.com/",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

HTTP_TIMEOUT = aiohttp.ClientTimeout(total=12, connect=5)
POLL_INTERVAL_SECONDS = 60
# ----------------------------


# ---------- DB ----------
def init_db():
    logger.info(f"Initializing database at {DB_PATH}")
    try:
        with sqlite3.connect(DB_PATH) as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    guild_id    INTEGER PRIMARY KEY,
                    club_id     INTEGER,
                    platform    TEXT,           -- common-gen5 / common-gen4
                    channel_id  INTEGER,        -- where new matches get posted
                    last_match_id TEXT,         -- last posted matchId
                    autopost    INTEGER DEFAULT 1,
                    updated_at  TEXT NOT NULL
                )
            """)
        logger.info("Database initialized successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}", exc_info=True)
        raise


def upsert_settings(guild_id: int, **fields):
    fields["updated_at"] = datetime.utcnow().isoformat()
    cols = ", ".join(fields.keys())
    qmarks = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{k}=excluded.{k}" for k in fields)
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            f"""
            INSERT INTO settings (guild_id, {cols})
            VALUES (?, {qmarks})
            ON CONFLICT(guild_id) DO UPDATE SET {updates}
            """,
            (guild_id, *fields.values()),
        )


def get_settings(guild_id: int):
    with sqlite3.connect(DB_PATH) as db:
        cur = db.execute(
            "SELECT guild_id, club_id, platform, channel_id, last_match_id, autopost FROM settings WHERE guild_id=?",
            (guild_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        keys = ["guild_id", "club_id", "platform", "channel_id", "last_match_id", "autopost"]
        return dict(zip(keys, row))


def set_last_match_id(guild_id: int, match_id: str):
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            "UPDATE settings SET last_match_id=?, updated_at=? WHERE guild_id=?",
            (match_id, datetime.utcnow().isoformat(), guild_id),
        )
# ------------------------


def platform_from_choice(gen: str | None) -> str:
    g = (gen or "gen5").lower()
    if g in ("gen4", "ps4", "xb1", "last", "old"):
        return "common-gen4"
    return "common-gen5"


def parse_club_id_from_any(s: str) -> int | None:
    """Allow either a numeric ID or an EA URL containing clubId=..."""
    if not s:
        return None
    s = s.strip()
    if s.isdigit():
        return int(s)
    m = re.search(r"[?&]clubId=(\d+)", s)
    if m:
        return int(m.group(1))
    return None


async def _get_json(session: aiohttp.ClientSession, url: str, params: dict):
    async with session.get(url, params=params, headers=HEADERS) as r:
        r.raise_for_status()
        return await r.json()


async def warmup_session(session: aiohttp.ClientSession):
    """
    Hit the HTML pages to pick up cookies (Cloudflare/WAF) before API calls.
    Best-effort: ignore errors.
    """
    html_headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Upgrade-Insecure-Requests": "1",
    }
    try:
        async with session.get(SITE_URL, headers=html_headers) as r:
            await r.text()
    except Exception:
        pass
    try:
        async with session.get(SITE_REFERER, headers=html_headers) as r:
            await r.text()
    except Exception:
        pass


async def fetch_json(session: aiohttp.ClientSession, url_tail: str, params: dict):
    """
    Use ONLY /api/fc; 3 attempts total.
    On 403 (Forbidden), warm up the session (grab cookies) and retry once.
    """
    last_exc = None
    warmed = False
    url = f"{EA_BASE}{url_tail}"
    
    logger.debug(f"Fetching {url} with params {params}")

    for attempt in range(3):
        try:
            result = await asyncio.wait_for(_get_json(session, url, params), timeout=6)
            logger.debug(f"Successfully fetched {url_tail}")
            return result
        except aiohttp.ClientResponseError as e:
            last_exc = e
            logger.warning(f"HTTP {e.status} on {url_tail} (attempt {attempt + 1}/3)")
            if e.status == 403 and not warmed:
                logger.info("Got 403, warming up session and retrying...")
                warmed = True
                await warmup_session(session)
                await asyncio.sleep(0.5)
                continue
            if e.status == 429 and attempt < 2:
                logger.warning(f"Rate limited, waiting {1 + attempt * 2}s")
                await asyncio.sleep(1 + attempt * 2)
                continue
            break
        except asyncio.TimeoutError as e:
            last_exc = e
            logger.warning(f"Timeout on {url_tail} (attempt {attempt + 1}/3)")
            if attempt < 2:
                await asyncio.sleep(1 + attempt * 2)
                continue
        except (aiohttp.ClientError, OSError) as e:
            last_exc = e
            logger.error(f"Network error on {url_tail}: {e}")
            if attempt < 2:
                await asyncio.sleep(1 + attempt * 2)
                continue
    
    logger.error(f"All attempts failed for {url_tail}: {last_exc}")
    raise RuntimeError(f"Alle endepunkt-forsøk feilet: {last_exc}")


async def fetch_club_info(session, platform: str, club_id: int):
    """
    Fetch club info; if invalid on chosen gen, auto-try the other gen.
    Returns (info_json, platform_used)
    """
    try:
        info = await fetch_json(session, "/clubs/info", {"platform": platform, "clubIds": str(club_id)})
        return info, platform
    except Exception:
        other = "common-gen4" if platform == "common-gen5" else "common-gen5"
        info = await fetch_json(session, "/clubs/info", {"platform": other, "clubIds": str(club_id)})
        return info, other


async def fetch_latest_match(session, platform: str, club_id: int):
    """
    Get the newest match across league and playoff.
    Returns (match_dict, match_type_str) or (None, None)
    """
    newest = None
    newest_type = None

    for mt in ("league", "playoff"):
        try:
            payload = await fetch_json(
                session, "/clubs/matches",
                {"platform": platform, "clubIds": str(club_id), "matchType": mt}
            )
        except Exception:
            # fallback to legacy numeric types if needed
            legacy = {"league": "gameType9", "playoff": "gameType13"}[mt]
            try:
                payload = await fetch_json(
                    session, "/clubs/matches",
                    {"platform": platform, "clubIds": str(club_id), "matchType": legacy}
                )
            except Exception:
                continue

        mlist = payload.get("matches", payload) if isinstance(payload, dict) else payload
        if not mlist:
                continue
        for m in mlist:
            ts = m.get("timestamp")
            if not ts:
                mj = m.get("matchJson")
                if isinstance(mj, str) and '"timestamp":' in mj:
                    try:
                        ts = int(re.search(r'"timestamp":\s*(\d+)', mj).group(1))
                    except Exception:
                        ts = None
            if ts is None:
                continue
            if (newest is None) or (ts > newest.get("timestamp", 0)):
                m["timestamp"] = ts
                newest = m
                newest_type = mt

    return newest, newest_type


def utc_to_str(ts: int) -> str:
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return str(ts)


def build_match_embed(club_id: int, platform: str, match: dict, match_type: str, club_name_hint: str | None = None):
    home = match.get("homeName", "Home")
    away = match.get("awayName", "Away")
    hs = match.get("homeScore", "?")
    as_ = match.get("awayScore", "?")
    stadium = match.get("stadium", None)
    when = utc_to_str(match.get("timestamp", 0))

    we_home = match.get("clubIdHome") == club_id
    gf = hs if we_home else as_
    ga = as_ if we_home else hs
    res = "W" if gf > ga else "D" if gf == ga else "L"
    color = 0x2ecc71 if res == "W" else 0xf1c40f if res == "D" else 0xe74c3c

    title = f"{home} {hs}–{as_} {away}"
    desc_lines = [
        f"**Resultat:** {res}",
        f"**Type:** {match_type}",
        f"**Tid:** {when}",
    ]
    if stadium:
        desc_lines.append(f"**Stadion:** {stadium}")
    if club_name_hint:
        desc_lines.append(f"**Klubb:** {club_name_hint} (ID {club_id})")

    embed = discord.Embed(
        title=title,
        description="\n".join(desc_lines),
        color=color
    )
    return embed


class ProClubsBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.poll_task = None

    async def setup_hook(self):
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            print(f"✓ Synket slash-kommandoer til guild {GUILD_ID}")
        else:
            synced = await self.tree.sync()
            print("✓ Synket globalt (kan ta tid første gang)")
        for cmd in synced:
            scope = f"guild {getattr(cmd, 'guild_id', '—')}" if getattr(cmd, 'guild_id', None) else "global"
            print(f"- {cmd.name} ({scope})")

    async def on_ready(self):
        init_db()
        logger.info(f"Bot logged in as {self.user} (ID: {self.user.id})")
        logger.info(f"Database path: {DB_PATH}")
        if not self.poll_task:
            self.poll_task = asyncio.create_task(self.poll_loop())
            logger.info("Started match polling loop")

    async def poll_loop(self):
        await self.wait_until_ready()
        logger.info(f"Poll loop started (interval: {POLL_INTERVAL_SECONDS}s)")
        while not self.is_closed():
            try:
                await self.poll_once_all_guilds()
            except Exception as e:
                logger.error(f"Error in poll loop: {e}", exc_info=True)
            # jitter to avoid hitting EA at the exact same second every minute
            await asyncio.sleep(POLL_INTERVAL_SECONDS + random.uniform(-5, 5))

    async def poll_once_all_guilds(self):
        with sqlite3.connect(DB_PATH) as db:
            cur = db.execute(
                "SELECT guild_id, club_id, platform, channel_id, last_match_id, autopost FROM settings"
            )
            rows = cur.fetchall()

        if not rows:
            return

        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
            # Warm up once per loop (cookies) to reduce 403s
            await warmup_session(session)
            for (guild_id, club_id, platform, channel_id, last_match_id, autopost) in rows:
                if not (club_id and platform and channel_id and autopost):
                    continue
                try:
                    info, used_platform = await fetch_club_info(session, platform, club_id)
                    details = (info.get(str(club_id)) or {}).get("details", {})
                    club_name = details.get("name", f"Club {club_id}")

                    match, mt = await fetch_latest_match(session, used_platform, club_id)
                    if not match or not mt:
                        continue

                    match_id = match.get("matchJson")
                    if isinstance(match_id, str) and '"matchId":"' in match_id:
                        try:
                            match_id = re.search(r'"matchId":"(\d+)"', match_id).group(1)
                        except Exception:
                            match_id = None
                    elif isinstance(match_id, dict):
                        match_id = match_id.get("matchId")
                    match_id = match.get("matchId", match_id)
                    if not match_id:
                        match_id = f"{match.get('timestamp', 0)}:{match.get('homeScore', '?')}-{match.get('awayScore', '?')}"

                    if str(match_id) == str(last_match_id):
                        continue  # already posted

                    channel = self.get_channel(int(channel_id))
                    if channel is None:
                        continue

                    embed = build_match_embed(
                        club_id,
                        used_platform,
                        match,
                        mt,
                        club_name_hint=club_name,
                    )
                    await channel.send(embed=embed)
                    set_last_match_id(guild_id, str(match_id))
                    logger.info(f"Posted new match {match_id} for guild {guild_id}")
                except Exception as e:  # noqa: BLE001
                    logger.error(f"Error polling guild {guild_id}: {e}", exc_info=True)


client = ProClubsBot()

# ---------- Slash commands ----------

@client.tree.command(name="setclub", description="Set the club to track (ID or EA URL) and generation.")
@app_commands.describe(
    club="Enter club ID or paste the EA URL (contains clubId=...)",
    gen="Game generation/platform"
)
@app_commands.choices(
    gen=[
        app_commands.Choice(name="gen5 (PS5/XSX/PC)", value="gen5"),
        app_commands.Choice(name="gen4 (PS4/XB1)", value="gen4"),
    ],
)
async def setclub(interaction: discord.Interaction, club: str, gen: app_commands.Choice[str]):
    await interaction.response.defer(ephemeral=True)
    logger.info(f"User {interaction.user} executing /setclub with club='{club}' gen='{gen.value}'")
    
    parsed_id = parse_club_id_from_any(club)
    if not parsed_id:
        logger.warning(f"Invalid club input: {club}")
        await interaction.followup.send("Invalid input. Provide a number (clubId) or an EA URL containing `clubId=...`.", ephemeral=True)
        return

    platform = platform_from_choice(gen.value)

    try:
        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
            # warm up before info call (reduces 403s)
            await warmup_session(session)
            info, used_platform = await fetch_club_info(session, platform, parsed_id)
            details = (info.get(str(parsed_id)) or {}).get("details", {})
            name = details.get("name", f"Club {parsed_id}")
    except Exception as e:
        logger.error(f"Failed to verify club {parsed_id}: {e}", exc_info=True)
        await interaction.followup.send(f"Could not verify club: `{e}`", ephemeral=True)
        return

    upsert_settings(interaction.guild_id, club_id=parsed_id, platform=used_platform)
    logger.info(f"Guild {interaction.guild_id} set club to {name} (ID: {parsed_id}, platform: {used_platform})")
    await interaction.followup.send(f"✅ Club set to **{name}** (ID `{parsed_id}`) on `{used_platform}`.", ephemeral=True)


@client.tree.command(name="setmatchchannel", description="Choose the channel where new matches will be posted.")
@app_commands.describe(channel="Channel to receive new match posts")
async def setmatchchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    await interaction.response.defer(ephemeral=True)
    st = get_settings(interaction.guild_id)
    if not st or not st.get("club_id"):
        await interaction.followup.send("Set a club first with `/setclub`.", ephemeral=True)
        return

    upsert_settings(interaction.guild_id, channel_id=channel.id, autopost=1)
    await interaction.followup.send(f"✅ New matches will be posted in {channel.mention}.", ephemeral=True)


@client.tree.command(name="postlatest", description="Post the latest match now (manual trigger).")
async def postlatest(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    st = get_settings(interaction.guild_id)
    if not st or not (st.get("club_id") and st.get("platform")):
        await interaction.followup.send("Set a club first with `/setclub`.", ephemeral=True)
        return

    channel_id = st.get("channel_id")
    if not channel_id:
        await interaction.followup.send("Set a channel first with `/setmatchchannel`.", ephemeral=True)
        return

    club_id = int(st["club_id"])
    platform = st["platform"]
    last_match_id = st.get("last_match_id")

    try:
        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
            await warmup_session(session)
            info, used_platform = await fetch_club_info(session, platform, club_id)
            details = (info.get(str(club_id)) or {}).get("details", {})
            club_name = details.get("name", f"Club {club_id}")

            match, mt = await fetch_latest_match(session, used_platform, club_id)
            if not match:
                await interaction.followup.send("No recent matches found.", ephemeral=True)
                return

            match_id = match.get("matchId")
            if not match_id:
                mj = match.get("matchJson")
                if isinstance(mj, str) and '"matchId":"' in mj:
                    try:
                        match_id = re.search(r'"matchId":"(\d+)"', mj).group(1)
                    except Exception:
                        match_id = None
            if not match_id:
                match_id = f"{match.get('timestamp', 0)}:{match.get('homeScore', '?')}-{match.get('awayScore', '?')}"

            channel = client.get_channel(int(channel_id))
            if channel is None:
                await interaction.followup.send("I can't access that channel anymore.", ephemeral=True)
                return

            embed = build_match_embed(club_id, used_platform, match, mt, club_name_hint=club_name)
            await channel.send(embed=embed)
            set_last_match_id(interaction.guild_id, str(match_id))
            await interaction.followup.send("Posted the latest match.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Could not fetch/post the match now: `{e}`", ephemeral=True)


@client.tree.command(name="clubstats", description="Show overall club statistics")
async def clubstats(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    st = get_settings(interaction.guild_id)
    if not st or not (st.get("club_id") and st.get("platform")):
        await interaction.followup.send("Set a club first with `/setclub`.", ephemeral=True)
        return

    club_id = int(st["club_id"])
    platform = st["platform"]

    try:
        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
            await warmup_session(session)

            info, used_platform = await fetch_club_info(session, platform, club_id)
            if isinstance(info, list):
                club_info = next(
                    (entry for entry in info if str(entry.get("clubId")) == str(club_id)),
                    {},
                )
            elif isinstance(info, dict):
                club_info = info.get(str(club_id), {})
            else:
                club_info = {}
            name = club_info.get("name", "Unknown Club")

            overall_data = await fetch_json(
                session,
                "/clubs/overallStats",
                {"clubIds": str(club_id), "platform": used_platform},
            )

            if isinstance(overall_data, list):
                stats = overall_data[0] if overall_data else {}
            else:
                stats = overall_data.get("value") if isinstance(overall_data, dict) else {}
                if isinstance(stats, list) and stats:
                    stats = stats[0]
                elif not isinstance(stats, dict):
                    stats = {}

            wins = int(stats.get("wins", 0) or 0)
            losses = int(stats.get("losses", 0) or 0)
            ties = int(stats.get("ties", 0) or 0)
            total_matches = int(stats.get("gamesPlayed", 0) or 0)
            goals_for = int(stats.get("goals", 0) or 0)
            goals_against = int(stats.get("goalsAgainst", 0) or 0)
            skill_rating = int(stats.get("skillRating", 0) or 0)
            promotions = int(stats.get("promotions", 0) or 0)
            relegations = int(stats.get("relegations", 0) or 0)
            win_streak = int(stats.get("wstreak", 0) or 0)
            unbeaten_streak = int(stats.get("unbeatenstreak", 0) or 0)

            win_pct = (wins / total_matches * 100) if total_matches else 0

            form_map = {"-1": "", "1": "W", "2": "L", "3": "D"}
            recent_form = "".join(form_map.get(str(stats.get(f"lastMatch{i}", "-1")), "") for i in range(5))

            members_data = await fetch_json(
                session,
                "/members/stats",
                {"clubId": str(club_id), "platform": used_platform},
            )

            if isinstance(members_data, list):
                members_list = members_data
            else:
                members_list = (
                    members_data.get("members") if isinstance(members_data, dict) else []
                )

            members = [m for m in members_list if isinstance(m, dict)]
            goals = sum(int(m.get("goals", 0) or 0) for m in members)
            assists = sum(int(m.get("assists", 0) or 0) for m in members)

            embed = discord.Embed(
                title=f"📊 {name}",
                description=f"Skill Rating: **{skill_rating}** | Platform: {used_platform}",
                color=discord.Color.blue(),
            )

            embed.add_field(name="Record", value=f"{wins}W - {losses}L - {ties}D", inline=True)
            embed.add_field(name="Matches", value=str(total_matches), inline=True)
            embed.add_field(name="Win %", value=f"{win_pct:.1f}%", inline=True)
            embed.add_field(name="Goals", value=str(goals), inline=True)
            embed.add_field(name="Assists", value=str(assists), inline=True)
            embed.add_field(name="GA", value=str(goals_against), inline=True)
            embed.add_field(name="Promotions", value=f"↗️ {promotions}", inline=True)
            embed.add_field(name="Relegations", value=f"↘️ {relegations}", inline=True)
            embed.add_field(name="Form (Last 5)", value=recent_form or "N/A", inline=True)

            if win_streak > 0:
                embed.add_field(name="🔥 Win Streak", value=str(win_streak), inline=True)
            if unbeaten_streak > 0:
                embed.add_field(name="🛡️ Unbeaten", value=str(unbeaten_streak), inline=True)

            if members:
                top_scorer = max(members, key=lambda m: int(m.get("goals", 0) or 0))
                top_assister = max(members, key=lambda m: int(m.get("assists", 0) or 0))

                embed.add_field(
                    name="🥇 Top Scorer",
                    value=f"{top_scorer.get('name', 'Unknown')} ({top_scorer.get('goals', 0)} goals)",
                    inline=False,
                )
                embed.add_field(
                    name="🎯 Top Assister",
                    value=f"{top_assister.get('name', 'Unknown')} ({top_assister.get('assists', 0)} assists)",
                    inline=False,
                )

        await interaction.followup.send(embed=embed)
    except Exception as e:  # noqa: BLE001
        await interaction.followup.send(
            f"Could not fetch club stats right now. Error: {e}", ephemeral=True
        )


@client.tree.command(name="playerleaderboard", description="Show top players by goals, assists, or rating")
@app_commands.describe(sort_by="Sort players by this stat")
@app_commands.choices(
    sort_by=[
        app_commands.Choice(name="Goals", value="goals"),
        app_commands.Choice(name="Assists", value="assists"),
        app_commands.Choice(name="Rating", value="rating"),
        app_commands.Choice(name="Matches", value="matches"),
    ]
)
async def playerleaderboard(interaction: discord.Interaction, sort_by: app_commands.Choice[str]):
    await interaction.response.defer(thinking=True)
    st = get_settings(interaction.guild_id)
    if not st or not (st.get("club_id") and st.get("platform")):
        await interaction.followup.send("Set a club first with `/setclub`.", ephemeral=True)
        return

    club_id = int(st["club_id"])
    platform = st["platform"]

    try:
        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
            await warmup_session(session)
            members_data = await fetch_json(
                session,
                "/members/stats",
                {"platform": platform, "clubId": str(club_id)},
            )

            members_list = members_data.get("members", [])
            if not members_list:
                await interaction.followup.send("No player data found for this club.", ephemeral=True)
                return

            players = []
            for member in members_list:
                if not isinstance(member, dict):
                    continue
                goals = int(member.get("goals", 0) or 0)
                assists = int(member.get("assists", 0) or 0)
                rating_val = member.get("ratingAve") or member.get("avgMatchRating") or 0
                try:
                    rating = float(rating_val)
                except Exception:
                    rating = 0.0
                matches = int(member.get("gamesPlayed", 0) or 0)
                players.append(
                    {
                        "name": member.get("name", "Unknown"),
                        "goals": goals,
                        "assists": assists,
                        "rating": rating,
                        "matches": matches,
                    }
                )

            sort_key = sort_by.value
            players.sort(key=lambda p: p[sort_key], reverse=True)

            lines = []
            for i, p in enumerate(players[:10], 1):
                if sort_key == "goals":
                    stat_str = f"{p['goals']} goals"
                elif sort_key == "assists":
                    stat_str = f"{p['assists']} assists"
                elif sort_key == "rating":
                    stat_str = f"{p['rating']:.2f} rating"
                else:
                    stat_str = f"{p['matches']} matches"
                lines.append(f"**{i}.** {p['name']} — {stat_str}")

            embed = discord.Embed(
                title=f"🏆 Top Players by {sort_by.name}",
                description="\n".join(lines) if lines else "No data",
                color=discord.Color.gold()
            )
            await interaction.followup.send(embed=embed)
            logger.info(f"Sent player leaderboard (sorted by {sort_by.value}) for guild {interaction.guild_id}")
    except Exception as e:
        logger.error(f"Error in playerleaderboard command: {e}", exc_info=True)
        await interaction.followup.send(f"Could not fetch player leaderboard: `{e}`", ephemeral=True)


@client.tree.command(name="playerstats", description="Show detailed stats for a specific player")
@app_commands.describe(player_name="Player name (case-insensitive)")
async def playerstats(interaction: discord.Interaction, player_name: str):
    await interaction.response.defer(thinking=True)
    st = get_settings(interaction.guild_id)
    if not st or not (st.get("club_id") and st.get("platform")):
        await interaction.followup.send("Set a club first with `/setclub`.", ephemeral=True)
        return

    club_id = int(st["club_id"])
    platform = st["platform"]

    try:
        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
            await warmup_session(session)
            members_data = await fetch_json(
                session, "/members/stats", {"platform": platform, "clubId": str(club_id)}
            )

            members_list = members_data.get("members", [])
            if not members_list:
                await interaction.followup.send("No player data found.", ephemeral=True)
                return

            # Find player (case-insensitive)
            player_name_lower = player_name.lower()
            found = None
            for member in members_list:
                if member.get("name", "").lower() == player_name_lower:
                    found = member
                    break

            if not found:
                await interaction.followup.send(
                    f"Player '{player_name}' not found in club.", ephemeral=True
                )
                return

            name = found.get("name", "Unknown")
            goals = int(found.get("goals", 0) or 0)
            assists = int(found.get("assists", 0) or 0)
            rating = float(found.get("ratingAve", 0) or 0)
            matches = int(found.get("gamesPlayed", 0) or 0)
            
            # EA FC only provides winRate (%), not raw W/L/D counts
            # /members/career/stats endpoint doesn't work in FC 25
            win_rate = float(found.get("winRate", 0) or 0)
            wins = int(round(matches * (win_rate / 100))) if matches else 0
            losses = matches - wins  # Remaining = losses + draws combined

            clean_sheets = int(
                found.get("cleanSheets", 0)
                or found.get("cleanSheetsDef", 0)
                or found.get("cleanSheetsGK", 0)
                or 0
            )

            pass_success_rate = float(found.get("passSuccessRate", 0) or 0)
            shot_success_rate = float(found.get("shotSuccessRate", 0) or 0)
            man_of_match = int(found.get("manOfTheMatch", 0) or 0)
            red_cards = int(found.get("redCards", 0) or 0)

            embed = discord.Embed(
                title=f"👤 {name}",
                color=discord.Color.green(),
            )
            embed.add_field(name="Matches", value=f"{matches}", inline=True)
            embed.add_field(name="Wins", value=f"{wins}", inline=True)
            embed.add_field(name="Win %", value=f"{win_rate:.1f}%", inline=True)
            embed.add_field(name="Rating", value=f"{rating:.2f}", inline=True)
            embed.add_field(name="Goals", value=f"{goals}", inline=True)
            embed.add_field(name="Assists", value=f"{assists}", inline=True)
            embed.add_field(name="Clean Sheets", value=f"{clean_sheets}", inline=True)
            embed.add_field(name="Pass %", value=f"{pass_success_rate:.1f}%", inline=True)
            embed.add_field(name="Shot %", value=f"{shot_success_rate:.1f}%", inline=True)
            embed.add_field(name="MOTM", value=f"{man_of_match}", inline=True)
            if red_cards > 0:
                embed.add_field(name="🟥 Red Cards", value=f"{red_cards}", inline=True)

            await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"Could not fetch player stats: `{e}`", ephemeral=True)


# ---------- run ----------
if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set DISCORD_TOKEN in your .env")
    init_db()
    client.run(TOKEN)
