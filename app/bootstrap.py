
"""Application construction and orderly startup/shutdown."""

from __future__ import annotations

import asyncio
import logging

from telegram import Chat, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.config import Settings
from app.core.command_registry import bot_commands
from app.core.module_loader import Module, load_modules
from app.database.session import create_database, initialize_schema
from app.handlers.common import (
    about_command,
    health_command,
    help_callback,
    help_command,
    id_command,
    modules_command,
    ping_command,
    report_command,
    settings_command,
    start_command,
)
from app.handlers.errors import error_handler
from app.modules.ai.assistant import AIEventRouter
from app.modules.ai.owner_control import owner_natural_control
from app.modules.appeals.handlers import (
    appeal_command,
    appeals_command,
    reviewappeal_command,
)
from app.modules.community.handlers import (
    antiflood_command,
    approve_command,
    filter_command,
    filters_command,
    getnote_command,
    group_member_event,
    lock_command,
    locks_command,
    notes_command,
    process_group_message,
    reports_command,
    review_report_command,
    rules_command,
    save_command,
    setgoodbye_command,
    setrules_command,
    setwelcome_command,
    stopfilter_command,
    unapprove_command,
    unlock_command,
)
from app.modules.community.verification import verify_human_callback
from app.modules.community.message_tools import (
    delete_command,
    groupinfo_command,
    purge_command,
    userinfo_command,
)
from app.modules.games.anagram_handlers import anagram_command, anagramanswer_command
from app.modules.games.board_handlers import (
    rps_command,
    tictactoe_command,
    tttcancel_command,
    tttmove_command,
)
from app.modules.games.casual_handlers import (
    gamerank_command,
    hangcancel_command,
    hangman_command,
    letter_command,
    numbercancel_command,
    numbergame_command,
    numberguess_command,
    scramble_command,
    scramblecancel_command,
    unscramble_command,
)
from app.modules.games.chain_handlers import (
    chain_command,
    wordchain_command,
    wordchaincancel_command,
)
from app.modules.games.handlers import (
    guess_command,
    wordcancel_command,
    wordrank_command,
    wordseek_command,
)
from app.modules.games.question_handlers import (
    mathanswer_command,
    questioncancel_command,
    quickmath_command,
    riddle_command,
    riddleanswer_command,
    tfanswer_command,
    trivia_command,
    triviaanswer_command,
    truefalse_command,
)
from app.modules.games.team_handlers import (
    teamguess_command,
    teamword_command,
    teamwordcancel_command,
)
from app.modules.moderation.handlers import (
    adminlist_command,
    banlist_command,
    kickme_command,
    moderation_command,
    modlog_command,
    modstats_command,
    mutelist_command,
    set_warn_limit_command,
    track_known_user,
    unban_command,
    unmute_command,
    warn_command,
    warning_query_command,
)
from app.modules.reminders.handlers import (
    cancelreminder_command,
    remind_command,
    reminders_command,
)
from app.services.group_history import GroupHistoryRepository, GroupMessage
from app.services.reminder_worker import reminder_worker
from app.services.scheduling import expiration_worker

logger = logging.getLogger(__name__)


