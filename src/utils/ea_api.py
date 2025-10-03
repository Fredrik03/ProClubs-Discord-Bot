"""
EA Sports FC Pro Clubs API utilities.
"""
import re
import random
import logging
import aiohttp

logger = logging.getLogger('ProClubsBot.EA_API')

EA_BASE = "https://proclubs.ea.com/api/fc"
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


def platform_from_choice(gen: str | None) -> str:
    """Convert generation choice to platform string."""
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
    """Raw GET request returning JSON."""
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


async def fetch_json(session: aiohttp.ClientSession, path: str, params: dict, max_attempts: int = 3):
    """
    Fetch JSON from EA API with retry logic.
    Raises RuntimeError if all attempts fail.
    """
    url = f"{EA_BASE}{path}"
    last_exc = None

    for attempt in range(1, max_attempts + 1):
        try:
            data = await _get_json(session, url, params)
            return data
        except aiohttp.ClientResponseError as e:
            last_exc = e
            if e.status in (403, 503):
                logger.warning(f"HTTP {e.status} on {path} (attempt {attempt}/{max_attempts})")
                if attempt < max_attempts:
                    await asyncio.sleep(random.uniform(0.5, 1.5))
                    continue
            else:
                logger.warning(f"HTTP {e.status} on {path} (attempt {attempt}/{max_attempts})")
                if attempt < max_attempts:
                    await asyncio.sleep(0.5)
                    continue
                break
        except Exception as e:
            last_exc = e
            logger.warning(f"Error on {path} (attempt {attempt}/{max_attempts}): {e}")
            if attempt < max_attempts:
                await asyncio.sleep(0.5)
                continue
            break

    logger.error(f"All attempts failed for {path}: {last_exc}")
    raise RuntimeError(f"Alle endepunkt-forsøk feilet: {last_exc}")


async def fetch_club_info(session, platform: str, club_id: int):
    """
    Fetch club info. Auto-fallback to other generation if needed.
    Returns (info_dict, used_platform)
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
    Get the newest match from the club's match history.
    Returns (match_dict, match_type_str) or (None, None)
    """
    # Use the working endpoint
    params = {
        "platform": platform,
        "clubIds": str(club_id),
        "matchType": "leagueMatch",
        "maxResultCount": "1"
    }
    
    try:
        payload = await fetch_json(session, "/clubs/matches", params)
        matches = payload if isinstance(payload, list) else payload.get("matches", [])
        if matches and len(matches) > 0:
            newest = matches[0]  # Already sorted by newest
            match_type = "league"
            logger.info(f"Found latest match for club {club_id}")
            return newest, match_type
    except Exception as e:
        logger.warning(f"Failed fetching latest match: {e}")
    
    return None, None


async def fetch_all_matches(session, platform: str, club_id: int, max_count: int = 100):
    """
    Get matches from the club's match history.
    Returns list of match dicts or empty list
    """
    # Use the working endpoint with leagueMatch type
    params = {
        "platform": platform,
        "clubIds": str(club_id),
        "matchType": "leagueMatch",
        "maxResultCount": str(max_count)
    }
    
    try:
        payload = await fetch_json(session, "/clubs/matches", params)
        matches = payload if isinstance(payload, list) else payload.get("matches", [])
        if matches:
            logger.info(f"Found {len(matches)} league matches for club {club_id}")
            return matches
    except Exception as e:
        logger.warning(f"Failed fetching matches: {e}")
    
    return []


def calculate_player_wld(matches, club_id: int, player_name: str):
    """
    Calculate wins/losses/draws for a specific player from match history.
    Returns (wins, losses, draws, matches_played)
    
    Match structure from EA API:
    - clubs: { "clubId": { "result": "1/2/3", "players": {...} } }
    - result: "1" = Win, "2" = Loss, "3" = Draw (probably)
    """
    wins = 0
    losses = 0
    draws = 0
    matches_played = 0
    
    logger.info(f"Analyzing {len(matches)} matches for player '{player_name}' in club {club_id}")
    
    for idx, match in enumerate(matches):
        # Check if player participated in this match
        clubs = match.get("clubs", {})
        
        # Find our club in the match
        our_club = clubs.get(str(club_id))
        if not our_club:
            logger.debug(f"Match {idx+1}: Club {club_id} not found in match")
            continue
        
        # Players are at the TOP level of match, nested by club ID
        # Structure: match.players[clubId][playerId]
        all_players = match.get("players", {})
        club_players = all_players.get(str(club_id), {})
        
        if not club_players:
            logger.debug(f"Match {idx+1}: No players data for club {club_id}")
            continue
            
        player_in_match = False
        
        # Search through club's players
        if isinstance(club_players, dict):
            for player_id, player_data in club_players.items():
                if isinstance(player_data, dict):
                    # Try different field names for player name
                    pname = player_data.get("playername") or player_data.get("name") or player_data.get("playerName")
                    if pname and pname.lower() == player_name.lower():
                        player_in_match = True
                        logger.debug(f"Match {idx+1}: Found player '{pname}'")
                        break
        
        if not player_in_match:
            logger.debug(f"Match {idx+1}: Player '{player_name}' not found in match")
            continue
        
        matches_played += 1
        
        # Determine result
        result = str(our_club.get("result", ""))
        if result == "1":  # Win
            wins += 1
            logger.debug(f"Match {idx+1}: WIN")
        elif result == "2":  # Loss
            losses += 1
            logger.debug(f"Match {idx+1}: LOSS")
        elif result == "3":  # Draw
            draws += 1
            logger.debug(f"Match {idx+1}: DRAW")
        elif result == "4":  # DNF/Forfeit - count as loss
            losses += 1
            logger.debug(f"Match {idx+1}: DNF/FORFEIT (counted as loss)")
        else:
            # Unknown result, don't count but log it
            logger.warning(f"Match {idx+1}: Unknown result '{result}' - skipping")
    
    logger.info(f"Final stats for {player_name}: {wins}W-{losses}L-{draws}D from {matches_played} matches")
    return wins, losses, draws, matches_played


# Import asyncio for sleep
import asyncio


