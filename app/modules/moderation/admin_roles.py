"""Owner-controlled Telegram group administrator promotion and mass-ban protection."""

from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy import select
from telegram import Chat, ChatMember, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from app.core.permissions import AuthorizationError
from app.database.models import GroupPolicy
from app.modules.moderation.handlers import TargetError, _resolve_target, _user_label
from app.services.group_policy import GroupPolicyRepository

logger = logging.getLogger(__name__)

RIGHTS = (
    "can_manage_chat", "can_delete_messages", "can_manage_video_chats",
    "can_restrict_members", "can_promote_members", "can_change_info",
    "can_invite_users", "can_post_stories", "can_edit_stories",
    "can_delete_stories", "can_post_messages", "can_edit_messages",
    "can_pin_messages", "can_manage_topics", "can_manage_direct_messages",
)

REMOVAL_WINDOW_SECONDS = 120
REMOVAL_ALLOWED = 4
BAN_PERMISSION_SUSPENSION_SECONDS = 3600


async def promote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, actor = update.effective_message, update.effective_chat, update.effective_user
    settings = context.application.bot_data["settings"]
    if not message or not chat or not actor:
        return
    if chat.type not in {Chat.GROUP, Chat.SUPERGROUP}:
        await message.reply_text("Use /promote in a group.")
        return
    if not settings.is_owner(actor.id):
        await message.reply_text("Only the configured XYRON owner can promote group admins.")
        return
    if not context.args:
        await message.reply_text(
            "Usage: /promote @username [l|f] (numeric ID or reply also supported). "
            "Default: l = limited admin; use f for full admin."
        )
        return
    try:
        # Accept /promote @username [l|f] and legacy /promote l|f @username.
        args = list(context.args)
        mode = "l"
        if args[0].casefold() in {"l", "f"}:
            mode = args.pop(0).casefold()
        elif len(args) > 1 and args[-1].casefold() in {"l", "f"}:
            mode = args.pop().casefold()
        target_id, rest = await _resolve_target(update, context, args, allow_name=True)
        if rest:
            raise TargetError("Usage: /promote @username [l|f]")
        bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)
        if bot_member.status not in {ChatMember.ADMINISTRATOR, ChatMember.OWNER}:
            raise AuthorizationError("XYRON must be a group administrator.")
        if not getattr(bot_member, "can_promote_members", False):
            raise AuthorizationError("XYRON needs the 'Add new admins' permission.")
        target = await context.bot.get_chat_member(chat.id, target_id)
        if target.status == ChatMember.OWNER:
            raise AuthorizationError("The group owner cannot be promoted.")
        if target.status == ChatMember.ADMINISTRATOR:
            raise AuthorizationError("That member is already an administrator.")
        rights = {name: False for name in RIGHTS}
        for name in RIGHTS:
            if name not in {"can_restrict_members", "can_promote_members"}:
                rights[name] = True
        rights["can_restrict_members"] = mode == "f"
        await context.bot.promote_chat_member(chat.id, target_id, **rights)
        policies = GroupPolicyRepository(context.application.bot_data["sessions"])
        policy = await policies.get(chat.id)
        promoted = dict(policy.get("xyron_promoted_admins", {}))
        promoted[str(target_id)] = {"mode": mode, "promoted_by": actor.id}
        await policies.set_value(chat.id, "xyron_promoted_admins", promoted)
        await message.reply_text(
            f"Promoted {await _user_label(context, chat.id, target_id)} as "
            f"{'limited' if mode == 'l' else 'full'} admin.",
            parse_mode="HTML",
        )
    except (AuthorizationError, TargetError) as exc:
        await message.reply_text(str(exc))
    except TelegramError as exc:
        await message.reply_text(f"Telegram could not promote that member: {exc}")


