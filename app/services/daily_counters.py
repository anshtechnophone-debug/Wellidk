"""Lightweight, batched daily message counters for every group."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import DailyMessageCount, GroupPolicy

logger = logging.getLogger(__name__)
LOCAL_TZ = ZoneInfo("Asia/Kolkata")
BATCH_SIZE = 100
FLUSH_SECONDS = 0.75


class DailyCounterRepository:
    """Persist only aggregated counts and the display name needed by /daily."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def add_batch(self, events: list[dict[str, object]]) -> int:
        if not events:
            return 0

        grouped: dict[tuple[int, int, date], dict[str, object]] = {}
        chat_ids = {int(event["chat_id"]) for event in events}
        async with self.sessions.begin() as session:
            policies = (
                await session.execute(
                    select(GroupPolicy.chat_id, GroupPolicy.policy).where(
                        GroupPolicy.chat_id.in_(chat_ids)
                    )
                )
            ).all()
            reset_by_chat: dict[int, datetime | None] = {}
            for chat_id, policy in policies:
                raw = policy.get("daily_reset_at") if isinstance(policy, dict) else None
                try:
                    reset_at = datetime.fromisoformat(str(raw)) if raw else None
                    if reset_at and reset_at.tzinfo is None:
                        reset_at = reset_at.replace(tzinfo=UTC)
                    reset_by_chat[int(chat_id)] = reset_at.astimezone(UTC) if reset_at else None
                except (TypeError, ValueError):
                    reset_by_chat[int(chat_id)] = None

            for event in events:
                chat_id = int(event["chat_id"])
                sent_at = event["sent_at"]
                if not isinstance(sent_at, datetime):
                    continue
                if sent_at.tzinfo is None:
                    sent_at = sent_at.replace(tzinfo=UTC)
                sent_at = sent_at.astimezone(UTC)
                reset_at = reset_by_chat.get(chat_id)
                if reset_at and sent_at < reset_at:
                    continue
                user_id = int(event["user_id"])
                day = sent_at.astimezone(LOCAL_TZ).date()
                key = (chat_id, user_id, day)
                entry = grouped.setdefault(
                    key,
                    {
                        "increment": 0,
                        "username": event.get("username"),
                        "display_name": str(event.get("display_name") or "Member")[:255],
                    },
                )
                entry["increment"] = int(entry["increment"]) + 1
                entry["username"] = event.get("username")
                entry["display_name"] = str(event.get("display_name") or "Member")[:255]

            payload = [
                {
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "day": day,
                    "message_count": int(values["increment"]),
                    "username": values["username"],
                    "display_name": str(values["display_name"]),
                }
                for (chat_id, user_id, day), values in grouped.items()
            ]
            if payload:
                dialect = session.bind.dialect.name
                if dialect == "sqlite":
                    statement = sqlite_insert(DailyMessageCount).values(payload)
                elif dialect == "postgresql":
                    statement = postgresql_insert(DailyMessageCount).values(payload)
                else:
                    statement = None

                if statement is not None:
                    await session.execute(
                        statement.on_conflict_do_update(
                            index_elements=[
                                DailyMessageCount.chat_id,
                                DailyMessageCount.user_id,
                                DailyMessageCount.day,
                            ],
                            set_={
                                "message_count": (
                                    DailyMessageCount.message_count
                                    + statement.excluded.message_count
                                ),
                                "username": statement.excluded.username,
                                "display_name": statement.excluded.display_name,
                            },
                        )
                    )
                else:
                    # Compatibility fallback for other SQL dialects.
                    for values in payload:
                        row = await session.get(
                            DailyMessageCount,
                            {
                                "chat_id": values["chat_id"],
                                "user_id": values["user_id"],
                                "day": values["day"],
                            },
                        )
                        if row is None:
                            session.add(DailyMessageCount(**values))
                        else:
                            row.message_count += values["message_count"]
                            row.username = values["username"]
                            row.display_name = values["display_name"]
        return sum(int(value["increment"]) for value in grouped.values())

    async def reset_day(self, chat_id: int, day: date) -> None:
        async with self.sessions.begin() as session:
            await session.execute(
                delete(DailyMessageCount).where(
                    DailyMessageCount.chat_id == chat_id,
                    DailyMessageCount.day == day,
                )
            )


async def daily_counter_worker(application, stop_event: asyncio.Event) -> None:
    """Flush compact counter events in batches away from Telegram update handlers."""
    queue: asyncio.Queue = application.bot_data["daily_counter_queue"]
    repository = DailyCounterRepository(application.bot_data["sessions"])
    while not stop_event.is_set() or not queue.empty():
        try:
            first = await asyncio.wait_for(queue.get(), timeout=FLUSH_SECONDS)
        except TimeoutError:
            continue
        batch = [first]
        while len(batch) < BATCH_SIZE:
            try:
                batch.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        try:
            saved = await repository.add_batch(batch)
            if saved:
                logger.debug(
                    "DAILY_COUNTER_FLUSH events=%s saved=%s queue_size=%s",
                    len(batch), saved, queue.qsize(),
                )
        except Exception:
            logger.exception("Daily counter batch failed; events=%s", len(batch))
        finally:
            for _ in batch:
                queue.task_done()
