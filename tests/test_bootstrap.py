from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bootstrap import build_application
from app.config import Settings
from app.core.command_registry import COMMANDS
from app.database.session import create_database
from app.handlers.common import health_command


def test_bot_application_registers_only_implemented_commands():
    application = build_application(Settings(bot_token="123:fake-token", owner_id=123456789))
    names = {
        command
        for handlers in application.handlers.values()
        for handler in handlers
        for command in getattr(handler, "commands", ())
    }
    assert {"ban", "tban", "tmute", "mutelist", "kickme", "warn", "settings", "help"} <= names
    assert {command.name for command in COMMANDS if command.enabled} <= names
    assert "play" not in names


@pytest.mark.asyncio
async def test_health_command_checks_database_and_is_registered():
    engine, sessions = create_database("sqlite+aiosqlite:///:memory:")
    message = SimpleNamespace(reply_text=AsyncMock())
    context = SimpleNamespace(
        application=SimpleNamespace(bot_data={"sessions": sessions, "worker_tasks": {}})
    )
    await health_command(SimpleNamespace(effective_message=message), context)
    assert "database online" in message.reply_text.await_args.args[0]
    application = build_application(Settings(bot_token="123:fake-token", owner_id=123456789))
    names = {
        command
        for handlers in application.handlers.values()
        for handler in handlers
        for command in getattr(handler, "commands", ())
    }
    assert "health" in names
    await engine.dispose()
