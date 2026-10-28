"""Automatic reaction-spam protection for group chats."""

from __future__ import annotations

import logging

from telegram import Chat, ChatMember, ChatPermissions, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


def _permission_names() -> tuple[str, ...]:
    return tuple(
        name for name in ChatPermissions.__slots__ if name.startswith("can_")
    )


# A user is restricted only when their current reaction set contains multiple
# reactions on the same message. Changing a single reaction is not punished.


async def reaction_guard_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Remove reaction permission after a member reacts twice to one message."""
    reaction = update.message_reaction
    if reaction is None or reaction.user is None:
        # Anonymous reactions don't expose a user ID, so they cannot be
        # restricted individually.
        return

    chat = reaction.chat
    user = reaction.user
    if chat.type not in {Chat.GROUP, Chat.SUPERGROUP} or user.is_bot:
        return

    settings = context.application.bot_data.get("settings")
    if settings and settings.is_owner(user.id):
        return

    new_reaction = reaction.new_reaction or []
    if len(new_reaction) < 2:
        # A single reaction, removal, or change from one emoji to another is fine.
        return

    sessions = context.application.bot_data.get("sessions")
    if sessions is None:
        return

    # Leave group admins and the group owner alone.
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        if member.status in {ChatMember.ADMINISTRATOR, ChatMember.OWNER}:
            return
        if member.status not in {ChatMember.MEMBER, ChatMember.RESTRICTED}:
            return
        if member.status == ChatMember.RESTRICTED and getattr(
            member, "can_react_to_messages", None
        ) is False:
            return

        bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)
        if (
            bot_member.status not in {ChatMember.ADMINISTRATOR, ChatMember.OWNER}
            or not getattr(bot_member, "can_restrict_members", False)
        ):
            logger.warning(
                "Reaction guard cannot restrict members in chat %s: "
                "XYRON needs administrator permission to restrict members",
                chat.id,
            )
            return

        # Preserve every existing/default permission and disable only reactions.
        group = await context.bot.get_chat(chat.id)
        defaults = getattr(group, "permissions", None)
        source = member if member.status == ChatMember.RESTRICTED else defaults
        if source is None:
            logger.warning(
                "Reaction guard could not read permissions for chat %s", chat.id
            )
            return

        default_values = {
            name: getattr(defaults, name, None) if defaults is not None else None
            for name in _permission_names()
        }
        permissions = {}
        for name in _permission_names():
            value = getattr(source, name, None)
            if value is None:
                value = default_values.get(name)
            if value is not None:
                permissions[name] = bool(value)
        permissions["can_react_to_messages"] = False

        await context.bot.restrict_chat_member(
            chat.id,
            user.id,
            permissions=ChatPermissions(**permissions),
            use_independent_chat_permissions=True,
        )

        # Remove this user's reactions from the triggering message when XYRON
        # also has permission to delete reactions. Restriction still applies
        # even if this optional cleanup is not permitted.
        if getattr(bot_member, "can_delete_messages", False):
            try:
                await context.bot.delete_message_reaction(
                    chat.id, reaction.message_id, user_id=user.id
                )
            except TelegramError as exc:
                logger.info(
                    "Could not remove triggering reaction in chat %s for user %s: %s",
                    chat.id, user.id, exc,
                )

        display = user.full_name or (f"@{user.username}" if user.username else str(user.id))
        try:
            await context.bot.send_message(
                chat.id,
                f"🚫 {display} has lost reaction permission for adding multiple reactions "
                f"to the same message. Other chat permissions were preserved.",
            )
        except TelegramError:
            logger.info("Could not post reaction-guard notice in chat %s", chat.id)

    except TelegramError as exc:
        logger.warning(
            "Reaction guard failed in chat %s for user %s: %s",
            chat.id,
            user.id,
            exc,
        )
