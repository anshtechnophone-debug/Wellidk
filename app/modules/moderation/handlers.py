"""Telegram moderation commands with execution-time authorization."""

from __future__ import annotations

import asyncio
import html
import logging
import re
import time
import unicodedata
from difflib import SequenceMatcher
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from telegram import Chat, ChatMember, ChatPermissions, Update
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError
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
from app.database.models import GlobalUser, Group, KnownUser, ModerationEvent
from app.services.group_history import GroupMessage
from app.services.activity_log import ActivityLogRepository
from app.services.moderation import ModerationRepository
from app.core.telegram_format import user_mention

logger = logging.getLogger(__name__)
PERMISSION_NAMES = tuple(name for name in ChatPermissions.__slots__ if name.startswith("can_"))


class TargetError(ValueError):
    """The command did not identify a known Telegram user."""



async def _resolve_username_via_userbot(
    context: ContextTypes.DEFAULT_TYPE, username: str
) -> int:
    """Resolve an unseen public Telegram username through a connected music account."""
    normalized = username.strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", normalized):
        raise TargetError("That does not look like a valid Telegram username.")

    plugin = context.application.bot_data.get("music_plugin")
    accounts = getattr(plugin, "accounts", None)
    clients = list(getattr(accounts, "clients", []) or [])
    for client in clients:
        try:
            if not client.is_connected():
                continue
            entity = await client.get_entity("@" + normalized)
            if not getattr(entity, "first_name", None):
                raise TargetError("That username belongs to a channel or group, not a user.")
            user_id = int(getattr(entity, "id", 0) or 0)
            if user_id > 0:
                return user_id
        except TargetError:
            raise
        except Exception:
            logger.debug("Music account could not resolve @%s", normalized, exc_info=True)

    raise TargetError(
        f"XYRON couldn't resolve @{normalized}. Connect a Telegram account with /addaccount "
        "in the bot's private chat, or use the person's numeric Telegram ID."
    )


def _repository(context: ContextTypes.DEFAULT_TYPE) -> ModerationRepository:
    return ModerationRepository(context.application.bot_data["sessions"])


async def _user_label(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> str:
    """Return the member's display name as a clickable Telegram mention."""
    display_name = None
    async with context.application.bot_data["sessions"]() as session:
        known = await session.scalar(
            select(KnownUser).where(
                KnownUser.chat_id == chat_id, KnownUser.user_id == user_id
            ).limit(1)
        )
        if known:
            display_name = known.first_name or (f"@{known.username}" if known.username else None)
        if not display_name:
            history = (await session.execute(
                select(GroupMessage.display_name)
                .where(GroupMessage.chat_id == chat_id, GroupMessage.user_id == user_id)
                .order_by(GroupMessage.sent_at.desc()).limit(1)
            )).first()
            if history:
                display_name = history[0]
    if not display_name:
        try:
            user = await context.bot.get_chat(user_id)
            display_name = getattr(user, "full_name", None)
        except Exception:
            pass
    return user_mention(user_id, display_name or f"User {user_id}")

async def _reply_html(update: Update, text: str) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(text, parse_mode="HTML")


def _normalize_name(value: str) -> str:
    """Normalize case and compatibility Unicode, including many styled fonts."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = "".join(
        char for char in normalized if unicodedata.category(char) != "Cf"
    )
    return " ".join(
        "".join(char if char.isalnum() else " " for char in normalized).split()
    )


async def _resolve_name_target(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, query: str
) -> int:
    """Resolve a name against users observed in this group's message history."""

    wanted = _normalize_name(query)
    if not wanted:
        raise TargetError("Give a username or display name to kick.")

    async with context.application.bot_data["sessions"]() as session:
        known_users = list(
            (
                await session.scalars(
                    select(KnownUser).where(KnownUser.chat_id == chat_id)
                )
            ).all()
        )
        history_rows = (
            await session.execute(
                select(GroupMessage.user_id, GroupMessage.display_name, GroupMessage.username)
                .where(GroupMessage.chat_id == chat_id)
                .distinct()
            )
        ).all()

    names_by_id: dict[int, set[str]] = {}
    for known in known_users:
        names_by_id.setdefault(known.user_id, set()).add(known.first_name or "")
        if known.username:
            names_by_id[known.user_id].add("@" + known.username)
    for user_id, display_name, username in history_rows:
        if display_name:
            names_by_id.setdefault(user_id, set()).add(display_name)
        if username:
            names_by_id.setdefault(user_id, set()).add("@" + username)

    exact = {
        user_id
        for user_id, names in names_by_id.items()
        if any(_normalize_name(name) == wanted for name in names if name)
    }
    if len(exact) == 1:
        return exact.pop()
    if len(exact) > 1:
        raise TargetError(
            "That name matches multiple users. Use @username, a numeric ID, "
            "or reply to the correct user's message."
        )

    best_by_user: dict[int, tuple[float, str]] = {}
    for user_id, names in names_by_id.items():
        for name in names:
            normalized = _normalize_name(name)
            if not normalized or name.startswith("@"):
                continue
            score = SequenceMatcher(None, wanted, normalized).ratio()
            if normalized.startswith(wanted + " "):
                score = max(score, 0.90)
            if score >= 0.86 and (
                user_id not in best_by_user or score > best_by_user[user_id][0]
            ):
                best_by_user[user_id] = (score, name)

    ranked = sorted(
        ((score, user_id, name) for user_id, (score, name) in best_by_user.items()),
        reverse=True,
    )
    if ranked and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= 0.08):
        return ranked[0][1]
    if ranked:
        options = []
        for _, user_id, _name in ranked[:5]:
            options.append("• " + await _user_label(context, chat_id, user_id))
        raise TargetError(
            "I found multiple possible matches. Use @username or reply to the right user:\n"
            + options
        )
    raise TargetError(
        "I couldn't match that name to anyone XYRON has seen in this group. "
        "Try @username or reply to their message."
    )


