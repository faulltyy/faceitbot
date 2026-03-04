"""Admin-only Telegram handlers — analytics dashboard and log viewer.

Every handler checks ``ADMIN_ID`` and silently returns for non-admins.
"""

from __future__ import annotations

import logging

from aiogram import Router, types
from aiogram.filters import Command

from app.config import ADMIN_ID
from app.services.analytics import AnalyticsService

logger = logging.getLogger(__name__)

admin_router = Router(name="admin")


# ---- guard ---------------------------------------------------------------- #

def _is_admin(message: types.Message) -> bool:
    """Return True only if the sender is the configured admin."""
    return message.from_user is not None and message.from_user.id == ADMIN_ID


# ---- /admin --------------------------------------------------------------- #

@admin_router.message(Command("admin"))
async def cmd_admin(message: types.Message) -> None:
    if not _is_admin(message):
        return

    text = (
        "🔐 <b>Admin Panel</b>\n\n"
        "Available commands:\n"
        "  /astats — analytics overview\n"
        "  /logs — recent events\n"
        "  /errors — recent errors\n"
        "  /users — recent users\n"
        "  /events_today — today's event breakdown\n"
    )
    await message.answer(text, parse_mode="HTML")


# ---- /astats -------------------------------------------------------------- #

@admin_router.message(Command("astats"))
async def cmd_astats(message: types.Message, analytics: AnalyticsService) -> None:
    if not _is_admin(message):
        return

    stats = await analytics.get_stats()

    # Format top commands
    top_lines = ""
    for i, cmd in enumerate(stats["top_commands"][:5], start=1):
        top_lines += f"  {i}. {cmd['command']} — {cmd['count']:,}\n"
    if not top_lines:
        top_lines = "  No command data yet.\n"

    text = (
        "📊 <b>BOT ANALYTICS</b>\n\n"
        f"👥 Total Users: {stats['total_users']:,}\n"
        f"🔥 Active (24h): {stats['active_24h']:,}\n"
        f"🔎 Searches: {stats['total_searches']:,}\n"
        f"🌐 API Requests: {stats['total_api_calls']:,}\n"
        f"❌ Errors: {stats['error_count']:,}\n\n"
        f"<b>Top Commands:</b>\n{top_lines}"
    )
    await message.answer(text, parse_mode="HTML")


# ---- /logs ---------------------------------------------------------------- #

@admin_router.message(Command("logs"))
async def cmd_logs(message: types.Message, analytics: AnalyticsService) -> None:
    if not _is_admin(message):
        return

    logs = await analytics.get_recent_logs(limit=20)
    if not logs:
        await message.answer("📜 No logs yet.")
        return

    lines = ["📜 <b>Recent Events</b>\n"]
    for entry in logs:
        meta_str = ""
        if entry["metadata"]:
            # Show first key-value pair for brevity
            for k, v in entry["metadata"].items():
                meta_str = f" ({k}={v})"
                break

        lines.append(
            f"<code>{entry['time']}</code> "
            f"<b>{entry['event']}</b> "
            f"@{entry['username']}{meta_str}"
        )

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n…"
    await message.answer(text, parse_mode="HTML")


# ---- /errors -------------------------------------------------------------- #

@admin_router.message(Command("errors"))
async def cmd_errors(message: types.Message, analytics: AnalyticsService) -> None:
    if not _is_admin(message):
        return

    errors = await analytics.get_recent_errors(limit=15)
    if not errors:
        await message.answer("✅ No recent errors!")
        return

    lines = ["❌ <b>Recent Errors</b>\n"]
    for err in errors:
        error_msg = ""
        if err["metadata"]:
            error_msg = f": {err['metadata'].get('error', '')[:80]}"
        lines.append(
            f"<code>{err['time']}</code> "
            f"<b>{err['event']}</b>{error_msg}"
        )

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n…"
    await message.answer(text, parse_mode="HTML")


# ---- /users --------------------------------------------------------------- #

@admin_router.message(Command("users"))
async def cmd_users(message: types.Message, analytics: AnalyticsService) -> None:
    if not _is_admin(message):
        return

    users = await analytics.get_recent_users(limit=20)
    if not users:
        await message.answer("👥 No users yet.")
        return

    lines = ["👥 <b>Recent Users</b>\n"]
    for u in users:
        lines.append(
            f"@{u['username']} "
            f"(id: <code>{u['user_id']}</code>) — "
            f"{u['events']} events, last: {u['last_seen']}"
        )

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n…"
    await message.answer(text, parse_mode="HTML")


# ---- /events_today -------------------------------------------------------- #

@admin_router.message(Command("events_today"))
async def cmd_events_today(message: types.Message, analytics: AnalyticsService) -> None:
    if not _is_admin(message):
        return

    events = await analytics.get_events_today()
    if not events:
        await message.answer("📅 No events today.")
        return

    total = sum(e["count"] for e in events)
    lines = [f"📅 <b>Events Today</b> ({total:,} total)\n"]
    for ev in events:
        lines.append(f"  • <b>{ev['event']}</b> — {ev['count']:,}")

    await message.answer("\n".join(lines), parse_mode="HTML")
