from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import Chat, ChatMember

from app.config import Settings
from app.core.permissions import AuthorizationError, require_admin_access
from app.database.group_bot_admins import GroupBotAdmin


def _user(user_id):
    return SimpleNamespace(id=user_id)


def _member(status, user_id, **permissions):
    return SimpleNamespace(status=status, user=_user(user_id), **permissions)


def _update(user_id=44, chat_id=-1001):
    return SimpleNamespace(
        effective_message=object(),
        effective_chat=SimpleNamespace(type=Chat.SUPERGROUP, id=chat_id, title="test"),
        effective_user=_user(user_id),
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
    admins = [bot_member]
    if actor.status in {ChatMember.ADMINISTRATOR, ChatMember.OWNER}:
        admins.append(actor)
    return SimpleNamespace(
        bot=SimpleNamespace(
            id=99,
            get_chat_administrators=AsyncMock(return_value=admins),
            get_chat_member=AsyncMock(),
            send_message=AsyncMock(),
        ),
        application=SimpleNamespace(bot_data={"sessions": lambda: _Session(record)}),
    )


@pytest.mark.asyncio
async def test_non_admin_without_group_bot_admin_is_rejected():
    context = _context(
        _member(ChatMember.ADMINISTRATOR, 99, can_restrict_members=True),
        _member(ChatMember.MEMBER, 44),
    )
    with pytest.raises(AuthorizationError, match="group administrator"):
        await require_admin_access(_update(), context, Settings(bot_token="token", owner_id=10), "can_restrict_members")


@pytest.mark.asyncio
async def test_real_admin_with_permission_is_authorized_without_bot_admin_role():
    actor = _member(ChatMember.ADMINISTRATOR, 44, can_restrict_members=True)
    context = _context(_member(ChatMember.ADMINISTRATOR, 99, can_restrict_members=True), actor)
    access = await require_admin_access(_update(), context, Settings(bot_token="token", owner_id=10), "can_restrict_members")
    assert access.actor is actor
    assert access.is_bot_admin is False
    context.bot.get_chat_administrators.assert_awaited_once_with(-1001)


@pytest.mark.asyncio
async def test_telegram_group_owner_always_has_full_moderation_access():
    owner = _member(ChatMember.OWNER, 44)
    context = _context(
        _member(ChatMember.ADMINISTRATOR, 99, can_restrict_members=True),
        owner,
    )
    access = await require_admin_access(
        _update(), context, Settings(bot_token="token", owner_id=10),
        "can_restrict_members",
    )
    assert access.actor is owner
    assert access.is_bot_admin is False


@pytest.mark.asyncio
async def test_real_admin_without_right_can_use_group_bot_admin_appointment():
    actor = _member(ChatMember.ADMINISTRATOR, 44, can_restrict_members=False)
    context = _context(
        _member(ChatMember.ADMINISTRATOR, 99, can_restrict_members=True),
        actor,
        record=object(),
    )
    access = await require_admin_access(_update(), context, Settings(bot_token="token", owner_id=10), "can_restrict_members")
    assert access.is_bot_admin is True


@pytest.mark.asyncio
async def test_group_bot_admin_appointment_does_not_transfer_to_another_group():
    actor = _member(ChatMember.ADMINISTRATOR, 44, can_restrict_members=False)
    context = _context(
        _member(ChatMember.ADMINISTRATOR, 99, can_restrict_members=True),
        actor,
        record=object(),
    )
    update = _update(chat_id=-1002)
    with pytest.raises(AuthorizationError, match="not a XYRON bot admin"):
        await require_admin_access(update, context, Settings(bot_token="token", owner_id=10), "can_restrict_members")


@pytest.mark.asyncio
async def test_xyron_must_have_the_required_telegram_permission():
    actor = _member(ChatMember.ADMINISTRATOR, 44, can_restrict_members=True)
    context = _context(_member(ChatMember.ADMINISTRATOR, 99, can_restrict_members=False), actor)
    with pytest.raises(AuthorizationError, match="XYRON is missing"):
        await require_admin_access(_update(), context, Settings(bot_token="token", owner_id=10), "can_restrict_members")
