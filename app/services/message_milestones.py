"""Durable per-group message counters and milestone detection."""

from __future__ import annotations

from sqlalchemy import BigInteger, Integer
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models import Base

MESSAGE_MILESTONES = (1_000, 2_000, 3_000, 5_000, 7_000, 10_000, 15_000, 20_000, 25_000)


class GroupMessageCounter(Base):
    """Lifetime message count for one Telegram group; independent of history expiry."""

    __tablename__ = "group_message_counters"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class MessageMilestoneRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def increment(self, chat_id: int) -> tuple[int, bool]:
        """Atomically count one message and report whether it crossed a milestone."""
        async with self.sessions.begin() as session:
            dialect = session.get_bind().dialect.name
            if dialect == "postgresql":
                statement = pg_insert(GroupMessageCounter).values(
                    chat_id=chat_id, message_count=1
                )
                statement = statement.on_conflict_do_update(
                    index_elements=[GroupMessageCounter.chat_id],
                    set_={"message_count": GroupMessageCounter.message_count + 1},
                ).returning(GroupMessageCounter.message_count)
            elif dialect == "sqlite":
                statement = sqlite_insert(GroupMessageCounter).values(
                    chat_id=chat_id, message_count=1
                )
                statement = statement.on_conflict_do_update(
                    index_elements=[GroupMessageCounter.chat_id],
                    set_={"message_count": GroupMessageCounter.message_count + 1},
                ).returning(GroupMessageCounter.message_count)
            else:
                row = await session.get(GroupMessageCounter, chat_id)
                if row is None:
                    row = GroupMessageCounter(chat_id=chat_id, message_count=1)
                    session.add(row)
                    count = 1
                else:
                    row.message_count += 1
                    count = row.message_count
                return count, count in MESSAGE_MILESTONES
            count = int((await session.execute(statement)).scalar_one())
        return count, count in MESSAGE_MILESTONES
