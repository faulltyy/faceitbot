"""Stats aggregation service with two-layer Redis caching.

Provides:
* ``get_player_stats()``  — average stats for the last N matches (default 20).
* ``get_player_matches_table()`` — per-match HTML ``<pre>`` table for the
  last 10 matches, including map, kills, K/D, K/R, ADR, and ELO.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import redis.asyncio as aioredis

from app.api.faceit import (
    FaceitClient,
    NoMatchesFound,
    enrich_match_data,
)
from app.config import (
    API_CONCURRENCY,
    MATCH_CACHE_TTL,
    SUMMARY_CACHE_TTL,
)
from app.services.formatter import format_matches_table

logger = logging.getLogger(__name__)


# ---- cache key helpers --------------------------------------------------- #

def _stats_summary_key(nickname: str) -> str:
    return f"summary:stats:{nickname.lower()}"


def _matches_summary_key(nickname: str) -> str:
    return f"summary:matches:{nickname.lower()}"


def _match_key(match_id: str, player_id: str) -> str:
    return f"match:{match_id}:{player_id}"


# ---- shared per-match fetch with cache ---------------------------------- #

async def _fetch_and_cache_matches(
    match_items: list[dict[str, Any]],
    player_id: str,
    client: FaceitClient,
    redis: aioredis.Redis,
) -> list[dict[str, Any]]:
    """Return enriched match data, using per-match Redis cache where possible.

    For each match: check cache first, otherwise call the enrichment pipeline
    from the API layer and cache the result for 7 days.
    """
    semaphore = asyncio.Semaphore(API_CONCURRENCY)

    # Split matches into cached vs. uncached
    cached_results: dict[int, dict[str, Any]] = {}  # idx → data
    uncached_indices: list[int] = []
    uncached_items: list[dict[str, Any]] = []

    for idx, m in enumerate(match_items):
        cache_key = _match_key(m["match_id"], player_id)
        raw = await redis.get(cache_key)
        if raw:
            logger.debug("Match cache HIT for %s", m["match_id"])
            cached_results[idx] = json.loads(raw)
        else:
            uncached_indices.append(idx)
            uncached_items.append(m)

    # Fetch uncached matches via the enrichment pipeline
    if uncached_items:
        enriched = await enrich_match_data(
            player_id, uncached_items, client, semaphore,
        )
        # enrich_match_data filters out None, but returns in order of valid items.
        # We need to map them back. Since enrichment can skip failed matches,
        # we'll store results and cache them.
        enriched_iter = iter(enriched)
        for idx, m in zip(uncached_indices, uncached_items):
            try:
                data = next(enriched_iter)
            except StopIteration:
                break
            cached_results[idx] = data
            # Cache for 7 days
            cache_key = _match_key(m["match_id"], player_id)
            await redis.set(cache_key, json.dumps(data), ex=MATCH_CACHE_TTL)

    # Rebuild the list in order
    result = []
    for idx in range(len(match_items)):
        if idx in cached_results:
            result.append(cached_results[idx])

    return result


# ---- public API ---------------------------------------------------------- #

async def get_player_stats(
    nickname: str,
    client: FaceitClient,
    redis: aioredis.Redis,
) -> str:
    """Return a formatted *average* stats message for *nickname*."""

    # 1. Summary cache check
    cached = await redis.get(_stats_summary_key(nickname))
    if cached:
        logger.info("Stats summary cache HIT for %s", nickname)
        return cached.decode()

    # 2. Resolve player
    player_id: str = await client.get_player_id(nickname)

    # 3. Fetch + enrich last 20 matches
    matches = await client.get_player_matches(player_id, limit=20)
    valid = await _fetch_and_cache_matches(matches, player_id, client, redis)

    if not valid:
        raise NoMatchesFound("Could not retrieve stats for any CS2 matches.")

    # 4. Aggregate
    total = len(valid)
    avg_kills = sum(s["kills"] for s in valid) / total
    avg_kd = sum(s["kd"] for s in valid) / total
    avg_kr = sum(s["kr"] for s in valid) / total
    avg_adr = sum(s["adr"] for s in valid) / total
    wins = sum(1 for s in valid if s.get("win") is True)
    winrate = (wins / total) * 100

    # 5. Format
    message = (
        f"📊 CS2 Stats for {nickname}\n"
        f"🎯 Avg Kills: {avg_kills:.2f}\n"
        f"⚔️ Avg K/D: {avg_kd:.2f}\n"
        f"💀 Avg K/R: {avg_kr:.2f}\n"
        f"💥 Avg ADR: {avg_adr:.2f}\n"
        f"🏆 Winrate for last {total} matches: {winrate:.0f}%"
    )

    # 6. Cache the summary
    await redis.set(_stats_summary_key(nickname), message, ex=SUMMARY_CACHE_TTL)
    return message


async def get_player_matches_table(
    nickname: str,
    client: FaceitClient,
    redis: aioredis.Redis,
) -> str:
    """Return a formatted ``<pre>`` table of the last 10 matches."""

    # 1. Summary cache check
    cached = await redis.get(_matches_summary_key(nickname))
    if cached:
        logger.info("Matches summary cache HIT for %s", nickname)
        return cached.decode()

    # 2. Get player info (nickname + current ELO)
    player_info = await client.get_player_info(nickname)
    player_id: str = player_info["player_id"]
    display_name: str = player_info["nickname"]
    current_elo: int | None = player_info["elo"]

    # 3. Fetch match history (up to 10)
    match_items = await client.get_player_matches(player_id, limit=10)

    # 4. Enrich (fetch stats + details, extract map, cache)
    valid = await _fetch_and_cache_matches(match_items, player_id, client, redis)

    if not valid:
        raise NoMatchesFound("Could not retrieve stats for any CS2 matches.")

    # 5. ELO tracking: reverse to chronological (oldest → newest),
    #    then walk forward to compute rolling ELO.
    #    We know current_elo is the ELO *after* the most recent match.
    if current_elo is not None:
        # valid list is newest-first; reverse for chrono processing
        chrono = list(reversed(valid))

        # Walk backwards from current_elo through the chrono list
        # to assign elo_after to each match.
        # Match N (newest) → elo_after = current_elo
        # Match N-1 → elo_after = current_elo - elo_change_N  (unknown)
        # Without per-match ELO data from the API, we can only reliably
        # assign current_elo to the NEWEST match.  For older matches
        # we leave elo_diff/elo_after as None.
        #
        # Assign the newest match's elo_after to current_elo.
        valid[0]["elo_after"] = current_elo

    # 6. Format the HTML table
    message = format_matches_table(
        nickname=display_name,
        matches=valid,
        current_elo=current_elo,
    )

    # 7. Cache
    await redis.set(_matches_summary_key(nickname), message, ex=SUMMARY_CACHE_TTL)
    return message
