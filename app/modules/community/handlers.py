"""Explicit group rules, notes, saved responses, locks, and flood controls."""

from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime, timedelta
from collections import defaultdict, deque
from html import escape

from telegram import Chat, ChatMember, ChatPermissions, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from app.config import Settings
from app.database.models import KnownUser
from app.core.permissions import AuthorizationError, require_admin_access
from app.services.group_policy import GroupPolicyRepository
from app.services.reports import ReportRepository
from app.services.moderation import ModerationRepository

logger = logging.getLogger(__name__)
_LINK = re.compile(r"(?:https?://|www\.|t\.me/|telegram\.me/)", re.IGNORECASE)
_ALLOWED_LOCKS = {"links", "forwards", "stickers", "media", "bots", "commands"}
_flood_events: dict[tuple[int, int], deque[float]] = defaultdict(deque)
_repeat_events: dict[tuple[int, int], deque[tuple[float, str]]] = defaultdict(deque)
_spam_protector_events: dict[tuple[int, int], deque[float]] = defaultdict(deque)
_SPAM_PROTECTOR_THRESHOLD = 15
_SPAM_PROTECTOR_WINDOW_SECONDS = 30
_SPAM_PROTECTOR_MUTE_SECONDS = 600


def _repository(context: ContextTypes.DEFAULT_TYPE) -> GroupPolicyRepository:
    return GroupPolicyRepository(context.application.bot_data["sessions"])


async def _admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await require_admin_access(update, context, context.application.bot_data["settings"], None)


async def _respond(update: Update, text: str) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(text)


def _target_user_id(update: Update, args: list[str]) -> tuple[int, list[str]]:
    message = update.effective_message
    if message and message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id, args
    if args and args[0].isdecimal() and int(args[0]) > 0:
        return int(args[0]), args[1:]
    raise ValueError("Reply to a user's message or provide a numeric Telegram user ID.")


async def _apply_saved_filter(message, text: str, filters: object) -> None:
    if not isinstance(filters, dict) or not text:
        return
    folded = text.casefold()
    for phrase, response in filters.items():
        if str(phrase).casefold() in folded:
            await message.reply_text(str(response)[:1000])
            break


def _formatted_rules(rules: str) -> str:
    """Render readable Telegram HTML while preserving the admin's line breaks."""
    body = escape(rules.strip()) if rules.strip() else "No group rules have been set for this chat yet."
    return (
        "<b>📜 GROUP RULES</b>\n"
        "<i>Please read and respect the community rules.</i>\n\n"
        f"{body}\n\n"
        "<i>Thank you for keeping the community respectful. ❤️</i>"
    )


