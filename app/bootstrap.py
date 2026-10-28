
"""Application construction and orderly startup/shutdown."""

from __future__ import annotations

import asyncio
import logging

from telegram import BotCommandScopeChat, BotCommandScopeDefault, Chat, Update
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
from app.modules.community.verification import community_access_gate, community_join_check_callback, verify_human_callback
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
from app.modules.moderation.bot_admins import admin_command, unadmin_command, botadminlist_command, demote_command, pin_command
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
from app.services.activity_log import ActivityEvent, ActivityLogRepository, activity_retention_worker
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
    if not user:
        return

    text = (message.text or message.caption or "").strip()
    if not text:
        attachment = getattr(message, "effective_attachment", None)
        if attachment is None:
            return
        if getattr(message, "photo", None):
            text = "[photo]"
        elif getattr(message, "video", None):
            text = "[video]"
        elif getattr(message, "animation", None):
            text = "[animation]"
        elif getattr(message, "document", None):
            text = "[document]"
        elif getattr(message, "audio", None):
            text = "[audio]"
        elif getattr(message, "voice", None):
            text = "[voice]"
        elif getattr(message, "video_note", None):
            text = "[video note]"
        elif getattr(message, "sticker", None):
            text = "[sticker]"
        elif getattr(message, "location", None):
            text = "[location]"
        elif getattr(message, "contact", None):
            text = "[contact]"
        elif getattr(message, "poll", None):
            text = "[poll]"
        else:
            text = "[media]"

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


