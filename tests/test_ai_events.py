import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from telegram import Chat, ChatMember

from app.config import Settings
from app.modules.ai.assistant import (
    AIEventRouter,
    AIResult,
    ContextMessage,
    detect_event,
    parse_analysis,
)


class FakeProvider:
    def __init__(self, text="Hello!", error=None):
        self.text = text
        self.error = error
        self.calls = []

    async def complete(self, system, prompt):
        self.calls.append((system, prompt))
        if self.error:
            raise self.error
        return AIResult(self.text, input_tokens=10, output_tokens=4)


def _message(text, *, message_id=1, user_id=23, reply=None, forward=None):
    user = SimpleNamespace(id=user_id, is_bot=False, full_name="Member")
    return SimpleNamespace(
        message_id=message_id,
        from_user=user,
        text=text,
        caption=None,
        date=datetime.now(UTC),
        reply_to_message=reply,
        forward_origin=forward,
        reply_text=AsyncMock(),
    )


def _setup(settings, provider, message, chat_type=Chat.SUPERGROUP):
    owner_member = SimpleNamespace(status=ChatMember.OWNER)
    bot = SimpleNamespace(
        id=99,
        username="xyron_bot",
        get_chat_member=AsyncMock(return_value=owner_member),
        send_message=AsyncMock(),
    )
    application = SimpleNamespace(bot_data={"settings": settings})
    context = SimpleNamespace(application=application, bot=bot)
    update = SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(id=-1001, type=chat_type),
        effective_user=message.from_user,
    )
    router = AIEventRouter(settings, provider)
    return router, update, context


def _enabled_settings(**overrides):
    values = {
        "bot_token": "token",
        "owner_id": 10,
        "ai_enabled": True,
        "ai_global_emergency_stop": False,
        "ai_api_key": "test-key",
        "ai_model": "test-model",
        "ai_response_cooldown_seconds": 0,
    }
    values.update(overrides)
    return Settings(**values)


def test_detect_event_requires_address_and_ignores_forwards():
    assert detect_event(_message("Hi everyone"), 99, "xyron_bot") is None
    assert detect_event(_message("Zyron, please help"), 99, "xyron_bot") == "conversation"
    assert detect_event(_message("Hello, @xyron_bot"), 99, "xyron_bot") == "conversation"
    assert detect_event(_message("XYRON admin, fight"), 99, "xyron_bot") == "moderation"
    assert detect_event(_message("@xyron_bot_evil hi"), 99, "xyron_bot") is None
    assert detect_event(_message("XYRON help", forward=object()), 99, "xyron_bot") is None
    bot_user = SimpleNamespace(id=99)
    replied = _message("hello", reply=SimpleNamespace(from_user=bot_user))
    assert detect_event(replied, 99, "xyron_bot") == "conversation"


@pytest.mark.asyncio
async def test_ordinary_messages_do_not_call_provider():
    provider = FakeProvider()
    message = _message("The meeting starts at noon")
    router, update, context = _setup(_enabled_settings(), provider, message)

    await router.handle(update, context)

    assert provider.calls == []
    assert len(router.messages[-1001]) == 1


def test_local_activity_threshold_is_configurable_and_does_not_call_provider():
    settings = _enabled_settings(ai_activity_threshold=5, ai_activity_window_seconds=120)
    router = AIEventRouter(settings, FakeProvider())
    now = datetime.now(UTC)
    for message_id in range(1, 5):
        router.remember(-1001, ContextMessage(message_id, 20, "member", now, "hello"))
        assert router.is_high_activity(-1001) is False
    router.remember(-1001, ContextMessage(5, 20, "member", now, "hello"))
    assert router.is_high_activity(-1001) is True


@pytest.mark.asyncio
async def test_mention_uses_bounded_recent_context_and_replies():
    provider = FakeProvider("Hello there")
    settings = _enabled_settings(ai_context_messages=2)
    message = _message("@xyron_bot hello", message_id=3)
    router, update, context = _setup(settings, provider, message)
    router.remember(-1001, ContextMessage(1, 40, "A", datetime.now(UTC), "earlier one"))
    router.remember(-1001, ContextMessage(2, 41, "B", datetime.now(UTC), "earlier two"))

    await router.handle(update, context)

    assert len(provider.calls) == 1
    assert "earlier one" not in provider.calls[0][1]
    assert "earlier two" in provider.calls[0][1]
    message.reply_text.assert_awaited_once_with("Hello there")