async def _send_rules_dm(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    group_id: int,
) -> bool:
    """Send the selected group's rules to a user's private chat."""
    rules = (await _repository(context).get(group_id)).get("rules", "")
    await context.bot.send_message(
        chat_id=user_id,
        text=_formatted_rules(str(rules or "")),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    return True


async def _rules_deep_link(context: ContextTypes.DEFAULT_TYPE, group_id: int) -> str | None:
    try:
        me = await context.bot.get_me()
    except TelegramError:
        logger.exception("Could not resolve XYRON username for rules deep link")
        return None
    if not me.username:
        return None
    return f"https://t.me/{me.username}?start=rules_{group_id}"


async def _deliver_or_link_rules(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    group_id: int | None = None,
) -> None:
    message, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if not message or not user:
        return
    target_group_id = group_id if group_id is not None else (chat.id if chat else None)
    if target_group_id is None:
        await message.reply_text("Please request the rules from the group where you need them.")
        return

    try:
        await _send_rules_dm(context, user.id, target_group_id)
    except TelegramError:
        logger.info("Could not DM rules to user %s; offering a start link", user.id)
        link = await _rules_deep_link(context, target_group_id)
        if link:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("📩 Click here to view the rules", url=link)]]
            )
            await message.reply_text(
                "📜 <b>Group Rules</b>\n\n"
                "I couldn't send you a private message yet. Tap below, then press "
                "<b>Start</b> and I'll send the rules to your DM.",
                parse_mode="HTML",
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
        else:
            await message.reply_text(
                "I couldn't send you the rules privately. Please open XYRON and press /start, "
                "then request the rules again."
            )
        return

    if chat and chat.type in {Chat.GROUP, Chat.SUPERGROUP}:
        await message.reply_text(
            f"📜 {escape(user.first_name or 'Member')}, I've sent the group rules to your DM. 💌",
            parse_mode="HTML",
        )


async def rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _deliver_or_link_rules(update, context)


async def plain_rules_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Respond only to exact rules keywords in a group, not ordinary conversation."""
    message, chat = update.effective_message, update.effective_chat
    if not message or not chat or chat.type not in {Chat.GROUP, Chat.SUPERGROUP}:
        return
    phrase = re.sub(r"\s+", " ", (message.text or "").strip().casefold())
    if phrase not in {"rules", "#rules", "hash rules"}:
        return
    await _deliver_or_link_rules(update, context)


async def reports_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await _admin(update, context)
        reports = await ReportRepository(context.application.bot_data["sessions"]).recent(
            update.effective_chat.id, limit=10
        )
        if not reports:
            await _respond(update, "No open reports in this group.")
            return
        lines = [
            f"#{report.id} message {report.message_id}, user {report.reported_user_id}; "
            f"reporter {report.reporter_id}; {report.reason or 'no reason'}"
            for report in reports
        ]
        await _respond(update, "Open reports:\n" + "\n".join(lines))
    except AuthorizationError as exc:
        await _respond(update, str(exc))


async def review_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await _admin(update, context)
        if (
            len(context.args) != 2
            or not context.args[0].isdecimal()
            or context.args[1].casefold() not in {"reviewed", "dismissed", "actioned"}
        ):
            raise ValueError("Usage: /reviewreport <report_id> <reviewed|dismissed|actioned>")
        changed = await ReportRepository(context.application.bot_data["sessions"]).set_status(
            update.effective_chat.id,
            int(context.args[0]),
            context.args[1].casefold(),
        )
        await _respond(
            update,
            "Report status updated." if changed else "No matching report exists in this group.",
        )
    except (AuthorizationError, ValueError) as exc:
        await _respond(update, str(exc))


async def setrules_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await _admin(update, context)
        raw = update.effective_message.text if update.effective_message else ""
        parts = re.split(r"\s+", raw.strip(), maxsplit=1)
        text = parts[1].strip() if len(parts) > 1 else ""
        if not text or len(text) > 4000:
            raise ValueError("Usage: /setrules <rules text, up to 4000 characters>")
        await _repository(context).set_value(update.effective_chat.id, "rules", text)
        await _respond(update, "Group rules saved.")
    except (AuthorizationError, ValueError) as exc:
        await _respond(update, str(exc))


async def save_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await _admin(update, context)
        if len(context.args) < 2:
            raise ValueError("Usage: /save <name> <response>")
        key = context.args[0].casefold()
        if not re.fullmatch(r"[a-z0-9_-]{1,32}", key):
            raise ValueError("Name must use 1-32 letters, digits, underscores, or hyphens.")
        value = " ".join(context.args[1:])
        policy = await _repository(context).get(update.effective_chat.id)
        notes = dict(policy.get("notes", {}))
        if len(notes) >= 50 and key not in notes:
            raise ValueError("This group already has 50 saved notes.")
        notes[key] = value[:1000]
        await _repository(context).set_value(update.effective_chat.id, "notes", notes)
        await _respond(update, f"Saved response '{key}'.")
    except (AuthorizationError, ValueError) as exc:
        await _respond(update, str(exc))


async def getnote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, args = update.effective_chat, context.args
    if not chat or len(args) != 1:
        await _respond(update, "Usage: /get <name>")
        return
    notes = (await _repository(context).get(chat.id)).get("notes", {})
    value = notes.get(args[0].casefold()) if isinstance(notes, dict) else None
    await _respond(update, str(value) if value else "No saved response with that name.")


async def notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return
    notes = (await _repository(context).get(chat.id)).get("notes", {})
    names = sorted(notes) if isinstance(notes, dict) else []
    await _respond(update, "Saved responses: " + (", ".join(names) if names else "none"))


async def filter_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await _admin(update, context)
        if len(context.args) < 2:
            raise ValueError("Usage: /filter <phrase> <response>")
        phrase = context.args[0].casefold()
        response = " ".join(context.args[1:])
        if not 2 <= len(phrase) <= 80 or len(response) > 1000:
            raise ValueError("Phrase must be 2-80 characters and response at most 1000.")
        policy = await _repository(context).get(update.effective_chat.id)
        filters = dict(policy.get("filters", {}))
        if len(filters) >= 50 and phrase not in filters:
            raise ValueError("This group already has 50 filters.")
        filters[phrase] = response
        await _repository(context).set_value(update.effective_chat.id, "filters", filters)
        await _respond(update, f"Saved response filter for '{phrase}'.")
    except (AuthorizationError, ValueError) as exc:
        await _respond(update, str(exc))


async def filters_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return
    filters = (await _repository(context).get(chat.id)).get("filters", {})
    names = sorted(filters) if isinstance(filters, dict) else []
    await _respond(update, "Saved filters: " + (", ".join(names) if names else "none"))


async def stopfilter_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await _admin(update, context)
        if len(context.args) != 1:
            raise ValueError("Usage: /stop <phrase>")
        policy = await _repository(context).get(update.effective_chat.id)
        filters = dict(policy.get("filters", {}))
        if filters.pop(context.args[0].casefold(), None) is None:
            raise ValueError("No matching saved filter.")
        await _repository(context).set_value(update.effective_chat.id, "filters", filters)
        await _respond(update, "Saved filter removed.")
    except (AuthorizationError, ValueError) as exc:
        await _respond(update, str(exc))


async def lock_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_lock(update, context, True)


async def unlock_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_lock(update, context, False)


async def _set_lock(update: Update, context: ContextTypes.DEFAULT_TYPE, enabled: bool) -> None:
    try:
        await _admin(update, context)
        if len(context.args) != 1 or context.args[0].casefold() not in _ALLOWED_LOCKS:
            raise ValueError("Choose a lock: links, forwards, stickers, media, bots, commands.")
        chat_id = update.effective_chat.id
        policy = await _repository(context).get(chat_id)
        locks = set(policy.get("locks", []))
        lock = context.args[0].casefold()
        if enabled:
            locks.add(lock)
        else:
            locks.discard(lock)
        await _repository(context).set_value(chat_id, "locks", sorted(locks))
        await _respond(update, f"{lock.title()} lock {'enabled' if enabled else 'disabled'}.")
    except (AuthorizationError, ValueError) as exc:
        await _respond(update, str(exc))


async def locks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return
    locks = (await _repository(context).get(chat.id)).get("locks", [])
    await _respond(update, "Enabled locks: " + (", ".join(locks) if locks else "none"))


async def antiflood_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await _admin(update, context)
        if not context.args or context.args[0].casefold() not in {"on", "off"}:
            raise ValueError(
                "Usage: /antiflood <on|off> [threshold 3-20] "
                "[window 2-60 seconds] [repeat threshold 2-10]"
            )
        policy = await _repository(context).get(update.effective_chat.id)
        old = policy.get("antiflood", {})
        antiflood = dict(old) if isinstance(old, dict) else {}
        antiflood["enabled"] = context.args[0].casefold() == "on"
        if len(context.args) > 1:
            threshold = int(context.args[1])
            if not 3 <= threshold <= 20:
                raise ValueError("Threshold must be between 3 and 20.")
            antiflood["threshold"] = threshold
        if len(context.args) > 2:
            window = int(context.args[2])
            if not 2 <= window <= 60:
                raise ValueError("Window must be between 2 and 60 seconds.")
            antiflood["window_seconds"] = window
        if len(context.args) > 3:
            repeat_threshold = int(context.args[3])
            if not 2 <= repeat_threshold <= 10:
                raise ValueError("Repeat threshold must be between 2 and 10.")
            antiflood["repeat_threshold"] = repeat_threshold
        if len(context.args) > 4:
            raise ValueError("Too many arguments.")
        await _repository(context).set_value(update.effective_chat.id, "antiflood", antiflood)
        state = "enabled" if antiflood["enabled"] else "disabled"
        await _respond(
            update,
            f"Flood protection {state}; threshold {antiflood.get('threshold', 5)} "
            f"messages per {antiflood.get('window_seconds', 10)} seconds.",
        )
    except (AuthorizationError, ValueError) as exc:
        await _respond(update, str(exc))


async def spamprotector_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only, persistent per-group spam protector switch."""
    user, chat = update.effective_user, update.effective_chat
    settings: Settings = context.application.bot_data["settings"]
    if not user or not settings.is_owner(user.id):
        await _respond(update, "Only the configured owner can control XYRON Spam Protector.")
        return
    if not chat or chat.type not in {Chat.GROUP, Chat.SUPERGROUP}:
        await _respond(update, "Use this command in the group you want to protect.")
        return
    if len(context.args) != 1 or context.args[0].casefold() not in {"on", "onn", "off"}:
        await _respond(update, "Usage: /spamprotector on|off")
        return
    enabled = context.args[0].casefold() in {"on", "onn"}
    await _repository(context).set_value(
        chat.id, "spam_protector",
        {"enabled": enabled, "threshold": 15, "window_seconds": 30, "mute_seconds": 600},
    )
    if enabled:
        await _respond(update, "XYRON Spam Protector: ON. 15 messages within 30 seconds triggers an automatic 10-minute mute.")
    else:
        await _respond(update, "XYRON Spam Protector: OFF. Automatic spam muting is disabled.")


async def plain_spamprotector_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Accept the owner's natural-language ON/OFF phrases as an alias."""
    message, user = update.effective_message, update.effective_user
    settings: Settings = context.application.bot_data["settings"]
    if not message or not user or not settings.is_owner(user.id):
        return
    phrase = re.sub(r"\s+", " ", (message.text or "").strip().casefold())
    if re.fullmatch(r"xyron\s+spam\s+protector\s+(?:on|onn)", phrase):
        context.args = ["on"]
    elif re.fullmatch(r"xyron\s+spam\s+protector\s+off", phrase):
        context.args = ["off"]
    else:
        return
    await spamprotector_command(update, context)


async def _auto_mute_spammer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user:
        return False
    from app.services.moderation import ModerationRepository
    from app.services.activity_log import ActivityLogRepository

    repository = ModerationRepository(context.application.bot_data["sessions"])
    actor_id = context.bot.id
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        if member.status != ChatMember.MEMBER:
            return False
        bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)
        if bot_member.status not in {ChatMember.ADMINISTRATOR, ChatMember.OWNER} or not getattr(bot_member, "can_restrict_members", False):
            logger.warning("Spam protector cannot mute in chat %s: missing restrict rights", chat.id)
            return False
    except TelegramError:
        return False

    permissions_obj = getattr(member, "permissions", None)
    previous = {
        name: bool(getattr(permissions_obj, name, True))
        for name in ChatPermissions.__slots__
        if name.startswith("can_")
    }
    created_snapshot = await repository.remember_permissions(chat.id, user.id, actor_id, previous)
    try:
        await context.bot.restrict_chat_member(
            chat.id, user.id,
            permissions=ChatPermissions(**{name: False for name in previous}),
            until_date=datetime.now(UTC) + timedelta(seconds=_SPAM_PROTECTOR_MUTE_SECONDS),
        )
    except TelegramError:
        if created_snapshot:
            await repository.forget_permissions(chat.id, user.id)
        logger.exception("Spam protector failed to mute user %s in chat %s", user.id, chat.id)
        return False
    await repository.schedule(
        chat.id, user.id, "unmute",
        datetime.now(UTC) + timedelta(seconds=_SPAM_PROTECTOR_MUTE_SECONDS), previous,
    )
    await repository.record(
        chat.id, actor_id, user.id, "spam_auto_mute",
        "XYRON Spam Protector: 15 messages within 30 seconds",
    )
    try:
        await ActivityLogRepository(context.application.bot_data["sessions"]).record(
            chat_id=chat.id, event_type="moderation", actor_id=actor_id,
            target_id=user.id, action="spam_auto_mute", status="success",
            details={"duration_seconds": 600, "threshold": 15, "window_seconds": 30},
        )
    except Exception:
        logger.exception("Could not log spam-protector mute")
    try:
        await message.reply_text(
            f"Spam detected. {user.mention_html()} has been automatically muted for 10 minutes.",
            parse_mode="HTML",
        )
    except TelegramError:
        pass
    return True


