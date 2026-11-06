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
    """Remember every user who interacts with XYRON in a private chat."""
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


async def _wait_for_stop(stop_event: asyncio.Event, seconds: float) -> bool:
    """Wait for the stop signal, returning True when cancellation was requested."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=max(0.0, seconds))
        return True
    except TimeoutError:
        return False


async def broadcast_stop_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    user = update.effective_user
    settings = context.application.bot_data["settings"]
    if not user or not settings.is_owner(user.id):
        if message:
            await message.reply_text("Only the bot owner can use this command.")
        return
    if not message or not update.effective_chat or update.effective_chat.type != Chat.PRIVATE:
        if message:
            await message.reply_text("Use /broadcast_stop in the bot's private chat.")
        return
    stop_event = context.application.bot_data.get("broadcast_stop_event")
    if stop_event is None or stop_event.is_set():
        await message.reply_text("No broadcast is currently running.")
        return
    stop_event.set()
    await message.reply_text("Stopping the broadcast after the current delivery...")


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

    await lock.acquire()

    async def run_broadcast() -> None:
        try:
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

            stop_event = asyncio.Event()
            context.application.bot_data["broadcast_stop_event"] = stop_event
            status = await message.reply_text(
                f"Broadcast started.\nTotal: {total}\nSent: 0\nFailed: 0\nRemaining: {total}"
            )
            sent = 0
            failed = 0
            stopped = False
            try:
                for index, recipient_id in enumerate(recipient_ids):
                    if stop_event.is_set():
                        stopped = True
                        break
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
                            if await _wait_for_stop(stop_event, wait_seconds):
                                stopped = True
                                break
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
                    if (index + 1) % 10 == 0 or remaining == 0 or stop_event.is_set():
                        try:
                            await status.edit_text(
                                f"Broadcast running.\nTotal: {total}\nSent: {sent}\n"
                                f"Failed: {failed}\nRemaining: {remaining}"
                            )
                        except TelegramError:
                            logger.debug("Could not update broadcast progress message", exc_info=True)
                    if stop_event.is_set():
                        stopped = True
                        break
                    if delay and remaining and await _wait_for_stop(stop_event, delay):
                        stopped = True
                        break

                state = "stopped" if stopped or stop_event.is_set() else "completed"
                logger.info(
                    "Owner broadcast %s owner_id=%s total=%s sent=%s failed=%s delay=%s",
                    state, user.id, total, sent, failed, delay,
                )
                try:
                    await status.edit_text(
                        f"Broadcast {state}.\nTotal: {total}\nSent: {sent}\n"
                        f"Failed: {failed}\nRemaining: {max(0, total - sent - failed)}"
                    )
                except TelegramError:
                    await message.reply_text(
                        f"Broadcast {state}. Total: {total}; sent: {sent}; failed: {failed}."
                    )
            finally:
                context.application.bot_data.pop("broadcast_stop_event", None)

        finally:
            lock.release()

    context.application.create_task(
        run_broadcast(),
        name="xyron-broadcast-worker",
    )
    await message.reply_text("Broadcast started in the background. Other commands and replies will continue working.")
