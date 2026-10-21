"""Restart-safe at-most-once reminder dispatcher."""

from __future__ import annotations

import asyncio
import logging

from telegram.error import TelegramError
from telegram.ext import Application

from app.services.reminders import ReminderRepository

logger = logging.getLogger(__name__)


async def reminder_worker(application: Application, stop_event: asyncio.Event) -> None:
    repository = ReminderRepository(application.bot_data["sessions"])
    while not stop_event.is_set():
        for reminder in await repository.due():
            # Mark before sending so a crash cannot replay a delivered message.
            if not await repository.mark_delivered(reminder.id):
                continue
            try:
                await application.bot.send_message(
                    reminder.chat_id,
                    f"Reminder for user {reminder.creator_id}: {reminder.message}",
                )
            except TelegramError:
                logger.warning(
                    "Reminder %s was marked delivered but Telegram could not send it",
                    reminder.id,
                    exc_info=True,
                )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=15)
        except TimeoutError:
            continue