async def approve_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _approval(update, context, True)


async def unapprove_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _approval(update, context, False)


async def _approval(update: Update, context: ContextTypes.DEFAULT_TYPE, approved: bool) -> None:
    try:
        await _admin(update, context)
        target_id, rest = _target_user_id(update, context.args)
        if rest:
            raise ValueError("This command does not accept extra arguments.")
        chat_id = update.effective_chat.id
        policy = await _repository(context).get(chat_id)
        users = set(int(value) for value in policy.get("approved_users", []))
        if approved:
            users.add(target_id)
        else:
            users.discard(target_id)
        await _repository(context).set_value(chat_id, "approved_users", sorted(users))
        await _respond(update, f"User {target_id} {'approved' if approved else 'unapproved'}.")
    except (AuthorizationError, ValueError) as exc:
        await _respond(update, str(exc))


async def setwelcome_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_greeting(update, context, "welcome")


async def setgoodbye_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_greeting(update, context, "goodbye")


async def _set_greeting(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str) -> None:
    try:
        await _admin(update, context)
        value = " ".join(context.args).strip()
        if len(value) > 1000:
            raise ValueError("Greeting template cannot exceed 1000 characters.")
        await _repository(context).set_value(update.effective_chat.id, key, value)
        await _respond(update, f"{key.title()} message {'saved' if value else 'disabled'}.")
    except (AuthorizationError, ValueError) as exc:
        await _respond(update, str(exc))


