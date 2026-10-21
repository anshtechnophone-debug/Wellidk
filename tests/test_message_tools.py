from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import Chat, ChatMember
from telegram.error import BadRequest

from app.config import Settings
from app.modules.community.message_tools import delete_command, purge_command, userinfo_command


def _context(member_statuses, **bot_methods):
    bot = SimpleNamespace(
        id=99,
        get_chat_member=AsyncMock(
            side_effect=[
                SimpleNamespace(status=status, can_delete_messages=can_delete)
                for status, can_delete in member_statuses
            ]
        ),
        **bot_methods,
    )
    application = SimpleNamespace(bot_data={"settings": Settings(bot_token="token", owner_id=10)})
    return SimpleNamespace(bot=bot, application=application)


def _update(reply=True, text="/del"):
    target = SimpleNamespace(message_id=100, from_user=SimpleNamespace(id=22)) if reply else None
    message = SimpleNamespace(
        message_id=104,
        reply_to_message=target,
        text=text,
        reply_text=AsyncMock(),
        delete=AsyncMock(),
    )
    return SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(id=-1001, type=Chat.SUPERGROUP),
        effective_user=SimpleNamespace(id=20),
    ), message


@pytest.mark.asyncio
async def test_delete_reports_confirmed_success():
    update, message = _update()
    context = _context(
        [
            (ChatMember.ADMINISTRATOR, True),
            (ChatMember.OWNER, False),
            (ChatMember.ADMINISTRATOR, True),
        ],
        delete_message=AsyncMock(),
    )

    await delete_command(update, context)

    context.bot.delete_message.assert_awaited_once_with(-1001, 100)
    assert message.reply_text.await_args.args[0] == "Message deleted."


@pytest.mark.asyncio
async def test_delete_api_failure_is_reported_as_failure():
    update, message = _update()
    context = _context(
        [
            (ChatMember.ADMINISTRATOR, True),
            (ChatMember.OWNER, False),
            (ChatMember.ADMINISTRATOR, True),
        ],
        delete_message=AsyncMock(side_effect=BadRequest("message is too old")),
    )

    await delete_command(update, context)

    assert "message is too old" in message.reply_text.await_args.args[0]
    assert message.reply_text.await_args.args[0] != "Message deleted."


@pytest.mark.asyncio
async def test_purge_is_bounded_and_deletes_command():
    update, message = _update(text="/purge 3")
    context = _context(
        [
            (ChatMember.ADMINISTRATOR, True),
            (ChatMember.OWNER, False),
            (ChatMember.ADMINISTRATOR, True),
        ],
        delete_message=AsyncMock(),
        send_message=AsyncMock(),
    )
    context.args = ["3"]

    await purge_command(update, context)

    assert [call.args[1] for call in context.bot.delete_message.await_args_list] == [100, 101, 102]
    message.delete.assert_awaited_once()
    assert "deleted 3" in context.bot.send_message.await_args.args[1]


@pytest.mark.asyncio
async def test_user_info_uses_telegram_membership_result():
    update, message = _update(reply=False, text="/info 22")
    member = SimpleNamespace(
        status=ChatMember.MEMBER,
        user=SimpleNamespace(id=22, full_name="Example User", username="example"),
    )
    context = SimpleNamespace(bot=SimpleNamespace(get_chat_member=AsyncMock(return_value=member)))
    context.args = ["22"]

    await userinfo_command(update, context)

    assert "@example" in message.reply_text.await_args.args[0]
    assert "Status: member" in message.reply_text.await_args.args[0]
