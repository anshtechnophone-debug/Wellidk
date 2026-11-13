"""Bounded background execution for non-critical persistence work."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable

logger = logging.getLogger(__name__)
_STOP = object()


class BackgroundTaskQueue:
    """Run persistence awaitables outside Telegram update handlers.

    The queue is bounded to cap memory use. Producers wait only when the queue
    is saturated; this preserves records rather than silently dropping them.
    Shutdown drains all accepted work before the worker exits.
    """

    def __init__(self, *, maxsize: int = 4096) -> None:
        self._queue: asyncio.Queue[Awaitable[object] | object] = asyncio.Queue(
            maxsize=maxsize
        )
        self._worker: asyncio.Task[None] | None = None
        self._closing = False

    def start(self) -> None:
        if self._worker is not None:
            return
        self._worker = asyncio.create_task(
            self._run(), name="xyron-background-persistence"
        )

    async def submit(self, work: Awaitable[object]) -> None:
        if self._closing:
            await work
            return
        if self._worker is None:
            self.start()
        if self._queue.full():
            logger.warning(
                "XYRON persistence queue is full; waiting to preserve queued data"
            )
        await self._queue.put(work)

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item is _STOP:
                    return
                try:
                    await item
                except Exception:
                    logger.exception("Background persistence task failed")
            finally:
                self._queue.task_done()

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        worker = self._worker
        if worker is None:
            return
        await self._queue.join()
        await self._queue.put(_STOP)
        await worker
        self._worker = None
