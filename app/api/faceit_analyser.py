"""Async FaceitAnalyser API client with exponential-backoff retry.

Wraps the FaceitAnalyser endpoints (https://docs.faceitanalyser.com/)
which provide pre-computed stats, map breakdowns, highlights, insights,
and enriched match data beyond the official FACEIT Data API v4.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from app.config import (
    FACEIT_ANALYSER_API_KEY,
    FACEIT_ANALYSER_BASE_URL,
    MAX_RETRIES,
    RETRY_BASE_DELAY,
)

logger = logging.getLogger(__name__)


# ---------- Custom exceptions ---------- #

class FaceitAnalyserError(Exception):
    """Generic FaceitAnalyser API error."""


class FAPlayerNotFound(FaceitAnalyserError):
    """Raised when the player ID yields no results."""


# ---------- Client ---------- #

class FaceitAnalyserClient:
    """Thin async wrapper around the FaceitAnalyser API."""

    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        self._external_session = session is not None
        self._session = session

    # -- lifecycle --------------------------------------------------------- #

    async def open(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()

    async def close(self) -> None:
        if self._session and not self._external_session:
            await self._session.close()

    # -- internal request with retry --------------------------------------- #

    async def _request(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """GET *path* with query-param auth and exponential back-off on 429."""

        assert self._session is not None, "Call .open() before making requests"

        url = f"{FACEIT_ANALYSER_BASE_URL}{path}"

        req_params: dict[str, Any] = {"key": FACEIT_ANALYSER_API_KEY}
        if params:
            req_params.update(params)

        for attempt in range(1, MAX_RETRIES + 1):
            async with self._session.get(url, params=req_params) as resp:
                if resp.status == 200:
                    return await resp.json()

                if resp.status == 404:
                    raise FAPlayerNotFound(f"Player not found: {path}")

                if resp.status == 429:
                    delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        "FA rate-limited (429). Retry %d/%d in %.1fs …",
                        attempt,
                        MAX_RETRIES,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                text = await resp.text()
                raise FaceitAnalyserError(
                    f"FaceitAnalyser API returned {resp.status}: {text[:300]}"
                )

        raise FaceitAnalyserError("Max retries exceeded (429)")

    # -- public methods ---------------------------------------------------- #

    async def get_player_stats(self, player_id: str) -> dict[str, Any]:
        """Global lifetime stats (Segment Object).

        Returns: playerId, m (matches), w (wins), l (losses), k, d, a,
        kdr, krr, hsp, wr, avg_k, avg_d, avg_kdr, avg_krr,
        highest_elo, lowest_elo, avg_elo, current_elo, etc.
        """
        return await self._request(f"/api/stats/{player_id}")

    async def get_player_maps(self, player_id: str) -> list[dict[str, Any]]:
        """Per-map breakdown — list of Segment Objects.

        Each segment has segment_value (map name) plus all stat fields.
        """
        data = await self._request(f"/api/maps/{player_id}")
        # API returns {"segments": [...]} or a list directly
        if isinstance(data, dict):
            return data.get("segments", [])
        return data if isinstance(data, list) else []

    async def get_player_highlights(self, player_id: str) -> dict[str, Any]:
        """Best/worst matches for: kills, assists, deaths, headshotpercent,
        kdr, krr, diff.  (HLTV excluded from display per user request.)
        """
        return await self._request(f"/api/highlights/{player_id}")

    async def get_player_insights(
        self,
        player_id: str,
        segment: str = "all",
    ) -> list[dict[str, Any]]:
        """Win vs loss comparison for a given segment.

        segment can be: map, weekday, hour, premade, hub, all, etc.
        Returns list of Segment Objects (typically 2: win & loss).
        """
        data = await self._request(f"/api/insights/{player_id}/{segment}")
        if isinstance(data, dict):
            return data.get("segments", [])
        return data if isinstance(data, list) else []

    async def get_player_matches(
        self,
        player_id: str,
        **filters: Any,
    ) -> list[dict[str, Any]]:
        """Enriched match list with all FA fields (headshots, multi-kills, etc.).

        Accepts optional filters as keyword args (map, kdr, date, etc.).
        """
        params = {k: v for k, v in filters.items() if v is not None}
        data = await self._request(f"/api/matches/{player_id}", params=params)
        if isinstance(data, dict):
            return data.get("segments", data.get("matches", []))
        return data if isinstance(data, list) else []

    async def get_player_graph(
        self,
        player_id: str,
        game: str = "cs2",
    ) -> dict[str, Any]:
        """ELO and KDR progression graph data."""
        return await self._request(f"/api/graph/{player_id}/{game}")
