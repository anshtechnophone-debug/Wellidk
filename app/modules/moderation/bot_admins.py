"""Group-owner management of group-scoped XYRON bot admins."""
from __future__ import annotations

import html
from datetime import UTC, datetime

from sqlalchemy import func, select
from telegram import Chat, ChatMember
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from app.config import Settings
from app.core.permissions import AuthorizationError
from app.database.group_bot_admins import GroupBotAdmin
from app.database.models import KnownUser
from app.services.group_history import GroupMessage
from app.services.activity_log import ActivityLogRepository
from app.core.telegram_format import user_mention


async def _reply(update, text: str, *, html_mode: bool = False) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(text, parse_mode="HTML" if html_mode else None)


async def _user_label(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> str:
    """Return the person's display name as a clickable Telegram mention."""
    display_name = None
    async with context.application.bot_data["sessions"]() as session:
        known = await session.scalar(select(KnownUser).where(KnownUser.user_id == user_id).limit(1))
        if known:
            display_name = known.first_name
        history = (await session.execute(
            select(GroupMessage.display_name)
            .where(GroupMessage.user_id == user_id)
            .order_by(GroupMessage.sent_at.desc()).limit(1)
        )).first()
        if history and not display_name:
            display_name = history[0]
    if not display_name:
        try:
            user = await context.bot.get_chat(user_id)
            display_name = getattr(user, "full_name", None)
        except Exception:
            pass
    return user_mention(user_id, display_name or f"User {user_id}")

async def _resolve_target(context, chat_id: int, value: str) -> int:
    value = value.strip()
    if value.isdecimal():
        target_id = int(value)
        if target_id > 0:
            return target_id
        raise ValueError("Provide a valid positive Telegram user ID.")
    username = value.lstrip("@").casefold()
    if not username:
        raise ValueError("Usage: /admin @username or reply to a user's message.")
    async with context.application.bot_data["sessions"]() as session:
        known = set((await session.scalars(
            select(KnownUser.user_id).where(
                KnownUser.chat_id == chat_id, func.lower(KnownUser.username) == username
            ).distinct()
        )).all())
        history = set((await session.scalars(
            select(GroupMessage.user_id).where(
                GroupMessage.chat_id == chat_id, func.lower(GroupMessage.username) == username
            ).distinct()
        )).all())
    matches = known | history
    if len(matches) == 1:
        return matches.pop()
    if len(matches) > 1:
        raise ValueError("That username matches multiple users. Use their numeric ID.")
    raise ValueError("I haven't seen that username in this group. Use a numeric ID or reply to their message.")


async def _require_group_owner(update, context) -> tuple[int, int]:
    user, chat = update.effective_user, update.effective_chat
    if not user or not chat or chat.type not in {Chat.GROUP, Chat.SUPERGROUP}:
        raise ValueError("Use this command in the group where you want to manage bot admins.")
    settings = context.application.bot_data["settings"]
    # The configured XYRON bot owner is a global override; otherwise only this
    # Telegram group's owner may manage this group's bot-admin appointments.
    if not settings.is_owner(user.id):
        member = await context.bot.get_chat_member(chat.id, user.id)
        if member.status != ChatMember.OWNER:
            raise PermissionError(
                "Only this group's Telegram owner or the XYRON bot owner can manage its bot admins."
            )
    return chat.id, user.id


async def notify_bot_admin(context: ContextTypes.DEFAULT_TYPE, target_id: int, chat_id: int) -> None:
    try:
        chat = await context.bot.get_chat(chat_id)
        await context.bot.send_message(
            target_id,
            f"✅ You are now a XYRON bot admin in {html.escape(chat.title or 'a Telegram group')}.\n"
            "Your bot-admin permission works only in that group. Real Telegram admin permissions always take priority.",
        )
    except TelegramError:
        # The appointment is still valid; Telegram may require the user to start the bot first.
        return


async def admin_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        chat_id, actor_id = await _require_group_owner(update, context)
        message = update.effective_message
        target_user = message.reply_to_message.from_user if message and message.reply_to_message else None
        if target_user:
            target_id = target_user.id
        elif len(context.args) == 1:
            target_id = await _resolve_target(context, chat_id, context.args[0])
        else:
            raise ValueError("Usage: reply to a user's message with /admin, or use /admin @username /admin <numeric_id>.")
        if target_id == actor_id:
            raise ValueError("You are already the group owner.")
        target = await context.bot.get_chat_member(chat_id, target_id)
        if target.status in {ChatMember.LEFT, ChatMember.BANNED}:
            raise ValueError("That user must be a member of this group before becoming a bot admin.")
        async with context.application.bot_data["sessions"].begin() as session:
            existing = await session.get(GroupBotAdmin, (chat_id, target_id))
            if existing:
                raise ValueError("That user is already a XYRON bot admin in this group.")
            session.add(GroupBotAdmin(chat_id=chat_id, user_id=target_id, added_by=actor_id, created_at=datetime.now(UTC)))
        await notify_bot_admin(context, target_id, chat_id)
        await _reply(update, f"✅ Added {await _user_label(context, target_id)} as a XYRON bot admin in this group.", html_mode=True)
    except (ValueError, PermissionError) as exc:
        await _reply(update, str(exc))
    except TelegramError as exc:
        await _reply(update, f"Telegram could not verify that member: {exc}")


async def unadmin_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        chat_id, _actor_id = await _require_group_owner(update, context)
        message = update.effective_message
        target_user = message.reply_to_message.from_user if message and message.reply_to_message else None
        if target_user:
            target_id = target_user.id
        elif len(context.args) == 1:
            target_id = await _resolve_target(context, chat_id, context.args[0])
        else:
            raise ValueError("Usage: reply to a bot admin with /unadmin, or use /unadmin <numeric_id>.")
        async with context.application.bot_data["sessions"].begin() as session:
            existing = await session.get(GroupBotAdmin, (chat_id, target_id))
            if not existing:
                raise ValueError("That user is not a XYRON bot admin in this group.")
            await session.delete(existing)
        await _reply(update, f"Removed {await _user_label(context, target_id)} from this group's XYRON bot admins.", html_mode=True)
    except (ValueError, PermissionError) as exc:
        await _reply(update, str(exc))
    except TelegramError as exc:
        await _reply(update, f"Telegram could not complete the request: {exc}")


async def botadminlist_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        chat_id, _actor_id = await _require_group_owner(update, context)
        async with context.application.bot_data["sessions"]() as session:
            rows = (await session.scalars(
                select(GroupBotAdmin).where(GroupBotAdmin.chat_id == chat_id).order_by(GroupBotAdmin.user_id)
            )).all()
        labels = [await _user_label(context, row.user_id) for row in rows]
        body = "\n".join(labels) if labels else "No bot admins appointed in this group."
        await _reply(update, f"XYRON bot admins in this group ({len(labels)}):\n{body}", html_mode=True)
    except (ValueError, PermissionError) as exc:
        await _reply(update, str(exc))


async def demote_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await unadmin_command(update, context)


async def pin_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from telegram import Chat
    from telegram.error import TelegramError
    from app.core.permissions import AuthorizationError, require_admin_access

    message, chat = update.effective_message, update.effective_chat
    if not message or not chat or chat.type not in {Chat.GROUP, Chat.SUPERGROUP}:
        await _reply(update, "Use /pin by replying to a group message.")
        return
    if not message.reply_to_message:
        await _reply(update, "Reply to the message you want to pin, then use /pin.")
        return
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
