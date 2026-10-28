"""Owner-only management of XYRON's independent bot-admin list."""
from __future__ import annotations
import html
from datetime import UTC, datetime
from sqlalchemy import select
from telegram import Chat
from telegram.ext import ContextTypes
from app.config import Settings
from app.database.models import BotAdmin, KnownUser
from app.services.group_history import GroupMessage
from app.services.activity_log import ActivityLogRepository

async def _reply(update, text: str, *, html_mode: bool = False) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(
            text, parse_mode="HTML" if html_mode else None
        )


async def _user_label(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> str:
    """Return a username or clickable display name, never a raw numeric ID."""
    username = None
    display_name = None
    async with context.application.bot_data["sessions"]() as session:
        known = await session.scalar(
            select(KnownUser).where(KnownUser.user_id == user_id).limit(1)
        )
        if known:
            username, display_name = known.username, known.first_name
        history = (await session.execute(
            select(GroupMessage.username, GroupMessage.display_name)
            .where(GroupMessage.user_id == user_id)
            .order_by(GroupMessage.sent_at.desc()).limit(1)
        )).first()
        if history:
            username = username or history.username
            display_name = display_name or history.display_name
    if username:
        return "@" + html.escape(username.lstrip("@"))
    try:
        user = await context.bot.get_chat(user_id)
        display_name = display_name or getattr(user, "full_name", None)
        username = getattr(user, "username", None) or username
    except Exception:
        pass
    if username:
        return "@" + html.escape(username.lstrip("@"))
    name = html.escape(display_name or "Telegram user")
    return f'<a href="tg://user?id={user_id}">{name}</a>'

async def admin_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, chat = update.effective_user, update.effective_chat
    settings: Settings = context.application.bot_data["settings"]
    if not user or not settings.is_owner(user.id) or not chat or chat.type != Chat.PRIVATE:
        await _reply(update, "Only the owner can manage bot admins, in DM."); return
    if len(context.args) != 1 or not context.args[0].isdecimal() or int(context.args[0]) <= 0:
        await _reply(update, "Usage: /admin <numeric_user_id>"); return
    target_id = int(context.args[0])
    if settings.is_owner(target_id):
        await _reply(update, "You are already the owner."); return
    async with context.application.bot_data["sessions"].begin() as session:
        if await session.get(BotAdmin, target_id):
            await _reply(update, f"{await _user_label(context, target_id)} is already a XYRON bot admin.", html_mode=True); return
        session.add(BotAdmin(user_id=target_id, added_by=user.id, created_at=datetime.now(UTC)))
    await _reply(update, f"Added {await _user_label(context, target_id)} as a XYRON bot admin. They do not need group-admin status.", html_mode=True)

async def unadmin_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, chat = update.effective_user, update.effective_chat
    settings: Settings = context.application.bot_data["settings"]
    if not user or user.id != settings.owner_id or not chat or chat.type != Chat.PRIVATE:
        await _reply(update, "Only the owner can manage bot admins, in DM."); return
    if len(context.args) != 1 or not context.args[0].isdecimal():
        await _reply(update, "Usage: /unadmin <numeric_user_id>"); return
    target_id = int(context.args[0])
    async with context.application.bot_data["sessions"].begin() as session:
        existing = await session.get(BotAdmin, target_id)
        if not existing:
            await _reply(update, "That user is not a XYRON bot admin."); return
        await session.delete(existing)
    await _reply(update, f"Removed {await _user_label(context, target_id)} from XYRON bot admins.", html_mode=True)

async def botadminlist_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, chat = update.effective_user, update.effective_chat
    settings: Settings = context.application.bot_data["settings"]
    if not user or user.id != settings.owner_id or not chat or chat.type != Chat.PRIVATE:
        await _reply(update, "Only the owner can view the bot-admin list, in DM."); return
    async with context.application.bot_data["sessions"]() as session:
        rows = (await session.scalars(select(BotAdmin).order_by(BotAdmin.user_id))).all()
        labels = [await _user_label(context, row.user_id) for row in rows]
    await _reply(update, "XYRON bot admins:\n" + ("\n".join(labels) if labels else "No additional admins."), html_mode=True)

async def pin_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from telegram import Chat
    from telegram.error import TelegramError
    from app.core.permissions import AuthorizationError, require_admin_access
    message, chat = update.effective_message, update.effective_chat
    if not message or not chat or chat.type not in {Chat.GROUP, Chat.SUPERGROUP}:
        await _reply(update, "Use /pin by replying to a group message."); return
    if not message.reply_to_message:
        await _reply(update, "Reply to the message you want to pin, then use /pin."); return
    try:
        await require_admin_access(update, context, context.application.bot_data["settings"], "can_pin_messages")
        await context.bot.pin_chat_message(chat.id, message.reply_to_message.message_id)
        await ActivityLogRepository(context.application.bot_data["sessions"]).record(
            chat_id=chat.id,
            event_type="admin_action",
            actor_id=update.effective_user.id if update.effective_user else None,
            message_id=message.reply_to_message.message_id,
            action="pin",
            status="success",
            details={"command": "/pin"},
        )
        await _reply(update, "Message pinned.")
    except AuthorizationError as exc:
        await _reply(update, str(exc))
    except TelegramError as exc:
        await _reply(update, f"Telegram could not pin that message: {exc.message}")
