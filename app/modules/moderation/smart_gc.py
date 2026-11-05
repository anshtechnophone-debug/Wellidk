"""Per-group Smart GC configuration and abusive-language protection."""

from __future__ import annotations

import logging
import re
import unicodedata

from telegram import Chat, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from app.services.group_policy import GroupPolicyRepository
from app.services.moderation import ModerationRepository

logger = logging.getLogger(__name__)

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
_WARNING_LIMIT = 5

_FEATURES = (
    ("abusive_language", "🤬 Abusive Language"),
    ("nsfw_detection", "🔞 NSFW Detection"),
    ("media_cleanup", "🧹 Media Cleanup"),
    ("approved_exemption", "⭐ Approved Users Exemption"),
)
_DEFAULT_FEATURES = {key: True for key, _ in _FEATURES}


def feature_enabled(smart_gc: object, feature: str, default: bool = True) -> bool:
    """Read a Smart GC feature flag while remaining compatible with old policies."""
    if not isinstance(smart_gc, dict):
        return default
    features = smart_gc.get("features")
    if not isinstance(features, dict):
        return default
    return bool(features.get(feature, default))


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def _menu(features: dict[str, bool]) -> InlineKeyboardMarkup:
    buttons = []
    for key, label in _FEATURES:
        state = bool(features.get(key, True))
        buttons.append(
            InlineKeyboardButton(
                f"{'🟢' if state else '🔴'} {label}: {'ON' if state else 'OFF'}",
                callback_data=f"sgc:toggle:{key}",
                style="success" if state else "danger",
            )
        )
    return InlineKeyboardMarkup([
        buttons[:2],
        buttons[2:],
        [InlineKeyboardButton("🟢 TURN ON SMART GC", callback_data="sgc:enable", style="success")],
    ])


def _menu_text(features: dict[str, bool], enabled: bool) -> str:
    status = "currently ON" if enabled else "currently OFF"
    return (
        f"🛡 <b>SMART GC SETTINGS</b> (Smart GC is {status})\n\n"
        "Choose which protections should run in this group. Tap a feature to switch it ON or OFF.\n"
        "Your selections are applied when you press the final button."
    )


async def _can_manage_smart_gc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user, chat = update.effective_user, update.effective_chat
    if not user or not chat or chat.type not in {Chat.GROUP, Chat.SUPERGROUP}:
        return False
    settings = context.application.bot_data['settings']
    if settings.is_owner(user.id):
        return True
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        return member.status in {'administrator', 'creator'}
    except TelegramError:
        return False


async def _show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in {Chat.GROUP, Chat.SUPERGROUP}:
        if message:
            await message.reply_text("Use this command inside the group you want to protect.")
        return
    if not await _can_manage_smart_gc(update, context):
        if message:
            await message.reply_text("Only group admins can configure Smart GC.")
        return

    policies = GroupPolicyRepository(context.application.bot_data["sessions"])
    policy = await policies.get(chat.id)
    active = policy.get("smart_gc", {})
    active = active if isinstance(active, dict) else {}
    draft = policy.get("smart_gc_draft")
    if not isinstance(draft, dict):
        current = active.get("features")
        draft = {
            key: bool(current.get(key, True)) if isinstance(current, dict) else True
            for key, _ in _FEATURES
        }
        await policies.set_value(chat.id, "smart_gc_draft", draft)
    else:
        draft = {key: bool(draft.get(key, True)) for key, _ in _FEATURES}

    await message.reply_text(
        _menu_text(draft, bool(active.get("enabled"))),
        reply_markup=_menu(draft),
        parse_mode="HTML",
    )


