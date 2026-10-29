from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import Chat

from app.modules.community import handlers


def test_formatted_rules_names_the_group_and_escapes_html():
    rendered = handlers._formatted_rules(
        "🚫 No advertising.\n\n🔒 Respect privacy: <private>",
        "Secret Society & Friends",
    )

    assert "<b>📜 RULES OF Secret Society &amp; Friends</b>" in rendered
    assert "🚫 No advertising.\n\n🔒 Respect privacy: &lt;private&gt;" in rendered


def test_default_rules_match_the_supplied_rules_exactly():
    assert handlers.DEFAULT_GROUP_RULES == """🚫 1. No Advertising
Do not advertise or promote anything.

🔞 2. No NSFW Content
Keep the group safe and appropriate.

🔒 3. Respect Privacy
Never share personal or sensitive information.

🛑 4. No Spam
Avoid spamming messages or reactions.

📢 5. No Group Promotion
Do not promote other groups or channels.

💬 6. No Unsolicited DMs
Always ask for permission before messaging someone privately."""


@pytest.mark.asyncio
async def test_send_rules_dm_uses_default_rules_and_group_title(monkeypatch):
    repository = SimpleNamespace(get=AsyncMock(return_value={"rules": ""}))
    monkeypatch.setattr(handlers, "_repository", lambda context: repository)
    bot = SimpleNamespace(
        get_chat=AsyncMock(return_value=SimpleNamespace(title="Random GC")),
        send_message=AsyncMock(),
    )
    context = SimpleNamespace(bot=bot)

    await handlers._send_rules_dm(context, 123, -100456)

    repository.get.assert_awaited_once_with(-100456)
    bot.send_message.assert_awaited_once()
    sent = bot.send_message.await_args.kwargs
    assert sent["chat_id"] == 123
    assert "<b>📜 RULES OF Random GC</b>" in sent["text"]
    assert handlers.DEFAULT_GROUP_RULES in sent["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", ["rules", "#rules", "hash rules", "  HASH   RULES  "])
async def test_plain_rules_request_accepts_supported_phrases(monkeypatch, phrase):
    delivery = AsyncMock()
    monkeypatch.setattr(handlers, "_deliver_or_link_rules", delivery)
    message = SimpleNamespace(text=phrase)
    update = SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(type=Chat.SUPERGROUP, id=-100123),
    )

    await handlers.plain_rules_request(update, SimpleNamespace())

    delivery.assert_awaited_once()
    assert delivery.await_args.args[0] is update


@pytest.mark.asyncio
async def test_plain_rules_request_ignores_normal_conversation(monkeypatch):
    delivery = AsyncMock()
    monkeypatch.setattr(handlers, "_deliver_or_link_rules", delivery)
    update = SimpleNamespace(
        effective_message=SimpleNamespace(text="where are the rules?"),
        effective_chat=SimpleNamespace(type=Chat.SUPERGROUP, id=-100123),
    )

    await handlers.plain_rules_request(update, SimpleNamespace())

    delivery.assert_not_awaited()
