from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import Chat, ChatMember

from app.config import Settings
from app.core.permissions import AuthorizationError, require_admin_access
from app.database.group_bot_admins import GroupBotAdmin


def _member(status, **permissions):
    return SimpleNamespace(status=status, **permissions)


def _update(user_id=44, chat_id=-1001):
    return SimpleNamespace(
        effective_message=object(),
        effective_chat=SimpleNamespace(type=Chat.SUPERGROUP, id=chat_id, title="test"),
        effective_user=SimpleNamespace(id=user_id),
    )


class _Session:
    def __init__(self, record=None):
        self.record = record

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, model, key):
        assert model is GroupBotAdmin
        return self.record if key == (-1001, 44) else None


def _context(bot_member, actor, record=None):
    return SimpleNamespace(
        bot=SimpleNamespace(
            id=99,
            get_chat_member=AsyncMock(side_effect=[bot_member, actor]),
            send_message=AsyncMock(),
        ),
        application=SimpleNamespace(bot_data={"sessions": lambda: _Session(record)}),
    )


@pytest.mark.asyncio
async def test_non_admin_without_group_bot_admin_is_rejected():
    context = _context(
        _member(ChatMember.ADMINISTRATOR, can_restrict_members=True),
        _member(ChatMember.MEMBER),
    )
    with pytest.raises(AuthorizationError, match="group administrator"):
        await require_admin_access(_update(), context, Settings(bot_token="token", owner_id=10), "can_restrict_members")


@pytest.mark.asyncio
async def test_real_admin_with_permission_is_authorized_without_bot_admin_role():
    actor = _member(ChatMember.ADMINISTRATOR, can_restrict_members=True)
    context = _context(_member(ChatMember.ADMINISTRATOR, can_restrict_members=True), actor)
    access = await require_admin_access(_update(), context, Settings(bot_token="token", owner_id=10), "can_restrict_members")
    assert access.actor is actor
    assert access.is_bot_admin is False


@pytest.mark.asyncio
async def test_real_admin_without_right_can_use_group_bot_admin_appointment():
    actor = _member(ChatMember.ADMINISTRATOR, can_restrict_members=False)
    context = _context(
        _member(ChatMember.ADMINISTRATOR, can_restrict_members=True),
        actor,
        record=object(),
    )
    access = await require_admin_access(_update(), context, Settings(bot_token="token", owner_id=10), "can_restrict_members")
    assert access.is_bot_admin is True


@pytest.mark.asyncio
async def test_group_bot_admin_appointment_does_not_transfer_to_another_group():
    actor = _member(ChatMember.ADMINISTRATOR, can_restrict_members=False)
    context = _context(
        _member(ChatMember.ADMINISTRATOR, can_restrict_members=True),
        actor,
        record=object(),
    )
    update = _update(chat_id=-1002)
    with pytest.raises(AuthorizationError, match="not a XYRON bot admin"):
        await require_admin_access(update, context, Settings(bot_token="token", owner_id=10), "can_restrict_members")


@pytest.mark.asyncio
async def test_xyron_must_have_the_required_telegram_permission():
    actor = _member(ChatMember.ADMINISTRATOR, can_restrict_members=True)
    context = _context(_member(ChatMember.ADMINISTRATOR, can_restrict_members=False), actor)
    with pytest.raises(AuthorizationError, match="XYRON is missing"):
        await require_admin_access(_update(), context, Settings(bot_token="token", owner_id=10), "can_restrict_members")
