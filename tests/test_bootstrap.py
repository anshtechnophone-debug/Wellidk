from app.bootstrap import build_application
from app.config import Settings


def test_bot_application_registers_only_implemented_commands():
    application = build_application(Settings(bot_token="123:fake-token", owner_id=123456789))
    names = {
        command
        for handlers in application.handlers.values()
        for handler in handlers
        for command in getattr(handler, "commands", ())
    }
    assert {"ban", "tban", "tmute", "kickme", "warn", "settings", "help"} <= names
    assert "play" not in names
