"""Short-lived audit log for group messages, commands, and moderation activity."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import BigInteger, Index, Integer, String, Text, delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models import Base, UTCDateTime, ModerationEvent

logger = logging.getLogger(__name__)


class ActivityEvent(Base):
    """One observed group activity event; records expire after 24 hours."""

    __tablename__ = "activity_events"
    __table_args__ = (
        Index("ix_activity_events_chat_created", "chat_id", "created_at"),
        Index("ix_activity_events_type_created", "event_type", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    target_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    action: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="observed")
    details: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=lambda: datetime.now(UTC)
    )


class ActivityLogRepository:
    """Write, query, and expire the temporary 24-hour activity log."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def record(
        self,
        *,
        chat_id: int,
        event_type: str,
        actor_id: int | None = None,
        target_id: int | None = None,
        message_id: int | None = None,
        action: str | None = None,
        status: str = "observed",
        details: dict[str, object] | None = None,
        created_at: datetime | None = None,
    ) -> None:
        timestamp = created_at or datetime.now(UTC)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        async with self.sessions.begin() as session:
            session.add(ActivityEvent(
                chat_id=chat_id,
                event_type=event_type[:32],
                actor_id=actor_id,
                target_id=target_id,
                message_id=message_id,
                action=action[:64] if action else None,
                status=status[:16],
                details=json.dumps(details or {}, ensure_ascii=False, default=str)[:12000],
                created_at=timestamp,
            ))

    async def prune_expired(self, *, retention_hours: int = 24) -> dict[str, int]:
        cutoff = datetime.now(UTC) - timedelta(hours=max(1, min(retention_hours, 24)))
        async with self.sessions.begin() as session:
            activity_result = await session.execute(
                delete(ActivityEvent).where(ActivityEvent.created_at < cutoff)
            )
            # The existing moderation log also contains action records; keep its
            # retention aligned with the temporary audit window.
            moderation_result = await session.execute(
                delete(ModerationEvent).where(ModerationEvent.created_at < cutoff)
            )
            from app.services.group_history import GroupMessage
            message_result = await session.execute(
                delete(GroupMessage).where(GroupMessage.sent_at < cutoff)
            )
            return {
                "activity": max(0, activity_result.rowcount or 0),
                "moderation": max(0, moderation_result.rowcount or 0),
                "messages": max(0, message_result.rowcount or 0),
            }

    async def search(
        self,
        *,
        chat_id: int,
        action_terms: tuple[str, ...] = (),
        event_types: tuple[str, ...] = (),
        actor_id: int | None = None,
        target_id: int | None = None,
        limit: int = 20,
    ) -> list[dict[str, object]]:
        """Search the last 24 hours in SQL, before records enter the AI prompt."""
        cutoff = datetime.now(UTC) - timedelta(hours=24)
        conditions = [
            ActivityEvent.chat_id == chat_id,
            ActivityEvent.created_at >= cutoff,
        ]
        if event_types:
            conditions.append(ActivityEvent.event_type.in_(event_types))
        if actor_id is not None:
            conditions.append(ActivityEvent.actor_id == actor_id)
        if target_id is not None:
            conditions.append(ActivityEvent.target_id == target_id)
        if action_terms:
            from sqlalchemy import or_
            conditions.append(or_(*[
                ActivityEvent.action.ilike(f"%{term}%") for term in action_terms
            ]))
        statement = (
            select(ActivityEvent)
            .where(*conditions)
            .order_by(ActivityEvent.created_at.desc(), ActivityEvent.id.desc())
            .limit(max(1, min(limit, 30)))
        )
        async with self.sessions() as session:
            rows = (await session.scalars(statement)).all()
        return [
            {
                "event_type": row.event_type,
                "actor_id": row.actor_id,
                "target_id": row.target_id,
                "message_id": row.message_id,
                "action": row.action,
                "status": row.status,
                "details": row.details,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]

    async def recent(self, *, chat_id: int, limit: int = 50) -> list[dict[str, object]]:
        """Compatibility wrapper for callers that need recent events."""
        return await self.search(chat_id=chat_id, limit=limit)



async def activity_retention_worker(application, stop_event: asyncio.Event) -> None:
    """Expire temporary activity/message/moderation rows every five minutes."""
    sessions = application.bot_data["sessions"]
    repository = ActivityLogRepository(sessions)
    # Prune immediately at startup so stale records do not linger after downtime.
    while not stop_event.is_set():
        try:
            removed = await repository.prune_expired(retention_hours=24)
            if any(removed.values()):
                logger.info("24-hour activity retention removed rows: %s", removed)
        except Exception:
            logger.exception("24-hour activity retention cleanup failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=300)
        except TimeoutError:
            continue
