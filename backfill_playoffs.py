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
    get_all_guild_settings,
)
import aiohttp


async def backfill():
    # Ensure DB is ready
    init_db()

    # Read all guilds from the database
    rows = get_all_guild_settings()
    if not rows:
        print("No guilds configured in database!")
        return

    async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
        await warmup_session(session)

        for (guild_id, club_id, platform, channel_id, last_match_id, autopost) in rows:
            if not club_id or not platform:
                continue

            print(f"\nBackfilling guild={guild_id} club={club_id} platform={platform}")
            print("=" * 70)

            # Fetch all available playoff matches
            params = {
                "platform": platform,
                "clubIds": str(club_id),
                "maxResultCount": "50",
                "matchType": "playoffMatch",
            }
            try:
                data = await fetch_json(session, "/clubs/matches", params)
            except Exception as e:
                print(f"  ERROR fetching playoff matches: {e}")
                continue

            matches = data if isinstance(data, list) else data.get("matches", [])

            if not matches:
                print("  No playoff matches found in API!")
                continue

            print(f"  Found {len(matches)} playoff matches from API")

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

                # Record club match result (INSERT OR IGNORE so re-runs are safe)
                record_playoff_match(guild_id, playoff_period, str(match_id), result, our_score, opp_score, clean_sheet)

                # Update player stats
                club_players = players_data.get(str(club_id), {})
                player_names = []
                for pid, pdata in club_players.items():
                    if isinstance(pdata, dict):
                        pname = pdata.get("playername", "Unknown")
                        goals = int(pdata.get("goals", 0) or 0)
                        assists = int(pdata.get("assists", 0) or 0)
                        rating = float(pdata.get("rating", 0) or 0)
                        update_playoff_stats(guild_id, pname, playoff_period, goals, assists, rating)
                        player_names.append(pname)

                print(f"  [{i+1}] {dt.strftime('%Y-%m-%d %H:%M')} | {result} {our_score}-{opp_score} vs {opp_name} | {', '.join(player_names)}")

            # Set last_playoff_match_id to the newest match
            newest_id = matches[-1].get("matchId", "")
            set_last_playoff_match_id(guild_id, str(newest_id))
            print(f"\n  last_playoff_match_id = {newest_id}")

            # Print summary
            period = datetime.now(timezone.utc).strftime("%Y-%m")
            total = count_playoff_matches(guild_id, period)
            print(f"  Period {period}: {total} matches recorded")

            stats = get_playoff_stats(guild_id, period)
            if stats:
                for s in stats:
                    avg_r = s['total_rating'] / s['matches_played'] if s['matches_played'] > 0 else 0
                    print(f"    {s['player_name']:20s} G:{s['goals']} A:{s['assists']} R:{avg_r:.1f} M:{s['matches_played']} Score:{s['playoff_score']:.1f}")

            club_stats = get_playoff_club_stats(guild_id, period)
            if club_stats:
                print(f"  Club: {club_stats['wins']}W-{club_stats['losses']}L-{club_stats['draws']}D | GF:{club_stats['goals_for']} GA:{club_stats['goals_against']} CS:{club_stats['clean_sheets']}")

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(backfill())
