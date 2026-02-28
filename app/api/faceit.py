"""Async FACEIT Data API v4 client with exponential-backoff retry."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from app.config import (
    FACEIT_API_KEY,
    FACEIT_BASE_URL,
    MAX_RETRIES,
    RETRY_BASE_DELAY,
)

logger = logging.getLogger(__name__)


# ---------- Custom exceptions ---------- #

class FaceitApiError(Exception):
    """Generic FACEIT API error."""


class PlayerNotFound(FaceitApiError):
    """Raised when the requested nickname does not exist."""


class NoMatchesFound(FaceitApiError):
    """Raised when the player has zero finished CS2 matches."""


# ---------- Client ---------- #

class FaceitClient:
    """Thin async wrapper around the FACEIT Data API v4."""

    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        self._external_session = session is not None
        self._session = session

    # -- lifecycle --------------------------------------------------------- #

    async def open(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {FACEIT_API_KEY}"},
            )

    async def close(self) -> None:
        if self._session and not self._external_session:
            await self._session.close()

    # -- internal request with retry --------------------------------------- #

    async def _request(self, url: str, params: dict[str, Any] | None = None) -> Any:
        """GET *url* with exponential back-off on HTTP 429."""

        assert self._session is not None, "Call .open() before making requests"

        for attempt in range(1, MAX_RETRIES + 1):
            async with self._session.get(url, params=params) as resp:
                if resp.status == 200:
                    return await resp.json()

                if resp.status == 404:
                    raise PlayerNotFound(f"Resource not found: {url}")

                if resp.status == 429:
                    delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        "Rate-limited (429). Retry %d/%d in %.1fs …",
                        attempt,
                        MAX_RETRIES,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                # Unexpected status
                text = await resp.text()
                raise FaceitApiError(
                    f"FACEIT API returned {resp.status}: {text[:300]}"
                )

        raise FaceitApiError("Max retries exceeded due to rate limiting (429)")

    # -- public methods ---------------------------------------------------- #

    async def get_player_id(self, nickname: str) -> str:
        """Resolve a FACEIT nickname to a ``player_id``."""

        data = await self._request(
            f"{FACEIT_BASE_URL}/players",
            params={"nickname": nickname},
        )
        return data["player_id"]

    async def get_player_matches(
        self,
        player_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return up to *limit* most-recent CS2 match summaries."""

        data = await self._request(
            f"{FACEIT_BASE_URL}/players/{player_id}/history",
            params={"game": "cs2", "offset": 0, "limit": limit},
        )
        items: list[dict[str, Any]] = data.get("items", [])
        if not items:
            raise NoMatchesFound("No CS2 matches found for this player")
        return items

    async def get_match_stats(self, match_id: str) -> dict[str, Any]:
        """Return raw match-stats payload for a single match."""

        return await self._request(
            f"{FACEIT_BASE_URL}/matches/{match_id}/stats",
        )
