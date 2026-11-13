
"""Persistent Telegram group-message history for XYRON."""

from __future__ import annotations

import asyncio
import logging

from datetime import UTC, datetime, timedelta

from sqlalchemy import BigInteger, Index, Integer, String, Text, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models import Base, UTCDateTime

logger = logging.getLogger(__name__)
HISTORY_RETENTION_DAYS = 30


class GroupMessage(Base):
    """A message received by XYRON in a group or supergroup."""

    __tablename__ = "group_message_history"
    __table_args__ = (
        Index(
            "ix_group_history_chat_date",
            "chat_id",
            "sent_at",
        ),
        Index(
            "ix_group_history_chat_user_date",
            "chat_id",
            "user_id",
            "sent_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    display_name: Mapped[str] = mapped_column(
        String(255), nullable=False, default=""
    )
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False
    )
    edited_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=lambda: datetime.now(UTC)
    )


class GroupHistoryRepository:
    """Save and search messages using the project's async SQLAlchemy sessions."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
    ) -> None:
        self.sessions = sessions

    async def save_message(
        self,
        *,
        chat_id: int,
        message_id: int,
        user_id: int,
        username: str | None,
        display_name: str,
        message_text: str,
        sent_at: datetime,
    ) -> None:
        """Insert a message, updating its content if it was already stored."""

        text = message_text.strip()
        if not text:
            return

        if sent_at.tzinfo is None or sent_at.utcoffset() is None:
            sent_at = sent_at.replace(tzinfo=UTC)

        async with self.sessions.begin() as session:
            existing = await session.scalar(
                select(GroupMessage).where(
                    GroupMessage.chat_id == chat_id,
                    GroupMessage.message_id == message_id,
                )
            )

            if existing:
                existing.message_text = text[:10000]
                existing.username = username
                existing.display_name = display_name[:255]
                existing.edited_at = datetime.now(UTC)
                return

            session.add(
                GroupMessage(
                    chat_id=chat_id,
                    message_id=message_id,
                    user_id=user_id,
                    username=username,
                    display_name=display_name[:255],
                    message_text=text[:10000],
                    sent_at=sent_at,
                    edited_at=datetime.now(UTC),
                )
            )

    async def search(
        self,
        *,
        chat_id: int,
        query: str = "",
        since: datetime | None = None,
        until: datetime | None = None,
        user_id: int | None = None,
        limit: int = 30,
    ) -> list[dict[str, object]]:
        """Search one group's stored history, newest matching messages first."""

        limit = max(1, min(limit, 100))
        statement = select(GroupMessage).where(
            GroupMessage.chat_id == chat_id
        )

        if user_id is not None:
            statement = statement.where(
                GroupMessage.user_id == user_id
            )

        if since is not None:
            statement = statement.where(
                GroupMessage.sent_at >= since
            )

        if until is not None:
            statement = statement.where(
                GroupMessage.sent_at <= until
            )

        terms = [term for term in query.split() if term]
        for term in terms[:8]:
            statement = statement.where(
                GroupMessage.message_text.ilike(f"%{term}%")
            )

        statement = statement.order_by(
            GroupMessage.sent_at.desc(),
            GroupMessage.message_id.desc(),
        ).limit(limit)

        async with self.sessions() as session:
            rows = (await session.scalars(statement)).all()

        return [
            {
                "chat_id": row.chat_id,
                "message_id": row.message_id,
                "user_id": row.user_id,
                "username": row.username,
                "display_name": row.display_name,
                "text": row.message_text,
                "sent_at": row.sent_at.isoformat(),
            }
            for row in reversed(rows)
        ]

    async def find_user_ids_by_name(
        self,
        *,
        chat_id: int,
        name: str,
        limit: int = 5,
    ) -> list[int]:
        """Resolve a group display name or username to IDs using recent saved rows."""
        needle = name.strip().lstrip("@")
        if len(needle) < 2:
            return []
        statement = (
            select(GroupMessage.user_id)
            .where(
                GroupMessage.chat_id == chat_id,
                GroupMessage.sent_at >= datetime.now(UTC) - timedelta(days=HISTORY_RETENTION_DAYS),
                (
                    GroupMessage.username.ilike(needle)
                    | GroupMessage.display_name.ilike(needle)
                    | GroupMessage.display_name.ilike(f"{needle}%")
                ),
            )
            .group_by(GroupMessage.user_id)
            .order_by(func.max(GroupMessage.sent_at).desc())
            .limit(max(1, min(limit, 10)))
        )
        async with self.sessions() as session:
            return [int(user_id) for user_id in (await session.scalars(statement)).all()]

    async def recent(
        self,
        *,
        chat_id: int,
        hours: int = 24,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        """Return recent messages from one group."""

        hours = max(1, min(hours, 24 * 90))
        return await self.search(
            chat_id=chat_id,
            since=datetime.now(UTC) - timedelta(hours=hours),
            limit=limit,
        )

    async def prune(
        self,
        *,
        older_than_days: int = HISTORY_RETENTION_DAYS,
    ) -> int:
        """Delete archived messages older than the chosen retention period."""

        older_than_days = max(1, min(older_than_days, 3650))
        cutoff = datetime.now(UTC) - timedelta(days=older_than_days)

        async with self.sessions.begin() as session:
            result = await session.execute(
                delete(GroupMessage).where(
                    GroupMessage.sent_at < cutoff
                )
            )
            return max(0, result.rowcount or 0)


async def group_history_retention_worker(application, stop_event: asyncio.Event) -> None:
    """Prune group message history to the most recent 30 days."""
    sessions = application.bot_data["sessions"]
    repository = GroupHistoryRepository(sessions)
    while not stop_event.is_set():
        try:
            deleted = await repository.prune(older_than_days=HISTORY_RETENTION_DAYS)
            if deleted:
                logger.info("Pruned %s group-history messages older than %s days", deleted, HISTORY_RETENTION_DAYS)
        except Exception:
            logger.exception("Group-history retention worker failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=3600)
        except TimeoutError:
            continue
