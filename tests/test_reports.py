import pytest
from sqlalchemy import select

from app.database.models import Base, ReportRecord
from app.database.session import create_database
from app.services.reports import ReportRepository


@pytest.fixture
async def reports(tmp_path):
    engine, sessions = create_database(f"sqlite+aiosqlite:///{tmp_path / 'reports.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield ReportRepository(sessions), sessions
    await engine.dispose()


@pytest.mark.asyncio
async def test_report_persists_and_duplicate_is_rejected(reports):
    repository, sessions = reports
    report_id, status = await repository.submit(-1001, 30, 22, 11, "abuse")
    assert report_id is not None and status == "created"
    duplicate_id, duplicate_status = await repository.submit(-1001, 30, 22, 11, "again")
    assert duplicate_id is None and duplicate_status == "duplicate"
    await repository.add_summary(report_id, "human review recommended")
    async with sessions() as session:
        report = await session.scalar(select(ReportRecord))
    assert report.reason == "abuse"
    assert report.ai_summary == "human review recommended"
    assert report.status == "open"


@pytest.mark.asyncio
async def test_reporter_rate_limit_applies_across_messages(reports):
    repository, _ = reports
    for message_id in range(1, 6):
        assert (await repository.submit(-1001, message_id, 22, 11, None))[1] == "created"
    assert (await repository.submit(-1001, 6, 22, 11, None))[1] == "rate_limited"


@pytest.mark.asyncio
async def test_report_review_status_is_scoped_to_the_originating_group(reports):
    repository, sessions = reports
    report_id, _ = await repository.submit(-1001, 30, 22, 11, "review")
    assert await repository.set_status(-1002, report_id, "actioned") is False
    assert await repository.set_status(-1001, report_id, "dismissed") is True
    assert await repository.set_status(-1001, report_id, "ban_everyone") is False
    open_reports = await repository.recent(-1001, status="open")
    dismissed_reports = await repository.recent(-1001, status="dismissed")
    assert not open_reports
    assert dismissed_reports[0].id == report_id
    async with sessions() as session:
        report = await session.get(ReportRecord, report_id)
    assert report.status == "dismissed"
