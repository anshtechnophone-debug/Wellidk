from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.moderation.handlers import (
    TargetError,
    _resolve_username_via_userbot,
)


def _context(client):
    accounts = SimpleNamespace(clients=[client] if client else [])
    plugin = SimpleNamespace(accounts=accounts)
    return SimpleNamespace(
        application=SimpleNamespace(bot_data={"music_plugin": plugin})
    )


@pytest.mark.asyncio
async def test_resolves_username_using_connected_music_userbot(monkeypatch):
    for name in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELETHON_SESSION"):
        monkeypatch.delenv(name, raising=False)
    client = SimpleNamespace(
        is_connected=lambda: True,
        get_entity=AsyncMock(
            return_value=SimpleNamespace(id=123456789, first_name="Test", username="testuser")
        ),
    )

    user_id = await _resolve_username_via_userbot(_context(client), "@testuser")

    assert user_id == 123456789
    client.get_entity.assert_awaited_once_with("@testuser")


@pytest.mark.asyncio
async def test_rejects_channel_username_instead_of_user(monkeypatch):
    for name in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELETHON_SESSION"):
        monkeypatch.delenv(name, raising=False)
    client = SimpleNamespace(
        is_connected=lambda: True,
        get_entity=AsyncMock(return_value=SimpleNamespace(id=987654321, title="Not a user")),
    )

    with pytest.raises(TargetError, match="channel or group"):
        await _resolve_username_via_userbot(_context(client), "@testchannel")


@pytest.mark.asyncio
async def test_reports_missing_userbot_and_numeric_id_fallback(monkeypatch):
    for name in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELETHON_SESSION"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(TargetError, match="numeric Telegram ID"):
        await _resolve_username_via_userbot(_context(None), "@unknownuser")
