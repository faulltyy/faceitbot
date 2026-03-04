"""Telegram bot command handlers: /start, /help, /stats, /matches.

Also exposes ``on_startup()`` which registers the bot-menu commands via
``bot.set_my_commands()`` so Telegram shows an autocomplete hint list.
"""

from __future__ import annotations

import logging

from aiogram import Bot, Router, types
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand

from app.api.faceit import FaceitApiError, NoMatchesFound, PlayerNotFound

logger = logging.getLogger(__name__)

router = Router(name="faceit_stats")


# ---- Bot-menu registration ----------------------------------------------- #

BOT_COMMANDS = [
    BotCommand(command="stats", description="Average stats for the last 20 CS2 matches"),
    BotCommand(command="matches", description="Per-match stats: /matches nick [count]"),
    BotCommand(command="help", description="Show help and usage examples"),
    BotCommand(command="admin", description="Admin panel (authorized only)"),
    BotCommand(command="astats", description="Bot analytics (admin only)"),
]


async def on_startup(bot: Bot) -> None:
    """Register slash-commands so Telegram shows the native autocomplete menu."""
    await bot.set_my_commands(BOT_COMMANDS)
    logger.info("Bot commands registered: %s", [c.command for c in BOT_COMMANDS])


# ---- /start & /help ------------------------------------------------------ #

HELP_TEXT = (
    "👋 <b>Welcome to FACEIT CS2 Stats Bot!</b>\n\n"
    "I can look up any player's recent CS2 statistics from FACEIT.\n\n"
    "<b>Commands:</b>\n"
    "  /stats &lt;nickname&gt; — average stats for the last 20 matches\n"
    "  /matches &lt;nickname&gt; [count] — last N matches (default 20, max 30)\n"
    "  /help — show this message\n\n"
    "<b>Examples:</b>\n"
    "  <code>/stats s1mple</code>\n"
    "  <code>/matches s1mple</code>\n"
    "  <code>/matches s1mple 10</code>\n\n"
    "💡 Just type <code>/</code> to see the command menu."
)


@router.message(CommandStart())
async def cmd_start(message: types.Message) -> None:
    """Handle ``/start``."""
    await message.answer(HELP_TEXT, parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(message: types.Message) -> None:
    """Handle ``/help``."""
    await message.answer(HELP_TEXT, parse_mode="HTML")


# ---- /stats -------------------------------------------------------------- #

@router.message(Command("stats"))
async def cmd_stats(message: types.Message, faceit_client, redis, analytics=None) -> None:
    """Handle ``/stats <nickname>``."""

    args = message.text.split(maxsplit=1) if message.text else []
    if len(args) < 2 or not args[1].strip():
        await message.answer(
            "⚠️ Usage: /stats <nickname>\nExample: /stats s1mple"
        )
        return

    nickname = args[1].strip()

    from app.services.stats import get_player_stats

    await message.answer(f"🔍 Looking up stats for {nickname} …")

    try:
        result = await get_player_stats(nickname, faceit_client, redis)
        await message.answer(result)
        if analytics:
            await analytics.track_event(
                user_id=message.from_user.id if message.from_user else None,
                username=message.from_user.username if message.from_user else None,
                event_name="player_search",
                metadata={"nickname": nickname, "type": "stats"},
            )
    except PlayerNotFound:
        await message.answer(f"❌ Player {nickname} not found on FACEIT.")
    except NoMatchesFound:
        await message.answer(f"ℹ️ No CS2 matches found for {nickname}.")
    except FaceitApiError as exc:
        logger.exception("FACEIT API error for nickname=%s", nickname)
        if analytics:
            await analytics.track_event(
                user_id=message.from_user.id if message.from_user else None,
                username=message.from_user.username if message.from_user else None,
                event_name="api_error",
                metadata={"nickname": nickname, "error": str(exc)[:200]},
            )
        await message.answer(
            f"⚠️ FACEIT API error: {exc}\nPlease try again later."
        )
    except Exception:
        logger.exception("Unexpected error for nickname=%s", nickname)
        await message.answer("❌ An unexpected error occurred. Please try again later.")


# ---- /matches ------------------------------------------------------------ #

@router.message(Command("matches"))
async def cmd_matches(message: types.Message, faceit_client, redis, analytics=None) -> None:
    """Handle ``/matches <nickname> [count]``."""

    parts = message.text.split() if message.text else []
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "⚠️ Usage: /matches <nickname> [count]\n"
            "Example: /matches s1mple 15\n"
            "Count is optional (1-30, default 20)."
        )
        return

    nickname = parts[1].strip()

    # Parse optional count
    limit = 20
    if len(parts) >= 3:
        try:
            limit = max(1, min(30, int(parts[2])))
        except ValueError:
            pass  # ignore bad count, use default

    from app.services.stats import get_player_matches_table

    await message.answer(f"🔍 Looking up last {limit} matches for {nickname} …")

    try:
        result = await get_player_matches_table(nickname, faceit_client, redis, limit=limit)
        await message.answer(result, parse_mode="HTML")
        if analytics:
            await analytics.track_event(
                user_id=message.from_user.id if message.from_user else None,
                username=message.from_user.username if message.from_user else None,
                event_name="player_search",
                metadata={"nickname": nickname, "type": "matches", "limit": limit},
            )
    except PlayerNotFound:
        await message.answer(f"❌ Player {nickname} not found on FACEIT.")
    except NoMatchesFound:
        await message.answer(f"ℹ️ No CS2 matches found for {nickname}.")
    except FaceitApiError as exc:
        logger.exception("FACEIT API error for nickname=%s", nickname)
        if analytics:
            await analytics.track_event(
                user_id=message.from_user.id if message.from_user else None,
                username=message.from_user.username if message.from_user else None,
                event_name="api_error",
                metadata={"nickname": nickname, "error": str(exc)[:200]},
            )
        await message.answer(
            f"⚠️ FACEIT API error: {exc}\nPlease try again later."
        )
    except Exception:
        logger.exception("Unexpected error for nickname=%s", nickname)
        await message.answer("❌ An unexpected error occurred. Please try again later.")
