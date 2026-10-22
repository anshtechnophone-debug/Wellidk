"""Join-gate verification for new group members."""

from __future__ import annotations

import logging
import re

from telegram import Chat, ChatMember, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)
_PAYLOAD = re.compile(r"^verify_(-?\d{1,15})_(\d{1,15})$")


def _verification_payload(chat_id: int, user_id: int) -> str:
    return f"verify_{chat_id}_{user_id}"


async def start_verification(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start deep links and normal manual starts."""
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    payload = context.args[0] if context.args else ""
    match = _PAYLOAD.fullmatch(payload)
    if match:
        chat_id, expected_user_id = int(match.group(1)), int(match.group(2))
        if user.id != expected_user_id:
            await message.reply_text("This verification link belongs to another Telegram account. Please open your own link from the group.")
            return
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("I am a human", callback_data=_verification_payload(chat_id, user.id))
        ]])
        await message.reply_text(
            "Please confirm that you are a human to unlock your group permissions.",
            reply_markup=keyboard,
        )
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Master (@s1dhu)", url="https://t.me/s1dhu")],
        [InlineKeyboardButton("Secret Society", url="https://t.me/+75hmQ06GsjhjMTg9")],
    ])
    await message.reply_text(
        "I was built to serve my Master only.\n\nMust join my Master's Secret Society♥️",
        reply_markup=keyboard,
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
