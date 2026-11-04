"""Central Telegram application error handler."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    if isinstance(error, TelegramError):
        logger.error("Telegram update failed: %s", error.message)
    else:
        logger.exception("Unhandled update error", exc_info=error)
    if isinstance(update, Update) and update.effective_message:
        # Show the real exception instead of hiding every failure behind a
        # generic message. Send as plain text so HTML/Markdown in an exception
        # can never break delivery of the error itself.
        detail = str(error).strip() if error is not None else ""
        if not detail:
            detail = type(error).__name__ if error is not None else "Unknown error"
        detail = detail[:3500]
        try:
            await update.effective_message.reply_text(
                f"⚠️ XYRON error: {detail}",
                parse_mode=None,
            )
        except TelegramError:
            logger.exception("Could not send the actual error notice to chat")
