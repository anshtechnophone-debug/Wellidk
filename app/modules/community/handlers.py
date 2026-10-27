"""Explicit group rules, notes, saved responses, locks, and flood controls."""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict, deque
from html import escape

from telegram import Chat, ChatMember, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from app.config import Settings
from app.core.permissions import AuthorizationError, require_admin_access
from app.services.group_policy import GroupPolicyRepository
from app.services.reports import ReportRepository
from app.services.moderation import ModerationRepository

logger = logging.getLogger(__name__)
_LINK = re.compile(r"(?:https?://|www\.|t\.me/|telegram\.me/)", re.IGNORECASE)
_ALLOWED_LOCKS = {"links", "forwards", "stickers", "media", "bots", "commands"}
_flood_events: dict[tuple[int, int], deque[float]] = defaultdict(deque)
_repeat_events: dict[tuple[int, int], deque[tuple[float, str]]] = defaultdict(deque)


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


async def rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return
    rules = (await _repository(context).get(chat.id)).get("rules", "")
    await _respond(update, str(rules) if rules else "No group rules have been set.")


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
        text = " ".join(context.args).strip()
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
    approved = user.id in set(int(value) for value in policy.get("approved_users", []))
    text = message.text or message.caption or ""
    if not locks and not flood_enabled:
        if not approved:
            await _apply_saved_filter(message, text, policy.get("filters", {}))
        return
    if user.is_bot and "bots" not in locks:
        return
    if approved and not user.is_bot:
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
