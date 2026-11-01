"""Owner-controlled Telegram group administrator promotion."""

from telegram import Chat, ChatMember
from telegram.error import TelegramError

from app.core.permissions import AuthorizationError
from app.modules.moderation.handlers import TargetError, _resolve_target, _user_label
from app.services.group_policy import GroupPolicyRepository


RIGHTS = (
    "can_manage_chat", "can_delete_messages", "can_manage_video_chats",
    "can_restrict_members", "can_promote_members", "can_change_info",
    "can_invite_users", "can_post_stories", "can_edit_stories",
    "can_delete_stories", "can_post_messages", "can_edit_messages",
    "can_pin_messages", "can_manage_topics", "can_manage_direct_messages",
)


async def promote_command(update, context):
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
    if not context.args or context.args[0].casefold() not in {"l", "f"}:
        await message.reply_text(
            "Reply to a member with /promote l or /promote f.\n"
            "l = limited admin (no member-removal permission).\n"
            "f = full admin (member-removal permission enabled).\n"
            "Neither mode can promote other admins or act anonymously."
        )
        return
    mode = context.args[0].casefold()
    try:
        target_id, rest = await _resolve_target(update, context, context.args[1:], allow_name=True)
        if rest:
            raise TargetError("Usage: /promote l or /promote f")
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