async def collect_group_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Persist group messages for future history searches."""

    message = update.effective_message
    chat = update.effective_chat

    if not message or not chat:
        return

    if chat.type not in {Chat.GROUP, Chat.SUPERGROUP}:
        return

    user = message.from_user
    if not user or user.is_bot:
        return

    text = (message.text or message.caption or "").strip()
    if not text:
        return

    sessions = context.application.bot_data.get("sessions")
    if sessions is None:
        logger.warning("Group history collector has no database session factory")
        return

    try:
        repository = GroupHistoryRepository(sessions)
        await repository.save_message(
            chat_id=chat.id,
            message_id=message.message_id,
            user_id=user.id,
            username=user.username,
            display_name=user.full_name,
            message_text=text,
            sent_at=message.date,
        )
    except Exception:
        logger.exception(
            "Failed to save group message: chat_id=%s message_id=%s",
            chat.id,
            message.message_id,
        )


def _register_general(application: Application) -> None:
    for command, callback in (
        ("start", start_command),
        ("help", help_command),
        ("about", about_command),
        ("ping", ping_command),
        ("health", health_command),
        ("id", id_command),
        ("modules", modules_command),
        ("report", report_command),
        ("settings", settings_command),
        ("modlog", modlog_command),
        ("adminlist", adminlist_command),
        ("banlist", banlist_command),
        ("modstats", modstats_command),
        ("remind", remind_command),
        ("reminders", reminders_command),
        ("cancelreminder", cancelreminder_command),
        ("appeal", appeal_command),
        ("appeals", appeals_command),
        ("reviewappeal", reviewappeal_command),
        ("warns", warning_query_command),
        ("resetwarn", warning_query_command),
        ("setwarnlimit", set_warn_limit_command),
        ("warn", warn_command),
        ("unban", unban_command),
        ("unmute", unmute_command),
        ("mutelist", mutelist_command),
        ("kickme", kickme_command),
        ("wordseek", wordseek_command),
        ("guess", guess_command),
        ("wordcancel", wordcancel_command),
        ("wordrank", wordrank_command),
        ("rules", rules_command),
        ("setrules", setrules_command),
        ("save", save_command),
        ("get", getnote_command),
        ("notes", notes_command),
        ("filter", filter_command),
        ("filters", filters_command),
        ("stop", stopfilter_command),
        ("lock", lock_command),
        ("unlock", unlock_command),
        ("locks", locks_command),
        ("antiflood", antiflood_command),
        ("approve", approve_command),
        ("unapprove", unapprove_command),
        ("setwelcome", setwelcome_command),
        ("setgoodbye", setgoodbye_command),
        ("del", delete_command),
        ("purge", purge_command),
        ("info", userinfo_command),
        ("groupinfo", groupinfo_command),
        ("reports", reports_command),
        ("reviewreport", review_report_command),
        ("hangman", hangman_command),
        ("letter", letter_command),
        ("hangcancel", hangcancel_command),
        ("scramble", scramble_command),
        ("unscramble", unscramble_command),
        ("scramblecancel", scramblecancel_command),
        ("numbergame", numbergame_command),
        ("numberguess", numberguess_command),
        ("numbercancel", numbercancel_command),
        ("gamerank", gamerank_command),
        ("trivia", trivia_command),
        ("triviaanswer", triviaanswer_command),
        ("truefalse", truefalse_command),
        ("tfanswer", tfanswer_command),
        ("riddle", riddle_command),
        ("riddleanswer", riddleanswer_command),
        ("quickmath", quickmath_command),
        ("mathanswer", mathanswer_command),
        ("questioncancel", questioncancel_command),
        ("rps", rps_command),
        ("tictactoe", tictactoe_command),
        ("tttmove", tttmove_command),
        ("tttcancel", tttcancel_command),
        ("wordchain", wordchain_command),
        ("chain", chain_command),
        ("wordchaincancel", wordchaincancel_command),
        ("anagram", anagram_command),
        ("anagramanswer", anagramanswer_command),
        ("teamword", teamword_command),
        ("teamguess", teamguess_command),
        ("teamwordcancel", teamwordcancel_command),
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

    # Lower group number runs first. The history collector runs independently
    # of the AI router, so ordinary group messages are saved even when they
    # do not mention XYRON.
    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            collect_group_message,
        ),
        group=8,
    )

    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            track_known_user,
        ),
        group=10,
    )
    application.add_handler(
        MessageHandler(filters.ALL, process_group_message),
        group=9,
    )
    application.add_handler(
        ChatMemberHandler(
            group_member_event,
            ChatMemberHandler.CHAT_MEMBER,
        )
    )

    application.bot_data["ai_router"] = AIEventRouter(
        application.bot_data["settings"]
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            owner_natural_control,
        ),
        group=0,
    )
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            application.bot_data["ai_router"].handle,
        ),
        group=1,
    )
    application.add_handler(CallbackQueryHandler(verify_human_callback, pattern="^verify_"))
    application.add_handler(
        CallbackQueryHandler(help_callback, pattern=r"^help:")
    )


async def _post_init(application: Application) -> None:
    settings: Settings = application.bot_data["settings"]

    engine, sessions = create_database(settings.database_url)
    application.bot_data["engine"] = engine
    application.bot_data["sessions"] = sessions

    # Importing GroupMessage above registers its model with Base.metadata,
    # so initialize_schema can create the group history table as well.
    await initialize_schema(engine)

    await application.bot.set_my_commands(bot_commands())

    stop_event = asyncio.Event()
    application.bot_data["shutdown_event"] = stop_event

    application.bot_data["worker_tasks"] = {
        "moderation_expiry": application.create_task(
            expiration_worker(application, stop_event),
            name="xyron-expiration-worker",
        ),
        "reminders": application.create_task(
            reminder_worker(application, stop_event),
            name="xyron-reminder-worker",
        ),
    }


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
        (
            Module(
                "general-moderation",
                "Core commands and group moderation",
                _register_general,
            ),
        ),
    )

    application.add_error_handler(error_handler)
    return application


def run() -> None:
    from app.logging_config import configure_logging

    settings = Settings.from_env()
    configure_logging(settings.log_level)

    try:
        build_application(settings).run_polling(
            # Telegram excludes chat_member updates by default unless explicitly requested.
            # The join-verification gate depends on these updates to mute new members.
            allowed_updates=Update.ALL_TYPES,
            close_loop=False,
        )
    except KeyboardInterrupt:
        logger.info("XYRON shutdown requested")
