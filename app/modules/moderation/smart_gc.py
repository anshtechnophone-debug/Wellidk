"""Smart GC abusive-language warning guard.

The guard is enabled per group with /smartgc on and uses the existing durable
warning table. Three warnings result in a ban.
"""
from __future__ import annotations

import logging
import re
import unicodedata

from telegram import Chat, ChatMember, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from app.services.group_policy import GroupPolicyRepository
from app.services.moderation import ModerationRepository

logger = logging.getLogger(__name__)

# Match whole uncensored words only. Censored spellings such as ch#t are
# intentionally not matched.
_ABUSIVE_WORDS = (
    "chut", "chutiya", "chutia", "lund", "randi", "randii",
    "bhosda", "bhosdi", "bhosdike", "madarchod", "madar chod",
    "behenchod", "bhenchod", "gaand", "gand", "gandu", "gaandu",
    "harami", "kamina", "kameena", "fuck", "fucking", "bitch",
    "asshole", "motherfucker",
)
_ABUSE_RE = re.compile(
    r"(?<![a-z0-9])(?:" + "|".join(re.escape(word) for word in _ABUSIVE_WORDS)
    + r")(?![a-z0-9])",
    re.IGNORECASE,
)
_WARNING_LIMIT = 3


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


async def smartgc_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only persistent Smart GC switch for this group."""
    user, chat = update.effective_user, update.effective_chat
    settings = context.application.bot_data["settings"]
    message = update.effective_message
    if not user or not settings.is_owner(user.id):
        if message:
            await message.reply_text("Only the configured owner can control Smart GC.")
        return
    if not chat or chat.type not in {Chat.GROUP, Chat.SUPERGROUP}:
        if message:
            await message.reply_text("Use this command inside the group you want to protect.")
        return
    if len(context.args) != 1 or context.args[0].casefold() not in {"on", "off"}:
        if message:
            await message.reply_text("Usage: /smartgc on or /smartgc off")
        return

    enabled = context.args[0].casefold() == "on"
    policies = GroupPolicyRepository(context.application.bot_data["sessions"])
    await policies.set_value(chat.id, "smart_gc", {"enabled": enabled})
    if message:
        await message.reply_text(
            "Smart GC is ON. Uncensored abusive language is monitored; "
            "3 warnings result in a ban."
            if enabled else "Smart GC is OFF. Automatic abuse warnings are disabled."
        )


async def smartgc_abuse_guard(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message, chat, user = (
        update.effective_message, update.effective_chat, update.effective_user
    )
    if (
        not message or not chat or not user or user.is_bot
        or chat.type not in {Chat.GROUP, Chat.SUPERGROUP}
    ):
        return

    text = message.text or message.caption or ""
    if not text or not _ABUSE_RE.search(_normalize(text)):
        return

    sessions = context.application.bot_data.get("sessions")
    if sessions is None:
        return
    policy = await GroupPolicyRepository(sessions).get(chat.id)
    smart_gc = policy.get("smart_gc", {})
    if not isinstance(smart_gc, dict) or not smart_gc.get("enabled"):
        return

    # Do not police the owner or existing group administrators.
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
    except TelegramError:
        logger.debug("Could not inspect member %s in %s for Smart GC", user.id, chat.id)
        return
    if member.status in {ChatMember.ADMINISTRATOR, ChatMember.OWNER}:
        return

    # Delete the uncensored message when the bot has the required permission.
    try:
        bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)
        if getattr(bot_member, "can_delete_messages", False):
            await message.delete()
    except TelegramError:
        logger.info("Smart GC could not delete abusive message in %s", chat.id)

    repository = ModerationRepository(sessions)
    bot_id = context.bot.id
    count = await repository.add_warning(
        chat.id, bot_id, user.id, "Smart GC: uncensored abusive language"
    )
    await repository.record(
        chat.id, bot_id, user.id, "smartgc_abuse_warn",
        "Uncensored abusive language",
    )

    mention = user.mention_html()
    if count < _WARNING_LIMIT:
        await context.bot.send_message(
            chat.id,
            f"⚠️ {mention}, please censor abusive words using #, $ or @ "
            f"(example: <code>ch#t</code>). This is warning {count}/{_WARNING_LIMIT}.",
            parse_mode="HTML",
        )
        return

    try:
        bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)
        if not getattr(bot_member, "can_restrict_members", False):
            await context.bot.send_message(
                chat.id,
                f"⚠️ {mention} reached 3 warnings, but I cannot ban them because "
                "I need the Restrict Members permission.",
                parse_mode="HTML",
            )
            return
        await context.bot.ban_chat_member(chat.id, user.id)
        await repository.record(
            chat.id, bot_id, user.id, "smartgc_three_warnings_ban",
            "Reached 3 abusive-language warnings",
        )
        await context.bot.send_message(
            chat.id,
            f"🚫 {mention} has been banned after 3 abusive-language warnings.",
            parse_mode="HTML",
        )
    except TelegramError:
        logger.exception("Smart GC failed to ban user %s in chat %s", user.id, chat.id)
        await context.bot.send_message(
            chat.id, f"⚠️ {mention} reached 3 warnings, but Telegram rejected the ban.",
            parse_mode="HTML",
        )
