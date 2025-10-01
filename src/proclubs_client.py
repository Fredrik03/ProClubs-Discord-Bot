from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential_jitter


# NOTE: EA's Pro Clubs endpoints are undocumented and may change.
# These endpoints are community-discovered and can break at any time.

BASE_URL = "https://proclubs.ea.com/api/fc/clubs"


class ProClubsApiError(RuntimeError):
    pass


class ProClubsClient:
    def __init__(
        self,
        platform: str,
        region: str = "us",
        client: Optional[httpx.AsyncClient] = None,
        debug: bool = False,
    ) -> None:
        self.platform = platform.lower()
        self.region = region.lower()
        self.debug = debug
        # Prefer HTTP/2 where possible; gracefully fall back if not available
        if client is not None:
            self._client = client
        else:
            try:
                self._client = httpx.AsyncClient(timeout=httpx.Timeout(20.0), http2=True)
            except Exception:
                # If http2 dependencies are missing, fall back to HTTP/1.1
                self._client = httpx.AsyncClient(timeout=httpx.Timeout(20.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    @retry(wait=wait_exponential_jitter(initial=1, max=10), stop=stop_after_attempt(3))
    async def _get(self, path: str, params: Dict[str, Any]) -> Any:
        url = f"{BASE_URL}/{path}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.ea.com/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
        }
        try:
            if self.debug:
                print(f"[ProClubsClient] GET {url} params={params}")
            r = await self._client.get(url, params=params, headers=headers)
            if self.debug:
                print(f"[ProClubsClient] <- {r.status_code} {r.reason_phrase}")
            r.raise_for_status()
            # Some endpoints return plain text 'null' or ''
            try:
                return r.json()
            except Exception:
                text = r.text.strip()
                if text and text.lower() != "null":
                    return text
                raise ProClubsApiError(f"Empty/NULL response from {url}")
        except Exception as exc:  # noqa: BLE001
            if self.debug:
                try:
                    body_preview = r.text[:200] if 'r' in locals() else "<no response>"
                except Exception:
                    body_preview = "<unavailable>"
                print(f"[ProClubsClient] ERROR {url} params={params} exc={exc} body={body_preview}")
            raise ProClubsApiError(f"Request failed: {exc}") from exc

    async def get_club_info(self, club_id: str) -> Any:
        # Correct path: /info?platform=<platform>&clubIds=<id>
        params: Dict[str, Any] = {"platform": self.platform, "clubIds": club_id}
        return await self._get("info", params)

    async def get_members(self, club_id: str) -> Any:
        # Path: /members?platform=<platform>&clubIds=<id>
        params: Dict[str, Any] = {"platform": self.platform, "clubIds": club_id}
        return await self._get("members", params)

    async def get_match_history(self, club_id: str, match_type: str = "gameType11") -> Any:
        # Path: /matches?matchType=<gameType11>&platform=<platform>&clubIds=<id>
        params: Dict[str, Any] = {
            "matchType": match_type,
            "platform": self.platform,
            "clubIds": club_id,
        }
        return await self._get("matches", params)

    async def get_season_stats(self, club_id: str) -> Any:
        # Path: /seasonalStats?platform=<platform>&clubIds=<id>
        params: Dict[str, Any] = {"platform": self.platform, "clubIds": club_id}
        return await self._get("seasonalStats", params)

    async def search_clubs_by_name(self, name: str) -> Any:
        # Path: /search?platform=<platform>&clubName=<name>
        params: Dict[str, Any] = {"platform": self.platform, "clubName": name}
        return await self._get("search", params)


async def example_usage() -> None:
    # Minimal smoke test when run standalone
    client = ProClubsClient(platform="common-gen5", debug=True)
    try:
        info = await client.get_club_info("669174")
        print(f"Club info: {info}")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(example_usage())
