"""General commands and commands that do not change group permissions."""

from __future__ import annotations

from sqlalchemy import text
from telegram import Chat, ChatMember, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from app.core.help_menu import build_help_page, parse_help_callback
from app.services.reports import ReportRepository


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from app.modules.community.verification import start_verification

    await start_verification(update, context)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        page = build_help_page()
        await update.effective_message.reply_text(page.text, reply_markup=page.keyboard)


async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    if query.data == "help:close":
        try:
            await query.answer()
            await query.delete_message()
        except TelegramError:
            pass
        return
    parsed = parse_help_callback(query.data)
    if parsed is None:
        try:
            await query.answer("This help menu is invalid or expired.", show_alert=True)
        except TelegramError:
            pass
        return
    category, page_number = parsed
    page = build_help_page(category, page_number)
    try:
        await query.edit_message_text(page.text, reply_markup=page.keyboard)
    except BadRequest:
        try:
            await query.answer("This help menu is no longer available.", show_alert=True)
        except TelegramError:
            pass
        return
    try:
        await query.answer()
    except TelegramError:
        pass


async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(
            "XYRON - One Bot. Every Feature.\n"
            "Initial release: secure group moderation and basic utilities."
        )


async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text("Pong")


async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    sessions = context.application.bot_data.get("sessions")
    if sessions is None:
        await message.reply_text("Health: database unavailable; application is not initialized.")
        return
    try:
        async with sessions() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        await message.reply_text("Health: database check failed.")
        return
    tasks = context.application.bot_data.get("worker_tasks", {})
    workers = [
        f"{name}={'running' if not task.done() else 'stopped'}" for name, task in tasks.items()
    ]
    await message.reply_text(
        f"Health: database online; {'; '.join(workers) if workers else 'workers unavailable'}."
    )


async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user:
        return
    target = message.reply_to_message.from_user if message.reply_to_message else user
    await message.reply_text(f"User ID: {target.id}\nChat ID: {chat.id}")


async def modules_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return
    await update.effective_message.reply_text(
        "Implemented: core, moderation, warnings, audit log, basic utilities.\n"
        "Implemented local games: Word Seek, Hangman, Scramble, Anagram, Word Chain, "
        "Trivia, True/False, Riddle, Quick Math, Number Guess, RPS, Tic-Tac-Toe, Team Word.\n"
        "Optional AI: direct replies and advisory incident/report summaries; "
        "disabled by default.\n"
        "Implemented group tools: rules, notes, filters, locks, flood/repeat checks, "
        "welcome/goodbye, reports/appeals, purge, group/user information, personal reminders.\n"
        "Unavailable/not implemented: remaining games, economy, music playback, media processing, "
        "recurring reminders/RSS, polls, verification, analytics, AI enforcement tools."
    )


async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, reporter = update.effective_message, update.effective_chat, update.effective_user
    if (
        not message
        or not chat
        or chat.type not in {Chat.GROUP, Chat.SUPERGROUP}
        or not reporter
        or not message.reply_to_message
        or not message.reply_to_message.from_user
    ):
        if message:
            await message.reply_text(
                "Reply to a group member's message, then use /report [reason]."
            )
        return
    reported = message.reply_to_message.from_user
    reason = " ".join(context.args).strip() or "No reason provided"
    sessions = context.application.bot_data.get("sessions")
    if sessions is None:
        await message.reply_text("Report storage is unavailable; no report was recorded.")
        return
    repository = ReportRepository(sessions)
    report_id, result = await repository.submit(
        chat.id,
        message.reply_to_message.message_id,
        reported.id,
        reporter.id,
        reason,
    )
    if result == "duplicate":
        await message.reply_text("You have already reported that message.")
        return
    if result == "rate_limited":
        await message.reply_text(
            "Report rate limit reached. Please wait before submitting another."
        )
        return
    settings = context.application.bot_data["settings"]
    if settings.report_destination_id is not None:
        recipients = [settings.report_destination_id]
    else:
        try:
            admins = await context.bot.get_chat_administrators(chat.id)
        except TelegramError:
            await message.reply_text("Report was recorded, but XYRON could not retrieve admins.")
            return
        recipients = [admin.user.id for admin in admins if not admin.user.is_bot]
    notice = (
        f"Report #{report_id} in {chat.title or chat.id}\n"
        f"Reporter: {reporter.full_name} ({reporter.id})\n"
        f"Reported user: {reported.full_name} ({reported.id})\nReason: {reason}\n"
        f"Message ID: {message.reply_to_message.message_id}"
    )
    delivered = 0
    for recipient in recipients:
        try:
            await context.bot.send_message(recipient, notice)
            delivered += 1
        except TelegramError:
            continue
    status = (
        "Report recorded and sent to moderators."
        if delivered
        else "Report recorded, but moderators could not be reached by private message."
    )
    router = context.application.bot_data.get("ai_router")
    if router:
        await router.analyze_report(report_id, update, context, message.reply_to_message, reason)
    await message.reply_text(status)


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user = update.effective_message, update.effective_user
    settings = context.application.bot_data["settings"]
    if not message or not user or user.id != settings.owner_id:
        if message:
            await message.reply_text("This command is restricted to the configured owner.")
        return
    async with context.application.bot_data["sessions"]() as session:
        from sqlalchemy import select

        from app.database.models import Group

        groups = list(await session.scalars(select(Group).order_by(Group.title)))
    if not groups:
        await message.reply_text(
            "No groups have been recorded yet. XYRON records a group after moderation activity."
        )
        return
    lines = []
    for group in groups:
        try:
            owner = await context.bot.get_chat_member(group.chat_id, settings.owner_id)
            bot = await context.bot.get_chat_member(group.chat_id, context.bot.id)
            owner_ok = owner.status in {ChatMember.ADMINISTRATOR, ChatMember.OWNER}
            perms = (
                ", ".join(
                    name.removeprefix("can_")
                    for name in ("can_restrict_members", "can_delete_messages", "can_pin_messages")
                    if getattr(bot, name, False)
                )
                or "none"
            )
            lines.append(
                f"{group.title or group.chat_id} ({group.chat_id}): "
                f"owner_admin={owner_ok}; bot={bot.status}; permissions={perms}"
            )
        except TelegramError:
            lines.append(f"{group.title or group.chat_id} ({group.chat_id}): unavailable to XYRON")
    await message.reply_text("Eligible group status:\n" + "\n".join(lines))
