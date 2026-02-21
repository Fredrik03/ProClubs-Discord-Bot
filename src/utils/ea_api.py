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
import os
import json
import random
import logging
import asyncio
from urllib.parse import urlencode
import aiohttp

logger = logging.getLogger('ProClubsBot.EA_API')

EA_BASE = "https://proclubs.ea.com/api/fc"
SITE_URL = "https://proclubs.ea.com/"
SITE_REFERER = "https://proclubs.ea.com/"

# Browser-like headers improve success rate with EA's edge/WAF
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://proclubs.ea.com/",
    "Origin": "https://proclubs.ea.com",
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
EA_USE_PLAYWRIGHT = os.getenv("EA_USE_PLAYWRIGHT", "1").lower() in ("1", "true", "yes", "on")
EA_PLAYWRIGHT_TIMEOUT_MS = int(os.getenv("EA_PLAYWRIGHT_TIMEOUT_MS", "12000"))

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except Exception:
    PLAYWRIGHT_AVAILABLE = False

_pw = None
_pw_browser = None
_pw_context = None
_pw_page = None  # Persistent page on EA domain for fetch() calls
_pw_init_lock = None


class EAApiForbiddenError(RuntimeError):
    """Raised when EA API consistently returns HTTP 403."""

    def __init__(self, path: str, message: str):
        super().__init__(message)
        self.path = path


class EAApiHttpError(RuntimeError):
    """HTTP error for EA API requests across transports."""

    def __init__(self, status: int, url: str, message: str):
        super().__init__(message)
        self.status = status
        self.url = url


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


def _build_url(path: str, params: dict) -> str:
    return f"{EA_BASE}{path}?{urlencode(params)}"


