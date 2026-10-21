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
        try:
            await update.effective_message.reply_text("XYRON could not complete that request.")
        except TelegramError:
            logger.info("Could not send error notice to chat")
