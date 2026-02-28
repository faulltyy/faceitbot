"""Entry point — wires up Redis, FACEIT client, and Telegram bot polling."""

from __future__ import annotations

import asyncio
import logging

import redis.asyncio as aioredis
from aiogram import Bot, Dispatcher

from app.bot.handlers import router
from app.config import REDIS_URL, TELEGRAM_BOT_TOKEN

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    from app.api.faceit import FaceitClient

    # --- Redis ---
    redis = aioredis.from_url(REDIS_URL, decode_responses=False)
    logger.info("Connected to Redis at %s", REDIS_URL)

    # --- FACEIT API client ---
    faceit_client = FaceitClient()
    await faceit_client.open()
    logger.info("FACEIT API client ready")

    # --- Telegram bot ---
    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    dp = Dispatcher()
    dp.include_router(router)

    # Store shared resources as workflow data — handlers receive them as kwargs
    dp["faceit_client"] = faceit_client
    dp["redis"] = redis

    logger.info("Starting Telegram bot polling …")
    try:
        await dp.start_polling(bot)
    finally:
        await faceit_client.close()
        await redis.aclose()
        await bot.session.close()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
