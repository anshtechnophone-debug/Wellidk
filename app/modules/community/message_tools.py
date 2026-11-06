"""Admin tools for single-message deletion, bounded purge, and group/user info."""

from __future__ import annotations

import html

from sqlalchemy import select
from telegram import Chat, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from app.core.permissions import AuthorizationError, require_admin_access
from app.services.activity_log import ActivityLogRepository
from app.services.group_history import GroupMessage
from app.core.telegram_format import user_mention


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
        sessions = getattr(getattr(context, "application", None), "bot_data", {}).get("sessions")
        if sessions is not None:
            await ActivityLogRepository(sessions).record(
                chat_id=chat.id,
                event_type="message_deletion",
                actor_id=update.effective_user.id if update.effective_user else None,
                message_id=message.reply_to_message.message_id,
                action="delete",
                status="success",
                details={"command": "/del"},
            )
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
    try:
        await ActivityLogRepository(context.application.bot_data["sessions"]).record(
            chat_id=chat.id,
            event_type="message_deletion",
            actor_id=update.effective_user.id if update.effective_user else None,
            action="purge",
            status="success" if failures == 0 else "partial",
            details={"first_message_id": first_id, "last_message_id": last_id,
                     "deleted": deleted, "failed": failures},
        )
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Could not audit purge action")
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
    """Show a user's current identity and names XYRON previously observed in this group."""
    message, chat = update.effective_message, update.effective_chat
    if not message or not chat:
        return

    target_user = (
        message.reply_to_message.from_user
        if message.reply_to_message and message.reply_to_message.from_user
        else None
    )
    if target_user is None:
        if len(context.args) != 1 or not context.args[0].isdecimal():
            await _reply(update, "Usage: reply to a user's message or use /info <numeric_user_id>.")
            return
        try:
            member = await context.bot.get_chat_member(chat.id, int(context.args[0]))
            target_user = member.user
        except TelegramError as exc:
            await _reply(update, f"Telegram could not find that member: {exc.message}")
            return

    sessions = getattr(getattr(context, "application", None), "bot_data", {}).get("sessions")
    rows = []
    if sessions is not None:
        async with sessions() as session:
            rows = (
                await session.execute(
                    select(GroupMessage.display_name, GroupMessage.username)
                    .where(
                        GroupMessage.chat_id == chat.id,
                        GroupMessage.user_id == target_user.id,
                    )
                    .order_by(GroupMessage.sent_at.desc())
                    .limit(500)
                )
            ).all()

    current_name = target_user.full_name.strip() or "Unavailable"
    old_names: list[str] = []
    seen_names = {current_name.casefold()}

    for display_name, username in rows:
        name = (display_name or "").strip()
        if name and name.casefold() not in seen_names:
            seen_names.add(name.casefold())
            old_names.append(name)
        if len(old_names) >= 10 and len(old_usernames) >= 10:
            break

    def history_line(values: list[str]) -> str:
        return ", ".join(html.escape(value) for value in values[:10]) if values else "No history recorded"

    try:
        current_member = await context.bot.get_chat_member(chat.id, target_user.id)
        member_status = str(current_member.status).casefold()
    except TelegramError:
        member_status = "unknown"

    text = (
        f"<b>UID:</b> <code>{target_user.id}</code>\n"
        f"<b>Name:</b> {user_mention(target_user.id, current_name)}\n"
        f"Status: {html.escape(member_status)}\n"
        f"<b>Old names:</b> {history_line(old_names)}"
    )
    await message.reply_text(text, parse_mode="HTML")
