from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import Chat, ChatMember

from app.config import Settings
from app.database.models import Base
from app.database.session import create_database
from app.modules.community.handlers import _flood_events, _repeat_events, process_group_message
from app.services.group_policy import DEFAULT_POLICY, GroupPolicyRepository


@pytest.fixture
async def group_data(tmp_path):
    engine, sessions = create_database(f"sqlite+aiosqlite:///{tmp_path / 'policy.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield GroupPolicyRepository(sessions), sessions
    await engine.dispose()


@pytest.mark.asyncio
async def test_group_policy_defaults_and_values_persist(group_data):
    repository, _ = group_data
    assert await repository.get(-1001) == DEFAULT_POLICY
    await repository.set_value(-1001, "rules", "Be respectful")
    await repository.set_value(-1001, "locks", ["links"])
    policy = await repository.get(-1001)
    assert policy["rules"] == "Be respectful"
    assert policy["locks"] == ["links"]
    assert policy["filters"] == {}


@pytest.mark.asyncio
async def test_normal_message_without_protections_skips_telegram_admin_lookups(group_data):
    _, sessions = group_data
    user = SimpleNamespace(id=22, is_bot=False)
    message = SimpleNamespace(
        text="ordinary chat",
        caption=None,
        from_user=user,
        reply_text=AsyncMock(),
    )
    update = SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(id=-3001, type=Chat.SUPERGROUP),
        effective_user=user,
    )
    bot = SimpleNamespace(id=99, get_chat_member=AsyncMock())
    context = SimpleNamespace(
        bot=bot,
        application=SimpleNamespace(
            bot_data={
                "settings": Settings(bot_token="token", owner_id=10),
                "sessions": sessions,
            }
        ),
    )

    await process_group_message(update, context)

    bot.get_chat_member.assert_not_awaited()
    message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_link_lock_deletes_only_non_admin_unapproved_messages(group_data):
    repository, sessions = group_data
    await repository.set_value(-1001, "locks", ["links"])
    user = SimpleNamespace(id=22, is_bot=False)
    message = SimpleNamespace(
        text="https://example.invalid",
        caption=None,
        from_user=user,
        effective_attachment=None,
        forward_origin=None,
        sticker=None,
        delete=AsyncMock(),
        reply_text=AsyncMock(),
    )
    update = SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(id=-1001, type=Chat.SUPERGROUP),
        effective_user=user,
    )
    members = [
        SimpleNamespace(status=ChatMember.MEMBER),
        SimpleNamespace(status=ChatMember.OWNER),
        SimpleNamespace(status=ChatMember.ADMINISTRATOR, can_delete_messages=True),
    ]
    bot = SimpleNamespace(
        id=99,
        get_chat_member=AsyncMock(side_effect=members),
    )
    context = SimpleNamespace(
        bot=bot,
        application=SimpleNamespace(
            bot_data={
                "settings": Settings(bot_token="token", owner_id=10),
                "sessions": sessions,
            }
        ),
    )

    await process_group_message(update, context)

    message.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_approved_member_is_exempt_from_link_lock(group_data):
    repository, sessions = group_data
    await repository.set_value(-1001, "locks", ["links"])
    await repository.set_value(-1001, "approved_users", [22])
    user = SimpleNamespace(id=22, is_bot=False)
    message = SimpleNamespace(
        text="https://example.invalid",
        caption=None,
        from_user=user,
        delete=AsyncMock(),
        reply_text=AsyncMock(),
    )
    update = SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(id=-1001, type=Chat.SUPERGROUP),
        effective_user=user,
    )
    context = SimpleNamespace(
        bot=SimpleNamespace(id=99, get_chat_member=AsyncMock()),
        application=SimpleNamespace(
            bot_data={
                "settings": Settings(bot_token="token", owner_id=10),
                "sessions": sessions,
            }
        ),
    )

    await process_group_message(update, context)

    context.bot.get_chat_member.assert_not_awaited()
    message.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_commands_lock_can_delete_command_updates(group_data):
    repository, sessions = group_data
    await repository.set_value(-1001, "locks", ["commands"])
    user = SimpleNamespace(id=22, is_bot=False)
    message = SimpleNamespace(
        text="/ping",
        caption=None,
        from_user=user,
        effective_attachment=None,
        forward_origin=None,
        sticker=None,
        delete=AsyncMock(),
        reply_text=AsyncMock(),
    )
    update = SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(id=-1001, type=Chat.SUPERGROUP),
        effective_user=user,
    )
    bot = SimpleNamespace(
        id=99,
        get_chat_member=AsyncMock(
            side_effect=[
                SimpleNamespace(status=ChatMember.MEMBER),
                SimpleNamespace(status=ChatMember.OWNER),
                SimpleNamespace(status=ChatMember.ADMINISTRATOR, can_delete_messages=True),
            ]
        ),
    )
    context = SimpleNamespace(
        bot=bot,
        application=SimpleNamespace(
            bot_data={
                "settings": Settings(bot_token="token", owner_id=10),
                "sessions": sessions,
            }
        ),
    )

    await process_group_message(update, context)

    message.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_repeated_message_detection_requires_opt_in_and_threshold(group_data):
    repository, sessions = group_data
    chat_id = -2001
    await repository.set_value(
        chat_id,
        "antiflood",
        {"enabled": True, "threshold": 10, "window_seconds": 10, "repeat_threshold": 3},
    )
    user = SimpleNamespace(id=22, is_bot=False)
    messages = [
        SimpleNamespace(
            text="repeated spam phrase",
            caption=None,
            from_user=user,
            effective_attachment=None,
            forward_origin=None,
            sticker=None,
            delete=AsyncMock(),
            reply_text=AsyncMock(),
        )
        for _ in range(3)
    ]
    member = SimpleNamespace(status=ChatMember.MEMBER)
    owner = SimpleNamespace(status=ChatMember.OWNER)
    bot_admin = SimpleNamespace(status=ChatMember.ADMINISTRATOR, can_delete_messages=True)
    context = SimpleNamespace(
        bot=SimpleNamespace(
            id=99,
            get_chat_member=AsyncMock(side_effect=[member, owner, bot_admin] * 3),
        ),
        application=SimpleNamespace(
            bot_data={
                "settings": Settings(bot_token="token", owner_id=10),
                "sessions": sessions,
            }
        ),
    )
    for message in messages:
        update = SimpleNamespace(
            effective_message=message,
            effective_chat=SimpleNamespace(id=chat_id, type=Chat.SUPERGROUP),
            effective_user=user,
        )
        await process_group_message(update, context)
    assert [message.delete.await_count for message in messages] == [0, 0, 1]
    _flood_events.pop((chat_id, user.id), None)
    _repeat_events.pop((chat_id, user.id), None)


