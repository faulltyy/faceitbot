"""Firebase-style analytics event tracking service.

All methods are fire-and-forget safe — errors are caught and logged,
never propagated to the bot handler layer.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import asyncpg
import redis.asyncio as aioredis

from app.config import METRICS_CACHE_TTL

logger = logging.getLogger(__name__)

_METRICS_CACHE_KEY = "analytics:metrics_cache"


class AnalyticsService:
    """Async analytics event tracker backed by PostgreSQL + Redis cache."""

    def __init__(self, pool: asyncpg.Pool, redis: aioredis.Redis) -> None:
        self._pool = pool
        self._redis = redis

    # ------------------------------------------------------------------ #
    #  Event tracking
    # ------------------------------------------------------------------ #

    async def track_event(
        self,
        user_id: int | None = None,
        username: str | None = None,
        event_name: str = "unknown",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Insert an analytics event row. Never raises."""
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO analytics_events (user_id, username, event_name, metadata)
                    VALUES ($1, $2, $3, $4)
                    """,
                    user_id,
                    username,
                    event_name,
                    json.dumps(metadata) if metadata else None,
                )
        except Exception:
            logger.exception("Failed to track event %s", event_name)

    # ------------------------------------------------------------------ #
    #  Aggregated metrics (cached)
    # ------------------------------------------------------------------ #

    async def get_stats(self) -> dict[str, Any]:
        """Return aggregated analytics. Cached in Redis for 60 s."""
        try:
            raw = await self._redis.get(_METRICS_CACHE_KEY)
            if raw:
                return json.loads(raw)
        except Exception:
            pass  # cache miss or error — compute fresh

        try:
            async with self._pool.acquire() as conn:
                total_users = await conn.fetchval(
                    "SELECT COUNT(DISTINCT user_id) FROM analytics_events WHERE user_id IS NOT NULL"
                )
                active_24h = await conn.fetchval(
                    "SELECT COUNT(DISTINCT user_id) FROM analytics_events "
                    "WHERE user_id IS NOT NULL AND created_at > NOW() - INTERVAL '24 hours'"
                )
                total_searches = await conn.fetchval(
                    "SELECT COUNT(*) FROM analytics_events WHERE event_name = 'player_search'"
                )
                total_api_calls = await conn.fetchval(
                    "SELECT COUNT(*) FROM analytics_events WHERE event_name = 'api_request'"
                )
                error_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM analytics_events WHERE event_name IN ('api_error', 'exception_occurred')"
                )
                top_commands_rows = await conn.fetch(
                    "SELECT metadata->>'command' AS cmd, COUNT(*) AS cnt "
                    "FROM analytics_events WHERE event_name = 'command_used' "
                    "AND metadata->>'command' IS NOT NULL "
                    "GROUP BY cmd ORDER BY cnt DESC LIMIT 10"
                )

            top_commands = [
                {"command": r["cmd"], "count": r["cnt"]}
                for r in top_commands_rows
            ]

            result = {
                "total_users": total_users or 0,
                "active_24h": active_24h or 0,
                "total_searches": total_searches or 0,
                "total_api_calls": total_api_calls or 0,
                "error_count": error_count or 0,
                "top_commands": top_commands,
            }

            # Cache result
            try:
                await self._redis.set(
                    _METRICS_CACHE_KEY,
                    json.dumps(result),
                    ex=METRICS_CACHE_TTL,
                )
            except Exception:
                pass

            return result

        except Exception:
            logger.exception("Failed to compute analytics stats")
            return {
                "total_users": 0,
                "active_24h": 0,
                "total_searches": 0,
                "total_api_calls": 0,
                "error_count": 0,
                "top_commands": [],
            }

    # ------------------------------------------------------------------ #
    #  Event queries
    # ------------------------------------------------------------------ #

    async def get_events_today(self) -> list[dict[str, Any]]:
        """Return event-name counts for today."""
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT event_name, COUNT(*) AS cnt "
                    "FROM analytics_events "
                    "WHERE created_at::date = CURRENT_DATE "
                    "GROUP BY event_name ORDER BY cnt DESC"
                )
            return [{"event": r["event_name"], "count": r["cnt"]} for r in rows]
        except Exception:
            logger.exception("Failed to get events_today")
            return []

    async def get_recent_errors(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent error / exception events."""
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT user_id, username, event_name, metadata, created_at "
                    "FROM analytics_events "
                    "WHERE event_name IN ('api_error', 'exception_occurred') "
                    "ORDER BY created_at DESC LIMIT $1",
                    limit,
                )
            return [
                {
                    "user_id": r["user_id"],
                    "username": r["username"],
                    "event": r["event_name"],
                    "metadata": json.loads(r["metadata"]) if r["metadata"] else None,
                    "time": r["created_at"].strftime("%Y-%m-%d %H:%M:%S"),
                }
                for r in rows
            ]
        except Exception:
            logger.exception("Failed to get recent_errors")
            return []

    async def get_recent_users(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return recently active distinct users."""
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT user_id, username, MAX(created_at) AS last_seen, COUNT(*) AS events "
                    "FROM analytics_events "
                    "WHERE user_id IS NOT NULL "
                    "GROUP BY user_id, username "
                    "ORDER BY last_seen DESC LIMIT $1",
                    limit,
                )
            return [
                {
                    "user_id": r["user_id"],
                    "username": r["username"] or "N/A",
                    "last_seen": r["last_seen"].strftime("%Y-%m-%d %H:%M"),
                    "events": r["events"],
                }
                for r in rows
            ]
        except Exception:
            logger.exception("Failed to get recent_users")
            return []

    async def get_recent_logs(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return the most recent events of any type."""
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT user_id, username, event_name, metadata, created_at "
                    "FROM analytics_events "
                    "ORDER BY created_at DESC LIMIT $1",
                    limit,
                )
            return [
                {
                    "user_id": r["user_id"],
                    "username": r["username"] or "—",
                    "event": r["event_name"],
                    "metadata": json.loads(r["metadata"]) if r["metadata"] else None,
                    "time": r["created_at"].strftime("%H:%M:%S"),
                }
                for r in rows
            ]
        except Exception:
            logger.exception("Failed to get recent_logs")
            return []
