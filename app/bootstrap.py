"""Application construction and orderly startup/shutdown."""

from __future__ import annotations

import asyncio
import logging

from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, filters

from app.config import Settings
from app.core.command_registry import bot_commands
from app.core.module_loader import Module, load_modules
from app.database.session import create_database, initialize_schema
from app.handlers.common import (
    about_command,
    help_command,
    id_command,
    modules_command,
    ping_command,
    report_command,
    settings_command,
    start_command,
)
from app.handlers.errors import error_handler
from app.modules.moderation.handlers import (
    adminlist_command,
    kickme_command,
    moderation_command,
    modlog_command,
    set_warn_limit_command,
    track_known_user,
    unban_command,
    unmute_command,
    warn_command,
    warning_query_command,
)
from app.services.scheduling import expiration_worker

logger = logging.getLogger(__name__)


def _register_general(application: Application) -> None:
    for command, callback in (
        ("start", start_command),
        ("help", help_command),
        ("about", about_command),
        ("ping", ping_command),
        ("id", id_command),
        ("modules", modules_command),
        ("report", report_command),
        ("settings", settings_command),
        ("modlog", modlog_command),
        ("adminlist", adminlist_command),
        ("warns", warning_query_command),
        ("resetwarn", warning_query_command),
        ("setwarnlimit", set_warn_limit_command),
        ("warn", warn_command),
        ("unban", unban_command),
        ("unmute", unmute_command),
        ("kickme", kickme_command),
    ):
        application.add_handler(CommandHandler(command, callback))
    for command in (
        "ban",
        "dban",
        "sban",
        "tban",
        "mute",
        "dmute",
        "smute",
        "tmute",
        "kick",
        "dkick",
        "skick",
    ):
        application.add_handler(CommandHandler(command, moderation_command))
    application.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND, track_known_user), group=10
    )


async def _post_init(application: Application) -> None:
    settings: Settings = application.bot_data["settings"]
    engine, sessions = create_database(settings.database_url)
    application.bot_data["engine"] = engine
    application.bot_data["sessions"] = sessions
    await initialize_schema(engine)
    await application.bot.set_my_commands(bot_commands())
    stop_event = asyncio.Event()
    application.bot_data["shutdown_event"] = stop_event
    application.create_task(
        expiration_worker(application, stop_event), name="xyron-expiration-worker"
    )


async def _post_shutdown(application: Application) -> None:
    stop_event = application.bot_data.get("shutdown_event")
    if stop_event:
        stop_event.set()
    engine = application.bot_data.get("engine")
    if engine:
        await engine.dispose()


def build_application(settings: Settings | None = None) -> Application:
    resolved = settings or Settings.from_env()
    application = (
        ApplicationBuilder()
        .token(resolved.bot_token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    application.bot_data["settings"] = resolved
    load_modules(
        application,
        (Module("general-moderation", "Core commands and group moderation", _register_general),),
    )
    application.add_error_handler(error_handler)
    return application


def run() -> None:
    from app.logging_config import configure_logging

    settings = Settings.from_env()
    configure_logging(settings.log_level)
    try:
        build_application(settings).run_polling(allowed_updates=None, close_loop=False)
    except KeyboardInterrupt:
        logger.info("XYRON shutdown requested")
