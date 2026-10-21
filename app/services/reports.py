"""Persist and rate-limit member reports without treating them as proof."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import ReportRecord


class ReportRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def submit(
        self,
        chat_id: int,
        message_id: int,
        reported_user_id: int,
        reporter_id: int,
        reason: str | None,
        now: datetime | None = None,
        per_hour_limit: int = 5,
    ) -> tuple[int | None, str]:
        current = now or datetime.now(UTC)
        async with self.sessions.begin() as session:
            duplicate = await session.scalar(
                select(ReportRecord.id).where(
                    ReportRecord.chat_id == chat_id,
                    ReportRecord.message_id == message_id,
                    ReportRecord.reporter_id == reporter_id,
                )
            )
            if duplicate:
                return None, "duplicate"
            count = await session.scalar(
                select(func.count())
                .select_from(ReportRecord)
                .where(
                    ReportRecord.chat_id == chat_id,
                    ReportRecord.reporter_id == reporter_id,
                    ReportRecord.created_at >= current - timedelta(hours=1),
                )
            )
            if int(count or 0) >= per_hour_limit:
                return None, "rate_limited"
            report = ReportRecord(
                chat_id=chat_id,
                message_id=message_id,
                reported_user_id=reported_user_id,
                reporter_id=reporter_id,
                reason=reason,
                status="open",
                created_at=current,
            )
            session.add(report)
            await session.flush()
            return report.id, "created"

    async def add_summary(self, report_id: int, summary: str) -> None:
        async with self.sessions.begin() as session:
            report = await session.get(ReportRecord, report_id)
            if report:
                report.ai_summary = summary[:4000]

    async def recent(
        self, chat_id: int, limit: int = 10, status: str | None = "open"
    ) -> list[ReportRecord]:
        async with self.sessions() as session:
            statement = select(ReportRecord).where(ReportRecord.chat_id == chat_id)
            if status:
                statement = statement.where(ReportRecord.status == status)
            result = await session.scalars(
                statement.order_by(ReportRecord.created_at.desc()).limit(limit)
            )
            return list(result)

    async def set_status(self, chat_id: int, report_id: int, status: str) -> bool:
        if status not in {"reviewed", "dismissed", "actioned"}:
            return False
        async with self.sessions.begin() as session:
            report = await session.get(ReportRecord, report_id)
            if report is None or report.chat_id != chat_id:
                return False
            report.status = status
            return True
