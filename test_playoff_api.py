"""
Test script to verify the EA API playoff match fetching works correctly.

Usage:
    python test_playoff_api.py <club_id> [platform]

Example:
    python test_playoff_api.py 12345
    python test_playoff_api.py 12345 common-gen4

Platform defaults to common-gen5 (PS5/XSX/PC).
"""

import asyncio
import sys
import logging
import aiohttp

# Set up logging so we can see what's happening
logging.basicConfig(
    level=logging.DEBUG,
    format="%(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("test_playoff_api")

sys.path.insert(0, "src")
from utils.ea_api import (
    fetch_latest_playoff_match,
    fetch_latest_match,
    EAApiForbiddenError,
    warmup_session,
)


async def run_tests(club_id: int, platform: str):
    async with aiohttp.ClientSession() as session:
        logger.info("=== Warming up session ===")
        await warmup_session(session)

        # --- Test 1: fetch_latest_playoff_match ---
        logger.info("\n=== Test 1: fetch_latest_playoff_match ===")
        try:
            match, mt = await fetch_latest_playoff_match(session, platform, club_id)
            if match:
                match_id = match.get("matchId", "unknown")
                timestamp = match.get("timestamp", "unknown")
                clubs = match.get("clubs", {})
                scores = {cid: c.get("score", "?") for cid, c in clubs.items()}
                print(f"[PASS] Playoff match found!")
                print(f"       match_id  : {match_id}")
                print(f"       timestamp : {timestamp}")
                print(f"       match_type: {mt}")
                print(f"       scores    : {scores}")
            else:
                print("[INFO] No playoff match returned (club may have no playoff history yet)")
        except EAApiForbiddenError as e:
            print(f"[FAIL] EA API returned 403 Forbidden: {e}")
        except Exception as e:
            print(f"[FAIL] Unexpected error: {type(e).__name__}: {e}")

        # --- Test 2: fetch_latest_match (league) for comparison ---
        logger.info("\n=== Test 2: fetch_latest_match (league, for comparison) ===")
        try:
            match, mt = await fetch_latest_match(session, platform, club_id)
            if match:
                match_id = match.get("matchId", "unknown")
                print(f"[PASS] League match found: match_id={match_id}, type={mt}")
            else:
                print("[INFO] No league match returned")
        except EAApiForbiddenError as e:
            print(f"[FAIL] EA API returned 403 Forbidden: {e}")
        except Exception as e:
            print(f"[FAIL] Unexpected error: {type(e).__name__}: {e}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    club_id = int(sys.argv[1])
    platform = sys.argv[2] if len(sys.argv) > 2 else "common-gen5"

    print(f"Testing EA API playoff fetch for club_id={club_id}, platform={platform}\n")
    asyncio.run(run_tests(club_id, platform))


if __name__ == "__main__":
    main()
