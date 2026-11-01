"""Per-group delayed cleanup for media messages."""

from __future__ import annotations

import asyncio
import logging

from telegram import Chat, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from app.core.permissions import AuthorizationError, require_admin_access
from app.services.group_policy import GroupPolicyRepository

logger = logging.getLogger(__name__)
MEDIA_DELETE_DELAY_SECONDS = 180
_MEDIA_FIELDS = (
    "photo",
    "video",
    "animation",
    "document",
    "audio",
    "voice",
    "video_note",
    "sticker",
    "paid_media",
)


def _is_media_message(message) -> bool:
    return any(getattr(message, field, None) for field in _MEDIA_FIELDS)


async def mediaclean_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Enable or disable automatic three-minute media cleanup in this group."""
    message, chat = update.effective_message, update.effective_chat
    if not message:
        return
    if not chat or chat.type not in {Chat.GROUP, Chat.SUPERGROUP}:
        await message.reply_text("Use /mediaclean inside the group you want to configure.")
        return

    try:
        await require_admin_access(
            update, context, context.application.bot_data["settings"], "can_delete_messages"
        )
    except AuthorizationError as exc:
        await message.reply_text(str(exc))
        return

    if len(context.args) != 1 or context.args[0].casefold() not in {"on", "off"}:
        await message.reply_text("Usage: /mediaclean on or /mediaclean off")
        return

    enabled = context.args[0].casefold() == "on"
    await GroupPolicyRepository(
        context.application.bot_data["sessions"]
    ).set_value(chat.id, "media_cleaner", {"enabled": enabled})

    await message.reply_text(
        "🧹 Media auto-clean is ON. Photos, videos, GIFs, stickers, documents, "
        "audio and voice messages will be deleted after 3 minutes."
        if enabled
        else "Media auto-clean is OFF. New media messages will no longer be scheduled for deletion."
    )


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
    """Schedule deletion of each media message when the group opted in."""
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
    cleaner = policy.get("media_cleaner", {})
    if not isinstance(cleaner, dict) or not cleaner.get("enabled"):
        return

    context.application.create_task(
        _delete_after_delay(context.bot, chat.id, message.message_id),
        update=update,
        name=f"xyron-media-delete-{chat.id}-{message.message_id}",
    )
