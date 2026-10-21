from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.database.models import Base, ModerationEvent, TemporaryAction
from app.database.session import create_database
from app.services.moderation import ModerationRepository


@pytest.fixture
async def repository(tmp_path):
    engine, sessions = create_database(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield ModerationRepository(sessions), sessions
    await engine.dispose()


@pytest.mark.asyncio
async def test_events_and_temporary_actions_persist(repository):
    repo, sessions = repository
    expiration = datetime.now(UTC) + timedelta(hours=1)
    await repo.record(-100123, 11, 22, "ban", "spam")
    await repo.schedule(-100123, 22, "unban", expiration)

    async with sessions() as session:
        event = await session.scalar(select(ModerationEvent))
        action = await session.scalar(select(TemporaryAction))
    assert (event.actor_id, event.target_id, event.reason) == (11, 22, "spam")
    assert action.action == "unban"
    assert action.expires_at == expiration


@pytest.mark.asyncio
async def test_scheduling_same_user_replaces_action_instead_of_duplicating(repository):
    repo, sessions = repository
    first = datetime.now(UTC) + timedelta(hours=1)
    second = first + timedelta(hours=1)
    await repo.schedule(-100123, 22, "unmute", first)
    await repo.schedule(-100123, 22, "unmute", second, {"can_send_messages": True})

    async with sessions() as session:
        actions = list(await session.scalars(select(TemporaryAction)))
    assert len(actions) == 1
    assert actions[0].action == "unmute"
    assert actions[0].expires_at == second
    assert actions[0].original_permissions == {"can_send_messages": True}


@pytest.mark.asyncio
async def test_temporary_ban_and_mute_can_coexist(repository):
    repo, sessions = repository
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    await repo.schedule(-100123, 22, "unban", expires_at)
    await repo.schedule(-100123, 22, "unmute", expires_at, {"can_send_messages": True})

    async with sessions() as session:
        actions = list(await session.scalars(select(TemporaryAction)))
    assert {action.action for action in actions} == {"unban", "unmute"}


@pytest.mark.asyncio
async def test_canceling_one_temporary_action_preserves_the_other(repository):
    repo, sessions = repository
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    await repo.schedule(-100123, 22, "unban", expires_at)
    await repo.schedule(-100123, 22, "unmute", expires_at, {"can_send_messages": True})
    await repo.cancel_temporary(-100123, 22, "unban")

    async with sessions() as session:
        actions = list(await session.scalars(select(TemporaryAction)))
    assert len(actions) == 1
    assert actions[0].action == "unmute"


@pytest.mark.asyncio
async def test_warning_count_and_reset(repository):
    repo, _ = repository
    assert await repo.add_warning(-100123, 11, 22, "first") == 1
    assert await repo.add_warning(-100123, 11, 22, "second") == 2
    assert await repo.warning_count(-100123, 22) == 2
    await repo.reset_warnings(-100123, 22)
    assert await repo.warning_count(-100123, 22) == 0
