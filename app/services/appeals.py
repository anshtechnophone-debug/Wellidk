"""Rate-limited appeal submission and group-scoped administrative review."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import AppealRecord


class AppealRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def submit(
        self,
        chat_id: int,
        user_id: int,
        message: str,
        moderation_event_id: int | None = None,
        now: datetime | None = None,
        hourly_limit: int = 3,
    ) -> tuple[int | None, str]:
        current = now or datetime.now(UTC)
        async with self.sessions.begin() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(AppealRecord)
                .where(
                    AppealRecord.chat_id == chat_id,
                    AppealRecord.user_id == user_id,
                    AppealRecord.created_at >= current - timedelta(hours=1),
                )
            )
            if int(count or 0) >= hourly_limit:
                return None, "rate_limited"
            record = AppealRecord(
                chat_id=chat_id,
                user_id=user_id,
                moderation_event_id=moderation_event_id,
                message=message[:2000],
                status="open",
                created_at=current,
            )
            session.add(record)
            await session.flush()
            return record.id, "created"

    async def recent(self, chat_id: int, limit: int = 20) -> list[AppealRecord]:
        async with self.sessions() as session:
            result = await session.scalars(
                select(AppealRecord)
                .where(AppealRecord.chat_id == chat_id, AppealRecord.status == "open")
                .order_by(AppealRecord.created_at.desc())
                .limit(limit)
            )
            return list(result)

    async def review(
        self,
        chat_id: int,
        appeal_id: int,
        reviewer_id: int,
        status: str,
        note: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        if status not in {"accepted", "rejected", "needs_info"}:
            return False
        async with self.sessions.begin() as session:
            appeal = await session.get(AppealRecord, appeal_id)
            if appeal is None or appeal.chat_id != chat_id or appeal.status != "open":
                return False
            appeal.status = status
            appeal.reviewed_by = reviewer_id
            appeal.review_note = note[:1000] if note else None
            appeal.reviewed_at = now or datetime.now(UTC)
            return True
