"""
EA Sports FC Pro Clubs API utilities.

This module handles all communication with EA's undocumented Pro Clubs API.

API DETAILS:
------------
Base URL: https://proclubs.ea.com/api/fc
The EA Pro Clubs API is undocumented and community-discovered.
Response formats can vary and may change without notice.

KEY FEATURES:
-------------
1. Session Warmup:
   - Visits EA's HTML pages before API calls
   - Acquires cookies and passes Cloudflare/WAF checks
   - Significantly reduces 403 Forbidden errors

2. Retry Logic:
   - All API calls include automatic retry (default: 3 attempts)
   - Exponential backoff with jitter for rate limiting
   - Detailed error logging for debugging

3. Platform Fallback:
   - Automatically tries other generation if first fails
   - Handles common-gen5 (PS5/XSX/PC) and common-gen4 (PS4/XB1)

4. Response Normalization:
   - EA API returns inconsistent formats (list/dict/nested)
   - Functions normalize responses for easier consumption

IMPORTANT ENDPOINTS:
--------------------
- /clubs/info: Get club details (name, stats, etc.)
- /clubs/matches: Get match history
- /members/stats: Get player statistics
- /clubs/overallStats: Get overall club statistics

LOGGING:
--------
All API operations are logged with [EA API] prefix:
- ✅ Successful requests
- ⚠️ Warnings (retries, HTTP errors)
- ❌ Failed requests after all retries

COMMON ISSUES:
--------------
1. HTTP 403: Rate limiting or WAF block
   - Solution: Session warmup and retry with backoff
2. HTTP 404: Club doesn't exist or wrong platform
   - Solution: Platform fallback logic
3. Empty responses: Club has no data yet
   - Solution: Graceful handling with None returns
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
    Warm up the session by visiting EA's HTML pages.
    This helps acquire cookies and pass through Cloudflare/WAF checks,
    reducing the likelihood of 403 Forbidden errors on API calls.
    
    Best-effort operation: errors are silently ignored.
    
    Args:
        session: aiohttp ClientSession to warm up
    """
    logger.debug("[EA API] Warming up session by visiting EA's website...")
    html_headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Upgrade-Insecure-Requests": "1",
    }
    
    # Visit main EA site
    try:
        async with session.get(SITE_URL, headers=html_headers) as r:
            await r.text()
            logger.debug(f"[EA API] Warmup: visited {SITE_URL} (status: {r.status})")
    except Exception as e:
        logger.debug(f"[EA API] Warmup: failed to visit {SITE_URL}: {e}")
    
    # Visit Pro Clubs page
    try:
        async with session.get(SITE_REFERER, headers=html_headers) as r:
            await r.text()
            logger.debug(f"[EA API] Warmup: visited {SITE_REFERER} (status: {r.status})")
    except Exception as e:
        logger.debug(f"[EA API] Warmup: failed to visit {SITE_REFERER}: {e}")
    
    logger.debug("[EA API] Session warmup complete")


