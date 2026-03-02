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
    """Fetch match stats + match details, return an enriched dict.

    Each API call is handled **independently** so that a 404 on match-stats
    doesn't prevent us from extracting the map from match-details (or vice
    versa).  On total failure, returns a fallback object with ``map="-"``.
    """
    match_id: str = match_item["match_id"]

    stats_data: dict[str, Any] | None = None
    details_data: dict[str, Any] | None = None

    async with semaphore:
        # Fetch stats and details independently — don't let one failure
        # discard the other response.
        try:
            stats_data = await client.get_match_stats(match_id)
        except Exception:
            logger.debug("match_stats failed for %s", match_id, exc_info=True)

        try:
            details_data = await client.get_match_details(match_id)
        except Exception:
            logger.debug("match_details failed for %s", match_id, exc_info=True)

    # ---- extract player stats ----
    parsed: dict[str, Any] | None = None
    if stats_data is not None:
        parsed = _extract_player_stats(stats_data, player_id)

    if parsed is None:
        # Stats unavailable — build a fallback but still try to get the map
        parsed = {
            "kills": 0,
            "kd": 0.0,
            "kr": 0.0,
            "adr": 0.0,
        }

    # ---- extract map (multi-source) ----
    raw_map: str | None = None

    # Source 1: match stats → round_stats.Map
    if stats_data is not None and raw_map is None:
        raw_map = _extract_map_from_stats(stats_data)

    # Source 2: match details → voting.map.name / voting.map.pick
    if details_data is not None and raw_map is None:
        raw_map = _extract_map_from_details(details_data)

    parsed["map"] = normalize_map_name(raw_map)

    logger.debug(
        "Match %s → raw_map=%r → normalized=%s",
        match_id, raw_map, parsed["map"],
    )

    # ---- win/loss ----
    parsed["win"] = _determine_win(match_item, player_id)

    # ELO fields are filled later by enrich_match_data
    parsed["elo_diff"] = None
    parsed["current_elo"] = None

    return parsed


async def enrich_match_data(
    player_id: str,
    matches: list[dict[str, Any]],
    client: FaceitClient,
    semaphore: asyncio.Semaphore,
    current_elo: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch details for every match and return fully enriched objects.

    Each returned dict contains: ``map``, ``kills``, ``kd``, ``kr``, ``adr``,
    ``win``, ``elo_diff``, ``current_elo``.

    **ELO calculation**: since the FACEIT API does not expose per-match ELO
    history, we back-calculate a rolling ELO from the player's current ELO
    using a heuristic of ±\ :data:`~app.config.DEFAULT_ELO_DIFF` per match.
    The newest match gets the exact ``current_elo``; older matches are
    approximations.

    Parameters
    ----------
    player_id:
        The FACEIT player ID.
    matches:
        Raw match-history items (from ``get_player_matches``),
        **newest first**.
    client:
        An open :class:`FaceitClient`.
    semaphore:
        Concurrency limiter.
    current_elo:
        The player's current FACEIT ELO (from the profile endpoint).
    """

    # 1. Fetch and enrich all matches concurrently
    results: list[dict[str, Any]] = list(
        await asyncio.gather(
            *[
                _enrich_single_match(m, player_id, client, semaphore)
                for m in matches
            ],
        )
    )

    # 2. Compute rolling ELO (newest → oldest)
    #    The matches list is newest-first.  We walk from index 0 (newest)
    #    to the end (oldest), assigning current_elo and elo_diff.
    if current_elo is not None:
        rolling = current_elo
        for match in results:
            win = match.get("win")
            if win is True:
                diff = DEFAULT_ELO_DIFF
            elif win is False:
                diff = -DEFAULT_ELO_DIFF
            else:
                diff = 0

            match["current_elo"] = rolling
            match["elo_diff"] = diff
            # Move backwards: the ELO *before* this match was:
            rolling -= diff

    return results
