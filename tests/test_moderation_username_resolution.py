from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.moderation.handlers import (
    TargetError,
    _resolve_target,
    _resolve_username_via_userbot,
)


def _context(client):
    accounts = SimpleNamespace(clients=[client] if client else [])
    plugin = SimpleNamespace(accounts=accounts)
    return SimpleNamespace(
        application=SimpleNamespace(bot_data={"music_plugin": plugin})
    )


@pytest.mark.asyncio
async def test_resolves_unseen_username_with_connected_music_account():
    client = SimpleNamespace(
        is_connected=lambda: True,
        get_entity=AsyncMock(
            return_value=SimpleNamespace(
                id=123456789, first_name="Test", username="testuser"
            )
        ),
    )

    user_id = await _resolve_username_via_userbot(_context(client), "@testuser")

    assert user_id == 123456789
    client.get_entity.assert_awaited_once_with("@testuser")


@pytest.mark.asyncio
async def test_rejects_channel_username():
    client = SimpleNamespace(
        is_connected=lambda: True,
        get_entity=AsyncMock(
            return_value=SimpleNamespace(id=987654321, title="Not a user")
        ),
    )

    with pytest.raises(TargetError, match="channel or group"):
        await _resolve_username_via_userbot(_context(client), "@testchannel")


@pytest.mark.asyncio
async def test_explains_when_no_music_account_is_connected():
    with pytest.raises(TargetError, match="numeric Telegram ID"):
        await _resolve_username_via_userbot(_context(None), "@unknownuser")


@pytest.mark.asyncio
async def test_rejects_malformed_username_before_lookup():
    client = SimpleNamespace(
        is_connected=lambda: True,
        get_entity=AsyncMock(),
    )

    with pytest.raises(TargetError, match="valid Telegram username"):
        await _resolve_username_via_userbot(_context(client), "@bad-name")

    client.get_entity.assert_not_awaited()


class _EmptyRows:
    def all(self):
        return []


class _EmptySession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def scalar(self, _statement):
        return None

    async def scalars(self, _statement):
        return _EmptyRows()


@pytest.mark.asyncio
async def test_resolves_unseen_username_without_at_prefix_for_moderation():
    client = SimpleNamespace(
        is_connected=lambda: True,
        get_entity=AsyncMock(
            return_value=SimpleNamespace(
                id=987654321, first_name="New", username="unseenuser"
            )
        ),
    )
    context = _context(client)
    context.application.bot_data["sessions"] = _EmptySession

    update = SimpleNamespace(
        effective_message=SimpleNamespace(reply_to_message=None),
        effective_chat=SimpleNamespace(id=-1001234567890),
    )

    user_id, remaining = await _resolve_target(
        update, context, ["unseenuser"], allow_name=True
    )

    assert user_id == 987654321
    assert remaining == []
    client.get_entity.assert_awaited_once_with("@unseenuser")
