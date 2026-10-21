"""Telegram commands for durable, user-owned reminders."""

from __future__ import annotations

from datetime import UTC

from telegram import Update
from telegram.ext import ContextTypes

from app.core.durations import DurationError, parse_duration
from app.services.reminders import ReminderRepository


def _repository(context: ContextTypes.DEFAULT_TYPE) -> ReminderRepository:
    return ReminderRepository(context.application.bot_data["sessions"])


async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user:
        return
    if len(context.args) < 2:
        await message.reply_text(
            "Usage: /remind <duration> <message>, for example /remind 30m drink water"
        )
        return
    try:
        delay = parse_duration(context.args[0])
    except DurationError as exc:
        await message.reply_text(str(exc))
        return
    reminder_text = " ".join(context.args[1:]).strip()
    if not 1 <= len(reminder_text) <= 1000:
        await message.reply_text("Reminder text must contain 1-1000 characters.")
        return
    settings = context.application.bot_data["settings"]
    existing = await _repository(context).list_for_user(chat.id, user.id, limit=1000)
    if len(existing) >= settings.max_reminders_per_user:
        await message.reply_text(
            f"You have reached the limit of {settings.max_reminders_per_user} reminders "
            "in this chat."
        )
        return
    reminder = await _repository(context).create(chat.id, user.id, reminder_text, delay)
    await message.reply_text(
        f"Reminder #{reminder.id} set for {reminder.due_at.astimezone(UTC):%Y-%m-%d %H:%M UTC}."
    )


async def reminders_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user = update.effective_chat, update.effective_user
    if not chat or not user:
        return
    reminders = await _repository(context).list_for_user(chat.id, user.id, limit=20)
    if not reminders:
        await update.effective_message.reply_text("You have no pending reminders in this chat.")
        return
    lines = [
        f"#{item.id} at {item.due_at.astimezone(UTC):%Y-%m-%d %H:%M UTC}: {item.message}"
        for item in reminders
    ]
    await update.effective_message.reply_text("Your pending reminders:\n" + "\n".join(lines))


async def cancelreminder_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user = update.effective_chat, update.effective_user
    if not chat or not user:
        return
    if len(context.args) != 1 or not context.args[0].isdecimal():
        await update.effective_message.reply_text("Usage: /cancelreminder <reminder_id>")
        return
    canceled = await _repository(context).cancel(int(context.args[0]), chat.id, user.id)
    await update.effective_message.reply_text(
        "Reminder cancelled." if canceled else "No pending reminder with that ID belongs to you."
    )
