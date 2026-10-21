from datetime import UTC, datetime

import pytest

from app.database.models import AppealRecord, Base
from app.database.session import create_database
from app.services.appeals import AppealRepository


@pytest.fixture
async def appeal_repository(tmp_path):
    engine, sessions = create_database(f"sqlite+aiosqlite:///{tmp_path / 'appeals.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield AppealRepository(sessions), sessions
    await engine.dispose()


@pytest.mark.asyncio
async def test_appeal_submit_review_and_group_isolation(appeal_repository):
    repository, sessions = appeal_repository
    appeal_id, status = await repository.submit(-1001, 22, "Please review the action.")
    assert status == "created" and appeal_id is not None
    assert await repository.review(-1002, appeal_id, 10, "accepted") is False
    assert await repository.review(-1001, appeal_id, 10, "accepted", "Admin review") is True
    assert await repository.review(-1001, appeal_id, 10, "rejected") is False
    assert await repository.recent(-1001) == []
    async with sessions() as session:
        appeal = await session.get(AppealRecord, appeal_id)
    assert appeal.status == "accepted"
    assert appeal.reviewed_by == 10
    assert appeal.review_note == "Admin review"
    assert appeal.reviewed_at is not None


@pytest.mark.asyncio
async def test_appeal_rate_limit_is_per_user_and_group(appeal_repository):
    repository, _ = appeal_repository
    now = datetime.now(UTC)
    for _ in range(3):
        assert (await repository.submit(-1001, 22, "Please review this action.", now=now))[
            1
        ] == "created"
    assert (await repository.submit(-1001, 22, "Another appeal please.", now=now))[
        1
    ] == "rate_limited"
    assert (await repository.submit(-1002, 22, "Different group appeal.", now=now))[1] == "created"
