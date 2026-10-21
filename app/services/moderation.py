"""Database-backed moderation event and temporary-action operations."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import (
    Group,
    ModerationEvent,
    RestrictionSnapshot,
    TemporaryAction,
    WarningRecord,
)


class ModerationRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def record(
        self,
        chat_id: int,
        actor_id: int,
        target_id: int,
        action: str,
        reason: str | None,
    ) -> None:
        async with self.sessions.begin() as session:
            await self._ensure_group(session, chat_id)
            session.add(
                ModerationEvent(
                    chat_id=chat_id,
                    actor_id=actor_id,
                    target_id=target_id,
                    action=action,
                    reason=reason,
                    created_at=datetime.now(UTC),
                )
            )

    async def schedule(
        self,
        chat_id: int,
        target_id: int,
        action: str,
        expires_at: datetime,
        original_permissions: dict[str, bool] | None = None,
    ) -> None:
        async with self.sessions.begin() as session:
            await self._ensure_group(session, chat_id)
            existing = await session.scalar(
                select(TemporaryAction).where(
                    TemporaryAction.chat_id == chat_id,
                    TemporaryAction.target_id == target_id,
                    TemporaryAction.action == action,
                )
            )
            if existing:
                existing.action = action
                existing.expires_at = expires_at
                existing.original_permissions = original_permissions
                existing.attempts = 0
            else:
                session.add(
                    TemporaryAction(
                        chat_id=chat_id,
                        target_id=target_id,
                        action=action,
                        expires_at=expires_at,
                        original_permissions=original_permissions,
                    )
                )

    async def cancel_temporary(
        self, chat_id: int, target_id: int, action: str | None = None
    ) -> None:
        async with self.sessions.begin() as session:
            statement = delete(TemporaryAction).where(
                TemporaryAction.chat_id == chat_id,
                TemporaryAction.target_id == target_id,
            )
            if action:
                statement = statement.where(TemporaryAction.action == action)
            await session.execute(statement)

    async def has_temporary(self, chat_id: int, target_id: int, action: str) -> bool:
        async with self.sessions() as session:
            return bool(
                await session.scalar(
                    select(TemporaryAction.id).where(
                        TemporaryAction.chat_id == chat_id,
                        TemporaryAction.target_id == target_id,
                        TemporaryAction.action == action,
                    )
                )
            )

    async def remember_permissions(
        self, chat_id: int, target_id: int, actor_id: int, permissions: dict[str, bool]
    ) -> bool:
        async with self.sessions.begin() as session:
            await self._ensure_group(session, chat_id)
            key = (chat_id, target_id)
            if await session.get(RestrictionSnapshot, key):
                return False
            session.add(
                RestrictionSnapshot(
                    chat_id=chat_id,
                    target_id=target_id,
                    actor_id=actor_id,
                    permissions=permissions,
                    updated_at=datetime.now(UTC),
                )
            )
            return True

    async def restriction_permissions(self, chat_id: int, target_id: int) -> dict[str, bool] | None:
        async with self.sessions() as session:
            snapshot = await session.get(RestrictionSnapshot, (chat_id, target_id))
            return snapshot.permissions if snapshot else None

    async def forget_permissions(self, chat_id: int, target_id: int) -> None:
        async with self.sessions.begin() as session:
            snapshot = await session.get(RestrictionSnapshot, (chat_id, target_id))
            if snapshot:
                await session.delete(snapshot)

    async def active_mutes(self, chat_id: int) -> list[RestrictionSnapshot]:
        async with self.sessions() as session:
            result = await session.scalars(
                select(RestrictionSnapshot)
                .where(RestrictionSnapshot.chat_id == chat_id)
                .order_by(RestrictionSnapshot.updated_at.desc())
            )
            return list(result)

    async def due_actions(self, now: datetime | None = None) -> list[TemporaryAction]:
        async with self.sessions() as session:
            result = await session.scalars(
                select(TemporaryAction)
                .where(TemporaryAction.expires_at <= (now or datetime.now(UTC)))
                .order_by(TemporaryAction.expires_at)
                .limit(100)
            )
            return list(result)

    async def delete_temporary(self, action_id: int) -> None:
        async with self.sessions.begin() as session:
            await session.execute(delete(TemporaryAction).where(TemporaryAction.id == action_id))

    async def note_expiration_failure(self, action_id: int) -> None:
        async with self.sessions.begin() as session:
            action = await session.get(TemporaryAction, action_id)
            if action:
                action.attempts += 1

    async def add_warning(
        self, chat_id: int, actor_id: int, target_id: int, reason: str | None
    ) -> int:
        async with self.sessions.begin() as session:
            await self._ensure_group(session, chat_id)
            session.add(
                WarningRecord(
                    chat_id=chat_id,
                    actor_id=actor_id,
                    target_id=target_id,
                    reason=reason,
                    created_at=datetime.now(UTC),
                )
            )
            await session.flush()
            count = await session.scalar(
                select(func.count())
                .select_from(WarningRecord)
                .where(
                    WarningRecord.chat_id == chat_id,
                    WarningRecord.target_id == target_id,
                )
            )
            return int(count or 0)

    async def warning_count(self, chat_id: int, target_id: int) -> int:
        async with self.sessions() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(WarningRecord)
                .where(
                    WarningRecord.chat_id == chat_id,
                    WarningRecord.target_id == target_id,
                )
            )
            return int(count or 0)

    async def reset_warnings(self, chat_id: int, target_id: int) -> None:
        async with self.sessions.begin() as session:
            await session.execute(
                delete(WarningRecord).where(
                    WarningRecord.chat_id == chat_id,
                    WarningRecord.target_id == target_id,
                )
            )

    async def warn_settings(self, chat_id: int) -> tuple[int, str]:
        async with self.sessions() as session:
            group = await session.get(Group, chat_id)
            return (group.warn_limit, group.warn_action) if group else (3, "mute")

    async def set_warn_limit(self, chat_id: int, limit: int) -> None:
        async with self.sessions.begin() as session:
            await self._ensure_group(session, chat_id)
            group = await session.get(Group, chat_id)
            group.warn_limit = limit

    async def recent_events(self, chat_id: int, limit: int = 10) -> list[ModerationEvent]:
        async with self.sessions() as session:
            result = await session.scalars(
                select(ModerationEvent)
                .where(ModerationEvent.chat_id == chat_id)
                .order_by(ModerationEvent.created_at.desc())
                .limit(limit)
            )
            return list(result)

    async def active_bans(self, chat_id: int) -> list[ModerationEvent]:
        events = await self.recent_events(chat_id, limit=1000)
        latest: dict[int, ModerationEvent] = {}
        ending_actions = {"unban", "auto_unban", "kick", "kickme"}
        ban_actions = {"ban", "tban", "warn_limit_ban"}
        for event in events:
            if event.target_id in latest:
                continue
            if event.action in ending_actions:
                latest[event.target_id] = event
            elif event.action in ban_actions:
                latest[event.target_id] = event
        return [event for event in latest.values() if event.action in ban_actions]

    async def event_counts(self, chat_id: int, since: datetime) -> dict[str, int]:
        async with self.sessions() as session:
            result = await session.execute(
                select(ModerationEvent.action, func.count())
                .where(
                    ModerationEvent.chat_id == chat_id,
                    ModerationEvent.created_at >= since,
                )
                .group_by(ModerationEvent.action)
                .order_by(ModerationEvent.action)
            )
            return {action: int(count) for action, count in result}

    @staticmethod
    async def _ensure_group(session: AsyncSession, chat_id: int) -> None:
        if await session.get(Group, chat_id) is None:
            session.add(Group(chat_id=chat_id, updated_at=datetime.now(UTC)))
            await session.flush()
