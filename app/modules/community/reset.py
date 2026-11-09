"""Admin reset controls for the daily and giveaway leaderboards."""

from __future__ import annotations

from datetime import UTC, datetime

from telegram import Chat, Update
from telegram.ext import ContextTypes

from app.core.permissions import AuthorizationError, require_admin_access
from app.services.group_policy import GroupPolicyRepository


async def reset_leaderboard_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Reset only the leaderboard image replied to by an authorized group admin."""
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not chat or not user:
        return
    if chat.type not in {Chat.GROUP, Chat.SUPERGROUP}:
        await message.reply_text("Use /reset in the group where the leaderboard was posted.")
        return

    if len(context.args) != 1 or context.args[0].casefold() not in {"daily", "prize"}:
        await message.reply_text(
            "Usage: reply to a leaderboard image with /reset daily or /reset prize."
        )
        return

    target = message.reply_to_message
    if not target or not target.from_user or target.from_user.id != context.bot.id:
        await message.reply_text("Reply directly to XYRON's leaderboard image to reset it.")
        return

    kind = context.args[0].casefold()
    caption = (target.caption or "").casefold()
    if kind == "daily" and not caption.startswith("🏆 daily chat leaderboard"):
        await message.reply_text("That isn't a /daily leaderboard image. Reply to the daily ranking.")
        return
    if kind == "prize" and not caption.startswith("🎁 giveaway prize leaderboard"):
        await message.reply_text("That isn't a /prize leaderboard image. Reply to the prize ranking.")
        return

    try:
        await require_admin_access(
            update,
            context,
            context.application.bot_data["settings"],
            None,
        )
    except AuthorizationError as exc:
        await message.reply_text(str(exc))
        return

    repo = GroupPolicyRepository(context.application.bot_data["sessions"])
    if kind == "daily":
        await repo.set_value(chat.id, "daily_reset_at", datetime.now(UTC).isoformat())
        await message.reply_text(
            "✅ Today's daily leaderboard has been reset. New messages will count from now."
        )
        return

    policy = await repo.get(chat.id)
    state = policy.get("giveaway")
    if not isinstance(state, dict) or not state.get("started_at"):
        await message.reply_text("There is no giveaway ranking to reset in this group.")
        return
    members = state.get("members", {})
    if not isinstance(members, dict):
        members = {}
    for entry in members.values():
        if isinstance(entry, dict):
            entry["score"] = 0.0
    state["members"] = members
    await repo.set_value(chat.id, "giveaway", state)
    await message.reply_text(
        "✅ Prize leaderboard reset. All giveaway points are now 0; participants and the giveaway status are unchanged."
    )
