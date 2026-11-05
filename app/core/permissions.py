"""Fresh Telegram permission checks with group-scoped XYRON trust roles."""
from __future__ import annotations

from dataclasses import dataclass

from telegram import Chat, ChatMember, ChatPermissions, Update
from telegram.ext import ContextTypes
from sqlalchemy import select

from app.config import Settings
from app.database.group_bot_admins import GroupBotAdmin


class AuthorizationError(RuntimeError):
    """An administrative operation is not authorized or cannot be performed."""


def bounded_permissions(saved: dict[str, bool] | None, defaults: dict[str, bool]) -> ChatPermissions:
    return ChatPermissions(**{
        field: bool(saved and saved.get(field, False) and defaults.get(field, False))
        for field in ChatPermissions.__slots__
    })


@dataclass(frozen=True, slots=True)
class AdminAccess:
    actor: ChatMember
    bot: ChatMember
    is_bot_admin: bool = False


async def require_admin_access(update: Update, context: ContextTypes.DEFAULT_TYPE,
                               settings: Settings, bot_permission: str | None) -> AdminAccess:
    message, chat, actor_user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not actor_user or chat.type not in {Chat.GROUP, Chat.SUPERGROUP}:
        raise AuthorizationError("This command is available only in groups.")

    bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)
    if bot_member.status not in {ChatMember.ADMINISTRATOR, ChatMember.OWNER}:
        raise AuthorizationError("XYRON must be a group administrator to do that.")

    actor = await context.bot.get_chat_member(chat.id, actor_user.id)
    is_real_admin = actor.status in {ChatMember.ADMINISTRATOR, ChatMember.OWNER}
    sessions = context.application.bot_data.get("sessions")
    is_bot_admin = False
    if sessions is not None:
        async with sessions() as session:
            is_bot_admin = await session.get(GroupBotAdmin, (chat.id, actor_user.id)) is not None

    # Real Telegram admins are always recognized as admins. A group-scoped
    # XYRON role can fill a missing action permission, but never crosses groups.
    if not is_real_admin and not is_bot_admin:
        raise AuthorizationError("You must be a group administrator or a XYRON bot admin in this group.")

    if bot_permission and not getattr(bot_member, bot_permission, False):
        human = {
            "can_restrict_members": "ban/restrict members",
            "can_delete_messages": "delete messages",
            "can_pin_messages": "pin messages",
        }.get(bot_permission, bot_permission.removeprefix("can_").replace("_", " "))
        raise AuthorizationError(f"XYRON is missing the permission to {human}.")

    if (bot_permission and actor.status == ChatMember.ADMINISTRATOR
            and not getattr(actor, bot_permission, False) and not is_bot_admin):
        human = {
            "can_restrict_members": "restrict members",
            "can_delete_messages": "delete messages",
            "can_pin_messages": "pin messages",
        }.get(bot_permission, bot_permission.removeprefix("can_").replace("_", " "))
        raise AuthorizationError(
            f"Your Telegram administrator privileges do not allow {human}, and you are not a XYRON bot admin in this group."
        )

    # When the member already has the Telegram right, treat them purely as a
    # Telegram admin so bot-admin-only limits do not override their real role.
    # Telegram group owners are the highest authority and must never be
    # downgraded to the limited XYRON bot-admin role.
    effective_bot_admin = is_bot_admin and actor.status != ChatMember.OWNER and not (
        is_real_admin and (bot_permission is None or getattr(actor, bot_permission, False))
    )
    return AdminAccess(actor=actor, bot=bot_member, is_bot_admin=effective_bot_admin)


async def ensure_target_is_moderatable(context: ContextTypes.DEFAULT_TYPE, chat_id: int,
                                       target_id: int, actor: ChatMember, settings: Settings) -> None:
    if settings.is_owner(target_id) or target_id in settings.protected_user_ids:
        raise AuthorizationError("That user is protected from moderation.")
    target = await context.bot.get_chat_member(chat_id, target_id)
    if target.status == ChatMember.OWNER:
        raise AuthorizationError("The group owner cannot be moderated by the bot.")
    if target.status == ChatMember.ADMINISTRATOR and actor.status != ChatMember.OWNER:
        raise AuthorizationError("Only the group owner can moderate another administrator.")
