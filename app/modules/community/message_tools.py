"""Admin tools for single-message deletion, bounded purge, and group/user info."""

from __future__ import annotations

from telegram import Chat, ChatMember, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from app.core.permissions import AuthorizationError, require_admin_access


async def _reply(update: Update, text: str) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(text)


async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat = update.effective_message, update.effective_chat
    if not message or not chat or not message.reply_to_message:
        await _reply(update, "Reply to the message you want deleted, then use /del.")
        return
    try:
        await require_admin_access(
            update, context, context.application.bot_data["settings"], "can_delete_messages"
        )
        await context.bot.delete_message(chat.id, message.reply_to_message.message_id)
        await _reply(update, "Message deleted.")
    except AuthorizationError as exc:
        await _reply(update, str(exc))
    except TelegramError as exc:
        await _reply(update, f"Telegram could not delete the message: {exc.message}")


async def purge_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat = update.effective_message, update.effective_chat
    if not message or not chat or not message.reply_to_message:
        await _reply(update, "Reply to the first message in the range, then use /purge [1-100].")
        return
    count = 1
    if context.args:
        if len(context.args) != 1 or not context.args[0].isdecimal():
            await _reply(update, "Usage: reply to the first message, then /purge [1-100].")
            return
        count = int(context.args[0])
    if not 1 <= count <= 100:
        await _reply(update, "Purge count must be between 1 and 100.")
        return
    try:
        await require_admin_access(
            update, context, context.application.bot_data["settings"], "can_delete_messages"
        )
    except AuthorizationError as exc:
        await _reply(update, str(exc))
        return

    first_id = message.reply_to_message.message_id
    last_id = min(first_id + count - 1, message.message_id - 1)
    deleted = 0
    failures = 0
    for message_id in range(first_id, last_id + 1):
        try:
            await context.bot.delete_message(chat.id, message_id)
            deleted += 1
        except TelegramError:
            failures += 1
    try:
        await message.delete()
    except TelegramError:
        failures += 1
    await context.bot.send_message(
        chat.id, f"Purge complete: deleted {deleted} message(s); {failures} could not be deleted."
    )


async def groupinfo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat or chat.type not in {Chat.GROUP, Chat.SUPERGROUP}:
        await _reply(update, "Use /groupinfo in a group.")
        return
    try:
        current = await context.bot.get_chat(chat.id)
        count = await context.bot.get_chat_member_count(chat.id)
    except TelegramError as exc:
        await _reply(update, f"Telegram could not retrieve group information: {exc.message}")
        return
    await _reply(
        update,
        f"Group: {current.title or chat.id}\nID: {chat.id}\nMembers: {count}\nType: {current.type}",
    )


async def userinfo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat = update.effective_message, update.effective_chat
    if not message or not chat:
        return
    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
    elif context.args and len(context.args) == 1 and context.args[0].isdecimal():
        target_id = int(context.args[0])
    else:
        await _reply(update, "Usage: reply to a user or use /info <numeric_user_id>.")
        return
    try:
        member = await context.bot.get_chat_member(chat.id, target_id)
    except TelegramError as exc:
        await _reply(update, f"Telegram could not find that member: {exc.message}")
        return
    role = {
        ChatMember.OWNER: "owner",
        ChatMember.ADMINISTRATOR: "administrator",
        ChatMember.MEMBER: "member",
        ChatMember.RESTRICTED: "restricted",
        ChatMember.LEFT: "left",
        ChatMember.BANNED: "banned",
    }.get(member.status, "unknown")
    username = f"@{member.user.username}" if member.user.username else "unavailable"
    await _reply(
        update,
        f"User: {member.user.full_name}\nID: {member.user.id}\n"
        f"Username: {username}\nStatus: {role}",
    )
