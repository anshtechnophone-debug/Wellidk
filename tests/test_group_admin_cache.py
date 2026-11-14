import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core import group_admin_cache as cache


@pytest.mark.asyncio
async def test_concurrent_requests_share_one_group_admin_lookup():
    member = SimpleNamespace(user=SimpleNamespace(id=7))
    async def delayed_lookup(chat_id):
        await asyncio.sleep(0.02)
        return [member]

    bot = SimpleNamespace(get_chat_administrators=AsyncMock(side_effect=delayed_lookup))
    bot_data = {}
    first, second = await asyncio.gather(
        cache.get_group_admins(bot, bot_data, -1001),
        cache.get_group_admins(bot, bot_data, -1001),
    )
    assert first is second
    assert first[7] is member
    bot.get_chat_administrators.assert_awaited_once_with(-1001)


@pytest.mark.asyncio
async def test_invalidation_forces_a_fresh_lookup():
    bot = SimpleNamespace(
        get_chat_administrators=AsyncMock(
            side_effect=[
                [SimpleNamespace(user=SimpleNamespace(id=7))],
                [SimpleNamespace(user=SimpleNamespace(id=8))],
            ]
        )
    )
    bot_data = {}
    first = await cache.get_group_admins(bot, bot_data, -1001)
    cache.invalidate_group_admin_cache(bot_data, -1001)
    second = await cache.get_group_admins(bot, bot_data, -1001)
    assert set(first) == {7}
    assert set(second) == {8}
    assert bot.get_chat_administrators.await_count == 2


@pytest.mark.asyncio
async def test_timeout_fails_closed_without_caching_empty_admins(monkeypatch):
    async def slow_lookup(_chat_id):
        await asyncio.sleep(0.05)
        return []

    monkeypatch.setattr(cache, "ADMIN_LOOKUP_TIMEOUT_SECONDS", 0.001)
    bot = SimpleNamespace(get_chat_administrators=AsyncMock(side_effect=slow_lookup))
    bot_data = {}
    with pytest.raises(cache.GroupAdminLookupError):
        await cache.get_group_admins(bot, bot_data, -1001)
    assert -1001 not in bot_data["_xyron_group_admin_cache"]
