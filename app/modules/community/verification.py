"""Join-gate verification for new group members and the XYRON community."""

from __future__ import annotations

import logging
import os
import re
from html import escape

from telegram import (
    Chat,
    ChatMember,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.error import TelegramError
from telegram.ext import ApplicationHandlerStop, ContextTypes

logger = logging.getLogger(__name__)
_PAYLOAD = re.compile(r"^verify_(-?\d{1,15})_(\d{1,15})$")
_ADMIN_GUIDE_PAYLOAD = re.compile(r"^botadmin_guide_(\d{1,15})$")
_RULES_PAYLOAD = re.compile(r"^rules_(-?\d{1,15})$")
COMMUNITY_URL = "https://t.me/+krgZfOwfpZxhODYx"
_COMMUNITY_CHECK = "community_join_check"


def _verification_payload(chat_id: int, user_id: int) -> str:
    return f"verify_{chat_id}_{user_id}"


def _community_chat_id() -> int | None:
    """Read the private XYRON ARMY channel ID from runtime configuration."""
    raw = os.getenv("XYRON_COMMUNITY_CHAT_ID", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.error("XYRON_COMMUNITY_CHAT_ID must be a numeric Telegram chat ID")
        return None


def _is_member(member) -> bool:
    return member.status in {
        ChatMember.MEMBER,
        ChatMember.ADMINISTRATOR,
        ChatMember.OWNER,
    } or bool(getattr(member, "is_member", False))


async def _send_manual_start_message(message) -> None:
    """Send the original manual /start message after the community gate passes."""
    await message.reply_text(
        'I was built to serve my <a href="https://t.me/S1DH7">Master</a> only.\n\n'
        'Must join my Master\'s <a href="https://t.me/+75hmQ06GsjhjMTg9">Secret Society</a>♥️',
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


def _community_join_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Join XYRON ARMY", url=COMMUNITY_URL)],
        [InlineKeyboardButton("I’ve Joined — Check", callback_data=_COMMUNITY_CHECK)],
    ])


async def _send_community_join_prompt(message) -> None:
    """Ask non-owners to join XYRON ARMY before showing the master notice."""
    await message.reply_text(
        "Before continuing, please join XYRON ARMY using the button below. "
        "After joining, tap “I’ve Joined — Check”. I’ll confirm your membership first.",
        reply_markup=_community_join_keyboard(),
        disable_web_page_preview=True,
    )


async def start_verification(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle group-verification deep links and gate manual starts on XYRON ARMY."""
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    payload = context.args[0] if context.args else ""
    giveaway_match = re.fullmatch(r"gw_(-?\d{1,15})", payload)
    if giveaway_match:
        group_id = int(giveaway_match.group(1))
        from app.modules.community.giveaway import _now, _parse_time, _repo, _rules_text

        try:
            policy = await _repo(context).get(group_id)
            state = policy.get("giveaway", {})
            if not isinstance(state, dict) or not state.get("active"):
                await message.reply_text("🏁 This giveaway is no longer active. Check the group for the next one.")
                return
            ends_at = _parse_time(state.get("ends_at"))
            if not ends_at or _now() >= ends_at:
                state["active"] = False
                state["ended_at"] = ends_at.isoformat() if ends_at else _now().isoformat()
                await _repo(context).set_value(group_id, "giveaway", state)
                await message.reply_text("🏁 This giveaway has ended.")
                return
            member = await context.bot.get_chat_member(group_id, user.id)
            if member.status not in {ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER} and not bool(getattr(member, "is_member", False)):
                await message.reply_text("Please join the group first, then tap Join Giveaway again.")
                return
            members = state.setdefault("members", {})
            key = str(user.id)
            if key not in members:
                members[key] = {
                    "name": user.full_name,
                    "username": user.username,
                    "score": 0.0,
                    "joined_at": _now().isoformat(),
                }
                await _repo(context).set_value(group_id, "giveaway", state)
            group = await context.bot.get_chat(group_id)
            started = _parse_time(state.get("started_at"))
            await message.reply_text(
                f"✅ <b>You're registered for {escape(group.title or 'the giveaway')}!</b>\n\n"
                "Your credits start counting from now. Only text messages sent in the group after joining count.\n"
                "• Reply: +1 credit\n"
                "• Standalone text: +0.5 credit\n\n"
                "Use /prize in the group to check your rank.",
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            await message.reply_text(
                _rules_text(group.title or "Giveaway", started, ends_at),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except TelegramError:
            await message.reply_text(
                "I couldn't verify that you're in the giveaway's group. Join the group first, then tap Join Giveaway again."
            )
        except (TypeError, ValueError):
            await message.reply_text("This giveaway link is invalid or has expired.")
        return

    rules_match = _RULES_PAYLOAD.fullmatch(payload)
    if rules_match:
        group_id = int(rules_match.group(1))
        try:
            from app.modules.community.handlers import _send_rules_dm

            await _send_rules_dm(context, user.id, group_id)
        except TelegramError:
            logger.exception("Could not deliver group rules to user %s for chat %s", user.id, group_id)
            await message.reply_text(
                "I couldn't load those group rules right now. Please request them again from the group."
            )
        return

    admin_guide_match = _ADMIN_GUIDE_PAYLOAD.fullmatch(payload)
    if admin_guide_match:
        expected_user_id = int(admin_guide_match.group(1))
        if user.id != expected_user_id:
            await message.reply_text("This command-guide link belongs to another Telegram account.")
            return
        from app.database.models import BotAdmin
        sessions = context.application.bot_data.get("sessions")
        is_bot_admin = False
        if sessions is not None:
            async with sessions() as session:
                is_bot_admin = await session.get(BotAdmin, user.id) is not None
        if not is_bot_admin:
            await message.reply_text("You are not currently on XYRON's bot-admin list. Ask the bot owner if you think this is a mistake.")
            return
        await message.reply_text(
            "✅ You are now a XYRON bot admin!\n\n"
            "Your moderation commands (use them in a group where XYRON is present):\n"
            "• /mute — permanently mute a member (reply to their message or use their user ID)\n"
            "• /mute 30m — temporary mute for 30 minutes\n"
            "• /tmute 2h — temporary mute for 2 hours\n"
            "• /unmute — restore a member's saved/default permissions\n"
            "• /mutelist — view XYRON-tracked mutes\n\n"
            "How to use: reply to the member's message with /mute, /tmute 30m, or /unmute. "
            "You can also put their numeric Telegram user ID after the command.\n\n"
            "Limits: bot admins can mute only one member per group every 10 minutes. "
            "Ban and kick commands are reserved for the bot owner. "
            "You do not need Telegram group-admin status to be a XYRON bot admin."
        )
        return

    match = _PAYLOAD.fullmatch(payload)
    if match:
        chat_id, expected_user_id = int(match.group(1)), int(match.group(2))
        if user.id != expected_user_id:
            await message.reply_text(
                "This verification link belongs to another Telegram account. "
                "Please open your own link from the group."
            )
            return
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("I am a human", callback_data=_verification_payload(chat_id, user.id))
        ]])
        await message.reply_text(
            "Please confirm that you are a human to unlock your group permissions.",
            reply_markup=keyboard,
        )
        return

    settings = context.application.bot_data.get("settings")
    if settings and settings.is_owner(user.id):
        await _send_manual_start_message(message)
        return

    # Ask non-owners to join and confirm XYRON ARMY before showing the master notice.
    await _send_community_join_prompt(message)


async def community_access_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Allow only the configured owner to use XYRON's commands and features."""
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return

    settings = context.application.bot_data.get("settings")
    if settings and settings.is_owner(user.id):
        return

    message = update.effective_message
    query = update.callback_query

    # This gate controls private-chat/community access, not group command
    # authorization. Group commands must reach their handlers: public commands
    # are usable by members, while sensitive commands enforce owner/admin and
    # Telegram-permission checks themselves. Natural-language AI remains
    # owner-only in owner_natural_control / AIEventRouter.
    if chat.type != Chat.PRIVATE:
        if query:
            await query.answer("Please message XYRON privately to continue.", show_alert=True)
            raise ApplicationHandlerStop
        return

    # Keep human-verification and the community membership-check callbacks working.
    if query and query.data and (
        _PAYLOAD.fullmatch(query.data)
        or query.data == _COMMUNITY_CHECK
        or re.fullmatch(r"giveaway:(?:join|rules):-?\d+", query.data)
    ):
        # Giveaway callbacks must reach their own handler; it performs the
        # active-giveaway and group-membership checks itself.
        return
    if message and message.text:
        parts = message.text.strip().split(maxsplit=1)
        if parts and parts[0].split("@", 1)[0].lower() == "/start" and len(parts) > 1:
            start_payload = parts[1].strip()
            if _PAYLOAD.fullmatch(start_payload):
                return
            admin_guide_match = _ADMIN_GUIDE_PAYLOAD.fullmatch(start_payload)
            if admin_guide_match and user.id == int(admin_guide_match.group(1)):
                return
            if _RULES_PAYLOAD.fullmatch(start_payload):
                return
            if re.fullmatch(r"gw_-?\d{1,15}", start_payload):
                return

    if query:
        await query.answer("XYRON is reserved for its Master.", show_alert=True)
        await context.bot.send_message(
            chat_id=user.id,
            text=(
                'I was built to serve my <a href="https://t.me/S1DH7">Master</a> only.\n\n'
                'Must join my Master\'s <a href="https://t.me/+75hmQ06GsjhjMTg9">Secret Society</a>♥️'
            ),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    elif message:
        await _send_community_join_prompt(message)

    raise ApplicationHandlerStop


async def community_join_check_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Verify the clicking user's channel membership before releasing the start message."""
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return

    community_id = _community_chat_id()
    if community_id is None:
        await query.answer(
            "Membership verification is not configured yet. Please contact the bot owner.",
            show_alert=True,
        )
        return

    try:
        member = await context.bot.get_chat_member(community_id, user.id)
    except TelegramError:
        logger.exception("Could not check XYRON ARMY membership for user %s", user.id)
        await query.answer(
            "I couldn't verify membership. Please try again shortly.",
            show_alert=True,
        )
        return

    if not _is_member(member):
        await query.answer(
            "You haven't joined XYRON ARMY yet. Join the channel, then check again.",
            show_alert=True,
        )
        return

    await query.answer("Membership confirmed.")
    try:
        await query.edit_message_text("✓ XYRON ARMY membership confirmed.")
    except TelegramError:
        pass
    await context.bot.send_message(
        chat_id=user.id,
        text=(
            'I was built to serve my <a href="https://t.me/S1DH7">Master</a> only.\n\n'
            'Must join my Master\'s <a href="https://t.me/+75hmQ06GsjhjMTg9">Secret Society</a>♥️'
        ),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def verify_human_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not query.data or not user:
        return
    match = _PAYLOAD.fullmatch(query.data)
    if not match:
        await query.answer("This verification link is invalid.", show_alert=True)
        return
    chat_id, expected_user_id = int(match.group(1)), int(match.group(2))
    if user.id != expected_user_id:
        await query.answer("This button belongs to another user.", show_alert=True)
        return

    try:
        chat = await context.bot.get_chat(chat_id)
        permissions = chat.permissions or ChatPermissions(
            can_send_messages=True,
            can_send_audios=True,
            can_send_documents=True,
            can_send_photos=True,
            can_send_videos=True,
            can_send_video_notes=True,
            can_send_voice_notes=True,
            can_send_polls=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True,
        )
        await context.bot.restrict_chat_member(chat_id, user.id, permissions=permissions)
    except TelegramError:
        logger.exception("Failed to release verified member %s from chat %s", user.id, chat_id)
        await query.answer("I couldn't restore your group permissions. Please contact an admin.", show_alert=True)
        return

    await query.answer("Human verification complete.")
    try:
        await query.edit_message_text("Verification successful. Your group permissions have been restored.")
    except TelegramError:
        pass
    try:
        await context.bot.send_message(chat_id, f"Verification complete for {user.mention_html()}.", parse_mode="HTML")
    except TelegramError:
        logger.info("Could not post verification completion in chat %s", chat_id)


async def gate_new_member(chat, member_update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Restrict a newly joined human and post their personal verification link."""
    user = member_update.new_chat_member.user
    if user.is_bot:
        return False
    if chat.type not in {Chat.GROUP, Chat.SUPERGROUP}:
        return False

    try:
        bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)
        if bot_member.status not in {ChatMember.ADMINISTRATOR, ChatMember.OWNER} or not getattr(bot_member, "can_restrict_members", False):
            logger.warning("Join verification unavailable in chat %s: XYRON lacks restrict permission", chat.id)
            await context.bot.send_message(chat.id, f"{user.mention_html()}, please ask an admin to enable member verification.", parse_mode="HTML")
            return True
        await context.bot.restrict_chat_member(
            chat.id,
            user.id,
            permissions=ChatPermissions(
                can_send_messages=False,
                can_send_audios=False,
                can_send_documents=False,
                can_send_photos=False,
                can_send_videos=False,
                can_send_video_notes=False,
                can_send_voice_notes=False,
                can_send_polls=False,
                can_send_other_messages=False,
                can_add_web_page_previews=False,
            ),
        )
        me = await context.bot.get_me()
        payload = _verification_payload(chat.id, user.id)
        url = f"https://t.me/{me.username}?start={payload}"
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Verify in DM", url=url)]])
        await context.bot.send_message(
            chat.id,
            f"{user.mention_html()}, please confirm that you are a human by opening XYRON's DM and pressing the verification button.",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return True
    except TelegramError:
        logger.exception("Failed to gate new member %s in chat %s", user.id, chat.id)
        return True
