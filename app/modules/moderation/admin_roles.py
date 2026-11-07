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
    # Promotion is a Telegram-admin action, never a XYRON bot-admin privilege.
    # The group owner can always promote; other Telegram admins need the
    # explicit Telegram can_promote_members right. A configured XYRON owner
    # or group-scoped bot admin receives no special promotion authority.
    actor_member = await context.bot.get_chat_member(chat.id, actor.id)
    if actor_member.status != ChatMember.OWNER and not (
        actor_member.status == ChatMember.ADMINISTRATOR
        and getattr(actor_member, "can_promote_members", False)
    ):
        await message.reply_text(
            "Only the group owner or a Telegram admin with 'Add new admins' permission can promote members."
        )
        return
    try:
        # One standard promotion level. Reply to a member, or provide
        # @username / numeric ID: /promote @username
        args = list(context.args)
        target_id, rest = await _resolve_target(update, context, args, allow_name=True)
        if rest:
            raise TargetError("Usage: reply to a member with /promote, or use /promote @username.")
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
        # Telegram only allows a bot to grant administrator rights that the
        # bot itself currently has. Build the grant from Telegram's live
        # ChatMemberAdministrator object instead of requesting every right.
        # Keep moderation and further-promotion powers disabled for this level.
        rights = {
            name: bool(getattr(bot_member, name, False))
            for name in RIGHTS
        }
        rights["can_restrict_members"] = False
        rights["can_promote_members"] = False
        logger.info(
            "Promoting user %s in chat %s with grantable rights: %s",
            target_id,
            chat.id,
            sorted(name for name, enabled in rights.items() if enabled),
        )
        try:
            await context.bot.promote_chat_member(chat.id, target_id, **rights)
        except TelegramError as exc:
            logger.warning(
                "Telegram rejected promotion: chat_id=%s target_id=%s "
                "bot_status=%s bot_can_promote=%s requested_rights=%s error=%s",
                chat.id,
                target_id,
                bot_member.status,
                getattr(bot_member, "can_promote_members", False),
                sorted(name for name, enabled in rights.items() if enabled),
                exc,
            )
            raise
        policies = GroupPolicyRepository(context.application.bot_data["sessions"])
        policy = await policies.get(chat.id)
        promoted = dict(policy.get("xyron_promoted_admins", {}))
        promoted[str(target_id)] = {"mode": "l", "promoted_by": actor.id}
        await policies.set_value(chat.id, "xyron_promoted_admins", promoted)
        await message.reply_text(
            f"Promoted {await _user_label(context, chat.id, target_id)} as an admin.",
            parse_mode="HTML",
        )
    except (AuthorizationError, TargetError) as exc:
        await message.reply_text(str(exc))
    except TelegramError as exc:
        logger.exception(
            "Promotion failed: chat_id=%s actor_id=%s",
            chat.id,
            actor.id,
        )
        await message.reply_text(f"Telegram could not promote that member: {exc}")


# Updates for the same administrator are serialized so concurrent Telegram
# chat_member updates cannot overwrite one another's rolling-window history.
_PROTECTOR_LOCKS: dict[tuple[int, int], asyncio.Lock] = {}


async def mass_ban_protector(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Suspend an administrator's ban permission after a fifth ban in 2 minutes.

    This monitors every identifiable Telegram administrator, not only admins
    promoted by XYRON. Telegram remains the authority for whether XYRON can
    edit that administrator's rights (bot admin hierarchy and can_promote_members).
    """
    event = update.chat_member
    chat = update.effective_chat
    if not event or not chat or not event.from_user:
        return
    if getattr(event.new_chat_member, "status", None) != ChatMember.BANNED:
        return

    actor_id = event.from_user.id
    bot_id = context.bot.id
    if actor_id == bot_id:
        return

    lock = _PROTECTOR_LOCKS.setdefault((chat.id, actor_id), asyncio.Lock())
    async with lock:
        await _mass_ban_protector_locked(update, context, actor_id)


async def _mass_ban_protector_locked(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    actor_id: int,
) -> None:
    chat = update.effective_chat
    if not chat:
        return

    policies = GroupPolicyRepository(context.application.bot_data["sessions"])
    policy = await policies.get(chat.id)
    actor_key = str(actor_id)
    now = time.time()

    # Only actual Telegram admins can trigger this protection. The group owner
    # is explicitly exempt and is never subjected to a rights change.
    try:
        current = await context.bot.get_chat_member(chat.id, actor_id)
    except TelegramError:
        logger.exception(
            "Could not inspect ban actor: chat_id=%s actor_id=%s",
            chat.id, actor_id,
        )
        return
    if current.status != ChatMember.ADMINISTRATOR:
        return

    histories = policy.get("xyron_removal_events", {})
    histories = dict(histories) if isinstance(histories, dict) else {}
    raw_history = histories.get(actor_key, [])
    recent = [
        float(stamp) for stamp in raw_history
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

    # Check XYRON's live authority immediately before changing rights. If the
    # target is above XYRON in Telegram's admin hierarchy, Telegram will reject
    # the change; we log that limitation and leave the group owner's controls
    # untouched. Keep the threshold history so a later event can retry.
    try:
        bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)
        if (
            bot_member.status != ChatMember.ADMINISTRATOR
            or not getattr(bot_member, "can_promote_members", False)
            or not bool(getattr(current, "can_restrict_members", False))
        ):
            await policies.set_value(chat.id, "xyron_removal_events", histories)
            logger.warning(
                "Mass-ban detected but XYRON cannot edit actor rights: "
                "chat_id=%s actor_id=%s bot_status=%s bot_can_promote=%s",
                chat.id,
                actor_id,
                bot_member.status,
                getattr(bot_member, "can_promote_members", False),
            )
            return

        # Preserve every other current admin right; suspend only the ability
        # to ban/restrict members. The original state is saved for restoration.
        rights = {name: bool(getattr(current, name, False)) for name in RIGHTS}
        rights["can_restrict_members"] = False
        await context.bot.promote_chat_member(chat.id, actor_id, **rights)

        suspensions[actor_key] = {
            "expires_at": now + BAN_PERMISSION_SUSPENSION_SECONDS,
            "restore_can_restrict_members": True,
        }
        histories[actor_key] = []
        await policies.set_value(chat.id, "xyron_removal_events", histories)
        await policies.set_value(
            chat.id, "xyron_ban_permission_suspensions", suspensions
        )
        label = await _user_label(context, chat.id, actor_id)
        await context.bot.send_message(
            chat.id,
            f"⛔ {label} exceeded 4 bans in 2 minutes. "
            "XYRON immediately paused their ban permission for 1 hour. "
            "Their other administrator permissions remain unchanged.",
            parse_mode="HTML",
        )
        logger.warning(
            "Mass-ban protection activated: chat_id=%s actor_id=%s bans=%s",
            chat.id, actor_id, len(recent),
        )
    except TelegramError:
        logger.exception(
            "Mass-ban detected but Telegram rejected rights suspension: "
            "chat_id=%s actor_id=%s",
            chat.id, actor_id,
        )
        await policies.set_value(chat.id, "xyron_removal_events", histories)


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
