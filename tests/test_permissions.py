from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import Chat, ChatMember

from app.config import Settings
from app.core.permissions import AuthorizationError, require_admin_access


def _member(status, **permissions):
    return SimpleNamespace(status=status, **permissions)


@pytest.mark.asyncio
async def test_non_admin_actor_is_rejected():
    update = SimpleNamespace(
        effective_message=object(),
        effective_chat=SimpleNamespace(type=Chat.SUPERGROUP, id=-1001, title="test"),
        effective_user=SimpleNamespace(id=44),
    )
    context = SimpleNamespace(
        bot=SimpleNamespace(
            id=99,
            get_chat_member=AsyncMock(
                side_effect=[
                    _member(ChatMember.ADMINISTRATOR, can_restrict_members=True),
                    _member(ChatMember.ADMINISTRATOR),
                    _member(ChatMember.MEMBER),
                ]
            ),
            send_message=AsyncMock(),
        )
    )
    settings = Settings(bot_token="token", owner_id=10)
    with pytest.raises(AuthorizationError, match="administrator"):
        await require_admin_access(update, context, settings, "can_restrict_members")


@pytest.mark.asyncio
async def test_owner_must_be_group_admin_before_actor_is_checked():
    update = SimpleNamespace(
        effective_message=object(),
        effective_chat=SimpleNamespace(type=Chat.SUPERGROUP, id=-1001, title="test"),
        effective_user=SimpleNamespace(id=44),
    )
    context = SimpleNamespace(
        bot=SimpleNamespace(
            id=99,
            get_chat_member=AsyncMock(
                side_effect=[
                    _member(ChatMember.ADMINISTRATOR, can_restrict_members=True),
                    _member(ChatMember.MEMBER),
                ]
            ),
            send_message=AsyncMock(),
        )
    )
    settings = Settings(bot_token="token", owner_id=10)
    with pytest.raises(AuthorizationError, match="configured owner"):
        await require_admin_access(update, context, settings, "can_restrict_members")
    context.bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_bot_permission_is_checked():
    update = SimpleNamespace(
        effective_message=object(),
        effective_chat=SimpleNamespace(type=Chat.SUPERGROUP, id=-1001, title="test"),
        effective_user=SimpleNamespace(id=44),
    )
    context = SimpleNamespace(
        bot=SimpleNamespace(
            id=99,
            get_chat_member=AsyncMock(
                side_effect=[
                    _member(ChatMember.ADMINISTRATOR),
                    _member(ChatMember.OWNER),
                    _member(ChatMember.ADMINISTRATOR, can_restrict_members=True),
                ]
            ),
            send_message=AsyncMock(),
        )
    )
    settings = Settings(bot_token="token", owner_id=10)
    with pytest.raises(AuthorizationError, match="XYRON is missing"):
        await require_admin_access(update, context, settings, "can_restrict_members")


@pytest.mark.asyncio
async def test_read_only_admin_check_does_not_require_unrelated_permission():
    update = SimpleNamespace(
        effective_message=object(),
        effective_chat=SimpleNamespace(type=Chat.SUPERGROUP, id=-1001, title="test"),
        effective_user=SimpleNamespace(id=44),
    )
    actor = _member(ChatMember.ADMINISTRATOR, can_manage_chat=False)
    context = SimpleNamespace(
        bot=SimpleNamespace(
            id=99,
            get_chat_member=AsyncMock(
                side_effect=[
                    _member(ChatMember.ADMINISTRATOR),
                    _member(ChatMember.OWNER),
                    actor,
                ]
            ),
            send_message=AsyncMock(),
        )
    )
    settings = Settings(bot_token="token", owner_id=10)
    access = await require_admin_access(update, context, settings, None)
    assert access.actor is actor


@pytest.mark.asyncio
async def test_actor_cannot_exceed_telegram_moderation_rights():
    update = SimpleNamespace(
        effective_message=object(),
        effective_chat=SimpleNamespace(type=Chat.SUPERGROUP, id=-1001, title="test"),
        effective_user=SimpleNamespace(id=44),
    )
    context = SimpleNamespace(
        bot=SimpleNamespace(
            id=99,
            get_chat_member=AsyncMock(
                side_effect=[
                    _member(ChatMember.ADMINISTRATOR, can_restrict_members=True),
                    _member(ChatMember.OWNER),
                    _member(ChatMember.ADMINISTRATOR, can_restrict_members=False),
                ]
            ),
            send_message=AsyncMock(),
        )
    )
    settings = Settings(bot_token="token", owner_id=10)
    with pytest.raises(AuthorizationError, match="Your Telegram administrator privileges"):
        await require_admin_access(update, context, settings, "can_restrict_members")
