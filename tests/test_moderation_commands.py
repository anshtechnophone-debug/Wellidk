from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from telegram import Chat, ChatMember, ChatPermissions
from telegram.error import BadRequest

from app.config import Settings
from app.database.models import Base, TemporaryAction
from app.database.session import create_database
from app.modules.moderation.handlers import moderation_command, unmute_command
from app.services.moderation import ModerationRepository


def _member(status, **permissions):
    return SimpleNamespace(status=status, **permissions)


@pytest.fixture
async def bot_context(tmp_path):
    engine, sessions = create_database(f"sqlite+aiosqlite:///{tmp_path / 'moderation.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    default_values = {name: True for name in ChatPermissions.__slots__}
    default_values["can_send_photos"] = False
    defaults = ChatPermissions(**default_values)
    bot = SimpleNamespace(
        id=99,
        get_chat_member=AsyncMock(),
        get_chat=AsyncMock(return_value=SimpleNamespace(permissions=defaults)),
        restrict_chat_member=AsyncMock(),
        ban_chat_member=AsyncMock(),
        unban_chat_member=AsyncMock(),
    )
    settings = Settings(bot_token="token", owner_id=10)
    application = SimpleNamespace(
        bot_data={"settings": settings, "sessions": sessions},
        bot=bot,
    )
    context = SimpleNamespace(application=application, bot=bot, args=["22"])
    await ModerationRepository(sessions).record(-1001, 10, 22, "fixture", None)
    yield context, sessions
    await engine.dispose()


def _update(message_text="/mute 22"):
    message = SimpleNamespace(
        text=message_text,
        reply_to_message=None,
        reply_text=AsyncMock(),
        delete=AsyncMock(),
    )
    update = SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(
            id=-1001, type=Chat.SUPERGROUP, title="test", permissions=None
        ),
        effective_user=SimpleNamespace(id=20, full_name="Moderator"),
    )
    return update, message


def _authorization_members():
    return [
        _member(ChatMember.ADMINISTRATOR, can_restrict_members=True),
        _member(ChatMember.OWNER),
        _member(ChatMember.ADMINISTRATOR, can_restrict_members=True),
        _member(ChatMember.MEMBER),
        _member(ChatMember.MEMBER),
    ]


@pytest.mark.asyncio
async def test_mute_api_success_records_and_saves_pre_mute_baseline(bot_context):
    context, sessions = bot_context
    context.bot.get_chat_member.side_effect = _authorization_members()
    update, message = _update()

    await moderation_command(update, context)

    context.bot.restrict_chat_member.assert_awaited_once()
    applied = context.bot.restrict_chat_member.await_args.kwargs["permissions"]
    assert not any(getattr(applied, name) for name in ChatPermissions.__slots__)
    repository = ModerationRepository(sessions)
    baseline = await repository.restriction_permissions(-1001, 22)
    assert baseline["can_send_messages"] is True
    assert baseline["can_send_photos"] is False
    assert "Muted user 22" in message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_mute_api_failure_is_reported_without_success_or_snapshot(bot_context):
    context, sessions = bot_context
    context.bot.get_chat_member.side_effect = _authorization_members()
    context.bot.restrict_chat_member.side_effect = BadRequest("not enough rights")
    update, message = _update()

    await moderation_command(update, context)

    response = message.reply_text.await_args.args[0]
    assert "Telegram rejected the action" in response
    assert "Muted user" not in response
    assert await ModerationRepository(sessions).restriction_permissions(-1001, 22) is None


@pytest.mark.asyncio
async def test_mute_duration_creates_restart_safe_expiration(bot_context):
    context, sessions = bot_context
    context.bot.get_chat_member.side_effect = _authorization_members()
    context.args = ["22", "10m"]
    update, message = _update("/mute 22 10m")

    await moderation_command(update, context)

    async with sessions() as session:
        action = await session.scalar(select(TemporaryAction))
    assert action.action == "unmute"
    assert datetime.now(UTC) < action.expires_at < datetime.now(UTC) + timedelta(minutes=11)
    assert "Temporarily muted user 22" in message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_unmute_intersects_saved_permissions_with_current_group_defaults(bot_context):
    context, sessions = bot_context
    saved = {name: True for name in ChatPermissions.__slots__}
    repository = ModerationRepository(sessions)
    await repository.remember_permissions(-1001, 22, 20, saved)
    context.bot.get_chat_member.side_effect = _authorization_members()[:3] + [
        _member(ChatMember.MEMBER)
    ]
    update, message = _update("/unmute 22")

    await unmute_command(update, context)

    restored = context.bot.restrict_chat_member.await_args.kwargs["permissions"]
    assert restored.can_send_messages is True
    assert restored.can_send_photos is False
    assert "Restored group permissions" in message.reply_text.await_args.args[0]
    assert await repository.restriction_permissions(-1001, 22) is None


@pytest.mark.asyncio
async def test_unmute_failure_keeps_snapshot_and_reports_telegram_reason(bot_context):
    context, sessions = bot_context
    repository = ModerationRepository(sessions)
    await repository.remember_permissions(-1001, 22, 20, {"can_send_messages": True})
    context.bot.get_chat_member.side_effect = _authorization_members()[:3] + [
        _member(ChatMember.MEMBER)
    ]
    context.bot.restrict_chat_member.side_effect = BadRequest("not enough rights")
    update, message = _update("/unmute 22")

    await unmute_command(update, context)

    assert "Telegram rejected the restriction change" in message.reply_text.await_args.args[0]
    assert await repository.restriction_permissions(-1001, 22) == {"can_send_messages": True}
    assert "Restored" not in message.reply_text.await_args.args[0]