async def _ensure_playwright_page():
    """Ensure a persistent Playwright page on the EA domain.

    Uses the system Chrome (channel="chrome") instead of bundled Chromium to
    bypass Akamai bot detection. Keeps a persistent page open on the EA site
    so that API calls can be made via fetch() with valid Akamai cookies.
    """
    global _pw, _pw_browser, _pw_context, _pw_page, _pw_init_lock
    if _pw_page is not None:
        return _pw_page

    if _pw_init_lock is None:
        _pw_init_lock = asyncio.Lock()

    async with _pw_init_lock:
        if _pw_page is not None:
            return _pw_page

        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("Playwright is not installed")

        _pw = await async_playwright().start()

        # Try system Chrome first (harder for Akamai to fingerprint),
        # fall back to bundled Chromium if Chrome isn't installed
        for channel in ("chrome", None):
            try:
                _pw_browser = await _pw.chromium.launch(
                    headless=True,
                    channel=channel,
                    args=[
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-http2",
                        "--disable-blink-features=AutomationControlled",
                    ],
                )
                logger.info(f"[EA API] Launched browser (channel={channel or 'bundled chromium'})")
                break
            except Exception as e:
                if channel is not None:
                    logger.info(f"[EA API] System Chrome not available, trying bundled Chromium: {e}")
                    continue
                raise

        _pw_context = await _pw_browser.new_context(
            user_agent=HEADERS["User-Agent"],
            locale="en-US",
            extra_http_headers={
                "Referer": HEADERS["Referer"],
            },
        )
        # Hide automation indicators from Akamai bot detection
        await _pw_context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            // Remove Playwright/headless indicators
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
        """)

        # Navigate to EA site — Akamai will run its bot detection JS and set cookies
        _pw_page = await _pw_context.new_page()
        try:
            resp = await _pw_page.goto(SITE_URL, wait_until="networkidle", timeout=30000)
            status = resp.status if resp else 'N/A'
            logger.info(f"[EA API] Warmup: visited {SITE_URL} (status {status})")

            if resp and resp.status == 403:
                # Akamai may need time to run its JS challenge and set _abck cookie
                logger.info("[EA API] Got 403, waiting for Akamai bot detection JS...")
                await _pw_page.wait_for_timeout(5000)
                resp2 = await _pw_page.reload(wait_until="networkidle", timeout=15000)
                logger.info(f"[EA API] Warmup reload: status {resp2.status if resp2 else 'N/A'}")
        except Exception as e:
            logger.warning(f"[EA API] Warmup navigation failed (non-fatal): {e}")

        logger.info("[EA API] Playwright transport initialized")
        return _pw_page


async def _get_json(session: aiohttp.ClientSession, url: str, params: dict):
    """Raw GET request returning JSON."""
    async with session.get(url, params=params, headers=HEADERS) as r:
        r.raise_for_status()
        return await r.json()


async def _reset_playwright_page():
    """Reset the persistent page by navigating back to the EA site."""
    global _pw_page
    if _pw_page is not None:
        try:
            await _pw_page.close()
        except Exception:
            pass
        _pw_page = None


async def _get_json_playwright(path: str, params: dict):
    """GET JSON using fetch() from the persistent EA domain page.

    Because the page has already passed Akamai's bot detection and has valid
    cookies (_abck, bm_sz), fetch() calls from within it succeed where direct
    HTTP requests would be blocked with 403.

    If fetch() fails (e.g. stale page context), the page is reset and
    re-initialized on the next call.
    """
    page = await _ensure_playwright_page()
    url = _build_url(path, params)
    try:
        result = await page.evaluate(
            """async (url) => {
                const resp = await fetch(url, {
                    method: 'GET',
                    credentials: 'include',
                    headers: { 'Accept': 'application/json, text/plain, */*' }
                });
                return { status: resp.status, body: await resp.text() };
            }""",
            url,
        )
    except Exception as e:
        # Page context is likely dead/stale — reset it for next attempt
        logger.warning(f"[EA API] fetch() failed, resetting page for next retry: {e}")
        await _reset_playwright_page()
        raise RuntimeError(f"Playwright fetch failed for {url}: {e}")

    status = result["status"]
    body = result["body"]
    logger.debug(f"[EA API] Playwright fetch for {path}: status={status}, body_len={len(body)}")

    if status >= 400:
        raise EAApiHttpError(status, url, f"{status}, body='{body[:200]}'")

    return json.loads(body)


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
    if EA_USE_PLAYWRIGHT and PLAYWRIGHT_AVAILABLE:
        try:
            await _ensure_playwright_page()  # Warmup happens during page init
            logger.debug("[EA API] Playwright warmup complete")
            return
        except Exception as e:
            logger.warning(f"[EA API] Playwright warmup failed, falling back to aiohttp warmup: {e}")
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
            if EA_USE_PLAYWRIGHT and PLAYWRIGHT_AVAILABLE:
                data = await _get_json_playwright(path, params)
            else:
                data = await _get_json(session, url, params)
            logger.info(f"[EA API] ✅ Successfully fetched {path} (attempt {attempt})")
            return data
        except EAApiHttpError as e:
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
    if isinstance(last_exc, aiohttp.ClientResponseError) and last_exc.status == 403:
        raise EAApiForbiddenError(path, f"EA API forbidden after {max_attempts} attempts: {last_exc}")
    if isinstance(last_exc, EAApiHttpError) and last_exc.status == 403:
        raise EAApiForbiddenError(path, f"EA API forbidden after {max_attempts} attempts: {last_exc}")
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
    except EAApiForbiddenError:
        # Do not try fallback platform when blocked by WAF; that only increases blocked traffic.
        raise
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
    
    EA API may require matchType parameter. Try different match types:
    - leagueMatch (league matches)
    - gameType11 (general matches)
    - playoffMatch (playoff matches)
    - No matchType (all matches)
    
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
    
    # Try different endpoint paths and match types - EA API may have changed
    endpoint_attempts = [
        "/clubs/matches",  # Current endpoint
        "/matches",        # Alternative endpoint (without /clubs prefix)
    ]
    
    match_type_attempts = [
        "leagueMatch",  # Try league matches first (most common)
        "gameType11",   # Generic match type
        None,           # No matchType (all matches)
        "playoffMatch", # Playoff matches
    ]
    
    for endpoint_path in endpoint_attempts:
        for match_type_attempt in match_type_attempts:
            # EA API parameters for fetching matches
            params = {
                "platform": platform,
                "clubIds": str(club_id),
                "maxResultCount": "1"  # Only get the most recent match
            }
            
            # Add matchType if specified
            if match_type_attempt:
                params["matchType"] = match_type_attempt
            
            try:
                logger.debug(f"[EA API] Attempting {endpoint_path} with matchType={match_type_attempt or 'none'}")
                payload = await fetch_json(session, endpoint_path, params)
                
                # EA API may return different formats
                matches = payload if isinstance(payload, list) else payload.get("matches", [])
                
                if matches and len(matches) > 0:
                    newest = matches[0]  # Matches are pre-sorted by EA API (newest first)
                    
                    # Detect match type from the response data
                    # Try to determine if it's a playoff match or league match
                    match_type = newest.get("matchType", "unknown")
                    if match_type == "unknown":
                        # Use the matchType we used to fetch if available
                        match_type = match_type_attempt or "league"
                    
                    # Log match details for debugging
                    match_id = newest.get("matchId", "unknown")
                    timestamp = newest.get("timestamp", 0)
                    logger.info(f"[EA API] ✅ Found latest match for club {club_id}: match_id={match_id}, timestamp={timestamp}, type={match_type}")
                    return newest, match_type
                else:
                    logger.debug(f"[EA API] No matches found for club {club_id} with {endpoint_path} and matchType={match_type_attempt or 'none'}")
                    continue  # Try next matchType
            except RuntimeError as e:
                # HTTP 400 or other API errors - try next matchType or endpoint
                error_msg = str(e)
                if "400" in error_msg or "Bad Request" in error_msg:
                    logger.debug(f"[EA API] HTTP 400 with {endpoint_path} and matchType={match_type_attempt or 'none'}, trying next option...")
                    continue
                else:
                    # Other errors (network, etc.) - log and try next
                    logger.debug(f"[EA API] Error with {endpoint_path} and matchType={match_type_attempt or 'none'}: {e}, trying next option...")
                    continue
            except EAApiForbiddenError:
                # Hard WAF block, do not spam every endpoint/matchType combination.
                raise
            except Exception as e:
                # Other unexpected errors - log and try next
                logger.debug(f"[EA API] Unexpected error with {endpoint_path} and matchType={match_type_attempt or 'none'}: {e}, trying next option...")
                continue
    
    # All attempts failed
    logger.error(f"[EA API] ❌ All endpoint and matchType combinations failed for club {club_id}")
    return None, None


async def fetch_latest_playoff_match(session, platform: str, club_id: int):
    """
    Fetch the latest playoff match specifically.
    Uses matchType=playoffMatch to only get playoff matches,
    separate from the league match fetch.

    Returns:
        Tuple of (match_dict, "playoffMatch") or (None, None) if no playoff matches found
    """
    logger.debug(f"[EA API] Fetching latest playoff match for club {club_id} on platform {platform}")

    params = {
        "platform": platform,
        "clubIds": str(club_id),
        "maxResultCount": "1",
        "matchType": "playoffMatch"
    }

    try:
        payload = await fetch_json(session, "/clubs/matches", params)
        matches = payload if isinstance(payload, list) else payload.get("matches", [])

        if matches and len(matches) > 0:
            newest = matches[0]
            match_id = newest.get("matchId", "unknown")
            logger.info(f"[EA API] ✅ Found latest playoff match for club {club_id}: match_id={match_id}")
            return newest, "playoffMatch"
        else:
            logger.debug(f"[EA API] No playoff matches found for club {club_id}")
            return None, None
    except Exception as e:
        logger.debug(f"[EA API] Failed to fetch playoff matches for club {club_id}: {e}")
        return None, None


async def fetch_all_matches(session, platform: str, club_id: int, max_count: int = 100, match_type: str = None):
    """
    Get matches from the club's match history.
    Returns list of match dicts or empty list

    Args:
        session: aiohttp ClientSession
        platform: Platform string (e.g., "common-gen5" or "common-gen4")
        club_id: Numeric club ID
        max_count: Maximum number of matches to fetch
        match_type: Optional match type filter (e.g., "leagueMatch", "playoffMatch").
                    When None, multiple match types are tried automatically.
    """
    endpoint_attempts = [
        "/clubs/matches",
        "/matches",
    ]

    # If a specific match type is requested, only try that one; otherwise try known types
    if match_type:
        match_type_attempts = [match_type]
    else:
        match_type_attempts = [
            "leagueMatch",
            "gameType11",
            None,
            "playoffMatch",
        ]

    for endpoint_path in endpoint_attempts:
        for mt in match_type_attempts:
            params = {
                "platform": platform,
                "clubIds": str(club_id),
                "maxResultCount": str(max_count),
            }
            if mt:
                params["matchType"] = mt

            try:
                logger.debug(f"[EA API] fetch_all_matches: trying {endpoint_path} matchType={mt or 'none'}")
                payload = await fetch_json(session, endpoint_path, params)
                matches = payload if isinstance(payload, list) else payload.get("matches", [])
                if matches:
                    logger.info(f"Found {len(matches)} matches for club {club_id} (endpoint={endpoint_path}, type={mt or 'none'})")
                    return matches
                logger.debug(f"[EA API] fetch_all_matches: no matches returned for {endpoint_path} matchType={mt or 'none'}")
            except EAApiForbiddenError:
                raise
            except Exception as e:
                logger.warning(f"[EA API] fetch_all_matches: failed for {endpoint_path} matchType={mt or 'none'}: {e}")

    logger.error(f"[EA API] ❌ fetch_all_matches: all attempts exhausted for club {club_id}")
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




