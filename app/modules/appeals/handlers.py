"""Member appeal submission and admin review commands."""

from __future__ import annotations

from telegram import Chat, Update
from telegram.ext import ContextTypes

from app.core.permissions import AuthorizationError, require_admin_access
from app.services.appeals import AppealRepository


def _repository(context: ContextTypes.DEFAULT_TYPE) -> AppealRepository:
    return AppealRepository(context.application.bot_data["sessions"])


async def appeal_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or chat.type not in {Chat.GROUP, Chat.SUPERGROUP} or not user:
        if message:
            await message.reply_text(
                "Submit an appeal in the group where the moderation action occurred."
            )
        return
    text = " ".join(context.args).strip()
    if not 10 <= len(text) <= 2000:
        await message.reply_text("Usage: /appeal <10-2000 characters explaining your request>")
        return
    appeal_id, status = await _repository(context).submit(chat.id, user.id, text)
    if status == "rate_limited":
        await message.reply_text("Appeal rate limit reached. Try again later.")
        return
    await message.reply_text(
        f"Appeal #{appeal_id} recorded for admin review. "
        "Submission does not automatically change moderation."
    )


async def appeals_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await require_admin_access(update, context, context.application.bot_data["settings"], None)
        appeals = await _repository(context).recent(update.effective_chat.id)
        if not appeals:
            await update.effective_message.reply_text("No open appeals in this group.")
            return
        lines = [
            f"#{appeal.id} user {appeal.user_id}: {appeal.message[:300]}" for appeal in appeals
        ]
        await update.effective_message.reply_text("Open appeals:\n" + "\n".join(lines))
    except AuthorizationError as exc:
        await update.effective_message.reply_text(str(exc))


async def reviewappeal_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await require_admin_access(update, context, context.application.bot_data["settings"], None)
        if (
            len(context.args) < 2
            or not context.args[0].isdecimal()
            or context.args[1].casefold() not in {"accepted", "rejected", "needs_info"}
        ):
            raise ValueError("Usage: /reviewappeal <id> <accepted|rejected|needs_info> [note]")
        appeal_id = int(context.args[0])
        status = context.args[1].casefold()
        note = " ".join(context.args[2:]) or None
        changed = await _repository(context).review(
            update.effective_chat.id,
            appeal_id,
            update.effective_user.id,
            status,
            note,
        )
        await update.effective_message.reply_text(
            "Appeal reviewed. No moderation action was automatically changed."
            if changed
            else "No open appeal with that ID exists in this group."
        )
    except (AuthorizationError, ValueError) as exc:
        await update.effective_message.reply_text(str(exc))
