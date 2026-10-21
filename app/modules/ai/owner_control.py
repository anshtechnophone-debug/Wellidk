"""Owner-only natural-language moderation intent parser and executor.

XYRON is the professional slave of the configured owner (Ansh). Only the owner
may issue natural-language moderation orders. Ordinary members and other admins
cannot control the AI this way.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

from telegram import Chat, Update
from telegram.ext import ContextTypes

from app.core.permissions import AuthorizationError
from app.modules.moderation.handlers import TargetError, _moderate, unmute_command

_DURATION_WORDS = {
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "ten": "10",
    "fifteen": "15",
    "thirty": "30",
}
_DURATION_UNITS = {
    "second": "s",
    "seconds": "s",
    "minute": "m",
    "minutes": "m",
    "hour": "h",
    "hours": "h",
    "day": "d",
    "days": "d",
}


class OwnerIntentError(ValueError):
    """An owner instruction is ambiguous or outside the explicit command subset."""


def parse_owner_instruction(
    text: str, bot_username: str | None = None
) -> tuple[str, str | None] | None:
    value = text.strip()
    if bot_username:
        value = re.sub(r"^@" + re.escape(bot_username) + r"\b[\s,:.!-]*", "", value, flags=re.I)
    value = re.sub(r"^(?:xyron|zyron)\b[\s,:.!-]*", "", value, flags=re.I).strip()
    value = re.sub(r"^(?:please\s+)?", "", value, flags=re.I)
    value = re.sub(r"(?<=\s)[1-9][0-9]{4,19}(?=\s|$)", "", value)
    value = " ".join(value.split())
    value = value.casefold()

    # Unmute (English + Hindi)
    if re.fullmatch(
        r"(?:unmute|un-mute)(?:\s+(?:him|her|this person|this user|isko))?", value
    ) or re.fullmatch(r"(?:isko\s+)?(?:unmute|unmute\s+karo|unmute\s+kar\s*do)", value):
        return "unmute", None

    # Ban / kick
    match = re.fullmatch(r"(ban|kick)(?:\s+(?:him|her|this person|this user|isko))?", value)
    if match:
        return match.group(1), None
    match = re.fullmatch(r"isko\s+(ban|kick)\s+(?:karo|kar\s*do|do)", value)
    if match:
        return match.group(1), None
    if re.fullmatch(r"(?:ban|kick)\s+(?:karo|kar\s*do|do)(?:\s+isko)?", value):
        return ("ban" if "ban" in value else "kick"), None

    # Mute with optional duration
    mute_match = re.fullmatch(
        r"(?:mute(?:\s+(?:him|her|this person|this user|isko))?|"
        r"isko\s+mute\s+(?:karo|kar\s*do|do)|mute\s+(?:karo|kar\s*do))"
        r"(?:\s+for\s+(\d+|one|two|three|four|five|ten|fifteen|thirty)\s+"
        r"(seconds?|minutes?|hours?|days?))?",
        value,
    )
    if mute_match:
        amount, unit_name = mute_match.groups()
        if amount is None:
            return "mute", None
        unit = _DURATION_UNITS[unit_name]
        return "mute", f"{_DURATION_WORDS.get(amount, amount)}{unit}"

    short_mute = re.fullmatch(
        r"mute\s+(\d+|one|two|three|four|five|ten|fifteen|thirty)\s*"
        r"(s|m|h|d|seconds?|minutes?|hours?|days?)?",
        value,
    )
    if short_mute:
        amount, unit_raw = short_mute.groups()
        if unit_raw is None:
            return "mute", f"{_DURATION_WORDS.get(amount, amount)}m"
        unit = _DURATION_UNITS.get(unit_raw, unit_raw[0] if unit_raw else "m")
        return "mute", f"{_DURATION_WORDS.get(amount, amount)}{unit}"

    return None


def _target_argument(update: Update, args: list[str]) -> list[str]:
    message = update.effective_message
    if message and message.reply_to_message and message.reply_to_message.from_user:
        return args
    if args and args[0].isdecimal() and int(args[0]) > 0:
        return args
    raise OwnerIntentError("Reply to the target user or include their numeric Telegram user ID.")


async def owner_natural_control(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, actor = update.effective_message, update.effective_chat, update.effective_user
    settings = context.application.bot_data["settings"]
    if not message or not chat or not actor or actor.id != settings.owner_id:
        return
    if chat.type not in {Chat.GROUP, Chat.SUPERGROUP}:
        return
    if getattr(message, "forward_origin", None) or getattr(message, "quote", None):
        return
    username = getattr(context.bot, "username", None)
    text = message.text or ""
    at_name = re.match(r"^@(\w+)\b", text)
    if at_name and (not username or at_name.group(1).casefold() != username.casefold()):
        return
    if not re.match(r"^(?:@\w+|xyron|zyron)\b", text, re.IGNORECASE):
        return
    intent = parse_owner_instruction(text, username)
    if intent is None:
        if not re.search(
            r"\b(?:ban|mute|unmute|kick|delete|purge|remove)\b|"
            r"\b(?:isko\s+(?:ban|mute)|(?:ban|mute)\s+karo)",
            text,
            re.IGNORECASE,
        ):
            return
        await message.reply_text(
            "Master, I can execute replied or numeric-target ban, kick, mute "
            "(with optional duration), and unmute orders only. "
            "Example: 'Zyron isko ban karo' or 'mute him for 1 hour'."
        )
        context.chat_data["xyron_owner_control_message_id"] = message.message_id
        return
    action, duration = intent
    try:
        if action == "unmute":
            args = _target_argument(update, [])
            if not (message.reply_to_message and message.reply_to_message.from_user):
                target = re.search(r"\b[1-9][0-9]{4,19}\b", text)
                if not target:
                    raise OwnerIntentError(
                        "Reply to the target or include their numeric Telegram user ID."
                    )
                args = [target.group()]
            proxy = SimpleNamespace(application=context.application, bot=context.bot, args=args)
            await unmute_command(update, proxy)
        else:
            args: list[str] = []
            if not (message.reply_to_message and message.reply_to_message.from_user):
                target = re.search(r"\b[1-9][0-9]{4,19}\b", text)
                if not target:
                    raise OwnerIntentError(
                        "Reply to the target or include their numeric Telegram user ID."
                    )
                args.append(target.group())
            if duration:
                args.append(duration)
            args = _target_argument(update, args)
            proxy = SimpleNamespace(application=context.application, bot=context.bot, args=args)
            await _moderate(update, proxy, action)
    except (AuthorizationError, TargetError, OwnerIntentError) as exc:
        await message.reply_text(str(exc))
    finally:
        context.chat_data["xyron_owner_control_message_id"] = message.message_id
