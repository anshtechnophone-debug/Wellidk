"""Update processor that runs separate chats concurrently but preserves per-chat order."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable

from telegram import Update
from telegram.ext import BaseUpdateProcessor


class PerChatUpdateProcessor(BaseUpdateProcessor):
    """Serialize updates per chat while allowing unrelated chats to run concurrently.

    A fixed set of locks keeps memory usage bounded. Hash collisions only cause
    harmless extra serialization; they never allow two updates from one chat
    to overlap.
    """

    def __init__(self, max_concurrent_updates: int = 32, lock_count: int = 128) -> None:
        if lock_count < 1:
            raise ValueError("lock_count must be positive")
        super().__init__(max_concurrent_updates=max_concurrent_updates)
        self._locks = tuple(asyncio.Lock() for _ in range(lock_count))

    def _lock_for(self, update: object) -> asyncio.Lock:
        if isinstance(update, Update):
            chat = update.effective_chat
            if chat is not None:
                key = ("chat", chat.id)
            else:
                user = update.effective_user
                key = ("user", user.id) if user is not None else ("global", 0)
        else:
            key = ("global", 0)
        return self._locks[hash(key) % len(self._locks)]

    async def do_process_update(
        self,
        update: object,
        coroutine: Awaitable[object],
    ) -> None:
        async with self._lock_for(update):
            await coroutine

    async def initialize(self) -> None:
        """No external resources need initialization."""

    async def shutdown(self) -> None:
        """No external resources need shutdown."""
