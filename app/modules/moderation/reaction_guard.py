"""Automatic reaction-spam protection for group chats."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from telegram import Chat, ChatMember, ChatPermissions, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# Per-process counts keyed by (chat_id, message_id, user_id). Each changed,
# non-empty reaction set counts as one action; selecting multiple reactions at
# once also triggers the guard immediately.
WINDOW = timedelta(days=30)
MAX_TRACKED_KEYS = 20_000


def _permission_names() -> tuple[str, ...]:
    return tuple(
        name for name in ChatPermissions.__slots__ if name.startswith("can_")
    )


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

    old_reaction = reaction.old_reaction or []
    new_reaction = reaction.new_reaction or []
    if not new_reaction or old_reaction == new_reaction:
        # Ignore reaction removals; count additions and changes only.
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
        if member.status == ChatMember.RESTRICTED and not getattr(
            member, "can_react_to_messages", True
        ):
            return

        key = (chat.id, reaction.message_id, user.id)
        now = datetime.now(UTC)
        counts = context.application.bot_data.setdefault(
            "reaction_guard_counts", {}
        )

        # Prune stale entries occasionally and cap memory use.
        if len(counts) > MAX_TRACKED_KEYS:
            cutoff = now - WINDOW
            for tracked_key, value in list(counts.items()):
                if value["updated_at"] < cutoff:
                    counts.pop(tracked_key, None)
            if len(counts) > MAX_TRACKED_KEYS:
                oldest = sorted(
                    counts.items(), key=lambda item: item[1]["updated_at"]
                )[: len(counts) - MAX_TRACKED_KEYS]
                for tracked_key, _ in oldest:
                    counts.pop(tracked_key, None)

        previous = counts.get(key)
        if previous is None or now - previous["updated_at"] > WINDOW:
            action_count = 1
        else:
            action_count = previous["count"] + 1
        counts[key] = {"count": action_count, "updated_at": now}

        if len(new_reaction) < 2 and action_count < 2:
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
        counts.pop(key, None)

        display = user.full_name or (f"@{user.username}" if user.username else str(user.id))
        try:
            await context.bot.send_message(
                chat.id,
                f"🚫 {display} has lost reaction permission for reacting more than once "
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