@pytest.mark.asyncio
async def test_bot_lock_can_delete_bot_messages_even_if_bot_is_admin(group_data):
    repository, sessions = group_data
    chat_id = -2002
    await repository.set_value(chat_id, "locks", ["bots"])
    user = SimpleNamespace(id=23, is_bot=True)
    message = SimpleNamespace(
        text="bot announcement",
        caption=None,
        from_user=user,
        effective_attachment=None,
        forward_origin=None,
        sticker=None,
        delete=AsyncMock(),
        reply_text=AsyncMock(),
    )
    update = SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(id=chat_id, type=Chat.SUPERGROUP),
        effective_user=user,
    )
    context = SimpleNamespace(
        bot=SimpleNamespace(
            id=99,
            get_chat_member=AsyncMock(
                side_effect=[
                    SimpleNamespace(status=ChatMember.ADMINISTRATOR),
                    SimpleNamespace(status=ChatMember.OWNER),
                    SimpleNamespace(status=ChatMember.ADMINISTRATOR, can_delete_messages=True),
                ]
            ),
        ),
        application=SimpleNamespace(
            bot_data={
                "settings": Settings(bot_token="token", owner_id=10),
                "sessions": sessions,
            }
        ),
    )

    await process_group_message(update, context)

    message.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_repeat_detection_ignores_identical_messages_outside_time_window(
    group_data, monkeypatch
):
    repository, sessions = group_data
    chat_id = -2003
    await repository.set_value(
        chat_id,
        "antiflood",
        {"enabled": True, "threshold": 10, "window_seconds": 2, "repeat_threshold": 3},
    )
    user = SimpleNamespace(id=22, is_bot=False)
    messages = [
        SimpleNamespace(
            text="hello group",
            caption=None,
            from_user=user,
            effective_attachment=None,
            forward_origin=None,
            sticker=None,
            delete=AsyncMock(),
            reply_text=AsyncMock(),
        )
        for _ in range(3)
    ]
    now = iter((0.0, 10.0, 20.0))
    monkeypatch.setattr(
        "app.modules.community.handlers.time",
        SimpleNamespace(monotonic=lambda: next(now)),
    )
    bot = SimpleNamespace(
        id=99,
        get_chat_member=AsyncMock(
            side_effect=[
                SimpleNamespace(status=ChatMember.MEMBER),
                SimpleNamespace(status=ChatMember.OWNER),
                SimpleNamespace(status=ChatMember.ADMINISTRATOR, can_delete_messages=True),
            ]
            * 3
        ),
    )
    context = SimpleNamespace(
        bot=bot,
        application=SimpleNamespace(
            bot_data={
                "settings": Settings(bot_token="token", owner_id=10),
                "sessions": sessions,
            }
        ),
    )
    for message in messages:
        update = SimpleNamespace(
            effective_message=message,
            effective_chat=SimpleNamespace(id=chat_id, type=Chat.SUPERGROUP),
            effective_user=user,
        )
        await process_group_message(update, context)
    assert all(message.delete.await_count == 0 for message in messages)
    _flood_events.pop((chat_id, user.id), None)
    _repeat_events.pop((chat_id, user.id), None)
