from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import Chat

from app.config import Settings
from app.modules.ai import owner_control
from app.modules.ai.owner_control import owner_natural_control, parse_owner_instruction


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("XYRON, ban this user", ("ban", None)),
        ("XYRON, ban 123456789", ("ban", None)),
        ("XYRON kick him", ("kick", None)),
        ("Zyron isko ban karo", ("ban", None)),
        ("XYRON mute this person for ten minutes", ("mute", "10m")),
        ("XYRON mute 123456789 for 10 minutes", ("mute", "10m")),
        ("@xyron_bot unmute him", ("unmute", None)),
        ("XYRON delete the messages", None),
    ],
)
def test_owner_instruction_parser_only_accepts_explicit_supported_phrases(text, expected):
    assert parse_owner_instruction(text, "xyron_bot") == expected


@pytest.mark.asyncio
async def test_non_owner_cannot_use_natural_language_control():
    message = SimpleNamespace(
        text="XYRON ban this user",
        message_id=10,
        reply_text=AsyncMock(),
    )
    user = SimpleNamespace(id=11)
    update = SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(type=Chat.SUPERGROUP),
        effective_user=user,
    )
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={"settings": Settings(bot_token="token", owner_id=10)}
        ),
        bot=SimpleNamespace(username="xyron_bot"),
        chat_data={},
    )

    await owner_natural_control(update, context)

    message.reply_text.assert_not_awaited()
    assert context.chat_data == {}


@pytest.mark.asyncio
async def test_owner_greeting_is_left_for_the_ai_conversation_router():
    message = SimpleNamespace(
        text="XYRON, hello",
        message_id=10,
        reply_text=AsyncMock(),
    )
    update = SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(type=Chat.SUPERGROUP),
        effective_user=SimpleNamespace(id=10),
    )
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={"settings": Settings(bot_token="token", owner_id=10)}
        ),
        bot=SimpleNamespace(username="xyron_bot"),
        chat_data={},
    )

    await owner_natural_control(update, context)

    message.reply_text.assert_not_awaited()
    assert context.chat_data == {}


@pytest.mark.asyncio
async def test_owner_message_addressed_to_another_bot_is_ignored():
    message = SimpleNamespace(
        text="@another_bot ban this user",
        message_id=10,
        reply_text=AsyncMock(),
    )
    update = SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(type=Chat.SUPERGROUP),
        effective_user=SimpleNamespace(id=10),
    )
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={"settings": Settings(bot_token="token", owner_id=10)}
        ),
        bot=SimpleNamespace(username="xyron_bot"),
        chat_data={},
    )

    await owner_natural_control(update, context)

    message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_numeric_owner_instruction_dispatches_only_to_existing_moderation_handler(
    monkeypatch,
):
    called = AsyncMock()
    monkeypatch.setattr(owner_control, "_moderate", called)
    message = SimpleNamespace(
        text="XYRON, ban 123456789",
        message_id=10,
        reply_to_message=None,
        reply_text=AsyncMock(),
    )
    user = SimpleNamespace(id=10)
    update = SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(type=Chat.SUPERGROUP),
        effective_user=user,
    )
    application = SimpleNamespace(bot_data={"settings": Settings(bot_token="token", owner_id=10)})
    context = SimpleNamespace(
        application=application,
        bot=SimpleNamespace(username="xyron_bot"),
        chat_data={},
    )

    await owner_natural_control(update, context)

    called.assert_awaited_once()
    assert called.await_args.args[2] == "ban"
    assert called.await_args.args[1].args == ["123456789"]
