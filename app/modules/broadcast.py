"""Owner-only private-message broadcasting."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from telegram import Chat, Update
from telegram.error import RetryAfter, TelegramError
from telegram.ext import ContextTypes

from app.database.models import BroadcastRecipient

logger = logging.getLogger(__name__)


async def track_private_recipient(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Remember users who have actually interacted with XYRON in private chat."""
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    sessions = context.application.bot_data.get("sessions")
    if not message or not chat or chat.type != Chat.PRIVATE or not user or not sessions:
        return
    now = datetime.now(UTC)
    try:
        async with sessions.begin() as session:
            recipient = await session.get(BroadcastRecipient, user.id)
            if recipient is None:
                session.add(BroadcastRecipient(user_id=user.id, first_seen=now, last_seen=now))
            else:
                recipient.last_seen = now
    except Exception:
        logger.exception("Could not save private broadcast recipient user_id=%s", user.id)


async def broadcast_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    settings = context.application.bot_data["settings"]
    if not user or not settings.is_owner(user.id):
        if message:
            await message.reply_text("Only the bot owner can use this command.")
        return
    if not message or not chat or chat.type != Chat.PRIVATE:
        if message:
            await message.reply_text("Use /broadcast in the bot's private chat.")
        return
    if len(context.args) != 1 or not context.args[0].isdecimal():
        await message.reply_text("Usage: reply to a message with /broadcast <seconds>.")
        return
    delay = int(context.args[0])
    if not 0 <= delay <= 3600:
        await message.reply_text("Delay must be between 0 and 3600 seconds.")
        return
    source = message.reply_to_message
    if source is None:
        await message.reply_text("Reply to the message you want to broadcast.")
        return

    lock = context.application.bot_data.setdefault("broadcast_lock", asyncio.Lock())
    if lock.locked():
        await message.reply_text("A broadcast is already running. Please wait for it to finish.")
        return

    async with lock:
        sessions = context.application.bot_data.get("sessions")
        if sessions is None:
            await message.reply_text("Broadcast recipient storage is unavailable.")
            return
        async with sessions() as session:
            recipient_ids = list((await session.scalars(
                select(BroadcastRecipient.user_id).order_by(BroadcastRecipient.first_seen)
            )).all())
        recipient_ids = [uid for uid in recipient_ids if uid not in settings.owner_ids]
        total = len(recipient_ids)
        if not total:
            await message.reply_text("No private-chat recipients have been recorded yet.")
            return

        status = await message.reply_text(
            f"Broadcast started.\nTotal: {total}\nSent: 0\nFailed: 0\nRemaining: {total}"
        )
        sent = 0
        failed = 0
        for index, recipient_id in enumerate(recipient_ids):
            try:
                try:
                    await context.bot.copy_message(
                        chat_id=recipient_id,
                        from_chat_id=chat.id,
                        message_id=source.message_id,
                    )
                except RetryAfter as exc:
                    wait_seconds = max(1, int(exc.retry_after))
                    logger.warning("Telegram rate limit during broadcast; waiting %ss", wait_seconds)
                    await asyncio.sleep(wait_seconds)
                    await context.bot.copy_message(
                        chat_id=recipient_id,
                        from_chat_id=chat.id,
                        message_id=source.message_id,
                    )
                sent += 1
            except TelegramError as exc:
                failed += 1
                logger.info("Broadcast delivery failed user_id=%s error=%s", recipient_id, exc)
            except Exception:
                failed += 1
                logger.exception("Unexpected broadcast delivery failure user_id=%s", recipient_id)

            remaining = total - index - 1
            if (index + 1) % 10 == 0 or remaining == 0:
                try:
                    await status.edit_text(
                        f"Broadcast running.\nTotal: {total}\nSent: {sent}\n"
                        f"Failed: {failed}\nRemaining: {remaining}"
                    )
                except TelegramError:
                    logger.debug("Could not update broadcast progress message", exc_info=True)
            if delay and remaining:
                await asyncio.sleep(delay)

        logger.info(
            "Owner broadcast completed owner_id=%s total=%s sent=%s failed=%s delay=%s",
            user.id, total, sent, failed, delay,
        )
        try:
            await status.edit_text(
                f"Broadcast completed.\nTotal: {total}\nSent: {sent}\nFailed: {failed}\nRemaining: 0"
            )
        except TelegramError:
            await message.reply_text(
                f"Broadcast completed. Total: {total}; sent: {sent}; failed: {failed}."
            )
