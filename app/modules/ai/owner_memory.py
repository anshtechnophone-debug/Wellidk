"""Owner-only abusive-language candidate reports from saved group history.

This is observe-only: keyword matches are unconfirmed candidates and never
trigger a mute, ban, warning, or other moderation action.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from telegram import Chat, Update
from telegram.ext import ContextTypes

from app.services.group_history import GroupMessage

_CANDIDATE_TERMS = (
    "madarchod", "bhenchod", "behenchod", "chutiya", "chutiye",
    "gandu", "gaand", "bsdk", "bhosd", "fuck", "fucker", "motherfucker",
    "bitch", "bastard", "asshole", "slut", "whore", "mc", "bc",
)


def _normalise(value: str) -> str:
    value = value.casefold()
    value = re.sub(r"[^a-z0-9]+", "", value)
    return re.sub(r"(.)\\1{2,}", r"\\1\\1", value)


def _candidate(text: str) -> bool:
    normal = _normalise(text)
    original = text.casefold()
    for term in _CANDIDATE_TERMS:
        if len(term) <= 2:
            if re.search(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", original):
                return True
        elif term in normal:
            return True
    return False


def _owner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    settings = context.application.bot_data.get("settings")
    return bool(user and settings and user.id == settings.owner_id)


async def handle_owner_memory_request(
    update: Update, context: ContextTypes.DEFAULT_TYPE, request: str
) -> bool:
    """Handle owner requests to report possible gaali in recent saved history."""
    message = update.effective_message
    chat = update.effective_chat
    if not message or not chat or not _owner(update, context):
        return False
    if chat.type not in {Chat.GROUP, Chat.SUPERGROUP}:
        return False

    text = request.strip().casefold()
    asks_abuse = bool(re.search(
        r"(?:who|anyone|kisi|kisne|kaun|kon|whoever).{0,60}"
        r"(?:abuse|abused|gaali|gali|insult|abusive|gaali.?galoch)|"
        r"(?:abuse|abused|gaali|gali|insult|gaali.?galoch).{0,60}"
        r"(?:who|anyone|kisne|kaun|kon|member|group)",
        text,
    )) or bool(re.search(
        r"^(?:show|find|check|scan|list).{0,50}(?:gaali|gali|abuse|abusive|insult)",
        text,
    ))
    if not asks_abuse:
        return False

    sessions = context.application.bot_data.get("sessions")
    if sessions is None:
        await message.reply_text("The database session is unavailable; I couldn't scan saved messages.")
        return True

    now = datetime.now(UTC)
    start = now - timedelta(hours=24)
    async with sessions() as session:
        rows = (await session.scalars(
            select(GroupMessage)
            .where(GroupMessage.sent_at >= start, GroupMessage.sent_at <= now)
            .order_by(GroupMessage.sent_at.desc())
            .limit(1000)
        )).all()

    matches = [row for row in rows if _candidate(row.message_text)]
    if not matches:
        await message.reply_text(
            "No saved messages matched the gaali candidate filter in the last 24 hours. "
            "This only checks messages XYRON saved; it doesn't prove no abuse occurred."
        )
        return True

    lines = [
        f"Possible gaali matches in saved group history (last 24 hours): {len(matches)}",
        "These are keyword candidates, not confirmed abuse. No moderation action was taken.",
    ]
    for row in matches[:20]:
        snippet = row.message_text.replace("\\n", " ")[:180]
        username = f" (@{row.username})" if row.username else ""
        lines.append(
            f"• {row.sent_at.isoformat()} | group {row.chat_id} | "
            f"{row.display_name}{username} (ID {row.user_id}) | "
            f"msg {row.message_id}: {snippet}"
        )
    if len(matches) > 20:
        lines.append(f"Showing latest 20 of {len(matches)} matches.")
    await message.reply_text("\\n".join(lines))
    return True
