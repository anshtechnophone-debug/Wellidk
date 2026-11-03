"""Fresh Telegram permission checks for administrative actions."""

from __future__ import annotations

from dataclasses import dataclass

from telegram import Chat, ChatMember, ChatPermissions, Update
from telegram.ext import ContextTypes

from sqlalchemy import select

from app.config import Settings
from app.database.models import BotAdmin


class AuthorizationError(RuntimeError):
    """An administrative operation is not authorized or cannot be performed."""


def bounded_permissions(
    saved: dict[str, bool] | None, defaults: dict[str, bool]
) -> ChatPermissions:
    """Restore saved rights only when current group defaults still permit them."""
    return ChatPermissions(
        **{
            field: bool(saved and saved.get(field, False) and defaults.get(field, False))
            for field in ChatPermissions.__slots__
        }
    )


@dataclass(frozen=True, slots=True)
class AdminAccess:
    actor: ChatMember
    bot: ChatMember
    is_bot_admin: bool = False


async def require_admin_access(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
    bot_permission: str | None,
) -> AdminAccess:
    message = update.effective_message
    chat = update.effective_chat
    actor_user = update.effective_user
    if (
        message is None
        or chat is None
        or actor_user is None
        or chat.type
        not in {
            Chat.GROUP,
            Chat.SUPERGROUP,
        }
    ):
        raise AuthorizationError("This command is available only in groups.")

    bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)
    owner_member = await context.bot.get_chat_member(chat.id, settings.owner_id)
    if settings.owner_admin_required and owner_member.status not in {
        ChatMember.ADMINISTRATOR,
        ChatMember.OWNER,
    }:
        try:
            await context.bot.send_message(
                settings.owner_id,
                f"Administrative operations are disabled in {chat.title or chat.id}: "
                "you are no longer a group administrator.",
            )
        except Exception:
            pass
        raise AuthorizationError("The configured owner must be a group administrator.")

    actor = await context.bot.get_chat_member(chat.id, actor_user.id)
    # Resolve the internal XYRON role when the database is available. Lightweight
    # contexts (and temporary DB outages) must not crash permission checks; in
    # that case only Telegram group administrators and the configured owner pass.
    is_bot_admin = settings.is_owner(actor_user.id)
    application = getattr(context, "application", None)
    bot_data = getattr(application, "bot_data", {}) if application else {}
    sessions = bot_data.get("sessions")
    if sessions is not None and not is_bot_admin:
        async with sessions() as session:
            is_bot_admin = bool(await session.get(BotAdmin, actor_user.id))
    if not is_bot_admin and actor.status not in {
        ChatMember.ADMINISTRATOR, ChatMember.OWNER
    }:
        raise AuthorizationError("You must be a group administrator or authorized XYRON bot admin.")

    if bot_member.status not in {ChatMember.ADMINISTRATOR, ChatMember.OWNER}:
        raise AuthorizationError("XYRON must be a group administrator to do that.")
    if bot_permission and not getattr(bot_member, bot_permission, False):
        human = {
            "can_restrict_members": "ban/restrict members",
            "can_delete_messages": "delete messages",
            "can_pin_messages": "pin messages",
        }.get(bot_permission, bot_permission.removeprefix("can_").replace("_", " "))
        raise AuthorizationError(f"XYRON is missing the permission to {human}.")

    # Telegram admins cannot exercise rights they do not personally hold.
    if (
        bot_permission
        and actor.status == ChatMember.ADMINISTRATOR
        and not getattr(actor, bot_permission, False)
    ):
        human = {
            "can_restrict_members": "restrict members",
            "can_delete_messages": "delete messages",
            "can_pin_messages": "pin messages",
        }.get(bot_permission, bot_permission.removeprefix("can_").replace("_", " "))
        raise AuthorizationError(
            f"Your Telegram administrator privileges do not allow {human}."
        )
    return AdminAccess(actor=actor, bot=bot_member, is_bot_admin=is_bot_admin)


async def ensure_target_is_moderatable(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    target_id: int,
    actor: ChatMember,
    settings: Settings,
) -> None:
    if settings.is_owner(target_id) or target_id in settings.protected_user_ids:
        raise AuthorizationError("That user is protected from moderation.")
    target = await context.bot.get_chat_member(chat_id, target_id)
    if target.status == ChatMember.OWNER:
        raise AuthorizationError("The group owner cannot be moderated by the bot.")
    if target.status == ChatMember.ADMINISTRATOR and actor.status != ChatMember.OWNER:
        raise AuthorizationError("Only the group owner can moderate another administrator.")
