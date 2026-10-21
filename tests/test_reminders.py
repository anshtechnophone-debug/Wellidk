import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from telegram.error import TelegramError

from app.database.models import Base, Reminder
from app.database.session import create_database
from app.services.reminder_worker import reminder_worker
from app.services.reminders import ReminderRepository


@pytest.fixture
async def reminder_repository(tmp_path):
    engine, sessions = create_database(f"sqlite+aiosqlite:///{tmp_path / 'reminders.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield ReminderRepository(sessions), sessions
    await engine.dispose()


@pytest.mark.asyncio
async def test_reminders_persist_and_are_owned_by_user_and_chat(reminder_repository):
    repository, _ = reminder_repository
    now = datetime.now(UTC)
    reminder = await repository.create(-1001, 20, "drink water", timedelta(minutes=10), now)
    assert reminder.due_at == now + timedelta(minutes=10)
    assert len(await repository.list_for_user(-1001, 20)) == 1
    assert await repository.list_for_user(-1002, 20) == []
    assert await repository.list_for_user(-1001, 21) == []
    assert await repository.cancel(reminder.id, -1001, 21) is False
    assert await repository.cancel(reminder.id, -1001, 20) is True
    assert await repository.list_for_user(-1001, 20) == []


@pytest.mark.asyncio
async def test_due_reminder_is_marked_before_delivery_and_never_replayed(reminder_repository):
    repository, sessions = reminder_repository
    now = datetime.now(UTC)
    reminder = await repository.create(-1001, 20, "check oven", timedelta(seconds=1), now)
    due = await repository.due(now + timedelta(seconds=2))
    assert [item.id for item in due] == [reminder.id]
    assert await repository.mark_delivered(reminder.id, now + timedelta(seconds=2)) is True
    assert await repository.mark_delivered(reminder.id, now + timedelta(seconds=3)) is False
    assert await repository.due(now + timedelta(hours=1)) == []
    async with sessions() as session:
        saved = await session.scalar(select(Reminder))
    assert saved.delivered_at == now + timedelta(seconds=2)


@pytest.mark.asyncio
async def test_worker_marks_reminder_before_send_and_does_not_replay_after_failure(
    reminder_repository,
):
    repository, sessions = reminder_repository
    reminder = await repository.create(
        -1001, 20, "take a break", timedelta(seconds=1), datetime.now(UTC) - timedelta(minutes=1)
    )
    stop_event = asyncio.Event()

    async def fail_delivery(*args, **kwargs):
        stop_event.set()
        raise TelegramError("destination unavailable")

    application = SimpleNamespace(
        bot=SimpleNamespace(send_message=AsyncMock(side_effect=fail_delivery)),
        bot_data={"sessions": sessions},
    )

    await reminder_worker(application, stop_event)

    application.bot.send_message.assert_awaited_once()
    assert await repository.due(datetime.now(UTC) + timedelta(days=1)) == []
    async with sessions() as session:
        saved = await session.get(Reminder, reminder.id)
    assert saved.delivered_at is not None
