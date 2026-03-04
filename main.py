"""Entry point — wires up PostgreSQL, Redis, FACEIT client, analytics, and Telegram bot."""

from __future__ import annotations

import asyncio
import logging

import redis.asyncio as aioredis
from aiogram import Bot, Dispatcher

from app.bot.admin_handlers import admin_router
from app.bot.handlers import on_startup, router
from app.config import ADMIN_ID, REDIS_URL, TELEGRAM_BOT_TOKEN
from app.db.migrations import run_migrations
from app.db.pool import close_pool, create_pool
from app.middleware.analytics import AnalyticsMiddleware
from app.services.admin_logger import setup_logging
from app.services.analytics import AnalyticsService

# Structured logging (console + rotating JSON file)
setup_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    from app.api.faceit import FaceitClient

    # --- PostgreSQL ---
    pg_pool = await create_pool()
    await run_migrations(pg_pool)
    logger.info("PostgreSQL ready, migrations applied")

    # --- Redis ---
    redis = aioredis.from_url(REDIS_URL, decode_responses=False)
    logger.info("Connected to Redis at %s", REDIS_URL)

    try:
        await redis.flushall()
        logger.info("Redis cache flushed on startup")
    except Exception as exc:
        logger.warning("Could not flush Redis (read-only?): %s", exc)

    # --- Analytics service ---
    analytics = AnalyticsService(pool=pg_pool, redis=redis)
    logger.info("AnalyticsService ready")

    # --- FACEIT API client ---
    faceit_client = FaceitClient()
    await faceit_client.open()
    logger.info("FACEIT API client ready")

    # --- Telegram bot ---
    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    dp = Dispatcher()
    dp.include_router(admin_router)   # admin commands first (priority)
    dp.include_router(router)

    # Shared resources — handlers receive them as kwargs
    dp["faceit_client"] = faceit_client
    dp["redis"] = redis
    dp["analytics"] = analytics

    # Register analytics middleware on message & callback_query
    dp.message.middleware(AnalyticsMiddleware())
    dp.callback_query.middleware(AnalyticsMiddleware())

    # Register bot commands on startup
    dp.startup.register(on_startup)

    logger.info("Admin ID: %s", ADMIN_ID or "NOT SET")
    logger.info("Starting Telegram bot polling …")
    try:
        await dp.start_polling(bot)
    finally:
        await faceit_client.close()
        await redis.aclose()
        await close_pool(pg_pool)
        await bot.session.close()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
