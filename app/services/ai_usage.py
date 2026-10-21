"""Database-backed best-effort AI request and token reservations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import AIUsageEvent


class AIUsageRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def reserve(
        self,
        chat_id: int,
        user_id: int,
        trigger_type: str,
        estimated_tokens: int,
        group_requests_per_hour: int,
        user_requests_per_day: int,
        daily_token_budget: int,
        now: datetime | None = None,
    ) -> int | None:
        current = now or datetime.now(UTC)
        hour_ago = current - timedelta(hours=1)
        day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        async with self.sessions.begin() as session:
            group_requests = await session.scalar(
                select(func.count())
                .select_from(AIUsageEvent)
                .where(AIUsageEvent.chat_id == chat_id, AIUsageEvent.created_at >= hour_ago)
            )
            user_requests = await session.scalar(
                select(func.count())
                .select_from(AIUsageEvent)
                .where(
                    AIUsageEvent.user_id == user_id,
                    AIUsageEvent.created_at >= day_start,
                )
            )
            daily_tokens = await session.scalar(
                select(func.coalesce(func.sum(AIUsageEvent.tokens), 0)).where(
                    AIUsageEvent.created_at >= day_start
                )
            )
            if (
                int(group_requests or 0) >= group_requests_per_hour
                or int(user_requests or 0) >= user_requests_per_day
                or int(daily_tokens or 0) + estimated_tokens > daily_token_budget
            ):
                return None
            event = AIUsageEvent(
                chat_id=chat_id,
                user_id=user_id,
                trigger_type=trigger_type,
                tokens=estimated_tokens,
                status="reserved",
                created_at=current,
            )
            session.add(event)
            await session.flush()
            return event.id

    async def finish(self, event_id: int, tokens: int, success: bool) -> None:
        async with self.sessions.begin() as session:
            event = await session.get(AIUsageEvent, event_id)
            if event:
                event.tokens = max(0, tokens)
                event.status = "success" if success else "failed"