async def group_member_event(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    member_update = update.chat_member
    if not member_update:
        return
    chat = member_update.chat
    old = member_update.old_chat_member.status
    new_member = member_update.new_chat_member
    new_status = new_member.status

    # Record moderation performed directly in Telegram by admins or other bots,
    # not only commands issued through XYRON. Ignore XYRON's own API updates
    # because its command handlers already write a richer audit event.
    actor = member_update.from_user
    target = new_member.user

    # Remember members from Telegram member updates, including people who have
    # not posted messages. Telegram does not provide a complete member-list scan.
    sessions = context.application.bot_data.get("sessions")
    if sessions is not None and not target.is_bot:
        try:
            async with sessions.begin() as session:
                known = await session.get(KnownUser, (chat.id, target.id))
                if known is None:
                    session.add(
                        KnownUser(
                            chat_id=chat.id,
                            user_id=target.id,
                            username=target.username.casefold() if target.username else None,
                            first_name=target.first_name or "",
                            last_seen=datetime.now(UTC),
                        )
                    )
                else:
                    known.username = target.username.casefold() if target.username else None
                    known.first_name = target.first_name or ""
                    known.last_seen = datetime.now(UTC)
        except Exception:
            logger.exception(
                "Could not update known-member directory chat_id=%s user_id=%s",
                chat.id, target.id,
            )

    action = None
    if old in {ChatMember.MEMBER, ChatMember.RESTRICTED} and new_status == ChatMember.BANNED:
        action = "ban"
    elif old == ChatMember.MEMBER and new_status == ChatMember.RESTRICTED:
        action = "mute"
    elif old == ChatMember.RESTRICTED and new_status == ChatMember.MEMBER:
        action = "unmute"
    elif old == ChatMember.BANNED and new_status in {
        ChatMember.LEFT, ChatMember.MEMBER, ChatMember.RESTRICTED,
    }:
        action = "unban"
    elif old in {ChatMember.MEMBER, ChatMember.RESTRICTED} and new_status == ChatMember.LEFT:
        action = "kick" if actor and actor.id != target.id else None

    if (
        action
        and actor
        and actor.id != context.bot.id
        and actor.id != target.id
        and context.application.bot_data.get("sessions") is not None
    ):
        try:
            await ModerationRepository(context.application.bot_data["sessions"]).record(
                chat.id, actor.id, target.id, action, None
            )
        except Exception:
            logger.exception(
                "Could not record external moderation event chat_id=%s target_id=%s action=%s",
                chat.id, target.id, action,
            )

    joined = old in {ChatMember.LEFT, ChatMember.BANNED} and new_status in {
        ChatMember.MEMBER,
        ChatMember.RESTRICTED,
    }
    left = old in {ChatMember.MEMBER, ChatMember.RESTRICTED} and new_status in {
        ChatMember.LEFT,
        ChatMember.BANNED,
    }
    if not (joined or left) or new_member.user.is_bot:
        return

    if joined:
        from app.modules.community.verification import gate_new_member

        await gate_new_member(chat, member_update, context)

    policy = await _repository(context).get(chat.id)
    template = str(policy.get("welcome" if joined else "goodbye", ""))
    if not template:
        return
    user = new_member.user
    values = {
        "first_name": escape(user.first_name or ""),
        "username": escape(f"@{user.username}" if user.username else ""),
        "id": str(user.id),
        "group": escape(chat.title or ""),
        "mention": f'<a href="tg://user?id={user.id}">{escape(user.first_name or "member")}</a>',
    }
    safe_template = escape(template)
    for key, value in values.items():
        safe_template = safe_template.replace("{" + key + "}", value)
    try:
        await context.bot.send_message(chat.id, safe_template, parse_mode="HTML")
    except TelegramError:
        logger.info("Could not send group greeting for chat %s", chat.id)

async def process_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat = update.effective_message, update.effective_chat
    user = update.effective_user
    if not message or not chat or chat.type not in {Chat.GROUP, Chat.SUPERGROUP} or not user:
        return
    repository = _repository(context)
    policy = await repository.get(chat.id)
    locks = set(policy.get("locks", []))
    antiflood = policy.get("antiflood", {})
    flood_enabled = isinstance(antiflood, dict) and bool(antiflood.get("enabled"))
    spam_config = policy.get("spam_protector", {})
    spam_enabled = isinstance(spam_config, dict) and bool(spam_config.get("enabled"))
    approved = user.id in set(int(value) for value in policy.get("approved_users", []))
    text = message.text or message.caption or ""
    if not locks and not flood_enabled and not spam_enabled:
        if not approved:
            await _apply_saved_filter(message, text, policy.get("filters", {}))
        return
    if user.is_bot and "bots" not in locks:
        return
    if approved and not user.is_bot and not spam_enabled:
        return
    try:
        actor = await context.bot.get_chat_member(chat.id, user.id)
    except TelegramError:
        return
    if actor.status in {ChatMember.ADMINISTRATOR, ChatMember.OWNER} and not user.is_bot:
        return
    settings: Settings = context.application.bot_data["settings"]
    try:
        owner = await context.bot.get_chat_member(chat.id, settings.owner_id)
        bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)
    except TelegramError:
        return
    if settings.owner_admin_required and owner.status not in {
        ChatMember.ADMINISTRATOR,
        ChatMember.OWNER,
    }:
        return
    if bot_member.status not in {ChatMember.ADMINISTRATOR, ChatMember.OWNER}:
        return

    # Dedicated spam protection is independent of the existing delete-only antiflood.
    # Group admins/owner are skipped above; approved users are still counted here.
    if spam_enabled and not user.is_bot:
        key = (chat.id, user.id)
        now = time.monotonic()
        events = _spam_protector_events[key]
        while events and events[0] <= now - _SPAM_PROTECTOR_WINDOW_SECONDS:
            events.popleft()
        events.append(now)
        if len(events) >= _SPAM_PROTECTOR_THRESHOLD:
            events.clear()
            await _auto_mute_spammer(update, context)
            return

    violation = None
    if "links" in locks and _LINK.search(text):
        violation = "links"
    elif "forwards" in locks and getattr(message, "forward_origin", None):
        violation = "forwards"
    elif "stickers" in locks and getattr(message, "sticker", None):
        violation = "stickers"
    elif "media" in locks and message.effective_attachment:
        violation = "media"
    elif "bots" in locks and user.is_bot:
        violation = "bots"
    elif "commands" in locks and text.startswith("/"):
        violation = "commands"
    if violation is None and flood_enabled:
        key = (chat.id, user.id)
        events = _flood_events[key]
        now = time.monotonic()
        window = int(antiflood.get("window_seconds", 10))
        while events and events[0] <= now - window:
            events.popleft()
        events.append(now)
        if len(events) >= int(antiflood.get("threshold", 5)):
            violation = "flood"
            events.clear()
        if violation is None and text.strip():
            repeated = _repeat_events[key]
            repeated.append((now, text.casefold()[:500]))
            while repeated and repeated[0][0] <= now - window:
                repeated.popleft()
            threshold = int(antiflood.get("repeat_threshold", 3))
            if len(repeated) >= threshold and len({item[1] for item in repeated}) == 1:
                violation = "repeated_message"
                repeated.clear()
    if violation:
        if not getattr(bot_member, "can_delete_messages", False):
            return
        try:
            await message.delete()
        except TelegramError:
            logger.info("Could not delete locked message in chat %s", chat.id)
        return

    if not approved:
        await _apply_saved_filter(message, text, policy.get("filters", {}))