@pytest.mark.asyncio
async def test_moderation_analysis_notifies_owner_without_executing_actions():
    analysis = json.dumps(
        {
            "category": "heated_argument",
            "relevant_message_ids": [1],
            "participant_ids": [23],
            "evidence": "Repeated direct insults",
            "confidence": 0.8,
            "severity": "medium",
            "recommendation": "human_review",
            "rationale": "Needs a human decision",
        }
    )
    provider = FakeProvider(analysis)
    message = _message("@xyron_bot admin, there is a fight")
    router, update, context = _setup(_enabled_settings(), provider, message)

    await router.handle(update, context)

    context.bot.send_message.assert_awaited_once()
    assert "No automatic moderation action was taken" in context.bot.send_message.await_args.args[1]
    message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_ai_global_kill_switch_stops_provider_calls():
    provider = FakeProvider()
    settings = _enabled_settings(ai_global_emergency_stop=True)
    message = _message("XYRON hello")
    router, update, context = _setup(settings, provider, message)

    await router.handle(update, context)

    assert provider.calls == []


@pytest.mark.asyncio
async def test_provider_outage_is_reported_without_crashing_update():
    provider = FakeProvider(error=httpx.ConnectError("offline"))
    message = _message("XYRON, hello")
    router, update, context = _setup(_enabled_settings(), provider, message)

    await router.handle(update, context)

    assert len(provider.calls) == 1
    assert "temporarily unavailable" in message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_group_request_limit_is_enforced():
    provider = FakeProvider()
    settings = _enabled_settings(ai_group_requests_per_hour=1)
    router, update, context = _setup(settings, provider, _message("XYRON hi"))

    await router.handle(update, context)
    update.effective_message = _message("XYRON again", message_id=2)
    update.effective_user = update.effective_message.from_user
    await router.handle(update, context)

    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_report_analysis_includes_trigger_message_and_reports_missing_history():
    analysis = json.dumps(
        {
            "category": "unclear",
            "relevant_message_ids": [55],
            "participant_ids": [23],
            "evidence": "Insufficient surrounding messages",
            "confidence": 0.4,
            "severity": "low",
            "recommendation": "human_review",
            "rationale": "Only the reported message is available",
        }
    )
    provider = FakeProvider(analysis)
    settings = _enabled_settings()
    trigger = _message("/report", message_id=56)
    router, update, context = _setup(settings, provider, trigger)
    reported_message = SimpleNamespace(
        message_id=55,
        from_user=SimpleNamespace(id=24, full_name="Reported", is_bot=False),
        text="quoted insult",
        caption=None,
        date=datetime.now(UTC),
    )

    await router.analyze_report(12, update, context, reported_message, "please review")

    prompt = provider.calls[0][1]
    assert "message_id=55" in prompt
    assert "no context available" not in prompt
    assert "please review" in prompt
    assert "context_available=True" in context.bot.send_message.await_args.args[1]


@pytest.mark.asyncio
async def test_report_analysis_is_deduplicated_in_process():
    provider = FakeProvider(
        json.dumps(
            {
                "category": "unclear",
                "relevant_message_ids": [],
                "participant_ids": [],
                "evidence": "none",
                "confidence": 0.2,
                "severity": "low",
                "recommendation": "human_review",
                "rationale": "review",
            }
        )
    )
    router, update, context = _setup(
        _enabled_settings(), provider, _message("/report", message_id=56)
    )
    reported = SimpleNamespace(
        message_id=55,
        from_user=SimpleNamespace(id=24, full_name="Reported", is_bot=False),
        text="text",
        caption=None,
        date=datetime.now(UTC),
    )
    await router.analyze_report(12, update, context, reported, "review")
    await router.analyze_report(12, update, context, reported, "review")
    assert len(provider.calls) == 1


def test_structured_analysis_rejects_unapproved_recommendations():
    valid = {
        "category": "ordinary_disagreement",
        "severity": "low",
        "evidence": "Two people disagreed about a plan.",
        "rationale": "No repeated targeting is visible.",
        "recommendation": "notify_admins",
        "confidence": 0.7,
        "relevant_message_ids": [1],
        "participant_ids": [2],
    }
    assert parse_analysis(json.dumps(valid)) == valid
    # Actionable recommendations are now accepted by the schema
    valid["recommendation"] = "mute"
    assert parse_analysis(json.dumps(valid)) is not None
    valid["recommendation"] = "ban"
    assert parse_analysis(json.dumps(valid)) is not None
    # Still reject unknown actions
    valid["recommendation"] = "ban_user"
    assert parse_analysis(json.dumps(valid)) is None
    valid["recommendation"] = "delete_all"
    assert parse_analysis(json.dumps(valid)) is None
