from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.database.models import AIUsageEvent, Base
from app.database.session import create_database
from app.services.ai_usage import AIUsageRepository


@pytest.fixture
async def usage_repository(tmp_path):
    engine, sessions = create_database(f"sqlite+aiosqlite:///{tmp_path / 'ai-usage.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield AIUsageRepository(sessions), sessions
    await engine.dispose()


@pytest.mark.asyncio
async def test_ai_request_limits_and_usage_survive_repository_recreation(usage_repository):
    repository, sessions = usage_repository
    now = datetime.now(UTC)
    event_id = await repository.reserve(-1001, 22, "conversation", 20, 1, 3, 50, now)
    assert event_id is not None
    await repository.finish(event_id, 25, True)

    restored = AIUsageRepository(sessions)
    assert await restored.reserve(-1001, 23, "conversation", 10, 1, 3, 50, now) is None
    assert await restored.reserve(-1002, 22, "conversation", 30, 2, 3, 50, now) is None
    next_id = await restored.reserve(-1002, 22, "conversation", 20, 2, 3, 50, now)
    assert next_id is not None
    async with sessions() as session:
        events = list(await session.scalars(select(AIUsageEvent).order_by(AIUsageEvent.id)))
    assert [event.tokens for event in events] == [25, 20]
    assert [event.status for event in events] == ["success", "reserved"]


@pytest.mark.asyncio
async def test_user_request_limit_applies_across_groups(usage_repository):
    repository, _ = usage_repository
    now = datetime.now(UTC)
    first = await repository.reserve(-1001, 22, "conversation", 10, 5, 1, 1000, now)
    assert first is not None
    assert await repository.reserve(-1002, 22, "conversation", 10, 5, 1, 1000, now) is None
    other_user = await repository.reserve(-1002, 23, "conversation", 10, 5, 1, 1000, now)
    assert other_user is not None