async def mass_ban_protector(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Suspend ban permission after a fifth ban in any rolling two-minute window."""
    event = update.chat_member
    chat = update.effective_chat
    if not event or not chat or not event.from_user:
        return
    if getattr(event.new_chat_member, "status", None) != ChatMember.BANNED:
        return

    policies = GroupPolicyRepository(context.application.bot_data["sessions"])
    policy = await policies.get(chat.id)
    actor_id = event.from_user.id
    actor_key = str(actor_id)
    promoted = policy.get("xyron_promoted_admins", {})
    if not isinstance(promoted, dict) or actor_key not in promoted:
        return
    if promoted[actor_key].get("mode") != "f":
        return

    now = time.time()
    histories = policy.get("xyron_removal_events", {})
    histories = dict(histories) if isinstance(histories, dict) else {}
    recent = [
        float(stamp) for stamp in histories.get(actor_key, [])
        if now - float(stamp) <= REMOVAL_WINDOW_SECONDS
    ]
    recent.append(now)
    histories[actor_key] = recent

    suspensions = policy.get("xyron_ban_permission_suspensions", {})
    suspensions = dict(suspensions) if isinstance(suspensions, dict) else {}
    active = suspensions.get(actor_key)
    if active and float(active.get("expires_at", 0)) > now:
        await policies.set_value(chat.id, "xyron_removal_events", histories)
        return

    if len(recent) <= REMOVAL_ALLOWED:
        await policies.set_value(chat.id, "xyron_removal_events", histories)
        return

    try:
        current = await context.bot.get_chat_member(chat.id, actor_id)
        if current.status != ChatMember.ADMINISTRATOR:
            return
        rights = {name: bool(getattr(current, name, False)) for name in RIGHTS}
        if not rights["can_restrict_members"]:
            return
        rights["can_restrict_members"] = False
        await context.bot.promote_chat_member(chat.id, actor_id, **rights)
        suspensions[actor_key] = {
            "expires_at": now + BAN_PERMISSION_SUSPENSION_SECONDS,
            "restore_can_restrict_members": True,
        }
        histories[actor_key] = []
        await policies.set_value(chat.id, "xyron_removal_events", histories)
        await policies.set_value(chat.id, "xyron_ban_permission_suspensions", suspensions)
        label = await _user_label(context, chat.id, actor_id)
        await context.bot.send_message(
            chat.id,
            f"⛔ {label} exceeded 4 bans in 2 minutes. "
            "Ban permission has been paused for 1 hour and will then be restored.",
            parse_mode="HTML",
        )
    except TelegramError:
        logger.exception("Could not suspend ban permission for admin %s in %s", actor_id, chat.id)


async def mass_ban_restore_worker(application, stop_event: asyncio.Event) -> None:
    """Restore timed-out ban permissions, including after a bot restart."""
    sessions = application.bot_data["sessions"]
    while not stop_event.is_set():
        try:
            async with sessions() as session:
                rows = (await session.scalars(select(GroupPolicy))).all()
            now = time.time()
            for row in rows:
                policy = row.policy or {}
                suspensions = policy.get("xyron_ban_permission_suspensions", {})
                if not isinstance(suspensions, dict) or not suspensions:
                    continue
                for actor_key, suspension in list(suspensions.items()):
                    if float(suspension.get("expires_at", 0)) > now:
                        continue
                    actor_id = int(actor_key)
                    try:
                        member = await application.bot.get_chat_member(row.chat_id, actor_id)
                        if member.status == ChatMember.ADMINISTRATOR:
                            rights = {name: bool(getattr(member, name, False)) for name in RIGHTS}
                            rights["can_restrict_members"] = True
                            await application.bot.promote_chat_member(row.chat_id, actor_id, **rights)
                            label = await _user_label(
                                type("Context", (), {"application": application})(),
                                row.chat_id,
                                actor_id,
                            )
                            await application.bot.send_message(
                                row.chat_id,
                                f"✅ Ban permission restored for {label}.",
                                parse_mode="HTML",
                            )
                    except TelegramError:
                        logger.exception(
                            "Could not restore ban permission for admin %s in %s",
                            actor_id, row.chat_id,
                        )
                        continue
                    suspensions.pop(actor_key, None)
                    await GroupPolicyRepository(sessions).set_value(
                        row.chat_id, "xyron_ban_permission_suspensions", suspensions
                    )
        except Exception:
            logger.exception("Mass-ban permission restoration worker failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=10)
        except TimeoutError:
            continue
