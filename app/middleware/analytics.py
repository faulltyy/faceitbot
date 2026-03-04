"""Analytics middleware — auto-tracks commands, callbacks, and exceptions."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

logger = logging.getLogger(__name__)


class AnalyticsMiddleware(BaseMiddleware):
    """Intercepts every update to log analytics events automatically.

    Expects ``data["analytics"]`` to be an :class:`AnalyticsService` instance
    injected via the dispatcher's workflow data.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        analytics = data.get("analytics")
        if analytics is None:
            return await handler(event, data)

        user_id: int | None = None
        username: str | None = None
        event_name: str | None = None
        metadata: dict[str, Any] | None = None

        try:
            # --- Message (commands, text) ---
            if isinstance(event, Message) and event.from_user:
                user_id = event.from_user.id
                username = event.from_user.username

                if event.text and event.text.startswith("/"):
                    command = event.text.split()[0].split("@")[0]  # strip @botname
                    event_name = "command_used"
                    metadata = {"command": command}

                    if command == "/start":
                        # Track bot_started as a separate event too
                        await analytics.track_event(
                            user_id=user_id,
                            username=username,
                            event_name="bot_started",
                        )

            # --- Callback query (button clicks) ---
            elif isinstance(event, CallbackQuery) and event.from_user:
                user_id = event.from_user.id
                username = event.from_user.username
                event_name = "button_clicked"
                metadata = {"callback_data": event.data}

        except Exception:
            logger.exception("Error extracting analytics context")

        # --- Run the actual handler ---
        try:
            result = await handler(event, data)
        except Exception as exc:
            # Track the exception
            try:
                await analytics.track_event(
                    user_id=user_id,
                    username=username,
                    event_name="exception_occurred",
                    metadata={
                        "error": str(exc)[:500],
                        "type": type(exc).__name__,
                    },
                )
            except Exception:
                logger.exception("Failed to track exception event")
            raise  # re-raise so aiogram handles it normally

        # --- Track the event (after successful handler execution) ---
        if event_name:
            try:
                await analytics.track_event(
                    user_id=user_id,
                    username=username,
                    event_name=event_name,
                    metadata=metadata,
                )
            except Exception:
                logger.exception("Failed to track event %s", event_name)

        return result
