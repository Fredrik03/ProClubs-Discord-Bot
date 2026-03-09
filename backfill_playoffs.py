"""
Backfill playoff matches from the EA API into the database.
Run once to retroactively track playoff matches that were missed.

Usage: python backfill_playoffs.py
"""
import asyncio
import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from utils.ea_api import (
    fetch_json, warmup_session, HTTP_TIMEOUT, interpret_match_result,
)
from database import (
    init_db, record_playoff_match, update_playoff_stats,
    set_last_playoff_match_id, count_playoff_matches,
    get_playoff_stats, get_playoff_club_stats,
)
from config import CLUB_ID, PLATFORM
import aiohttp

GUILD_ID = 1422547301628645389


async def backfill():
    club_id = int(CLUB_ID)
    platform = PLATFORM

    # Ensure DB is ready
    init_db()

    print(f"Backfilling playoffs for guild={GUILD_ID} club={club_id} platform={platform}")
    print("=" * 70)

    async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
        await warmup_session(session)

        # Fetch all available playoff matches
        params = {
            "platform": platform,
            "clubIds": str(club_id),
            "maxResultCount": "50",
            "matchType": "playoffMatch",
        }
        data = await fetch_json(session, "/clubs/matches", params)
        matches = data if isinstance(data, list) else data.get("matches", [])

        if not matches:
            print("No playoff matches found in API!")
            return

        print(f"Found {len(matches)} playoff matches from API")
        print()

        # Process oldest first so last_playoff_match_id ends up as the newest
        matches.reverse()

        for i, match in enumerate(matches):
            match_id = match.get("matchId", "unknown")
            ts = int(match.get("timestamp", 0))
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            playoff_period = dt.strftime("%Y-%m")

            clubs = match.get("clubs", {})
            players_data = match.get("players", {})

            our_club = clubs.get(str(club_id), {})
            if not our_club:
                print(f"  [{i+1}] Match {match_id}: Club {club_id} not found, skipping")
                continue

            # Get result
            result = interpret_match_result(our_club)
            our_score = int(our_club.get("score", 0) or 0)

            # Find opponent
            opp_ids = [cid for cid in clubs if str(cid) != str(club_id)]
            opp_club = clubs.get(opp_ids[0], {}) if opp_ids else {}
            opp_score = int(opp_club.get("score", 0) or 0)
            opp_name = opp_club.get("details", {}).get("name", "Unknown")

            clean_sheet = (opp_score == 0)

            # Record club match result
            record_playoff_match(GUILD_ID, playoff_period, str(match_id), result, our_score, opp_score, clean_sheet)

            # Update player stats
            club_players = players_data.get(str(club_id), {})
            player_names = []
            for pid, pdata in club_players.items():
                if isinstance(pdata, dict):
                    pname = pdata.get("playername", "Unknown")
                    goals = int(pdata.get("goals", 0) or 0)
                    assists = int(pdata.get("assists", 0) or 0)
                    rating = float(pdata.get("rating", 0) or 0)
                    update_playoff_stats(GUILD_ID, pname, playoff_period, goals, assists, rating)
                    player_names.append(pname)

            print(f"  [{i+1}] {dt.strftime('%Y-%m-%d %H:%M')} | {result} {our_score}-{opp_score} vs {opp_name} | Players: {', '.join(player_names)}")

        # Set last_playoff_match_id to the newest match
        newest_id = matches[-1].get("matchId", "")
        set_last_playoff_match_id(GUILD_ID, str(newest_id))
        print(f"\nSet last_playoff_match_id = {newest_id}")

    # Print summary
    print("\n" + "=" * 70)
    print("BACKFILL COMPLETE - Database state:")

    period = datetime.now(timezone.utc).strftime("%Y-%m")
    total = count_playoff_matches(GUILD_ID, period)
    print(f"\nPlayoff period {period}: {total} matches recorded")

    stats = get_playoff_stats(GUILD_ID, period)
    if stats:
        print("\nPlayer Stats:")
        for s in stats:
            avg_r = s['total_rating'] / s['matches_played'] if s['matches_played'] > 0 else 0
            print(f"  {s['player_name']:20s} G:{s['goals']} A:{s['assists']} R:{avg_r:.1f} M:{s['matches_played']} Score:{s['playoff_score']:.1f}")

    club_stats = get_playoff_club_stats(GUILD_ID, period)
    if club_stats:
        print(f"\nClub Stats: {club_stats['wins']}W-{club_stats['losses']}L-{club_stats['draws']}D | GF:{club_stats['goals_for']} GA:{club_stats['goals_against']} CS:{club_stats['clean_sheets']}")


if __name__ == "__main__":
    asyncio.run(backfill())
