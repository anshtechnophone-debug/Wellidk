"""Owner-only management of XYRON's independent bot-admin list."""
from __future__ import annotations
from datetime import UTC, datetime
from sqlalchemy import select
from telegram import Chat
from telegram.ext import ContextTypes
from app.config import Settings
from app.database.models import BotAdmin

async def _reply(update, text: str) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(text)

async def admin_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, chat = update.effective_user, update.effective_chat
    settings: Settings = context.application.bot_data["settings"]
    if not user or user.id != settings.owner_id or not chat or chat.type != Chat.PRIVATE:
        await _reply(update, "Only the owner can manage bot admins, in DM."); return
    if len(context.args) != 1 or not context.args[0].isdecimal() or int(context.args[0]) <= 0:
        await _reply(update, "Usage: /admin <numeric_user_id>"); return
    target_id = int(context.args[0])
    if target_id == settings.owner_id:
        await _reply(update, "You are already the owner."); return
    async with context.application.bot_data["sessions"].begin() as session:
        if await session.get(BotAdmin, target_id):
            await _reply(update, f"User {target_id} is already a XYRON bot admin."); return
        session.add(BotAdmin(user_id=target_id, added_by=user.id, created_at=datetime.now(UTC)))
    await _reply(update, f"Added {target_id} as a XYRON bot admin. They do not need group-admin status.")

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
    await _reply(update, f"Removed {target_id} from XYRON bot admins.")

async def botadminlist_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, chat = update.effective_user, update.effective_chat
    settings: Settings = context.application.bot_data["settings"]
    if not user or user.id != settings.owner_id or not chat or chat.type != Chat.PRIVATE:
        await _reply(update, "Only the owner can view the bot-admin list, in DM."); return
    async with context.application.bot_data["sessions"]() as session:
        rows = (await session.scalars(select(BotAdmin).order_by(BotAdmin.user_id))).all()
    await _reply(update, "XYRON bot admins:\n" + ("\n".join(str(row.user_id) for row in rows) if rows else "No additional admins."))

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
        await _reply(update, "Message pinned.")
    except AuthorizationError as exc:
        await _reply(update, str(exc))
    except TelegramError as exc:
        await _reply(update, f"Telegram could not pin that message: {exc.message}")
