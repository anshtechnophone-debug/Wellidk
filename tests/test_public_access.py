from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import Chat, ChatMember
from telegram.ext import ApplicationHandlerStop

from app.modules.community import verification


def test_channel_member_statuses_are_recognized():
    assert verification._is_member(SimpleNamespace(status=ChatMember.MEMBER))
    assert verification._is_member(SimpleNamespace(status=ChatMember.ADMINISTRATOR))
    assert verification._is_member(SimpleNamespace(status=ChatMember.OWNER))
    assert not verification._is_member(SimpleNamespace(status=ChatMember.LEFT))
    assert not verification._is_member(SimpleNamespace(status=ChatMember.BANNED))


@pytest.mark.asyncio
async def test_joined_channel_member_can_use_private_help_without_prompt(monkeypatch):
    member_check = AsyncMock(return_value=True)
    join_prompt = AsyncMock()
    monkeypatch.setattr(verification, "_is_community_member", member_check)
    monkeypatch.setattr(verification, "_send_community_join_prompt", join_prompt)

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=123),
        effective_chat=SimpleNamespace(type=Chat.PRIVATE),
        effective_message=SimpleNamespace(text="/help"),
        callback_query=None,
    )
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={"settings": SimpleNamespace(is_owner=lambda user_id: False)}
        )
    )

    await verification.community_access_gate(update, context)

    member_check.assert_awaited_once()
    join_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_nonmember_is_stopped_and_shown_channel_join_prompt(monkeypatch):
    member_check = AsyncMock(return_value=False)
    join_prompt = AsyncMock()
    monkeypatch.setattr(verification, "_is_community_member", member_check)
    monkeypatch.setattr(verification, "_send_community_join_prompt", join_prompt)
    monkeypatch.setattr(verification, "_community_chat_id", lambda: -1001234567890)

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=123),
        effective_chat=SimpleNamespace(type=Chat.PRIVATE),
        effective_message=SimpleNamespace(text="/help"),
        callback_query=None,
    )
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={"settings": SimpleNamespace(is_owner=lambda user_id: False)}
        )
    )

    with pytest.raises(ApplicationHandlerStop):
        await verification.community_access_gate(update, context)

    member_check.assert_awaited_once()
    join_prompt.assert_awaited_once_with(update.effective_message)


@pytest.mark.asyncio
async def test_start_onboarding_is_ignored_in_groups():
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=123),
        effective_chat=SimpleNamespace(type=Chat.SUPERGROUP),
    )
    context = SimpleNamespace(args=[])

    await verification.start_verification(update, context)

    message.reply_text.assert_not_awaited()
