"""Central Telegram application error handler.

Unexpected update errors are reported privately to configured XYRON owners.
They must never create noisy error messages in public groups.
"""

from __future__ import annotations

import logging
import time

from telegram import Update
from telegram.error import BadRequest, RetryAfter, TelegramError, TimedOut
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)
_REPORT_COOLDOWN_SECONDS = 60.0
_last_reports: dict[str, float] = {}


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    if error is None:
        return

    detail = str(error).casefold()

    # These are expected transient Telegram conditions. Reporting every
    # occurrence creates a second flood and does not help the owner diagnose it.
    if isinstance(error, RetryAfter):
        logger.warning("Telegram rate limit reached; retry after %s seconds", error.retry_after)
        return
    if isinstance(error, TimedOut):
        logger.warning("Telegram request timed out; update will not be error-DM'd")
        return
    if isinstance(error, BadRequest) and any(marker in detail for marker in (
        "message is not modified",
        "query is too old",
        "response timeout expired",
        "peer_id_invalid",
        "message to be replied not found",
    )):
        logger.debug("Ignored expected stale/unavailable Telegram operation: %s", error)
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
    report_key = f"{type(error).__name__}:{detail[:180]}:{chat.id if chat else 'none'}"
    now = time.monotonic()
    previous = _last_reports.get(report_key, 0.0)
    if now - previous < _REPORT_COOLDOWN_SECONDS:
        logger.debug("Suppressed duplicate owner error report: %s", report_key)
        return
    _last_reports[report_key] = now
    # Bound the in-memory deduplication cache during long-running deployments.
    if len(_last_reports) > 1000:
        cutoff = now - _REPORT_COOLDOWN_SECONDS
        for key, sent_at in list(_last_reports.items()):
            if sent_at < cutoff:
                _last_reports.pop(key, None)

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
