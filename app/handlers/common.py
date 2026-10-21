"""General commands and commands that do not change group permissions."""

from __future__ import annotations

from telegram import Chat, ChatMember, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from app.core.command_registry import COMMANDS


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(
            "XYRON is online. Use /help to see commands that are implemented and enabled."
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    visible = [command for command in COMMANDS if command.enabled]
    categories: dict[str, list[str]] = {}
    for command in visible:
        access = "owner only" if command.owner_only else "admin" if command.admin else "member"
        categories.setdefault(command.category, []).append(
            f"{command.usage} - {command.description} ({access})"
        )
    text = "\n\n".join(
        f"{category}\n" + "\n".join(entries) for category, entries in categories.items()
    )
    if update.effective_message:
        await update.effective_message.reply_text(text)


async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(
            "XYRON - One Bot. Every Feature.\n"
            "Initial release: secure group moderation and basic utilities."
        )


async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text("Pong")


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
        "Unavailable/not implemented: anti-spam suite, games/economy, music playback, AI, "
        "media processing, reminders/RSS, polls, welcome/verification, analytics."
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
    try:
        admins = await context.bot.get_chat_administrators(chat.id)
    except TelegramError:
        await message.reply_text("XYRON could not retrieve this group's administrators.")
        return
    recipients = [admin.user.id for admin in admins if not admin.user.is_bot]
    notice = (
        f"Report in {chat.title or chat.id}\nReporter: {reporter.full_name} ({reporter.id})\n"
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
    await message.reply_text(
        "Report sent to group administrators."
        if delivered
        else (
            "No group administrator has opened a private chat with XYRON; "
            "report could not be delivered."
        )
    )


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
