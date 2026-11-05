"""Five-minute media cleanup controlled by Smart GC."""

from __future__ import annotations

import asyncio
import logging

from telegram import Chat, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from app.services.group_policy import GroupPolicyRepository

logger = logging.getLogger(__name__)
MEDIA_DELETE_DELAY_SECONDS = 300
_MEDIA_FIELDS = (
    "photo",
    "video",
    "animation",
    "document",
    "audio",
    "sticker",
)


def _is_media_message(message) -> bool:
    return any(getattr(message, field, None) for field in _MEDIA_FIELDS)


async def _delete_after_delay(bot, chat_id: int, message_id: int) -> None:
    await asyncio.sleep(MEDIA_DELETE_DELAY_SECONDS)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info(
            "Deleted media message after %s seconds: chat=%s message=%s",
            MEDIA_DELETE_DELAY_SECONDS,
            chat_id,
            message_id,
        )
    except TelegramError:
        # This can happen if an admin already deleted it, or XYRON lacks
        # delete_messages permission. Do not let one failure stop other jobs.
        logger.warning(
            "Could not delete scheduled media message: chat=%s message=%s",
            chat_id,
            message_id,
            exc_info=True,
        )


async def delayed_media_delete_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Schedule Smart GC media cleanup, excluding voice notes and approved users."""
    message, chat = update.effective_message, update.effective_chat
    if (
        not message
        or not chat
        or chat.type not in {Chat.GROUP, Chat.SUPERGROUP}
        or not _is_media_message(message)
    ):
        return

    sessions = context.application.bot_data.get("sessions")
    if sessions is None:
        return
    policy = await GroupPolicyRepository(sessions).get(chat.id)
    smart_gc = policy.get("smart_gc", {})
    approved = {int(value) for value in policy.get("approved_users", [])}
    if not isinstance(smart_gc, dict) or not smart_gc.get("enabled"):
        return
    if message.from_user and message.from_user.id in approved:
        return

    context.application.create_task(
        _delete_after_delay(context.bot, chat.id, message.message_id),
        update=update,
        name=f"xyron-media-delete-{chat.id}-{message.message_id}",
    )
