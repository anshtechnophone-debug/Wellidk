from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import Chat

from app.modules.community import handlers


def test_formatted_rules_preserves_spacing_and_escapes_html():
    rendered = handlers._formatted_rules(
        "🚫 No advertising.\n\n🔒 Respect privacy: <private>"
    )

    assert "<b>📜 GROUP RULES</b>" in rendered
    assert "🚫 No advertising.\n\n🔒 Respect privacy: &lt;private&gt;" in rendered
    assert "Thank you for keeping the community respectful." in rendered


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

    delivery.assert_awaited_once_with(update, delivery.call_args.args[1] if False else pytest.ANY)


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
