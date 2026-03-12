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
from app.api.faceit_analyser import FaceitAnalyserClient, FAPlayerNotFound
from app.config import (
    API_CONCURRENCY,
    DEFAULT_ELO_DIFF,
    FA_HIGHLIGHTS_CACHE_TTL,
    FA_INSIGHTS_CACHE_TTL,
    FA_MAPS_CACHE_TTL,
    FA_STATS_CACHE_TTL,
    MATCH_CACHE_TTL,
    SUMMARY_CACHE_TTL,
)
from app.services.formatter import (
    format_highlights,
    format_insights,
    format_map_stats_table,
    format_matches_table,
    format_overview,
)

logger = logging.getLogger(__name__)


# ---- cache key helpers --------------------------------------------------- #

def _stats_summary_key(nickname: str) -> str:
    return f"summary:stats:{nickname.lower()}"


def _matches_summary_key(nickname: str) -> str:
    return f"summary:matches:{nickname.lower()}"


def _match_key(match_id: str, player_id: str) -> str:
    return f"match:{match_id}:{player_id}"


def _fa_overview_key(nickname: str) -> str:
    return f"fa:overview:{nickname.lower()}"


def _fa_maps_key(nickname: str) -> str:
    return f"fa:maps:{nickname.lower()}"


def _fa_highlights_key(nickname: str) -> str:
    return f"fa:highlights:{nickname.lower()}"


def _fa_insights_key(nickname: str, segment: str) -> str:
    return f"fa:insights:{nickname.lower()}:{segment.lower()}"


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
        try:
            raw = await redis.get(cache_key)
        except Exception:
            raw = None
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
                try:
                    await redis.set(cache_key, json.dumps(data), ex=MATCH_CACHE_TTL)
                except Exception:
                    pass  # read-only Redis

    # Rebuild the list in order
    result = []
    for idx in range(len(match_items)):
        if idx in cached_results:
            result.append(cached_results[idx])

    # Compute ELO across ALL matches (cached + fresh)
    # Primary source: match history items from /players/{id}/history
    # contain an "elo" field with the player's ELO at match time.
    if current_elo is not None:
        # 1. Build match_id → elo lookup from ALL available sources
        elo_by_match: dict[str, int] = {}

        # Source A: match history items (most reliable — straight from API)
        for item in match_items:
            mid = item.get("match_id")
            if not mid:
                continue
            # Try common field names for ELO in history items
            elo_val = item.get("elo") or item.get("Elo") or item.get("faceit_elo")
            if elo_val is not None:
                try:
                    elo_by_match[str(mid)] = int(elo_val)
                except (TypeError, ValueError):
                    pass

        # Source B: match_elo from enriched player_stats
        for match in result:
            mid = match.get("match_id")
            me = match.get("match_elo")
            if mid and me is not None and str(mid) not in elo_by_match:
                elo_by_match[str(mid)] = int(me)

        # Source C: elo_history from get_player_game_stats endpoint
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
                    stats.get("Elo") or stats.get("elo")
                    or stats.get("ELO") or item.get("elo")
                )
                if mid and elo_val is not None:
                    mid_str = str(mid)
                    if mid_str not in elo_by_match:
                        try:
                            elo_by_match[mid_str] = int(elo_val)
                        except (TypeError, ValueError):
                            pass

        all_match_ids = [m["match_id"] for m in match_items]
        logger.info(
            "ELO lookup: %d/%d matches have real ELO data",
            len(elo_by_match), len(all_match_ids),
        )

        if elo_by_match:
            # --- Real ELO: process oldest → newest, compute diffs ---
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
                        match["elo_diff"] = None  # oldest match
                    prev_elo = elo_after
                else:
                    match["current_elo"] = None
                    match["elo_diff"] = None
        else:
            # --- Fallback: ±DEFAULT_ELO_DIFF heuristic ---
            logger.info("No real ELO data found, using ±%d heuristic", DEFAULT_ELO_DIFF)
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
    try:
        cached = await redis.get(_stats_summary_key(nickname))
    except Exception:
        cached = None
    if cached:
        logger.info("Stats summary cache HIT for %s", nickname)
        return cached.decode()

    # 2. Resolve player (includes ELO)
    player_info = await client.get_player_info(nickname)
    player_id: str = player_info["player_id"]
    current_elo: int | None = player_info["elo"]

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
    elo_line = f"🏅 Current ELO: {current_elo}\n" if current_elo else ""
    message = (
        f"📊 CS2 Stats for {nickname}\n"
        f"{elo_line}"
        f"🎯 Avg Kills: {avg_kills:.2f}\n"
        f"⚔️ Avg K/D: {avg_kd:.2f}\n"
        f"💀 Avg K/R: {avg_kr:.2f}\n"
        f"💥 Avg ADR: {avg_adr:.2f}\n"
        f"🏆 Winrate for last {total} matches: {winrate:.0f}%"
    )

    # 6. Cache the summary
    try:
        await redis.set(_stats_summary_key(nickname), message, ex=SUMMARY_CACHE_TTL)
    except Exception:
        pass  # read-only Redis
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
    try:
        cached = await redis.get(cache_key)
    except Exception:
        cached = None
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
    try:
        await redis.set(cache_key, message, ex=SUMMARY_CACHE_TTL)
    except Exception:
        pass  # read-only Redis
    return message