async def _resolve_target(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    args: list[str],
    *,
    allow_name: bool = False,
) -> tuple[int, list[str]]:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        raise TargetError("This command requires a group message.")
    if message.reply_to_message and message.reply_to_message.from_user:
        reply_user_id = message.reply_to_message.from_user.id
        if args and args[0].isdecimal() and int(args[0]) == reply_user_id:
            return reply_user_id, args[1:]
        return reply_user_id, args
    if not args:
        raise TargetError("Reply to the user's message or provide a username, name, or numeric ID.")
    value = args[0]
    if value.isdecimal() and int(value) > 0:
        return int(value), args[1:]
    username = value[1:] if value.startswith("@") else value
    # Admins commonly type either /ban @username or /ban username. For a
    # single username-shaped token, try the saved registry and connected
    # music MTProto accounts even when XYRON has never seen that user.
    username_target = bool(re.fullmatch(r"[A-Za-z0-9_]{5,32}", username)) and (
        value.startswith("@") or (allow_name and len(args) == 1)
    )
    if username_target:
        username_key = username.casefold()
        async with context.application.bot_data["sessions"]() as session:
            known = await session.scalar(
                select(KnownUser).where(
                    KnownUser.chat_id == chat.id,
                    KnownUser.username == username_key,
                )
            )
            history_ids = set((await session.scalars(
                select(GroupMessage.user_id).where(
                    GroupMessage.chat_id == chat.id,
                    func.lower(GroupMessage.username) == username_key,
                ).distinct()
            )).all())
        if known:
            return known.user_id, args[1:]
        if len(history_ids) == 1:
            return history_ids.pop(), args[1:]
        if len(history_ids) > 1:
            raise TargetError("That username matches multiple saved users. Reply to the correct member's message.")

        # The global registry is only an optimization; a connected music
        # account is the authoritative fallback for completely unseen users.
        async with context.application.bot_data["sessions"]() as session:
            global_matches = list(
                (
                    await session.scalars(
                        select(GlobalUser).where(
                            func.lower(GlobalUser.username) == username_key,
                            GlobalUser.seen_in_groups.is_(True),
                            GlobalUser.is_bot.is_(False),
                        )
                    )
                ).all()
            )
        if len(global_matches) == 1:
            return global_matches[0].user_id, args[1:]
        if len(global_matches) > 1:
            raise TargetError(
                "That username matches multiple users in XYRON's global registry. "
                "Use the user's numeric Telegram ID."
            )
        if allow_name:
            try:
                return await _resolve_username_via_userbot(context, username), args[1:]
            except TargetError as lookup_error:
                if value.startswith("@"):
                    try:
                        return await _resolve_name_target(context, chat.id, value), args[1:]
                    except TargetError:
                        raise lookup_error from None
        if value.startswith("@"):
            raise TargetError(
                "XYRON couldn't resolve this username. Make sure it is public, "
                "or use the person's numeric Telegram ID."
            )
    if allow_name:
        return await _resolve_name_target(context, chat.id, " ".join(args)), []
    raise TargetError("Use @username or reply to the user's message. Numeric IDs are also supported.")


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
        await context.bot.restrict_chat_member(
            chat_id,
            target_id,
            permissions=bounded_permissions(defaults, defaults),
        )
        return defaults, True
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
    # Also accept plain-text kick Name in group chats, without requiring a slash.
    if (
        not command
        and message.text
        and not message.text.lstrip().startswith("/")
        and message.text.split(maxsplit=1)[0].casefold() == "kick"
    ):
        command = message.text.split()[1:]
    silent = action.startswith("s")
    delete_target_message = action.startswith("d")
    normalized = action[1:] if action[0] in {"s", "d"} else action
    is_temporary = normalized in {"tban", "tmute"}
    if delete_target_message and message.reply_to_message is None:
        raise TargetError("Reply to the message to delete before using this command.")

    # Resolve Telegram and XYRON permissions first. Telegram group owners are
    # always full moderators; ordinary XYRON bot admins remain mute-only.
    settings: Settings = context.application.bot_data["settings"]
    access = await require_admin_access(
        update, context, settings, "can_restrict_members"
    )
    if silent or delete_target_message:
        await _check_additional_permission(access, "can_delete_messages")

    if access.is_bot_admin:
        if normalized in {"ban", "tban", "kick"}:
            raise AuthorizationError(
                "XYRON bot admins can mute members only. Ban and kick are owner-only."
            )
        if normalized in {"mute", "tmute"}:
            cutoff = datetime.now(UTC) - timedelta(minutes=10)
            async with context.application.bot_data["sessions"]() as session:
                recent_mute = await session.scalar(
                    select(ModerationEvent)
                    .where(
                        ModerationEvent.chat_id == chat.id,
                        ModerationEvent.actor_id == actor_user.id,
                        ModerationEvent.action.in_(("mute", "tmute")),
                        ModerationEvent.created_at >= cutoff,
                    )
                    .order_by(ModerationEvent.created_at.desc())
                    .limit(1)
                )
            if recent_mute is not None:
                retry_at = recent_mute.created_at + timedelta(minutes=10)
                seconds_left = max(
                    1, int((retry_at - datetime.now(UTC)).total_seconds())
                )
                minutes_left = max(1, (seconds_left + 59) // 60)
                raise AuthorizationError(
                    "Cooldown active: XYRON bot admins may mute only one member "
                    f"every 10 minutes. Try again in about {minutes_left} minute(s)."
                )
    target_id, remaining = await _resolve_target(
        update, context, command, allow_name=True
    )
    # Preserve the resolved username in this group's registry so later
    # /unban and /unmute commands can still identify the pending target.
    username_token = command[0].lstrip("@").casefold() if command else ""
    if re.fullmatch(r"[a-z0-9_]{5,32}", username_token):
        async with context.application.bot_data["sessions"].begin() as session:
            known = await session.get(KnownUser, (chat.id, target_id))
            if known is None:
                session.add(KnownUser(
                    chat_id=chat.id, user_id=target_id, username=username_token,
                    first_name="", last_seen=datetime.now(UTC),
                ))
            elif not known.username:
                known.username = username_token
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
    await ensure_target_is_moderatable(
        context, chat.id, target_id, access.actor, settings,
        allow_absent=normalized in {"ban", "tban", "mute", "tmute"},
    )

    repository = _repository(context)
    previous_permissions: dict[str, bool] | None = None
    created_snapshot = False
    target_absent = False
    try:
        await context.bot.get_chat_member(chat.id, target_id)
    except BadRequest as exc:
        detail = str(exc).casefold()
        target_absent = any(term in detail for term in (
            "user not participant", "user_not_participant", "chat member not found",
            "user not found", "participant_id_invalid", "participant id invalid",
        ))
        if not target_absent:
            raise
    if target_absent and (normalized == "tban" or temporary_mute):
        raise TargetError(
            "Temporary bans/mutes require the user to be in the group. Use /ban or /mute for a pending action."
        )
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
        if target_absent:
            previous_permissions, is_new_snapshot = defaults, True
        else:
            previous_permissions, is_new_snapshot = await _pre_restriction_permissions(
                context, chat.id, target_id, repository, defaults
            )
        if is_new_snapshot:
            created_snapshot = await repository.remember_permissions(
                chat.id, target_id, actor_user.id, previous_permissions
            )

    pending = False
    try:
        if normalized in {"ban", "tban", "kick"}:
            if target_absent and normalized in {"tban"}:
                raise TargetError("Temporary bans require the user to be in the group. Use /ban for a pending ban.")
            try:
                await context.bot.ban_chat_member(
                    chat.id,
                    target_id,
                    until_date=expiration_from_now(duration)
                    if normalized == "tban" and duration
                    else None,
                )
            except BadRequest as exc:
                detail = str(exc).casefold()
                absent = any(term in detail for term in (
                    "user not participant", "user_not_participant", "chat member not found",
                    "user not found", "participant_id_invalid", "participant id invalid",
                ))
                if normalized != "ban" or not absent:
                    raise
                await repository.record(chat.id, actor_user.id, target_id, "pending_ban", reason)
                pending = True
            if normalized == "kick" and not pending:
                await context.bot.unban_chat_member(chat.id, target_id, only_if_banned=True)
        else:
            if target_absent and normalized in {"tmute"}:
                raise TargetError("Temporary mutes require the user to be in the group. Use /mute for a pending mute.")
            if target_absent:
                await repository.record(chat.id, actor_user.id, target_id, "pending_mute", reason)
                pending = True
            else:
                await context.bot.restrict_chat_member(
                    chat.id,
                    target_id,
                    permissions=ChatPermissions(**{name: False for name in PERMISSION_NAMES}),
                    until_date=expiration_from_now(duration) if temporary_mute and duration else None,
                )
    except TelegramError:
        if created_snapshot and not pending:
            await repository.forget_permissions(chat.id, target_id)
        raise

    if normalized in {"ban", "kick"}:
        await repository.cancel_temporary(chat.id, target_id, "unban")
    elif normalized == "mute":
        await repository.cancel_temporary(chat.id, target_id, "unmute")

    if not pending:
        await repository.record(chat.id, actor_user.id, target_id, normalized, reason)
    try:
        await ActivityLogRepository(context.application.bot_data["sessions"]).record(
            chat_id=chat.id,
            event_type="moderation",
            actor_id=actor_user.id,
            target_id=target_id,
            action=normalized,
            status="success",
            details={"reason": reason, "duration": str(duration) if duration else None},
            created_at=getattr(message, "date", None) or datetime.now(UTC),
        )
    except Exception:
        logger.exception("Could not write temporary moderation audit event")
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
        if deleted and message.reply_to_message:
            try:
                await ActivityLogRepository(context.application.bot_data["sessions"]).record(
                    chat_id=chat.id,
                    event_type="message_deletion",
                    actor_id=actor_user.id,
                    target_id=(
                        message.reply_to_message.from_user.id
                        if message.reply_to_message.from_user else None
                    ),
                    message_id=message.reply_to_message.message_id,
                    action=normalized,
                    status="success",
                    details={"trigger": "moderation command"},
                )
            except Exception:
                logger.exception("Could not audit message deletion by moderation command")
        if not deleted:
            await _reply(
                update, "Action succeeded, but XYRON could not delete the replied-to message."
            )
    if silent:
        await _remove_command(update, context)
    else:
        label = (
            "Ban scheduled"
            if pending and normalized == "ban"
            else "Mute scheduled"
            if pending and normalized == "mute"
            else "Temporarily muted"
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
        await _reply_html(
            update, f"{label} {await _user_label(context, chat.id, target_id)}{suffix}." + (f" Reason: {html.escape(reason)}" if reason else "")
        )


async def pending_moderation_join_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Apply group-scoped pending bans/mutes as soon as a target joins."""
    change = update.chat_member
    chat = update.effective_chat
    if not change or not chat or not change.new_chat_member:
        return
    old_status = getattr(change.old_chat_member, "status", "")
    new_status = getattr(change.new_chat_member, "status", "")
    if new_status not in {ChatMember.MEMBER, ChatMember.RESTRICTED}:
        return
    if old_status not in {ChatMember.LEFT, ChatMember.BANNED, "left", "kicked"}:
        return
    target = getattr(change.new_chat_member, "user", None)
    if target is None or target.is_bot:
        return
    sessions = context.application.bot_data.get("sessions")
    if sessions is None:
        return
    try:
        async with sessions() as session:
            event = await session.scalar(
                select(ModerationEvent)
                .where(
                    ModerationEvent.chat_id == chat.id,
                    ModerationEvent.target_id == target.id,
                )
                .order_by(ModerationEvent.created_at.desc(), ModerationEvent.id.desc())
                .limit(1)
            )
        if event is None or event.action not in {"pending_ban", "pending_mute"}:
            return
        repository = _repository(context)
        if event.action == "pending_ban":
            await context.bot.ban_chat_member(chat.id, target.id)
            await repository.record(chat.id, event.actor_id, target.id, "ban", event.reason)
            logger.info("Applied pending ban in chat %s for user %s", chat.id, target.id)
            return
        defaults = await _current_default_permissions(context, chat.id)
        if await repository.restriction_permissions(chat.id, target.id) is None:
            await repository.remember_permissions(
                chat.id, target.id, event.actor_id, defaults
            )
        await context.bot.restrict_chat_member(
            chat.id,
            target.id,
            permissions=ChatPermissions(**{name: False for name in PERMISSION_NAMES}),
        )
        await repository.record(chat.id, event.actor_id, target.id, "mute", event.reason)
        logger.info("Applied pending mute in chat %s for user %s", chat.id, target.id)
    except TelegramError:
        logger.exception(
            "Could not apply pending moderation in chat %s for user %s",
            chat.id, getattr(getattr(change.new_chat_member, "user", None), "id", None),
        )


async def moderation_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    actor = update.effective_user
    name = message.text.split()[0].split("@")[0].lstrip("/").lower() if message and message.text else "moderation"
    async def log_failure(reason: str) -> None:
        if chat and chat.type in {Chat.GROUP, Chat.SUPERGROUP}:
            try:
                await ActivityLogRepository(context.application.bot_data["sessions"]).record(
                    chat_id=chat.id,
                    event_type="moderation",
                    actor_id=actor.id if actor else None,
                    target_id=int(context.args[0]) if context.args and context.args[0].isdecimal() else None,
                    action=name,
                    status="failed",
                    details={"reason": reason[:1000], "command_text": (message.text or "")[:1000] if message else ""},
                )
            except Exception:
                logger.exception("Could not write failed moderation audit event")
    try:
        await _moderate(update, context, name)
    except TargetError as exc:
        await log_failure(str(exc))
        await _reply_html(update, str(exc))
    except (AuthorizationError, DurationError) as exc:
        await log_failure(str(exc))
        await _reply(update, str(exc))
    except (BadRequest, Forbidden) as exc:
        await log_failure(exc.message)
        await _reply(update, f"Telegram rejected the action: {exc.message}")
    except TelegramError as exc:
        await log_failure(str(exc))
        logger.exception("Telegram moderation request failed")
        await _reply(
            update, "Telegram could not complete that moderation action. No success was recorded."
        )


async def _require_target_admin(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, allow_absent: bool = False
) -> tuple[int, AdminAccess]:
    access = await require_admin_access(
        update, context, context.application.bot_data["settings"], "can_restrict_members"
    )
    target_id, rest = await _resolve_target(update, context, context.args, allow_name=True)
    if rest:
        raise TargetError("This command does not accept extra arguments.")
    await ensure_target_is_moderatable(
        context,
        update.effective_chat.id,
        target_id,
        access.actor,
        context.application.bot_data["settings"],
        allow_absent=allow_absent,
    )
    return target_id, access


async def _resolve_historical_ban_username(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, username: str
) -> int:
    """Resolve an absent banned user from XYRON's saved ban and username history."""
    wanted = _normalize_name(username.lstrip("@"))
    async with context.application.bot_data["sessions"]() as session:
        banned_ids = set(
            (await session.scalars(
                select(ModerationEvent.target_id).where(
                    ModerationEvent.chat_id == chat_id,
                    ModerationEvent.action.in_(("ban", "tban", "pending_ban")),
                ).distinct()
            )).all()
        )
        if not banned_ids:
            raise TargetError(
                "I couldn't find a saved ban for that username. XYRON needs a saved username-to-user-ID record."
            )
        known_rows = (
            await session.execute(
                select(KnownUser.user_id, KnownUser.username).where(
                    KnownUser.chat_id == chat_id,                    KnownUser.user_id.in_(banned_ids),
                    KnownUser.username.is_not(None),
                )
            )
        ).all()
        history_rows = (
            await session.execute(
                select(GroupMessage.user_id, GroupMessage.username).where(
                    GroupMessage.chat_id == chat_id,
                    GroupMessage.user_id.in_(banned_ids),
                    GroupMessage.username.is_not(None),
                ).distinct()
            )
        ).all()

    matches = {
        user_id
        for user_id, saved_username in [*known_rows, *history_rows]
        if saved_username and _normalize_name(saved_username) == wanted
    }
    if len(matches) == 1:
        return matches.pop()
    if len(matches) > 1:
        raise TargetError(
            "That username matches multiple saved ban records. Please use the user's numeric Telegram ID."
        )
    raise TargetError(
        "I couldn't match that username to a saved banned user. If they changed their username or XYRON never recorded it, use their numeric Telegram ID."
    )


async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        message, chat, actor_user = (
            update.effective_message, update.effective_chat, update.effective_user
        )
        if not message or not chat or not actor_user:
            return
        access = await require_admin_access(
            update, context, context.application.bot_data["settings"], "can_restrict_members"
        )
        args = list(context.args)
        if message.reply_to_message and message.reply_to_message.from_user:
            target_id = message.reply_to_message.from_user.id
            if args and args[0].isdecimal() and int(args[0]) == target_id:
                args = args[1:]
        elif args and (
            args[0].startswith("@")
            or re.fullmatch(r"[A-Za-z0-9_]{5,32}", args[0])
        ):
            username = args[0].lstrip("@")
            args = args[1:]
            if args:
                raise TargetError("This command does not accept extra arguments.")
            try:
                target_id, rest = await _resolve_target(
                    update, context, ["@" + username], allow_name=True
                )
                if rest:
                    raise TargetError("This command does not accept extra arguments.")
            except TargetError:
                target_id = await _resolve_historical_ban_username(
                    context, chat.id, username
                )
        else:
            target_id, rest = await _resolve_target(
                update, context, args, allow_name=True
            )
            if rest:
                raise TargetError("This command does not accept extra arguments.")

        await ensure_target_is_moderatable(
            context, chat.id, target_id, access.actor,
            context.application.bot_data["settings"], allow_absent=True,
        )
        await context.bot.unban_chat_member(chat.id, target_id, only_if_banned=True)
        await _repository(context).cancel_temporary(chat.id, target_id, "unban")
        await _repository(context).record(chat.id, actor_user.id, target_id, "unban", None)
        await ActivityLogRepository(context.application.bot_data["sessions"]).record(
            chat_id=chat.id, event_type="moderation", actor_id=actor_user.id,
            target_id=target_id, action="unban", status="success",
        )
        await _reply_html(update, f"Unbanned {await _user_label(context, chat.id, target_id)}.")
    except (AuthorizationError, TargetError) as exc:
        await _reply(update, str(exc))
    except TelegramError as exc:
        await _reply(update, f"Telegram rejected the unban: {exc.message}")

async def unmute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        target_id, _ = await _require_target_admin(update, context, allow_absent=True)
        chat_id, actor_id = update.effective_chat.id, update.effective_user.id
        repository = _repository(context)
        defaults = await _current_default_permissions(context, chat_id)
        saved = await repository.restriction_permissions(chat_id, target_id)
        try:
            await context.bot.get_chat_member(chat_id, target_id)
            target_absent = False
        except BadRequest as exc:
            detail = str(exc).casefold()
            target_absent = any(term in detail for term in (
                "user not participant", "user_not_participant", "chat member not found",
                "user not found", "participant_id_invalid", "participant id invalid",
            ))
            if not target_absent:
                raise
        if not target_absent:
            await context.bot.restrict_chat_member(
                chat_id,
                target_id,
                permissions=bounded_permissions(saved if saved is not None else defaults, defaults),
            )
        await repository.cancel_temporary(chat_id, target_id, "unmute")
        await repository.forget_permissions(chat_id, target_id)
        await repository.record(chat_id, actor_id, target_id, "unmute", None)
        await ActivityLogRepository(context.application.bot_data["sessions"]).record(
            chat_id=chat_id, event_type="moderation", actor_id=actor_id,
            target_id=target_id, action="unmute", status="success",
        )
        await _reply_html(update, f"Restored group permissions for {await _user_label(context, chat_id, target_id)}.")
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
        rows = []
        for entry in entries[:50]:
            target = await _user_label(context, update.effective_chat.id, entry.target_id)
            actor = await _user_label(context, update.effective_chat.id, entry.actor_id)
            rows.append(f"{target}; set by {actor}; updated {entry.updated_at:%Y-%m-%d %H:%M} UTC")
        await _reply_html(update, "Saved XYRON mutes:\n" + "\n".join(rows))
    except AuthorizationError as exc:
        await _reply(update, str(exc))


async def banlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await require_admin_access(update, context, context.application.bot_data["settings"], None)
        entries = await _repository(context).active_bans(update.effective_chat.id)
        if not entries:
            await _reply(update, "XYRON has no currently tracked bans in this group.")
            return
        rows = []
        for event in entries[:50]:
            target = await _user_label(context, update.effective_chat.id, event.target_id)
            actor = await _user_label(context, update.effective_chat.id, event.actor_id)
            rows.append(f"{target}; {event.action}; set by {actor}")
        await _reply_html(update, "Tracked bans (up to 50):\n" + "\n".join(rows))
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
        # Never let the configured XYRON owner self-remove, even if they are
        # currently a regular member rather than a Telegram group administrator.
        if user.id in settings.owner_ids:
            raise AuthorizationError("The configured XYRON owner cannot be removed with this command.")
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
        await ActivityLogRepository(context.application.bot_data["sessions"]).record(
            chat_id=chat.id, event_type="moderation", actor_id=actor_user.id,
            target_id=target_id, action="warn", status="success",
            details={"reason": reason},
        )
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
            target_label = await _user_label(context, chat.id, target_id)
            await _reply_html(
                update, f"Warning limit reached ({count}/{limit}); {action} applied to {target_label}."
            )
        else:
            await _reply_html(
                update, f"Warned {await _user_label(context, chat.id, target_id)} ({count}/{limit})."
            )
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
            await _reply_html(update, f"Cleared warnings for {await _user_label(context, update.effective_chat.id, target_id)}.")
            return
        count = await repository.warning_count(update.effective_chat.id, target_id)
        await _reply_html(update, f"{await _user_label(context, update.effective_chat.id, target_id)} has {count} warning(s).")
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
        lines = []
        for item in events:
            target = await _user_label(context, update.effective_chat.id, item.target_id)
            actor = await _user_label(context, update.effective_chat.id, item.actor_id)
            lines.append(f"{item.action}: {target} by {actor} ({item.created_at:%Y-%m-%d %H:%M} UTC)")
        await _reply_html(update, "Recent moderation events:\n" + "\n".join(lines))
    except (AuthorizationError, TargetError, ValueError) as exc:
        await _reply(update, str(exc))


async def adminlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await require_admin_access(update, context, context.application.bot_data["settings"], None)
        admins = await context.bot.get_chat_administrators(update.effective_chat.id)
        names = []
        for member in admins:
            names.append(user_mention(member.user.id, member.user.full_name))
        await _reply_html(update, "Group administrators:\n" + "\n".join(names))
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
    now = time.monotonic()
    cache = context.application.bot_data.setdefault("_xyron_known_user_refresh", {})
    key = (chat.id, user.id)
    previous = cache.get(key, 0.0)
    if now - previous < 60:
        return

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
    cache[key] = now
    if len(cache) > 5000:
        cutoff = now - 300
        for cached_key, refreshed_at in list(cache.items()):
            if refreshed_at < cutoff:
                cache.pop(cached_key, None)


# Imported allowlist: 255 IDs with no Cyrillic, Han, Hiragana, Katakana, or Hangul characters in their exported display names.
# Script-based filtering only; this does not establish nationality. Restricted to the owner's specified supergroup.
UNBANALL_CHAT_ID = -1002587093927
UNBANALL_USER_IDS = (
    6553160554,
    7667546260,
    6572441833,
    8653202725,
    8853763418,
    8845063434,
    7674533340,
    1590084864,
    8803700099,
    8934333029,
    6867869959,
    9080266219,
    8372103735,
    8896868626,
    8973251920,
    5628638472,
    8889053676,
    8580492316,
    8610835026,
    6303361097,
    8814922858,
    8864040934,
    8829287567,
    8811971893,
    8851917803,
    8841503980,
    7390718781,
    8669582731,
    7954494241,
    8868347232,
    9175812141,
    8560741589,
    8849943398,
    7085170334,
    8069683750,    7720648367,
    8889349162,
    8705062020,
    8660512964,
    8693281592,
    8019302656,
    8524242431,
    8604036563,
    8347990315,
    8778115783,
    8808973690,
    5877727899,
    8290992686,
    8749583351,
    8463285728,
    8985909242,
    1686411160,
    8633813623,
    8484706019,
    8078543480,
    8698050182,
    8889340380,
    8373821044,
    8648785969,
    7942706170,
    8019157975,
    8306013177,
    692239090,
    8168338346,
    8865213399,
    8650865487,
    6009675121,
    8542441463,
    8636213510,
    8734234468,
    8630377825,
    8695798246,
    8872686957,
    8761296221,
    8264608438,
    8979549811,
    8679969947,
    7008957043,
    8312473496,
    8172176251,
    7620470916,
    903340691,
    5896462900,
    8948139592,
    8707714131,
    8934014710,
    6630093375,
    8391276136,
    7953700771,
    8340103728,
    8548858903,
    7984605339,
    8700186399,
    5472881108,
    7960480411,
    8862011121,
    8513924588,
    8625219003,
    8608219456,
    8704138969,
    8848159227,
    8658862097,
    6998615566,
    8417952236,
    8861208190,
    8378080124,
    8937230984,
    8980326573,
    8089632569,
    8620429134,
    8368790029,
    1239298838,
    8895634319,
    7132017632,
    8966462939,
    1845315134,
    8416915975,
    8909090774,
    8976018691,
    8703502200,
    8998988046,
    8997455088,
    8969351507,
    1689074470,
    8915847166,
    6580231961,
    8158598230,
    8480644842,
    5193689188,
    8890098275,
    8876216717,
    6663941438,
    7004260703,
    8976907279,
    5341891583,
    5742547365,
    7693591902,
    1402987430,
    8469024260,
    8668444476,
    8657070568,
    8376683396,
    7834286018,
    8327262684,
    8807168308,
    6084615674,
    8895879820,
    8959129146,
    8863028617,
    8748054896,
    8459423884,
    8981557124,
    8361878588,
    6153916610,
    7957849613,
    508007139,
    6368074418,
    8897692116,
    8858590004,
    1953106135,
    8761961363,
    8703521221,
    8879402380,
    5296050031,
    8938826048,
    7833072366,
    5401617102,
    8858085168,
    6774523267,
    8417987069,
    6440230978,
    8859532356,
    8815036255,
    8711447508,
    8809357684,
    7907122838,
    5684229654,
    5208212254,
    8971801882,
    7055917260,
    8729177185,
    6053394372,
    8083660515,
    7901055017,
    6430880509,
    5268437296,
    5969805105,
    8989018848,
    1594966362,
    7092647672,
    8975877704,
    8989515267,
    8749005412,
    7782262930,
    7903305458,
    2145710367,
    8498972665,
    2035607918,
    8739012475,
    7915690236,
    8968213421,
    8818348044,
    8853397125,
    8872863488,
    8617612053,
    8269355407,
    7601177717,
    7974841147,
    8827606759,
    7217157035,
    5793587706,
    8959895241,
    8502830754,
    8239131169,
    8795009226,
    8204488959,
    8756183738,
    8806102046,
    7965049286,
    8844492275,
    8009368450,
    7329945802,
    8160402632,
    7999362121,
    8750481091,
    1721621331,
    5648337382,
    7964991936,
    7810057542,
    5701090679,
    8998281617,
    8282689234,
    8604548205,
    8848328906,
    8840874987,    8668969423,
    8915238507,
    612900440,
    8821040340,
    8803865301,
    7591769821,
    6480484296,
    7245442728,
    7429313725,
    5060064564,
    1184457571,
    8169704219,
    8608700447,
    8419177475,
    8736633846,
    7673815765,
    8006200387,
    8399940307,
    6046861331,
    987781270,
)


async def unbanall_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat = update.effective_message, update.effective_chat
    actor_user = update.effective_user
    if not message or not chat or not actor_user:
        return
    if chat.id != UNBANALL_CHAT_ID:
        await _reply(update, "This imported unban list is configured only for its designated group.")
        return
    try:
        access = await require_admin_access(
            update, context, context.application.bot_data["settings"], "can_restrict_members"
        )
        if access.actor.status != ChatMember.OWNER:
            raise AuthorizationError("Only the Telegram group owner can run /unbanall.")
        if context.args:
            raise TargetError("Use /unbanall without arguments.")

        total = len(UNBANALL_USER_IDS)
        progress = await message.reply_text(
            f"🔄 Starting imported unban list: {total} IDs\n\n"
            f"Progress: 0/{total}\n✅ Unbanned: 0\n↪️ Already clear: 0\n❌ Failed: 0"
        )
        success = already_clear = failed = 0
        for index, target_id in enumerate(UNBANALL_USER_IDS, start=1):
            while True:
                try:
                    member = await context.bot.get_chat_member(chat.id, target_id)
                    if member.status != ChatMember.BANNED:
                        already_clear += 1
                    else:
                        await context.bot.unban_chat_member(chat.id, target_id, only_if_banned=True)
                        success += 1
                    break
                except RetryAfter as exc:
                    delay = exc.retry_after
                    if hasattr(delay, "total_seconds"):
                        delay = delay.total_seconds()
                    delay = max(1, float(delay))
                    try:
                        await progress.edit_text(
                            f"⏳ Rate limited; waiting {int(delay)}s\n"
                            f"Progress: {index - 1}/{total}\n"
                            f"✅ Unbanned: {success} | ↪️ Already clear: {already_clear} | ❌ Failed: {failed}"
                        )
                    except TelegramError:
                        pass
                    await asyncio.sleep(delay)
                except TelegramError as exc:
                    failed += 1
                    logger.warning("Unbanall failed for %s in %s: %s", target_id, chat.id, exc)
                    break
                except Exception:
                    failed += 1
                    logger.exception("Unexpected unbanall failure for %s in %s", target_id, chat.id)
                    break
            if index % 10 == 0 or index == total:
                try:
                    await progress.edit_text(
                        f"🔄 UNBANALL {index}/{total}\n"
                        f"✅ Unbanned: {success}\n↪️ Already clear: {already_clear}\n❌ Failed: {failed}"
                    )
                except TelegramError:
                    pass
        await progress.edit_text(
            f"✅ UNBANALL FINISHED\n\nIDs loaded: {total}\n"
            f"Successfully unbanned: {success}\nAlready not banned: {already_clear}\n"
            f"Failed: {failed}\nChecked: {success + already_clear + failed}/{total}"
        )
    except (AuthorizationError, TargetError) as exc:
        await _reply(update, str(exc))
    except TelegramError as exc:
        await _reply(update, f"Telegram rejected the operation: {exc}")
