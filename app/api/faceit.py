"""Async FACEIT Data API v4 client with exponential-backoff retry.

Provides low-level API methods *and* the higher-level
:func:`enrich_match_data` pipeline that combines match stats + match details
into fully enriched match objects (map, kills, K/D, K/R, ADR, ELO).
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import aiohttp

from app.config import (
    DEFAULT_ELO_DIFF,
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


# ---------- Map helpers ---------- #

_DE_PREFIX = re.compile(r"^de_", re.IGNORECASE)


def normalize_map_name(raw: str | None) -> str:
    """``de_mirage`` → ``Mirage``, ``None`` → ``-``."""
    if not raw:
        return "-"
    name = _DE_PREFIX.sub("", raw.strip())
    return name.capitalize() if name else "-"


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

    async def get_player_info(self, nickname: str) -> dict[str, Any]:
        """Resolve a FACEIT nickname to player details including ELO.

        Returns a dict with keys: ``player_id``, ``nickname``, ``elo``.
        ``elo`` may be *None* if the player has no CS2 data.
        """

        data = await self._request(
            f"{FACEIT_BASE_URL}/players",
            params={"nickname": nickname},
        )
        elo: int | None = None
        games = data.get("games", {})
        cs2 = games.get("cs2", {})
        if cs2:
            try:
                elo = int(cs2.get("faceit_elo", 0)) or None
            except (TypeError, ValueError):
                pass

        return {
            "player_id": data["player_id"],
            "nickname": data.get("nickname", nickname),
            "elo": elo,
        }

    async def get_player_id(self, nickname: str) -> str:
        """Resolve a FACEIT nickname to a ``player_id``."""
        info = await self.get_player_info(nickname)
        return info["player_id"]

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

    async def get_player_game_stats(
        self,
        player_id: str,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        """Return per-match stats with ELO for the last *limit* CS2 matches.

        Uses ``GET /players/{player_id}/games/cs2/stats``.
        Each item in the returned list typically contains an ``elo`` field.
        """
        try:
            data = await self._request(
                f"{FACEIT_BASE_URL}/players/{player_id}/games/cs2/stats",
                params={"offset": 0, "limit": limit},
            )
            items = data.get("items", [])
            # Log the first item's structure to help debug ELO field names
            if items:
                first = items[0]
                logger.info(
                    "game_stats first item keys: %s",
                    list(first.keys()) if isinstance(first, dict) else type(first),
                )
                stats = first.get("stats", {})
                if stats:
                    logger.info(
                        "game_stats.stats keys: %s",
                        list(stats.keys())[:15],  # first 15 to avoid spam
                    )
            else:
                logger.info("game_stats returned 0 items for %s", player_id)
            return items
        except Exception:
            logger.warning(
                "Failed to fetch per-match game stats for %s", player_id,
                exc_info=True,
            )
            return []

    async def get_match_stats(self, match_id: str) -> dict[str, Any]:
        """Return raw match-stats payload for a single match."""

        return await self._request(
            f"{FACEIT_BASE_URL}/matches/{match_id}/stats",
        )

    async def get_match_details(self, match_id: str) -> dict[str, Any]:
        """Return full match details (includes ``voting``, ``teams``, etc.)."""

        return await self._request(
            f"{FACEIT_BASE_URL}/matches/{match_id}",
        )


# ---------- Map extraction (multi-source) ---------- #

def _extract_map_from_stats(stats_data: dict[str, Any]) -> str | None:
    """Try to pull map from ``round_stats.Map`` (match stats response)."""
    for rnd in stats_data.get("rounds", []):
        rs = rnd.get("round_stats", {})
        # Try common key variants
        for key in ("Map", "map", "MAP"):
            val = rs.get(key)
            if val:
                return str(val)
    return None


def _extract_map_from_details(details: dict[str, Any]) -> str | None:
    """Try to pull map from match details ``voting`` field.

    Falls back to ``competition_name`` if voting data is missing.
    """
    voting = details.get("voting")
    if voting:
        # voting.map.pick is typically ["de_mirage"]
        map_obj = voting.get("map", {})
        if isinstance(map_obj, dict):
            # Check "name" first (most reliable when present)
            name = map_obj.get("name")
            if name:
                return str(name)
            pick = map_obj.get("pick")
            if isinstance(pick, list) and pick:
                return str(pick[0])

    # Fallback: competition_name sometimes contains the map
    comp = details.get("competition_name")
    if comp:
        return str(comp)

    return None


# ---------- Player stats extraction ---------- #

def _extract_player_stats(
    stats_data: dict[str, Any],
    player_id: str,
) -> dict[str, Any] | None:
    """Pull Kills / K/D / K/R / ADR from the match-stats payload.

    Returns ``None`` when the player cannot be found in the match.
    """
    for rnd in stats_data.get("rounds", []):
        for team in rnd.get("teams", []):
            for player in team.get("players", []):
                if player.get("player_id") == player_id:
                    ps = player.get("player_stats", {})
                    try:
                        return {
                            "kills": float(ps.get("Kills", 0)),
                            "kd":    float(ps.get("K/D Ratio", 0)),
                            "kr":    float(ps.get("K/R Ratio", 0)),
                            "adr":   float(ps.get("ADR", 0)),
                        }
                    except (TypeError, ValueError):
                        return None
    return None


def _determine_win(
    match_item: dict[str, Any],
    player_id: str,
) -> bool | None:
    """Determine whether *player_id* won from the history item."""
    results = match_item.get("results", {})
    winner = results.get("winner")
    teams = match_item.get("teams", {})

    for faction_key, team_info in teams.items():
        for p in team_info.get("players", []):
            if p.get("player_id") == player_id:
                return faction_key == winner
    return None


# ---------- enrich_match_data ---------- #

def _fallback_match(match_item: dict[str, Any], player_id: str) -> dict[str, Any]:
    """Return a minimal match dict when API calls fail."""
    return {
        "map": "-",
        "kills": 0,
        "kd": 0.0,
        "kr": 0.0,
        "adr": 0.0,
        "win": _determine_win(match_item, player_id),
        "elo_diff": None,
        "current_elo": None,
    }


async def _enrich_single_match(
    match_item: dict[str, Any],
    player_id: str,
    client: FaceitClient,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    """Fetch match stats + match details in **parallel**, return enriched dict.

    Uses ``asyncio.gather(return_exceptions=True)`` so that a failure on one
    call doesn't cancel the other.  If both fail, returns a fallback object.
    """
    match_id: str = match_item["match_id"]

    async with semaphore:
        raw_stats, raw_details = await asyncio.gather(
            client.get_match_stats(match_id),
            client.get_match_details(match_id),
            return_exceptions=True,
        )

    # Separate successes from failures
    stats_data: dict[str, Any] | None = None
    details_data: dict[str, Any] | None = None

    if isinstance(raw_stats, dict):
        stats_data = raw_stats
    elif isinstance(raw_stats, BaseException):
        logger.debug("match_stats failed for %s: %s", match_id, raw_stats)

    if isinstance(raw_details, dict):
        details_data = raw_details
    elif isinstance(raw_details, BaseException):
        logger.debug("match_details failed for %s: %s", match_id, raw_details)

    # ---- extract player stats ----
    parsed: dict[str, Any] | None = None
    if stats_data is not None:
        parsed = _extract_player_stats(stats_data, player_id)

    if parsed is None:
        parsed = {
            "kills": 0,
            "kd": 0.0,
            "kr": 0.0,
            "adr": 0.0,
        }

    # ---- extract map (multi-source) ----
    raw_map: str | None = None
    if stats_data is not None:
        raw_map = _extract_map_from_stats(stats_data)
    if raw_map is None and details_data is not None:
        raw_map = _extract_map_from_details(details_data)

    parsed["map"] = normalize_map_name(raw_map)

    logger.info(
        "Match %s → map=%s (stats=%s, details=%s)",
        match_id,
        parsed["map"],
        "OK" if stats_data is not None else "FAIL",
        "OK" if details_data is not None else "FAIL",
    )

    # ---- win/loss ----
    parsed["win"] = _determine_win(match_item, player_id)

    # ELO fields are filled later by the service layer
    parsed["elo_diff"] = None
    parsed["current_elo"] = None

    return parsed


async def enrich_match_data(
    player_id: str,
    matches: list[dict[str, Any]],
    client: FaceitClient,
    semaphore: asyncio.Semaphore,
) -> list[dict[str, Any]]:
    """Fetch details for every match and return enriched objects.

    Each returned dict contains: ``map``, ``kills``, ``kd``, ``kr``, ``adr``,
    ``win``.  ELO fields (``elo_diff``, ``current_elo``) are set to ``None``
    and must be populated by the caller.
    """
    return list(
        await asyncio.gather(
            *[
                _enrich_single_match(m, player_id, client, semaphore)
                for m in matches
            ],
        )
    )
