"""Async PostgreSQL connection pool via asyncpg."""

from __future__ import annotations

import logging

import asyncpg

from app.config import DATABASE_URL

logger = logging.getLogger(__name__)


async def create_pool() -> asyncpg.Pool:
    """Create and return an asyncpg connection pool."""
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    logger.info("PostgreSQL pool created (%s)", DATABASE_URL.split("@")[-1])
    return pool


async def close_pool(pool: asyncpg.Pool) -> None:
    """Gracefully close the connection pool."""
    await pool.close()
    logger.info("PostgreSQL pool closed")
