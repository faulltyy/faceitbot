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
    DEFAULT_ELO_DIFF,
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
    current_elo: int | None = None,
    elo_history: list[dict[str, Any]] | None = None,
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
            cached = json.loads(raw)
            # Invalidate stale cache entries that are missing map data
            if cached.get("map") in (None, "-", ""):
                logger.debug("Match cache STALE (no map) for %s, re-fetching", m["match_id"])
                uncached_indices.append(idx)
                uncached_items.append(m)
            else:
                logger.debug("Match cache HIT for %s", m["match_id"])
                cached_results[idx] = cached
        else:
            uncached_indices.append(idx)
            uncached_items.append(m)

    # Fetch uncached matches via the enrichment pipeline
    if uncached_items:
        enriched = await enrich_match_data(
            player_id, uncached_items, client, semaphore,
        )
        # enrich_match_data returns all matches (including fallbacks).
        for i, idx in enumerate(uncached_indices):
            if i < len(enriched):
                data = enriched[i]
                cached_results[idx] = data
                cache_key = _match_key(uncached_items[i]["match_id"], player_id)
                await redis.set(cache_key, json.dumps(data), ex=MATCH_CACHE_TTL)

    # Rebuild the list in order
    result = []
    for idx in range(len(match_items)):
        if idx in cached_results:
            result.append(cached_results[idx])

    # Compute ELO across ALL matches (cached + fresh)
    # Sources (in priority): match_elo from stats, elo_history from game stats API
    # Fallback: ±DEFAULT_ELO_DIFF heuristic
    if current_elo is not None:
        # 1. Build match_id → elo lookup from ALL sources
        elo_by_match: dict[str, int] = {}

        # Source A: match_elo extracted from player_stats (most reliable)
        for i, match in enumerate(result):
            mid = match.get("match_id")
            me = match.get("match_elo")
            if mid and me is not None:
                elo_by_match[str(mid)] = int(me)

        # Source B: elo_history from get_player_game_stats endpoint
        if elo_history:
            for item in elo_history:
                stats = item.get("stats", {})
                mid = (
                    stats.get("matchId")
                    or stats.get("match_id")
                    or stats.get("Match Id")
                    or item.get("matchId")
                    or item.get("match_id")
                )
                elo_val = (
                    stats.get("Elo")
                    or stats.get("elo")
                    or stats.get("ELO")
                    or item.get("elo")
                )
                if mid and elo_val is not None:
                    mid_str = str(mid)
                    if mid_str not in elo_by_match:  # don't overwrite source A
                        try:
                            elo_by_match[mid_str] = int(elo_val)
                        except (TypeError, ValueError):
                            pass

        all_match_ids = [m["match_id"] for m in match_items]
        has_real_elo = bool(elo_by_match)

        if has_real_elo:
            # --- Real ELO: process oldest → newest, compute diffs ---
            logger.info(
                "Using real ELO data (%d/%d matches have ELO)",
                len(elo_by_match), len(result),
            )
            # result is newest-first; reverse for chronological processing
            chrono_ids = list(reversed(all_match_ids))
            chrono_matches = list(reversed(result))

            prev_elo: int | None = None
            for mid, match in zip(chrono_ids, chrono_matches):
                elo_after = elo_by_match.get(mid)
                if elo_after is not None:
                    match["current_elo"] = elo_after
                    if prev_elo is not None:
                        match["elo_diff"] = elo_after - prev_elo
                    else:
                        match["elo_diff"] = None  # first match, no previous
                    prev_elo = elo_after
                else:
                    match["current_elo"] = None
                    match["elo_diff"] = None

            # Ensure newest match has current_elo from profile
            if result and result[0].get("current_elo") is None:
                result[0]["current_elo"] = current_elo
        else:
            # --- Fallback: ±DEFAULT_ELO_DIFF heuristic ---
            logger.info("No real ELO data, using ±%d heuristic", DEFAULT_ELO_DIFF)
            rolling = current_elo
            for match in result:
                win = match.get("win")
                if win is True:
                    diff = DEFAULT_ELO_DIFF
                elif win is False:
                    diff = -DEFAULT_ELO_DIFF
                else:
                    diff = 0
                match["current_elo"] = rolling
                match["elo_diff"] = diff
                rolling -= diff

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
    limit: int = 20,
) -> str:
    """Return a formatted ``<pre>`` table of the last *limit* matches."""

    # 1. Summary cache check (include limit in key so different counts
    #    don't collide)
    cache_key = f"{_matches_summary_key(nickname)}:{limit}"
    cached = await redis.get(cache_key)
    if cached:
        logger.info("Matches summary cache HIT for %s (limit=%d)", nickname, limit)
        return cached.decode()

    # 2. Get player info (nickname + current ELO)
    player_info = await client.get_player_info(nickname)
    player_id: str = player_info["player_id"]
    display_name: str = player_info["nickname"]
    current_elo: int | None = player_info["elo"]

    # 3. Fetch match history
    match_items = await client.get_player_matches(player_id, limit=limit)

    # 4. Fetch per-match ELO history
    elo_history = await client.get_player_game_stats(player_id, limit=limit)

    # 5. Enrich (fetch stats + details, extract map, compute ELO, cache)
    valid = await _fetch_and_cache_matches(
        match_items, player_id, client, redis,
        current_elo=current_elo,
        elo_history=elo_history,
    )

    if not valid:
        raise NoMatchesFound("Could not retrieve stats for any CS2 matches.")

    # 6. Format the HTML table
    message = format_matches_table(
        nickname=display_name,
        matches=valid,
        current_elo=current_elo,
    )

    # 7. Cache
    await redis.set(cache_key, message, ex=SUMMARY_CACHE_TTL)
    return message
