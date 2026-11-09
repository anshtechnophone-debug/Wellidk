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
_QUOTE_REACT_PAYLOAD = re.compile(r"^quote_react_\d{1,10}$")


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


async def _send_public_welcome(message, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the simple private-chat welcome and its three main actions."""
    me = await context.bot.get_me()
    add_url = f"https://t.me/{me.username}?startgroup=true" if me.username else None
    rows = []
    if add_url:
        rows.append([InlineKeyboardButton("➕ Add to Group", url=add_url)])
    rows.append([InlineKeyboardButton(
        "XYRON Support Group",
        url="https://t.me/Random_Group_ChatV",
    )])
    await message.reply_text(
        "Hey there! I'm <b>XYRON</b> — your all-in-one assistant. ⚡\n\n"
        "I'm here to keep your groups organized, secure, and running smoothly "
        "with powerful moderation tools and smart features.\n\n"
        "✦ Use /help to explore my commands and discover everything I can do.\n\n"
        "<b>Let's make your group a better place with XYRON!</b> 🚀",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(rows),
        disable_web_page_preview=True,
    )

def _community_join_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Join XYRON ARMY", url=COMMUNITY_URL)],
        [InlineKeyboardButton("I’ve Joined — Check", callback_data=_COMMUNITY_CHECK)],
    ])


async def _send_community_join_prompt(message) -> None:
    """Ask users who are not in the official channel to join before using XYRON."""
    await message.reply_text(
        "To use XYRON, first join our official channel using the button below. "
        "Then tap “I've Joined — Check” and I'll verify your membership.",
        reply_markup=_community_join_keyboard(),
        disable_web_page_preview=True,
    )


async def _is_community_member(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    community_id = _community_chat_id()
    if community_id is None:
        return False
    try:
        return _is_member(await context.bot.get_chat_member(community_id, user_id))
    except TelegramError:
        logger.exception("Could not check XYRON channel membership for user %s", user_id)
        return False


async def bot_added_as_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send one private setup note when XYRON first becomes a group administrator."""
    change = update.my_chat_member
    if not change:
        return
    old_status = change.old_chat_member.status
    new_member = change.new_chat_member
    if new_member.status not in {ChatMember.ADMINISTRATOR, ChatMember.OWNER}:
        return
    if old_status in {ChatMember.ADMINISTRATOR, ChatMember.OWNER}:
        return
    chat = change.chat
    if chat.type not in {Chat.GROUP, Chat.SUPERGROUP}:
        return

    actor = change.from_user
    sessions = context.application.bot_data.get("sessions")
    if sessions is None:
        logger.warning("Cannot check one-time XYRON setup notice for group %s: database unavailable", chat.id)
        return

    from app.services.group_policy import GroupPolicyRepository

    policies = GroupPolicyRepository(sessions)
    try:
        policy = await policies.get(chat.id)
    except Exception:
        logger.exception("Could not read setup-notice state for group %s", chat.id)
        return
    if policy.get("xyron_setup_dm_sent"):
        return

    permission_notes = []
    if getattr(new_member, "can_restrict_members", False):
        permission_notes.append("✓ Restrict Members permission is enabled.")
    else:
        permission_notes.append(
            "⚠️ Please grant Restrict Members so XYRON can apply human verification and mutes."
        )
    if getattr(new_member, "can_delete_messages", False):
        permission_notes.append("✓ Delete Messages permission is enabled.")
    else:
        permission_notes.append(
            "⚠️ Please grant Delete Messages so XYRON can remove messages when needed."
        )

    text = (
        f"Thanks for adding XYRON to <b>{escape(chat.title or 'your group')}</b>! 👋\n\n"
        "<b>About human verification</b>\n"
        "Human verification is enabled by default. It asks new members to complete a quick "
        "check so automated accounts have a harder time joining your group.\n\n"
        "You can turn it off at any time with /verification off. "
        "Turn it back on with /verification on.\n\n"
        + "\n".join(permission_notes)
        + "\n\nOpen Help in this chat with the Help button on XYRON's welcome screen "
        "to see the command guide."
    )
    try:
        await context.bot.send_message(
            actor.id,
            text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except TelegramError:
        try:
            me = await context.bot.get_me()
            start_url = f"https://t.me/{me.username}?start=welcome_{chat.id}" if me.username else None
            markup = InlineKeyboardMarkup(
                [[InlineKeyboardButton("Open XYRON in DM", url=start_url)]]
            ) if start_url else None
            await context.bot.send_message(
                chat.id,
                f"{actor.mention_html()}, XYRON is now an administrator. "
                "Open XYRON in a private chat to read the one-time setup note.",
                parse_mode="HTML",
                reply_markup=markup,
            )
        except TelegramError:
            logger.info("Could not deliver XYRON setup notice to inviter %s", actor.id)
        return

    try:
        await policies.set_value(chat.id, "xyron_setup_dm_sent", True)
    except Exception:
        logger.exception("Setup note was sent, but its one-time marker could not be saved for group %s", chat.id)

async def start_verification(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle deep links and require official-channel membership for public access."""
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat or chat.type != Chat.PRIVATE:
        return

    payload = context.args[0] if context.args else ""
    settings = context.application.bot_data.get("settings")
    is_owner = bool(settings and settings.is_owner(user.id))
    if not is_owner and _community_chat_id() is None:
        await message.reply_text("Channel membership verification is not configured yet. Please contact the XYRON team.")
        return
    # Human-verification deep links must reach their own two-button flow even
    # when the user has not joined the official channel yet.
    verification_match = _PAYLOAD.fullmatch(payload)
    if verification_match:
        chat_id, expected_user_id = map(int, verification_match.groups())
        if user.id != expected_user_id:
            await message.reply_text("This verification link belongs to another Telegram account. Open your own link from the group.")
            return
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Join XYRON Channel", url=COMMUNITY_URL)],
            [InlineKeyboardButton("✅ Joined — Verify", callback_data=_verification_payload(chat_id, user.id))],
        ])
        await message.reply_text(
            "First join the official XYRON channel, then tap “Joined — Verify”. "
            "I'll check your channel membership and complete your human-confirmation step together.",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
        return

    if not is_owner and not await _is_community_member(context, user.id):
        await _send_community_join_prompt(message)
        return

    if not payload:
        await _send_public_welcome(message, context)
        return

    ttt_join_match = re.fullmatch(r"tttj_(\d{1,10})", payload)
    if ttt_join_match:
        from app.modules.games.board_handlers import join_ttt_from_start
        await join_ttt_from_start(update, context, int(ttt_join_match.group(1)))
        return
    family_match = re.fullmatch(r"fr_(\d{1,10})_(\d{1,15})_(-?\d{1,15})_(\d{1,10})", payload)
    if family_match:
        request_id, expected_user_id, group_id, message_id = map(int, family_match.groups())
        if user.id != expected_user_id:
            await message.reply_text("This Family request link belongs to another Telegram account.")
            return
        from app.modules.games.family import FamilyRepository
        repository = FamilyRepository(context.application.bot_data["sessions"])
        if not await repository.is_pending_request_for(request_id, user.id):
            await message.reply_text("This Family request has expired or was already answered.")
            return
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Accept", callback_data=f"famreq:a:{request_id}"),
            InlineKeyboardButton("Decline", callback_data=f"famreq:d:{request_id}"),
        ]])
        try:
            await context.bot.edit_message_reply_markup(chat_id=group_id, message_id=message_id, reply_markup=keyboard)
        except TelegramError:
            await message.reply_text("I couldn't update the group buttons. Please ask an admin to check my permissions.")
            return
        await message.reply_text(f"✅ XYRON is ready! Return to the group and tap Accept or Decline for request #{request_id}.")
        return
    legacy_family_match = re.fullmatch(r"family_req_(\d{1,10})_(\d{1,15})", payload)
    if legacy_family_match:
        request_id, expected_user_id = map(int, legacy_family_match.groups())
        if user.id != expected_user_id:
            await message.reply_text("This Family request link belongs to another Telegram account.")
            return
        await message.reply_text(f"XYRON is ready for request #{request_id}. Please ask for a fresh request link in the group.")
        return
    if _QUOTE_REACT_PAYLOAD.fullmatch(payload):
        await message.reply_text("You're all set! Return to the group and tap 👍 or 👎 on the quote again.")
        return
    giveaway_match = re.fullmatch(r"gw_(-?\d{1,15})", payload)
    if giveaway_match:
        group_id = int(giveaway_match.group(1))
        from app.modules.community.giveaway import _now, _parse_time, _repo, _rules_text
        try:
            policy = await _repo(context).get(group_id)
            state = policy.get("giveaway", {})
            if not isinstance(state, dict) or not state.get("active"):
                await message.reply_text("This giveaway is no longer active. Check the group for the next one.")
                return
            ends_at = _parse_time(state.get("ends_at"))
            if not ends_at or _now() >= ends_at:
                state["active"] = False
                state["ended_at"] = ends_at.isoformat() if ends_at else _now().isoformat()
                await _repo(context).set_value(group_id, "giveaway", state)
                await message.reply_text("This giveaway has ended.")
                return
            member = await context.bot.get_chat_member(group_id, user.id)
            if member.status not in {ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER} and not bool(getattr(member, "is_member", False)):
                await message.reply_text("Join the group first, then tap Join Giveaway again.")
                return
            members = state.setdefault("members", {})
            key = str(user.id)
            if key not in members:
                members[key] = {"name": user.full_name, "username": user.username, "score": 0.0, "joined_at": _now().isoformat()}
                await _repo(context).set_value(group_id, "giveaway", state)
            group = await context.bot.get_chat(group_id)
            started = _parse_time(state.get("started_at"))
            await message.reply_text(
                f"✅ <b>You're registered for {escape(group.title or 'the giveaway')}!</b>\n\n"
                "Only text messages sent in the group after joining count.\n"
                "• Reply: +1 credit\n• Standalone text: +0.5 credit\n\n"
                "Use /prize in the group to check your rank.",
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            await message.reply_text(_rules_text(group.title or "Giveaway", started, ends_at), parse_mode="HTML", disable_web_page_preview=True)
        except TelegramError:
            await message.reply_text("I couldn't verify that you're in the giveaway group. Join it first, then try again.")
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
            await message.reply_text("I couldn't load those group rules right now. Please request them again from the group.")
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
            "Use /mute, /tmute 30m, /unmute, and /mutelist in groups where XYRON is present. "
            "Bot admins can mute only one member per group every 10 minutes. "
            "Ban and kick are reserved for the bot owner. Telegram group-admin status is not required."
        )
        return

    match = _PAYLOAD.fullmatch(payload)
    if match:
        chat_id, expected_user_id = int(match.group(1)), int(match.group(2))
        if user.id != expected_user_id:
            await message.reply_text("This verification link belongs to another Telegram account. Open your own link from the group.")
            return
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Join XYRON Channel", url=COMMUNITY_URL)],
            [InlineKeyboardButton("✅ Joined — Verify", callback_data=_verification_payload(chat_id, user.id))],
        ])
        await message.reply_text(
            "First join the official XYRON channel, then tap “Joined — Verify”. "
            "I'll check your membership and complete your human-confirmation step together.",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
        return

    await _send_public_welcome(message, context)

async def community_access_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Require official-channel membership in private chats; keep group features public."""
    user, chat = update.effective_user, update.effective_chat
    if not user or not chat:
        return
    message, query = update.effective_message, update.callback_query
    settings = context.application.bot_data.get("settings")
    if settings and settings.is_owner(user.id):
        return

    if chat.type != Chat.PRIVATE:
        if query and query.data and query.data.startswith("help:"):
            await query.answer("Use /help in XYRON's private chat.", show_alert=True)
            raise ApplicationHandlerStop
        return

    # Let /start handle the join prompt and all supported deep links.
    if message and message.text:
        first = message.text.strip().split(maxsplit=1)[0].split("@", 1)[0].lower()
        if first == "/start":
            return

    # Membership confirmation callbacks must reach their dedicated handlers.
    if query and (query.data == _COMMUNITY_CHECK or query.data.startswith("verify_")):
        return

    # Human verification and other private actions require a configured channel
    # and confirmed membership.
    if _community_chat_id() is None:
        if query:
            await query.answer("Channel membership verification is not configured yet.", show_alert=True)
        elif message:
            await message.reply_text("Channel membership verification is not configured yet. Please contact the XYRON team.")
        raise ApplicationHandlerStop
    if not await _is_community_member(context, user.id):
        if query:
            await query.answer("Join the official XYRON channel first.", show_alert=True)
            try:
                await context.bot.send_message(user.id, "Please join the official XYRON channel to continue.", reply_markup=_community_join_keyboard())
            except TelegramError:
                pass
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
    await _send_public_welcome(update.effective_message, context)


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

    if not await _is_community_member(context, user.id):
        await query.answer(
            "Join the official XYRON channel first, then tap Joined — Verify.",
            show_alert=True,
        )
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
