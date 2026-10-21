"""Durable, at-most-once personal reminders."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import Reminder


class ReminderRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def create(
        self,
        chat_id: int,
        creator_id: int,
        message: str,
        delay: timedelta,
        now: datetime | None = None,
    ) -> Reminder:
        current = now or datetime.now(UTC)
        async with self.sessions.begin() as session:
            reminder = Reminder(
                chat_id=chat_id,
                creator_id=creator_id,
                message=message[:1000],
                due_at=current + delay,
            )
            session.add(reminder)
            await session.flush()
            return reminder

    async def list_for_user(self, chat_id: int, creator_id: int, limit: int = 20) -> list[Reminder]:
        async with self.sessions() as session:
            result = await session.scalars(
                select(Reminder)
                .where(
                    Reminder.chat_id == chat_id,
                    Reminder.creator_id == creator_id,
                    Reminder.delivered_at.is_(None),
                )
                .order_by(Reminder.due_at)
                .limit(limit)
            )
            return list(result)

    async def cancel(self, reminder_id: int, chat_id: int, creator_id: int) -> bool:
        async with self.sessions.begin() as session:
            reminder = await session.get(Reminder, reminder_id)
            if (
                not reminder
                or reminder.chat_id != chat_id
                or reminder.creator_id != creator_id
                or reminder.delivered_at is not None
            ):
                return False
            await session.delete(reminder)
            return True

    async def due(self, now: datetime | None = None, limit: int = 50) -> list[Reminder]:
        current = now or datetime.now(UTC)
        async with self.sessions() as session:
            result = await session.scalars(
                select(Reminder)
                .where(Reminder.due_at <= current, Reminder.delivered_at.is_(None))
                .order_by(Reminder.due_at)
                .limit(limit)
            )
            return list(result)

    async def mark_delivered(self, reminder_id: int, now: datetime | None = None) -> bool:
        current = now or datetime.now(UTC)
        async with self.sessions.begin() as session:
            reminder = await session.get(Reminder, reminder_id)
            if reminder is None or reminder.delivered_at is not None:
                return False
            reminder.delivered_at = current
            return True