async def fetch_json(session: aiohttp.ClientSession, path: str, params: dict, max_attempts: int = 3):
    """
    Fetch JSON from EA API with retry logic.
    
    Args:
        session: aiohttp ClientSession to use for the request
        path: API endpoint path (e.g., "/clubs/info")
        params: Query parameters as a dictionary
        max_attempts: Maximum number of retry attempts (default: 3)
    
    Returns:
        JSON response data as dict/list
        
    Raises:
        RuntimeError: If all retry attempts fail
    """
    url = f"{EA_BASE}{path}"
    last_exc = None
    
    logger.debug(f"[EA API] Fetching {path} with params: {params}")

    for attempt in range(1, max_attempts + 1):
        try:
            logger.debug(f"[EA API] Attempt {attempt}/{max_attempts} for {path}")
            data = await _get_json(session, url, params)
            logger.info(f"[EA API] ✅ Successfully fetched {path} (attempt {attempt})")
            return data
        except aiohttp.ClientResponseError as e:
            last_exc = e
            if e.status in (403, 503):
                # 403 Forbidden / 503 Service Unavailable - likely rate limiting or WAF
                logger.warning(f"[EA API] ⚠️ HTTP {e.status} on {path} (attempt {attempt}/{max_attempts}) - possible rate limit or WAF block")
                if attempt < max_attempts:
                    sleep_time = random.uniform(0.5, 1.5)
                    logger.debug(f"[EA API] Waiting {sleep_time:.2f}s before retry...")
                    await asyncio.sleep(sleep_time)
                    continue
            else:
                # Other HTTP errors (404, 500, etc.)
                logger.warning(f"[EA API] ⚠️ HTTP {e.status} on {path} (attempt {attempt}/{max_attempts})")
                if attempt < max_attempts:
                    logger.debug(f"[EA API] Waiting 0.5s before retry...")
                    await asyncio.sleep(0.5)
                    continue
                break
        except Exception as e:
            # Network errors, timeouts, JSON parse errors, etc.
            last_exc = e
            logger.warning(f"[EA API] ⚠️ Error on {path} (attempt {attempt}/{max_attempts}): {type(e).__name__}: {e}")
            if attempt < max_attempts:
                logger.debug(f"[EA API] Waiting 0.5s before retry...")
                await asyncio.sleep(0.5)
                continue
            break

    logger.error(f"[EA API] ❌ All {max_attempts} attempts failed for {path}: {last_exc}")
    raise RuntimeError(f"EA API request failed after {max_attempts} attempts: {last_exc}")


async def fetch_club_info(session, platform: str, club_id: int):
    """
    Fetch club information from EA API.
    Automatically falls back to the other generation platform if the first attempt fails.
    
    Args:
        session: aiohttp ClientSession
        platform: Platform string (e.g., "common-gen5" or "common-gen4")
        club_id: Numeric club ID
    
    Returns:
        Tuple of (info_dict, used_platform)
        - info_dict: Club information from EA API
        - used_platform: The platform that worked (may differ from input if fallback occurred)
    """
    logger.debug(f"[EA API] Fetching club info for club {club_id} on platform {platform}")
    try:
        info = await fetch_json(session, "/clubs/info", {"platform": platform, "clubIds": str(club_id)})
        logger.info(f"[EA API] ✅ Successfully fetched club info for {club_id} on {platform}")
        return info, platform
    except Exception as e:
        # Try the other generation platform
        other = "common-gen4" if platform == "common-gen5" else "common-gen5"
        logger.warning(f"[EA API] Failed to fetch club info on {platform}, trying fallback platform {other}")
        info = await fetch_json(session, "/clubs/info", {"platform": other, "clubIds": str(club_id)})
        logger.info(f"[EA API] ✅ Successfully fetched club info for {club_id} on fallback platform {other}")
        return info, other


async def fetch_latest_match(session, platform: str, club_id: int):
    """
    Get the newest match from the club's match history.
    
    Args:
        session: aiohttp ClientSession
        platform: Platform string (e.g., "common-gen5" or "common-gen4")
        club_id: Numeric club ID
    
    Returns:
        Tuple of (match_dict, match_type_str) or (None, None) if no matches found
        - match_dict: Match data from EA API
        - match_type_str: Type of match (e.g., "league")
    """
    logger.debug(f"[EA API] Fetching latest match for club {club_id} on platform {platform}")
    
    # EA API parameters for fetching matches
    params = {
        "platform": platform,
        "clubIds": str(club_id),
        "matchType": "leagueMatch",  # Only fetch league matches
        "maxResultCount": "1"  # Only get the most recent match
    }
    
    try:
        payload = await fetch_json(session, "/clubs/matches", params)
        
        # EA API may return different formats
        matches = payload if isinstance(payload, list) else payload.get("matches", [])
        
        if matches and len(matches) > 0:
            newest = matches[0]  # Matches are pre-sorted by EA API (newest first)
            match_type = "league"
            
            # Log match details for debugging
            match_id = newest.get("matchId", "unknown")
            timestamp = newest.get("timestamp", 0)
            logger.info(f"[EA API] ✅ Found latest match for club {club_id}: match_id={match_id}, timestamp={timestamp}")
            return newest, match_type
        else:
            logger.debug(f"[EA API] No matches found for club {club_id}")
    except Exception as e:
        logger.error(f"[EA API] ❌ Failed fetching latest match for club {club_id}: {e}", exc_info=True)
    
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


