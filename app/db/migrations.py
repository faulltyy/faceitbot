"""Startup database migrations — run once on bot start."""

from __future__ import annotations

import logging

import asyncpg

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS analytics_events (
    id         BIGSERIAL   PRIMARY KEY,
    user_id    BIGINT,
    username   TEXT,
    event_name TEXT        NOT NULL,
    metadata   JSONB,
    created_at TIMESTAMP   DEFAULT NOW()
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_events_name    ON analytics_events(event_name);",
    "CREATE INDEX IF NOT EXISTS idx_events_created ON analytics_events(created_at);",
    "CREATE INDEX IF NOT EXISTS idx_events_user    ON analytics_events(user_id);",
]


async def run_migrations(pool: asyncpg.Pool) -> None:
    """Create tables and indexes if they do not exist."""
    async with pool.acquire() as conn:
        await conn.execute(_CREATE_TABLE)
        for idx_sql in _CREATE_INDEXES:
            await conn.execute(idx_sql)
    logger.info("Database migrations completed")