async def smartgc_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Open the feature menu; /smartgc off immediately disables all protection."""
    args = [arg.casefold() for arg in context.args]
    if args == ["off"]:
        user, chat, message = update.effective_user, update.effective_chat, update.effective_message
        if not chat or chat.type not in {Chat.GROUP, Chat.SUPERGROUP}:
            if message:
                await message.reply_text("Use this command inside the group you want to protect.")
            return
        if not await _can_manage_smart_gc(update, context):
            if message:
                await message.reply_text("Only group admins can configure Smart GC.")
            return
        policies = GroupPolicyRepository(context.application.bot_data["sessions"])
        current = await policies.get(chat.id)
        smart_gc = current.get("smart_gc", {})
        smart_gc = dict(smart_gc) if isinstance(smart_gc, dict) else {}
        smart_gc["enabled"] = False
        await policies.set_value(chat.id, "smart_gc", smart_gc)
        await policies.set_value(chat.id, "smart_gc_draft", None)
        if message:
            await message.reply_text("🔴 <b>Smart GC is OFF.</b> All Smart GC protections are disabled.", parse_mode="HTML")
        return
    if args not in ([], ["on"]):
        if update.effective_message:
            await update.effective_message.reply_text("Usage: /smartgc, /smartgc on, or /smartgc off")
        return
    await _show_menu(update, context)


async def smartgc_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only inline editor for this group's Smart GC settings."""
    query = update.callback_query
    if not query:
        return
    message = query.message
    chat = getattr(message, "chat", None)
    if not chat or chat.type not in {Chat.GROUP, Chat.SUPERGROUP}:
        await query.answer("Open Smart GC settings inside the group.", show_alert=True)
        return
    try:
        member = await context.bot.get_chat_member(chat.id, query.from_user.id)
        settings = context.application.bot_data["settings"]
        allowed = settings.is_owner(query.from_user.id) or member.status in {"administrator", "creator"}
    except TelegramError:
        allowed = False
    if not allowed:
        await query.answer("Only group admins can change Smart GC.", show_alert=True)
        return

    data = query.data or ""
    policies = GroupPolicyRepository(context.application.bot_data["sessions"])
    policy = await policies.get(chat.id)
    active = policy.get("smart_gc", {})
    active = active if isinstance(active, dict) else {}
    draft = policy.get("smart_gc_draft")
    if not isinstance(draft, dict):
        current = active.get("features")
        draft = {
            key: bool(current.get(key, True)) if isinstance(current, dict) else True
            for key, _ in _FEATURES
        }
    else:
        draft = {key: bool(draft.get(key, True)) for key, _ in _FEATURES}

    if data.startswith("sgc:toggle:"):
        feature = data.split(":", 2)[2]
        if feature not in _DEFAULT_FEATURES:
            await query.answer("Unknown Smart GC setting.", show_alert=True)
            return
        draft[feature] = not draft[feature]
        await policies.set_value(chat.id, "smart_gc_draft", draft)
        await query.answer(f"{feature.replace('_', ' ').title()}: {'ON' if draft[feature] else 'OFF'}")
        await query.edit_message_text(
            _menu_text(draft, bool(active.get("enabled"))),
            reply_markup=_menu(draft),
            parse_mode="HTML",
        )
        return

    if data == "sgc:enable":
        active["enabled"] = True
        active["features"] = draft
        await policies.set_value(chat.id, "smart_gc", active)
        await policies.set_value(chat.id, "smart_gc_draft", None)
        enabled_lines = [
            f"{'🟢' if draft[key] else '🔴'} {label}: {'ON' if draft[key] else 'OFF'}"
            for key, label in _FEATURES
        ]
        await query.answer("Smart GC settings saved.")
        await query.edit_message_text(
            "🛡 <b>SMART GC IS ON</b>\n\n" + "\n".join(enabled_lines),
            parse_mode="HTML",
        )
        return

    await query.answer("This Smart GC menu is no longer valid.", show_alert=True)


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
    if (
        not isinstance(smart_gc, dict)
        or not smart_gc.get("enabled")
        or not feature_enabled(smart_gc, "abusive_language")
    ):
        return
    approved = {int(value) for value in policy.get("approved_users", [])}
    if feature_enabled(smart_gc, "approved_exemption") and user.id in approved:
        return

    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
    except TelegramError:
        logger.debug("Could not inspect member %s in %s for Smart GC", user.id, chat.id)
        return
    if member.status in {"administrator", "creator"}:
        return

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
                f"⚠️ {mention} reached 5 warnings, but I cannot ban them because "
                "I need the Restrict Members permission.",
                parse_mode="HTML",
            )
            return
        await context.bot.ban_chat_member(chat.id, user.id)
        await repository.record(
            chat.id, bot_id, user.id, "smartgc_five_warnings_ban",
            "Reached 5 abusive-language warnings",
        )
        await context.bot.send_message(
            chat.id,
            f"🚫 {mention} has been banned after 5 abusive-language warnings.",
            parse_mode="HTML",
        )
    except TelegramError:
        logger.exception("Smart GC failed to ban user %s in chat %s", user.id, chat.id)
        await context.bot.send_message(
            chat.id, f"⚠️ {mention} reached 5 warnings, but Telegram rejected the ban.",
            parse_mode="HTML",
        )