# ---- FaceitAnalyser-powered commands ------------------------------------- #

async def get_player_overview(
    nickname: str,
    faceit_client: FaceitClient,
    fa_client: FaceitAnalyserClient,
    redis: aioredis.Redis,
) -> str:
    """Return formatted lifetime overview from FaceitAnalyser."""

    # Cache check
    cache_key = _fa_overview_key(nickname)
    try:
        cached = await redis.get(cache_key)
    except Exception:
        cached = None
    if cached:
        logger.info("FA overview cache HIT for %s", nickname)
        return cached.decode()

    # Resolve player_id via FACEIT
    player_info = await faceit_client.get_player_info(nickname)
    player_id: str = player_info["player_id"]
    display_name: str = player_info["nickname"]

    # Fetch from FA
    data = await fa_client.get_player_stats(player_id)

    # Format
    message = format_overview(display_name, data)

    # Cache
    try:
        await redis.set(cache_key, message.encode(), ex=FA_STATS_CACHE_TTL)
    except Exception:
        pass
    return message


async def get_player_map_stats(
    nickname: str,
    faceit_client: FaceitClient,
    fa_client: FaceitAnalyserClient,
    redis: aioredis.Redis,
) -> str:
    """Return formatted per-map stats table from FaceitAnalyser."""

    cache_key = _fa_maps_key(nickname)
    try:
        cached = await redis.get(cache_key)
    except Exception:
        cached = None
    if cached:
        logger.info("FA maps cache HIT for %s", nickname)
        return cached.decode()

    player_info = await faceit_client.get_player_info(nickname)
    player_id: str = player_info["player_id"]
    display_name: str = player_info["nickname"]

    segments = await fa_client.get_player_maps(player_id)

    message = format_map_stats_table(display_name, segments)

    try:
        await redis.set(cache_key, message.encode(), ex=FA_MAPS_CACHE_TTL)
    except Exception:
        pass
    return message


async def get_player_highlights(
    nickname: str,
    faceit_client: FaceitClient,
    fa_client: FaceitAnalyserClient,
    redis: aioredis.Redis,
) -> str:
    """Return formatted highlights from FaceitAnalyser."""

    cache_key = _fa_highlights_key(nickname)
    try:
        cached = await redis.get(cache_key)
    except Exception:
        cached = None
    if cached:
        logger.info("FA highlights cache HIT for %s", nickname)
        return cached.decode()

    player_info = await faceit_client.get_player_info(nickname)
    player_id: str = player_info["player_id"]
    display_name: str = player_info["nickname"]

    data = await fa_client.get_player_highlights(player_id)

    message = format_highlights(display_name, data)

    try:
        await redis.set(cache_key, message.encode(), ex=FA_HIGHLIGHTS_CACHE_TTL)
    except Exception:
        pass
    return message


async def get_player_insights(
    nickname: str,
    segment: str,
    faceit_client: FaceitClient,
    fa_client: FaceitAnalyserClient,
    redis: aioredis.Redis,
) -> str:
    """Return formatted win/loss insights from FaceitAnalyser."""

    cache_key = _fa_insights_key(nickname, segment)
    try:
        cached = await redis.get(cache_key)
    except Exception:
        cached = None
    if cached:
        logger.info("FA insights cache HIT for %s/%s", nickname, segment)
        return cached.decode()

    player_info = await faceit_client.get_player_info(nickname)
    player_id: str = player_info["player_id"]
    display_name: str = player_info["nickname"]

    segments_data = await fa_client.get_player_insights(player_id, segment)

    message = format_insights(display_name, segment, segments_data)

    try:
        await redis.set(cache_key, message.encode(), ex=FA_INSIGHTS_CACHE_TTL)
    except Exception:
        pass
    return message

