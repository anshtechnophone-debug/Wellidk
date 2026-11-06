"""Central Telegram application error handler.

Unexpected update errors are reported privately to configured XYRON owners.
They must never create noisy error messages in public groups.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    if error is None:
        return

    # Telegram raises this when an edit tries to apply content/markup that is
    # already present. It is an idempotent no-op, not a failed command.
    if isinstance(error, BadRequest) and "message is not modified" in str(error).casefold():
        logger.debug("Ignored harmless unchanged-message edit")
        return

    if isinstance(error, TelegramError):
        logger.error("Telegram update failed: %s", error)
    else:
        logger.exception("Unhandled update error", exc_info=error)

    settings = context.application.bot_data.get("settings")
    owner_ids = tuple(getattr(settings, "owner_ids", ()) or ())
    if not owner_ids:
        logger.error("Could not DM error report: no configured XYRON owner IDs")
        return

    chat = update.effective_chat if isinstance(update, Update) else None
    user = update.effective_user if isinstance(update, Update) else None
    message = update.effective_message if isinstance(update, Update) else None
    command_text = (message.text or message.caption or "") if message else ""
    location = (
        f"Chat: {chat.title or chat.id} (ID: {chat.id})"
        if chat else "Chat: unavailable"
    )
    actor = (
        f"User: {user.full_name} (ID: {user.id})"
        if user else "User: unavailable"
    )
    report = (
        "⚠️ XYRON update error\n"
        f"{location}\n"
        f"{actor}\n"
        f"Update: {command_text[:1200] or 'No message text'}\n"
        f"Error: {type(error).__name__}: {str(error)[:2500]}"
    )

    # Diagnostics go only to configured owner DMs, never to the originating chat.
    for owner_id in owner_ids:
        try:
            await context.bot.send_message(chat_id=owner_id, text=report)
        except TelegramError as exc:
            # An owner may have deleted/deactivated their Telegram account or
            # blocked the bot. A failed diagnostic DM must not raise a second
            # traceback or obscure the original update error.
            if "user is deactivated" in str(exc).casefold():
                logger.warning(
                    "Skipping error DM: configured owner account is deactivated "
                    "(owner_id=%s)",
                    owner_id,
                )
            else:
                logger.warning(
                    "Could not DM XYRON error report to owner_id=%s: %s",
                    owner_id,
                    exc,
                )
