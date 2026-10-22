"""Join-gate verification for new group members and the XYRON community."""

from __future__ import annotations

import logging
import os
import re

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


async def start_verification(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle group-verification deep links and gate manual starts on XYRON ARMY."""
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    payload = context.args[0] if context.args else ""
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
    if settings and user.id == settings.owner_id:
        await _send_manual_start_message(message)
        return

    community_id = _community_chat_id()
    if community_id is not None:
        try:
            member = await context.bot.get_chat_member(community_id, user.id)
            if _is_member(member):
                await _send_manual_start_message(message)
                return
        except TelegramError:
            logger.exception(
                "Could not check XYRON ARMY membership for user %s; showing join gate",
                user.id,
            )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚔️ Join My Army", url=COMMUNITY_URL)],
        [InlineKeyboardButton("✓ I've Joined — Check", callback_data=_COMMUNITY_CHECK)],
    ])
    await message.reply_text(
        "☠️ <b>XYRON ARMY ACCESS REQUIRED</b> ☠️\n\n"
        "Join the XYRON community to proceed. Once you've joined, tap "
        "<b>I've Joined — Check</b> and XYRON will verify your membership.\n\n"
        "♛ <b>JOIN XYRON ARMY TO STAY IN POWER.</b> ♛",
        parse_mode="HTML",
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


async def community_access_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Block private bot interactions until XYRON ARMY membership is confirmed."""
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return

    message = update.effective_message
    query = update.callback_query
    # In groups, gate commands and inline-button actions, but do not spam the
    # group by gating every ordinary message.
    if chat.type != Chat.PRIVATE and not query and not (
        message and message.text and message.text.lstrip().startswith("/")
    ):
        return

    settings = context.application.bot_data.get("settings")
    if settings and user.id == settings.owner_id:
        return

    # Keep the join button functional and preserve the existing group-human
    # verification deep link/button; all other private bot features are gated.
    if query and query.data in {_COMMUNITY_CHECK}:
        return
    if query and query.data and _PAYLOAD.fullmatch(query.data):
        return
    if message and message.text:
        parts = message.text.strip().split(maxsplit=1)
        if parts and parts[0].split("@", 1)[0].lower() == "/start" and len(parts) > 1:
            if _PAYLOAD.fullmatch(parts[1].strip()):
                return

    community_id = _community_chat_id()
    if community_id is not None:
        try:
            member = await context.bot.get_chat_member(community_id, user.id)
            if _is_member(member):
                return
        except TelegramError:
            logger.exception(
                "Could not check XYRON ARMY membership for private access by user %s",
                user.id,
            )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚔️ Join My Army", url=COMMUNITY_URL)],
        [InlineKeyboardButton("✓ I've Joined — Check", callback_data=_COMMUNITY_CHECK)],
    ])
    gate_text = (
        "☠️ <b>XYRON ARMY ACCESS REQUIRED</b> ☠️\n\n"
        "Join the XYRON community to use XYRON's commands and features. "
        "After joining, tap <b>I've Joined — Check</b>.\n\n"
        "♛ <b>JOIN XYRON ARMY TO STAY IN POWER.</b> ♛"
    )
    if query:
        await query.answer("Join XYRON ARMY to use XYRON.", show_alert=True)
        await context.bot.send_message(
            chat_id=user.id,
            text=gate_text,
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    elif message:
        await message.reply_text(
            gate_text,
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
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

    await query.answer("Membership confirmed. Welcome to XYRON.")
    try:
        await query.edit_message_text("✓ XYRON ARMY membership confirmed. Access granted.")
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