async def collect_group_activity(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Record group messages and issued commands in the rolling 24-hour audit log."""
    message = update.effective_message
    chat = update.effective_chat
    if not message or not chat or chat.type not in {Chat.GROUP, Chat.SUPERGROUP}:
        return
    sessions = context.application.bot_data.get("sessions")
    if sessions is None:
        return
    user = message.from_user
    raw = (message.text or message.caption or "").strip()
    if message.text and message.text.lstrip().startswith("/"):
        command = message.text.split(maxsplit=1)[0].split("@")[0].lstrip("/").lower()
        args = message.text.split(maxsplit=1)[1] if len(message.text.split(maxsplit=1)) > 1 else ""
        status = "received"
        await ActivityLogRepository(sessions).record(
            chat_id=chat.id,
            event_type="command",
            actor_id=user.id if user else None,
            message_id=message.message_id,
            action=command,
            status=status,
            details={"command_text": message.text[:2000], "arguments": args[:1000],
                     "reply_to_message_id": message.reply_to_message.message_id
                     if message.reply_to_message else None},
            created_at=message.date,
        )
        return
    attachment = getattr(message, "effective_attachment", None)
    if message.photo:
        media_type = "photo"
    elif message.video:
        media_type = "video"
    elif message.animation:
        media_type = "animation"
    elif message.document:
        media_type = "document"
    elif message.audio:
        media_type = "audio"
    elif message.voice:
        media_type = "voice"
    elif message.video_note:
        media_type = "video_note"
    elif message.sticker:
        media_type = "sticker"
    elif message.location:
        media_type = "location"
    elif message.contact:
        media_type = "contact"
    elif message.poll:
        media_type = "poll"
    elif message.new_chat_members or message.left_chat_member or message.pinned_message:
        media_type = "service"
    else:
        media_type = "text"
    try:
        await ActivityLogRepository(sessions).record(
            chat_id=chat.id,
            event_type=(
                "message_edit" if update.edited_message
                else "admin_action" if message.pinned_message
                else "message"
            ),
            actor_id=user.id if user else None,
            message_id=message.message_id,
            action="pin" if message.pinned_message else None,
            status="observed",
            details={
                "text_or_caption": raw[:10000],
                "media_type": media_type,
                "pinned_message_id": message.pinned_message.message_id
                if message.pinned_message else None,
                "has_attachment": attachment is not None,
                "reply_to_message_id": message.reply_to_message.message_id
                if message.reply_to_message else None,
                "date": message.date.isoformat() if message.date else None,
            },
            created_at=message.edit_date or message.date,
        )
    except Exception:
        logger.exception("Failed to log group activity: chat_id=%s", chat.id)


async def collect_chat_member_activity(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Record member status/restriction/admin-role changes Telegram delivers."""
    change = update.chat_member
    if not change or not update.effective_chat:
        return
    sessions = context.application.bot_data.get("sessions")
    if sessions is None:
        return
    old = change.old_chat_member
    new = change.new_chat_member
    old_status = getattr(old, "status", "unknown")
    new_status = getattr(new, "status", "unknown")
    old_permissions = {
        name: bool(getattr(old, name, False))
        for name in getattr(old, "__slots__", ())
        if name.startswith("can_")
    }
    new_permissions = {
        name: bool(getattr(new, name, False))
        for name in getattr(new, "__slots__", ())
        if name.startswith("can_")
    }
    action = "member_status_change"
    if old_status != new_status:
        action = f"{old_status}_to_{new_status}"
    elif old_permissions != new_permissions:
        action = "permissions_changed"
    try:
        await ActivityLogRepository(sessions).record(
            chat_id=update.effective_chat.id,
            event_type="admin_or_member_change",
            actor_id=change.from_user.id if change.from_user else None,
            target_id=new.user.id if getattr(new, "user", None) else None,
            action=action,
            status="observed",
            details={
                "old_status": old_status,
                "new_status": new_status,
                "old_permissions": old_permissions,
                "new_permissions": new_permissions,
            },
        )
    except Exception:
        logger.exception("Failed to log chat member update")


async def plain_moderation_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle only exact standalone moderation words without a slash."""
    message = update.effective_message
    if not message or not message.text:
        return
    action = message.text.strip().casefold()
    if action == "unmute":
        await unmute_command(update, context)
    elif action == "unban":
        await unban_command(update, context)
    else:
        # moderation_command derives the action from the exact message text;
        # its target resolver uses the replied-to user's message.
        await moderation_command(update, context)


def _register_general(application: Application) -> None:
    for command, callback in (
        ("admin", admin_command),
        ("unadmin", unadmin_command),
        ("botadminlist", botadminlist_command),
        ("pin", pin_command),
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

    # Owner can demote a replied-to user from XYRON's independent bot-admin list.
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS
            & filters.TEXT
            & ~filters.COMMAND
            & filters.Regex(r"(?i)^\s*demote\s*$"),
            demote_command,
        ),
        group=0,
    )

    # Accept exact standalone moderation words without a slash. Longer
    # conversational sentences (e.g. "mute kra hai kal kisi ko") never match.
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS
            & filters.TEXT
            & ~filters.COMMAND
            & filters.Regex(r"(?i)^\s*(mute|unmute|kick|ban|unban)\s*$"),
            plain_moderation_command,
        ),
        group=0,
    )

    # Also accept plain-text "kick Name" as well as /kick Name.
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS
            & filters.TEXT
            & ~filters.COMMAND
            & filters.Regex(r"(?i)^kick\s+.+$"),
            moderation_command,
        ),
        group=0,
    )

    application.add_handler(
        MessageHandler(filters.ALL, collect_group_activity),
        group=7,
    )
    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            collect_group_message,
        ),
        group=8,
    )
    application.add_handler(
        ChatMemberHandler(
            collect_chat_member_activity,
            ChatMemberHandler.CHAT_MEMBER,
        ),
        group=11,
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
    # Private access gate runs before command/callback handlers; owner and membership bypass it.
    application.add_handler(MessageHandler(filters.ALL, community_access_gate), group=-1)
    application.add_handler(CallbackQueryHandler(community_access_gate), group=-1)

    application.add_handler(CallbackQueryHandler(verify_human_callback, pattern="^verify_"))
    application.add_handler(CallbackQueryHandler(community_join_check_callback, pattern="^community_join_check$"))
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

    # Hide the slash-command menu from everyone by default; publish it only
    # in the configured owner's private chat.
    await application.bot.set_my_commands([], scope=BotCommandScopeDefault())
    for owner_id in settings.owner_ids:
        await application.bot.set_my_commands(
            bot_commands(),
            scope=BotCommandScopeChat(chat_id=owner_id),
        )

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
        "activity_retention": application.create_task(
            activity_retention_worker(application, stop_event),
            name="xyron-activity-retention-worker",
        ),
    }


async def _post_shutdown(application: Application) -> None:
    stop_event = application.bot_data.get("shutdown_event")
    if stop_event:
        stop_event.set()

    ai_router = application.bot_data.get("ai_router")
    provider = getattr(ai_router, "provider", None)
    close_provider = getattr(provider, "close", None)
    if close_provider:
        await close_provider()

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
