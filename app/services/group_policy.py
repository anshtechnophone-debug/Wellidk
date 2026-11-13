"""Transaction-safe access to explicit per-group bot policies."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import time

_POLICY_CACHE_TTL_SECONDS = 3.0
_POLICY_CACHE_MAX_ENTRIES = 4096
_POLICY_CACHE: dict[int, tuple[float, dict[str, object]]] = {}

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import Group, GroupPolicy

DEFAULT_POLICY: dict[str, object] = {
    "rules": "",
    "notes": {},
    "filters": {},
    "locks": [],
    "approved_users": [],
    "welcome": "",
    "goodbye": "",
    "antiflood": {
        "enabled": False,
        "threshold": 5,
        "window_seconds": 10,
        "repeat_threshold": 3,
    },
    "spam_protector": {
        "enabled": False,
        "threshold": 15,
        "window_seconds": 30,
        "mute_seconds": 600,
    },
}


class GroupPolicyRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def get(self, chat_id: int) -> dict[str, object]:
        now = time.monotonic()
        cached = _POLICY_CACHE.get(chat_id)
        if cached is not None and cached[0] > now:
            return deepcopy(cached[1])

        async with self.sessions() as session:
            row = await session.get(GroupPolicy, chat_id)
            policy = deepcopy(row.policy) if row else deepcopy(DEFAULT_POLICY)

        if len(_POLICY_CACHE) >= _POLICY_CACHE_MAX_ENTRIES:
            oldest_chat_id = min(_POLICY_CACHE, key=lambda key: _POLICY_CACHE[key][0])
            _POLICY_CACHE.pop(oldest_chat_id, None)
        _POLICY_CACHE[chat_id] = (time.monotonic() + _POLICY_CACHE_TTL_SECONDS, policy)
        return deepcopy(policy)

    async def set_value(self, chat_id: int, key: str, value: object) -> dict[str, object]:
        async with self.sessions.begin() as session:
            if await session.get(Group, chat_id) is None:
                session.add(Group(chat_id=chat_id, updated_at=datetime.now(UTC)))
                await session.flush()
            row = await session.get(GroupPolicy, chat_id)
            if row is None:
                row = GroupPolicy(
                    chat_id=chat_id,
                    policy=deepcopy(DEFAULT_POLICY),
                    updated_at=datetime.now(UTC),
                )
                session.add(row)
                await session.flush()
            policy = deepcopy(row.policy)
            policy[key] = value
            row.policy = policy
            row.updated_at = datetime.now(UTC)

        result = deepcopy(policy)
        _POLICY_CACHE[chat_id] = (time.monotonic() + _POLICY_CACHE_TTL_SECONDS, result)
        return deepcopy(result)

    async def update_mapping(
        self, chat_id: int, section: str, key: str, value: object
    ) -> dict[str, object]:
        policy = await self.get(chat_id)
        current = policy.get(section)
        mapping = dict(current) if isinstance(current, dict) else {}
        mapping[key] = value
        return await self.set_value(chat_id, section, mapping)
