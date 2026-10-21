"""Telegram moderation commands with execution-time authorization."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from telegram import Chat, ChatMember, ChatPermissions, Update
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import ContextTypes

from app.config import Settings
from app.core.durations import DurationError, expiration_from_now, parse_duration
from app.core.permissions import (
    AdminAccess,
    AuthorizationError,
    bounded_permissions,
    ensure_target_is_moderatable,
    require_admin_access,
)
from app.database.models import Group, KnownUser
from app.services.moderation import ModerationRepository

logger = logging.getLogger(__name__)
PERMISSION_NAMES = tuple(name for name in ChatPermissions.__slots__ if name.startswith("can_"))


class TargetError(ValueError):
    """The command did not identify a known Telegram user."""


def _repository(context: ContextTypes.DEFAULT_TYPE) -> ModerationRepository:
    return ModerationRepository(context.application.bot_data["sessions"])


async def _resolve_target(
    update: Update, context: ContextTypes.DEFAULT_TYPE, args: list[str]
) -> tuple[int, list[str]]:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        raise TargetError("This command requires a group message.")
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id, args
    if not args:
        raise TargetError("Reply to the user's message or provide a numeric user ID.")
    value = args[0]
    remaining = args[1:]
    if value.isdecimal() and int(value) > 0:
        return int(value), remaining
    if value.startswith("@") and len(value) > 1:
        async with context.application.bot_data["sessions"]() as session:
            known = await session.scalar(
                select(KnownUser).where(
                    KnownUser.chat_id == chat.id,
                    KnownUser.username == value[1:].casefold(),
                )
            )
        if known:
            return known.user_id, remaining
    raise TargetError(
        "That username is not known to XYRON. Ask the user to send a message, "
        "or use their numeric ID."
    )


def _permissions_dict(permissions: ChatPermissions | None) -> dict[str, bool] | None:
    if permissions is None:
        return None
    return {name: bool(getattr(permissions, name, False)) for name in PERMISSION_NAMES}


async def _current_default_permissions(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int
) -> dict[str, bool]:
    chat = await context.bot.get_chat(chat_id)
    permissions = _permissions_dict(getattr(chat, "permissions", None))
    if permissions is None:
        raise TargetError("Telegram did not provide this group's default member permissions.")
    return permissions


async def _pre_restriction_permissions(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    target_id: int,
    repository: ModerationRepository,
    defaults: dict[str, bool],
) -> tuple[dict[str, bool], bool]:
    saved = await repository.restriction_permissions(chat_id, target_id)
    if saved is not None:
        return saved, False
    target = await context.bot.get_chat_member(chat_id, target_id)
    if target.status == ChatMember.RESTRICTED:
        raise TargetError(
            "This member is already restricted without a saved XYRON baseline; "
            "refusing to overwrite an unknown restriction."
        )
    return defaults, True


async def _check_additional_permission(access: AdminAccess, permission: str) -> None:
    if not getattr(access.bot, permission, False):
        raise AuthorizationError("XYRON is missing the permission to delete messages.")
    if access.actor.status == ChatMember.ADMINISTRATOR and not getattr(
        access.actor, permission, False
    ):
        raise AuthorizationError(
            "Your Telegram administrator privileges do not allow deleting messages."
        )


async def _remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    try:
        await message.delete()
    except TelegramError:
        logger.info(
            "Could not delete moderation command in chat %s",
            update.effective_chat.id if update.effective_chat else "unknown",
        )


async def _delete_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    message = update.effective_message
    if message is None or message.reply_to_message is None:
        return True
    try:
        await message.reply_to_message.delete()
        return True
    except TelegramError:
        return False


async def _reply(update: Update, text: str) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(text)


async def _moderate(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str) -> None:
    message, chat, actor_user = (
        update.effective_message,
        update.effective_chat,
        update.effective_user,
    )
    if not message or not chat or not actor_user:
        return
    command = context.args
    silent = action.startswith("s")
    delete_target_message = action.startswith("d")
    normalized = action[1:] if action[0] in {"s", "d"} else action
    is_temporary = normalized in {"tban", "tmute"}
    if delete_target_message and message.reply_to_message is None:
        raise TargetError("Reply to the message to delete before using this command.")
    if silent or delete_target_message:
        access = await require_admin_access(
            update, context, context.application.bot_data["settings"], "can_restrict_members"
        )
        await _check_additional_permission(access, "can_delete_messages")
    else:
        access = await require_admin_access(
            update, context, context.application.bot_data["settings"], "can_restrict_members"
        )
    target_id, remaining = await _resolve_target(update, context, command)
    duration = None
    if (
        normalized == "mute"
        and remaining
        and re.fullmatch(r"[0-9]{1,6}[smhdw]", remaining[0], re.IGNORECASE)
    ):
        is_temporary = True
    if is_temporary:
        if not remaining:
            raise TargetError("A duration is required, for example 2h or 6d.")
        duration = parse_duration(remaining[0])
        remaining = remaining[1:]
    temporary_mute = normalized == "tmute" or (normalized == "mute" and duration is not None)
    reason = " ".join(remaining).strip() or None
    settings: Settings = context.application.bot_data["settings"]
    await ensure_target_is_moderatable(context, chat.id, target_id, access.actor, settings)

    repository = _repository(context)
    previous_permissions: dict[str, bool] | None = None
    created_snapshot = False
    if normalized in {"mute", "tmute"}:
        defaults = await _current_default_permissions(context, chat.id)
        existing_snapshot = await repository.restriction_permissions(chat.id, target_id)
        if (
            normalized == "tmute"
            and existing_snapshot is not None
            and not await repository.has_temporary(chat.id, target_id, "unmute")
        ):
            raise TargetError(
                "This member has a permanent XYRON mute; unmute them before a temporary mute."
            )
        previous_permissions, is_new_snapshot = await _pre_restriction_permissions(
            context, chat.id, target_id, repository, defaults
        )
        if is_new_snapshot:
            created_snapshot = await repository.remember_permissions(
                chat.id, target_id, actor_user.id, previous_permissions
            )

    try:
        if normalized in {"ban", "tban", "kick"}:
            await context.bot.ban_chat_member(
                chat.id,
                target_id,
                until_date=expiration_from_now(duration)
                if normalized == "tban" and duration
                else None,
            )
            if normalized == "kick":
                await context.bot.unban_chat_member(chat.id, target_id, only_if_banned=True)
        else:
            await context.bot.restrict_chat_member(
                chat.id,
                target_id,
                permissions=ChatPermissions(**{name: False for name in PERMISSION_NAMES}),
                until_date=expiration_from_now(duration) if temporary_mute and duration else None,
            )
    except TelegramError:
        if created_snapshot:
            await repository.forget_permissions(chat.id, target_id)
        raise

    if normalized in {"ban", "kick"}:
        await repository.cancel_temporary(chat.id, target_id, "unban")
    elif normalized == "mute":
        await repository.cancel_temporary(chat.id, target_id, "unmute")

    await repository.record(chat.id, actor_user.id, target_id, normalized, reason)
    if duration:
        await repository.schedule(
            chat.id,
            target_id,
            "unban" if normalized == "tban" else "unmute",
            expiration_from_now(duration),
            previous_permissions if temporary_mute else None,
        )
    if delete_target_message:
        deleted = await _delete_reply(update, context)
        if not deleted:
            await _reply(
                update, "Action succeeded, but XYRON could not delete the replied-to message."
            )
    if silent:
        await _remove_command(update, context)
    else:
        label = (
            "Temporarily muted"
            if temporary_mute
            else {
                "ban": "Banned",
                "tban": "Temporarily banned",
                "kick": "Removed",
                "mute": "Muted",
                "tmute": "Temporarily muted",
            }[normalized]
        )
        suffix = f" for {duration}" if duration else ""
        await _reply(
            update, f"{label} user {target_id}{suffix}." + (f" Reason: {reason}" if reason else "")
        )


async def moderation_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = update.effective_message.text.split()[0].split("@")[0].lstrip("/").lower()
    try:
        await _moderate(update, context, name)
    except (AuthorizationError, TargetError, DurationError) as exc:
        await _reply(update, str(exc))
    except (BadRequest, Forbidden) as exc:
        await _reply(update, f"Telegram rejected the action: {exc.message}")
    except TelegramError:
        logger.exception("Telegram moderation request failed")
        await _reply(
            update, "Telegram could not complete that moderation action. No success was recorded."
        )


async def _require_target_admin(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> tuple[int, AdminAccess]:
    access = await require_admin_access(
        update, context, context.application.bot_data["settings"], "can_restrict_members"
    )
    target_id, rest = await _resolve_target(update, context, context.args)
    if rest:
        raise TargetError("This command does not accept extra arguments.")
    await ensure_target_is_moderatable(
        context,
        update.effective_chat.id,
        target_id,
        access.actor,
        context.application.bot_data["settings"],
    )
    return target_id, access


async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        target_id, _ = await _require_target_admin(update, context)
        chat_id, actor_id = update.effective_chat.id, update.effective_user.id
        await context.bot.unban_chat_member(chat_id, target_id, only_if_banned=True)
        await _repository(context).cancel_temporary(chat_id, target_id, "unban")
        await _repository(context).record(chat_id, actor_id, target_id, "unban", None)
        await _reply(update, f"Unbanned user {target_id}.")
    except (AuthorizationError, TargetError) as exc:
        await _reply(update, str(exc))
    except TelegramError as exc:
        await _reply(update, f"Telegram rejected the unban: {exc.message}")


async def unmute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        target_id, _ = await _require_target_admin(update, context)
        chat_id, actor_id = update.effective_chat.id, update.effective_user.id
        repository = _repository(context)
        defaults = await _current_default_permissions(context, chat_id)
        saved = await repository.restriction_permissions(chat_id, target_id)
        await context.bot.restrict_chat_member(
            chat_id,
            target_id,
            permissions=bounded_permissions(saved if saved is not None else defaults, defaults),
        )
        await repository.cancel_temporary(chat_id, target_id, "unmute")
        await repository.forget_permissions(chat_id, target_id)
        await repository.record(chat_id, actor_id, target_id, "unmute", None)
        await _reply(update, f"Restored group permissions for user {target_id}.")
    except (AuthorizationError, TargetError) as exc:
        await _reply(update, str(exc))
    except TelegramError as exc:
        await _reply(update, f"Telegram rejected the restriction change: {exc.message}")


async def mutelist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await require_admin_access(update, context, context.application.bot_data["settings"], None)
        entries = await _repository(context).active_mutes(update.effective_chat.id)
        if not entries:
            await _reply(update, "XYRON has no saved mute restrictions in this group.")
            return
        rows = [
            f"User {entry.target_id}; set by {entry.actor_id}; "
            f"updated {entry.updated_at:%Y-%m-%d %H:%M} UTC"
            for entry in entries[:50]
        ]
        await _reply(update, "Saved XYRON mutes:\n" + "\n".join(rows))
    except AuthorizationError as exc:
        await _reply(update, str(exc))


async def banlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await require_admin_access(update, context, context.application.bot_data["settings"], None)
        entries = await _repository(context).active_bans(update.effective_chat.id)
        if not entries:
            await _reply(update, "XYRON has no currently tracked bans in this group.")
            return
        rows = [
            f"User {event.target_id}; {event.action}; set by {event.actor_id}"
            for event in entries[:50]
        ]
        await _reply(update, "Tracked bans (up to 50):\n" + "\n".join(rows))
    except AuthorizationError as exc:
        await _reply(update, str(exc))


async def modstats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await require_admin_access(update, context, context.application.bot_data["settings"], None)
        counts = await _repository(context).event_counts(
            update.effective_chat.id, datetime.now(UTC) - timedelta(days=30)
        )
        if not counts:
            await _reply(update, "No moderation events recorded in the last 30 days.")
            return
        lines = [f"{action}: {count}" for action, count in counts.items()]
        await _reply(update, "Moderation events over the last 30 days:\n" + "\n".join(lines))
    except AuthorizationError as exc:
        await _reply(update, str(exc))


async def kickme_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user = update.effective_chat, update.effective_user
    if not chat or not user or chat.type not in {Chat.GROUP, Chat.SUPERGROUP}:
        await _reply(update, "Use /kickme in a group.")
        return
    try:
        settings: Settings = context.application.bot_data["settings"]
        bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)
        owner_member = await context.bot.get_chat_member(chat.id, settings.owner_id)
        if settings.owner_admin_required and owner_member.status not in {
            ChatMember.ADMINISTRATOR,
            ChatMember.OWNER,
        }:
            raise AuthorizationError("The configured owner must be a group administrator.")
        if bot_member.status not in {ChatMember.ADMINISTRATOR, ChatMember.OWNER}:
            raise AuthorizationError("XYRON must be a group administrator to remove members.")
        if not getattr(bot_member, "can_restrict_members", False):
            raise AuthorizationError("XYRON is missing the permission to ban/restrict members.")
        actor = await context.bot.get_chat_member(chat.id, user.id)
        if actor.status == ChatMember.OWNER:
            raise AuthorizationError("The group owner cannot remove themselves with this command.")
        if actor.status == ChatMember.ADMINISTRATOR:
            raise AuthorizationError(
                "Group administrators cannot remove themselves with this command."
            )
        await context.bot.ban_chat_member(chat.id, user.id)
        await context.bot.unban_chat_member(chat.id, user.id, only_if_banned=True)
        await _repository(context).record(chat.id, user.id, user.id, "kickme", None)
        await _reply(update, "You have been removed from the group.")
    except AuthorizationError as exc:
        await _reply(update, str(exc))
    except TelegramError as exc:
        await _reply(update, f"Telegram could not remove you: {exc.message}")


async def warn_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, actor_user = update.effective_chat, update.effective_user
    try:
        access = await require_admin_access(
            update, context, context.application.bot_data["settings"], "can_restrict_members"
        )
        target_id, rest = await _resolve_target(update, context, context.args)
        await ensure_target_is_moderatable(
            context, chat.id, target_id, access.actor, context.application.bot_data["settings"]
        )
        reason = " ".join(rest).strip() or None
        repository = _repository(context)
        count = await repository.add_warning(chat.id, actor_user.id, target_id, reason)
        limit, action = await repository.warn_settings(chat.id)
        await repository.record(chat.id, actor_user.id, target_id, "warn", reason)
        if count >= limit:
            if action == "ban":
                await context.bot.ban_chat_member(chat.id, target_id)
            else:
                defaults = await _current_default_permissions(context, chat.id)
                saved_permissions, is_new_snapshot = await _pre_restriction_permissions(
                    context, chat.id, target_id, repository, defaults
                )
                if is_new_snapshot:
                    await repository.remember_permissions(
                        chat.id, target_id, actor_user.id, saved_permissions
                    )
                await context.bot.restrict_chat_member(
                    chat.id,
                    target_id,
                    permissions=ChatPermissions(**{name: False for name in PERMISSION_NAMES}),
                )
                await repository.schedule(
                    chat.id,
                    target_id,
                    "unmute",
                    expiration_from_now(parse_duration("1d")),
                    saved_permissions,
                )
            await repository.record(chat.id, actor_user.id, target_id, f"warn_limit_{action}", None)
            await _reply(
                update, f"Warning limit reached ({count}/{limit}); {action} applied to {target_id}."
            )
        else:
            await _reply(update, f"Warned user {target_id} ({count}/{limit}).")
    except (AuthorizationError, TargetError, DurationError) as exc:
        await _reply(update, str(exc))
    except TelegramError as exc:
        await _reply(update, f"Telegram rejected the warning action: {exc.message}")


async def warning_query_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await require_admin_access(update, context, context.application.bot_data["settings"], None)
        target_id, rest = await _resolve_target(update, context, context.args)
        repository = _repository(context)
        if update.effective_message.text.startswith("/resetwarn"):
            if rest:
                raise TargetError("This command does not accept extra arguments.")
            await repository.reset_warnings(update.effective_chat.id, target_id)
            await repository.record(
                update.effective_chat.id, update.effective_user.id, target_id, "resetwarn", None
            )
            await _reply(update, f"Cleared warnings for user {target_id}.")
            return
        count = await repository.warning_count(update.effective_chat.id, target_id)
        await _reply(update, f"User {target_id} has {count} warning(s).")
    except (AuthorizationError, TargetError) as exc:
        await _reply(update, str(exc))


async def set_warn_limit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await require_admin_access(update, context, context.application.bot_data["settings"], None)
        if (
            len(context.args) != 1
            or not context.args[0].isdecimal()
            or not 1 <= int(context.args[0]) <= 20
        ):
            raise TargetError("Usage: /setwarnlimit <1-20>")
        await _repository(context).set_warn_limit(update.effective_chat.id, int(context.args[0]))
        await _reply(update, f"Warning threshold set to {context.args[0]}.")
    except (AuthorizationError, TargetError) as exc:
        await _reply(update, str(exc))


async def modlog_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await require_admin_access(update, context, context.application.bot_data["settings"], None)
        count = int(context.args[0]) if context.args else 10
        if count < 1 or count > 25:
            raise TargetError("Count must be between 1 and 25.")
        events = await _repository(context).recent_events(update.effective_chat.id, count)
        if not events:
            await _reply(update, "No moderation events recorded yet.")
            return
        lines = [
            f"{item.action}: {item.target_id} by {item.actor_id} "
            f"({item.created_at:%Y-%m-%d %H:%M} UTC)"
            for item in events
        ]
        await _reply(update, "Recent moderation events:\n" + "\n".join(lines))
    except (AuthorizationError, TargetError, ValueError) as exc:
        await _reply(update, str(exc))


async def adminlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await require_admin_access(update, context, context.application.bot_data["settings"], None)
        admins = await context.bot.get_chat_administrators(update.effective_chat.id)
        names = [f"{member.user.full_name} ({member.user.id})" for member in admins]
        await _reply(update, "Group administrators:\n" + "\n".join(names))
    except (AuthorizationError, TelegramError) as exc:
        await _reply(update, str(exc))


async def track_known_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat = update.effective_message, update.effective_chat
    if (
        not message
        or not chat
        or chat.type not in {Chat.GROUP, Chat.SUPERGROUP}
        or not message.from_user
    ):
        return
    user = message.from_user
    async with context.application.bot_data["sessions"].begin() as session:
        group = await session.get(Group, chat.id)
        if group is None:
            session.add(
                Group(chat_id=chat.id, title=chat.title or "", updated_at=datetime.now(UTC))
            )
        else:
            group.title = chat.title or ""
            group.updated_at = datetime.now(UTC)
        known = await session.get(KnownUser, (chat.id, user.id))
        if known:
            known.username = user.username.casefold() if user.username else None
            known.first_name = user.first_name
            known.last_seen = datetime.now(UTC)
        else:
            session.add(
                KnownUser(
                    chat_id=chat.id,
                    user_id=user.id,
                    username=user.username.casefold() if user.username else None,
                    first_name=user.first_name,
                    last_seen=datetime.now(UTC),
                )
            )
