"""Telegram bot /stats command handler."""

from __future__ import annotations

import logging

from aiogram import Router, types
from aiogram.filters import Command

from app.api.faceit import FaceitApiError, NoMatchesFound, PlayerNotFound

logger = logging.getLogger(__name__)

router = Router(name="stats")


@router.message(Command("stats"))
async def cmd_stats(message: types.Message, faceit_client, redis) -> None:
    """Handle ``/stats <nickname>``."""

    args = message.text.split(maxsplit=1) if message.text else []
    if len(args) < 2 or not args[1].strip():
        await message.answer(
            "⚠️ Usage: /stats <nickname>\nExample: /stats s1mple"
        )
        return

    nickname = args[1].strip()

    # Lazy imports to avoid circular deps at module level
    from app.services.stats import get_player_stats  # noqa: WPS433

    await message.answer(f"🔍 Looking up stats for **{nickname}** …", parse_mode="Markdown")

    try:
        result = await get_player_stats(nickname, faceit_client, redis)
        await message.answer(result)
    except PlayerNotFound:
        await message.answer(f"❌ Player **{nickname}** not found on FACEIT.", parse_mode="Markdown")
    except NoMatchesFound:
        await message.answer(
            f"ℹ️ No CS2 matches found for **{nickname}**.", parse_mode="Markdown"
        )
    except FaceitApiError as exc:
        logger.exception("FACEIT API error for nickname=%s", nickname)
        await message.answer(
            f"⚠️ FACEIT API error: {exc}\nPlease try again later."
        )
    except Exception:
        logger.exception("Unexpected error for nickname=%s", nickname)
        await message.answer("❌ An unexpected error occurred. Please try again later.")
