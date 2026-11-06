"""Central registry for Telegram users XYRON has encountered."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from telegram import Chat, Update
from telegram.ext import ContextTypes

from app.database.models import GlobalUser

logger = logging.getLogger(__name__)


async def _record_user(user, chat, sessions) -> None:
    if user is None or sessions is None:
        return
    if chat is not None and chat.type not in {Chat.PRIVATE, Chat.GROUP, Chat.SUPERGROUP}:
        return

    now = datetime.now(UTC)
    try:
        async with sessions.begin() as session:
            record = await session.get(GlobalUser, user.id)
            if record is None:
                record = GlobalUser(
                    user_id=user.id,
                    username=user.username.casefold() if user.username else None,
                    first_name=user.first_name or "",
                    last_name=user.last_name or "",
                    is_bot=bool(user.is_bot),
                    seen_in_private=bool(chat and chat.type == Chat.PRIVATE),
                    seen_in_groups=bool(chat and chat.type in {Chat.GROUP, Chat.SUPERGROUP}),
                    first_seen=now,
                    last_seen=now,
                )
                session.add(record)
            else:
                record.username = user.username.casefold() if user.username else None
                record.first_name = user.first_name or ""
                record.last_name = user.last_name or ""
                record.is_bot = bool(user.is_bot)
                if chat and chat.type == Chat.PRIVATE:
                    record.seen_in_private = True
                elif chat and chat.type in {Chat.GROUP, Chat.SUPERGROUP}:
                    record.seen_in_groups = True
                record.last_seen = now
    except Exception:
        logger.exception("Could not update global user registry for user_id=%s", user.id)


async def track_global_user(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Record users who send messages or commands in private chats and groups."""
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if message is None or user is None:
        return
    await _record_user(user, chat, context.application.bot_data.get("sessions"))


async def track_global_chat_member(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Record group users when Telegram reports a membership change."""
    member_update = update.chat_member
    if member_update is None:
        return
    await _record_user(
        member_update.new_chat_member.user,
        member_update.chat,
        context.application.bot_data.get("sessions"),
    )
