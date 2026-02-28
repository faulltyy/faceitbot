"""Stats aggregation service with two-layer Redis caching."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import redis.asyncio as aioredis

from app.api.faceit import FaceitClient, NoMatchesFound
from app.config import (
    API_CONCURRENCY,
    MATCH_CACHE_TTL,
    MAX_MATCHES,
    SUMMARY_CACHE_TTL,
)

logger = logging.getLogger(__name__)


# ---- cache key helpers --------------------------------------------------- #

def _summary_key(nickname: str) -> str:
    return f"summary:{nickname.lower()}"


def _match_key(match_id: str, player_id: str) -> str:
    return f"match:{match_id}:{player_id}"


# ---- per-match stat extraction ------------------------------------------ #

def _extract_player_stats(
    match_data: dict[str, Any],
    player_id: str,
) -> dict[str, Any] | None:
    """Pull Kills / K/D / K/R / ADR from the match-stats payload.

    Returns ``None`` when the player cannot be found in the match (e.g.
    the match was cancelled before they played).
    """
    for rnd in match_data.get("rounds", []):
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
    """Determine whether *player_id* won a match from the history item.

    The ``results.winner`` field in the player-history response contains the
    team key (e.g. ``"faction1"`` or ``"faction2"``). We find which faction
    the player belongs to and compare.
    """
    results = match_item.get("results", {})
    winner = results.get("winner")
    teams = match_item.get("teams", {})

    for faction_key, team_info in teams.items():
        players_in_team = team_info.get("players", [])
        for p in players_in_team:
            if p.get("player_id") == player_id:
                return faction_key == winner
    return None


# ---- public API ---------------------------------------------------------- #

async def get_player_stats(
    nickname: str,
    client: FaceitClient,
    redis: aioredis.Redis,
) -> str:
    """Return a formatted stats message for *nickname*.

    Two Redis cache layers are used:
    * **summary cache** (15 min) — the entire formatted message.
    * **match cache** (7 days) — individual match stats (immutable data).
    """

    # 1. Summary cache check
    cached = await redis.get(_summary_key(nickname))
    if cached:
        logger.info("Summary cache HIT for %s", nickname)
        return cached.decode()

    # 2. Resolve player_id
    player_id: str = await client.get_player_id(nickname)

    # 3. Fetch match history (up to MAX_MATCHES)
    matches: list[dict[str, Any]] = await client.get_player_matches(
        player_id, limit=MAX_MATCHES,
    )

    # 4. Fetch per-match stats (cache-aware, concurrency-limited)
    semaphore = asyncio.Semaphore(API_CONCURRENCY)

    async def _fetch_one(match_item: dict[str, Any]) -> dict[str, Any] | None:
        match_id: str = match_item["match_id"]
        cache_key = _match_key(match_id, player_id)

        # Check match cache
        raw = await redis.get(cache_key)
        if raw:
            logger.debug("Match cache HIT for %s", match_id)
            return json.loads(raw)

        # Fetch from API (rate-limited)
        async with semaphore:
            try:
                stats_data = await client.get_match_stats(match_id)
            except Exception:
                logger.warning("Failed to fetch stats for match %s", match_id)
                return None

        parsed = _extract_player_stats(stats_data, player_id)
        if parsed is None:
            return None

        # Determine win/loss from history item
        won = _determine_win(match_item, player_id)
        parsed["win"] = won

        # Cache individual match stats
        await redis.set(cache_key, json.dumps(parsed), ex=MATCH_CACHE_TTL)
        return parsed

    results = await asyncio.gather(*[_fetch_one(m) for m in matches])
    valid = [r for r in results if r is not None]

    if not valid:
        raise NoMatchesFound("Could not retrieve stats for any CS2 matches.")

    # 5. Aggregate
    total = len(valid)
    avg_kills = sum(s["kills"] for s in valid) / total
    avg_kd = sum(s["kd"] for s in valid) / total
    avg_kr = sum(s["kr"] for s in valid) / total
    avg_adr = sum(s["adr"] for s in valid) / total
    wins = sum(1 for s in valid if s.get("win") is True)
    winrate = (wins / total) * 100

    # 6. Format message
    message = (
        f"📊 CS2 Stats for {nickname}\n"
        f"🎯 Avg Kills: {avg_kills:.2f}\n"
        f"⚔️ Avg K/D: {avg_kd:.2f}\n"
        f"💀 Avg K/R: {avg_kr:.2f}\n"
        f"💥 Avg ADR: {avg_adr:.2f}\n"
        f"🏆 Winrate for last {total} matches: {winrate:.0f}%"
    )

    # 7. Cache the summary
    await redis.set(_summary_key(nickname), message, ex=SUMMARY_CACHE_TTL)
    return message
