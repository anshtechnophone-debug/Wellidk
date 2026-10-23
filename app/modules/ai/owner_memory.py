"""Owner-only group intelligence from XYRON's saved message history.

Reports are evidence-limited: they describe only messages XYRON actually saved
for the current group. Abuse matches are unconfirmed keyword candidates only.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from telegram import Chat, Update
from telegram.ext import ContextTypes

from app.services.group_history import GroupMessage

IST = ZoneInfo("Asia/Kolkata")
_CANDIDATE_TERMS = (
    "madarchod", "bhenchod", "behenchod", "chutiya", "chutiye",
    "gandu", "gaand", "bsdk", "bhosd", "fuck", "fucker", "motherfucker",
    "bitch", "bastard", "asshole", "slut", "whore", "mc", "bc",
)


def _normalise(value: str) -> str:
    value = value.casefold()
    value = re.sub(r"[^a-z0-9]+", "", value)
    return re.sub(r"(.)\1{2,}", r"\1\1", value)


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
    """Handle owner-only activity, recent-history, and abuse reports."""
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

    asks_activity = bool(re.search(
        r"(?:most active|highest activity|active member|activity stats|"
        r"message count|who sent (?:the )?most|sabse zyada active|"
        r"jyada active|zyada active|kitne messages|kisne sabse zyada messages)",
        text,
    ))
    asks_summary = bool(re.search(
        r"(?:what.?s happening|what is happening|what happened|what.?s going on|"
        r"gc mein kya|group mein kya|group summary|summari[sz]e|"
        r"last night|yesterday|recent (?:chat|messages|activity)|"
        r"what did i miss|kya chal raha|kya chal rha|kya hua tha|"
        r"who argued|who was arguing|kiski ladai|kaun lad raha)",
        text,
    ))

    if not (asks_abuse or asks_activity or asks_summary):
        return False

    sessions = context.application.bot_data.get("sessions")
    if sessions is None:
        await message.reply_text("The database session is unavailable; I couldn't read saved group history.")
        return True

    now = datetime.now(UTC)
    # Use the current calendar day in India for "today" activity reports.
    local_now = datetime.now(IST)
    local_start = datetime.combine(local_now.date(), time.min, tzinfo=IST)
    start_today = local_start.astimezone(UTC)

    if asks_activity:
        async with sessions() as session:
            total = await session.scalar(
                select(func.count()).select_from(GroupMessage).where(
                    GroupMessage.chat_id == chat.id,
                    GroupMessage.sent_at >= start_today,
                    GroupMessage.sent_at <= now,
                )
            )
            rows = (await session.execute(
                select(
                    GroupMessage.user_id,
                    func.max(GroupMessage.display_name).label("display_name"),
                    func.max(GroupMessage.username).label("username"),
                    func.count().label("message_count"),
                )
                .where(
                    GroupMessage.chat_id == chat.id,
                    GroupMessage.sent_at >= start_today,
                    GroupMessage.sent_at <= now,
                )
                .group_by(GroupMessage.user_id)
                .order_by(func.count().desc())
                .limit(10)
            )).all()
        if not total:
            await message.reply_text(
                "I haven't saved any messages in this group today (India time), Malik. "
                "I can't calculate activity before message collection began."
            )
            return True
        lines = [
            f"Saved group activity today (India time): {total} messages from {len(rows)} top member(s).",
            "Ranked only from messages XYRON saved in this group:",
        ]
        for index, row in enumerate(rows, 1):
            label = row.display_name or f"User {row.user_id}"
            if row.username:
                label += f" (@{row.username})"
            lines.append(f"{index}. {label}: {row.message_count} messages")
        lines.append("This is not necessarily the group's complete activity history.")
        await message.reply_text("\n".join(lines))
        return True

    if asks_abuse:
        start = now - timedelta(hours=24)
        async with sessions() as session:
            rows = (await session.scalars(
                select(GroupMessage)
                .where(
                    GroupMessage.chat_id == chat.id,
                    GroupMessage.sent_at >= start,
                    GroupMessage.sent_at <= now,
                )
                .order_by(GroupMessage.sent_at.desc())
                .limit(1000)
            )).all()
        matches = [row for row in rows if _candidate(row.message_text)]
        if not matches:
            await message.reply_text(
                "No saved messages in this group matched the gaali keyword filter in the last 24 hours. "
                "This doesn't prove no abuse occurred; XYRON can only check messages it saved."
            )
            return True
        lines = [
            f"Possible gaali matches in this group's saved history (last 24 hours): {len(matches)}",
            "Keyword matches are unconfirmed candidates, not a verdict. No moderation action was taken.",
        ]
        for row in matches[:20]:
            snippet = row.message_text.replace("\n", " ")[:180]
            username = f" (@{row.username})" if row.username else ""
            lines.append(
                f"• {row.sent_at.astimezone(IST).strftime('%d %b %H:%M')} IST | "
                f"{row.display_name}{username} (ID {row.user_id}) | msg {row.message_id}: {snippet}"
            )
        if len(matches) > 20:
            lines.append(f"Showing latest 20 of {len(matches)} matches.")
        await message.reply_text("\n".join(lines))
        return True

    # A concise, chronological evidence digest. AI can interpret it separately,
    # but this deterministic fallback never fabricates a summary.
    hours = 36 if re.search(r"last night|yesterday", text) else 6
    start = now - timedelta(hours=hours)
    async with sessions() as session:
        rows = (await session.scalars(
            select(GroupMessage)
            .where(
                GroupMessage.chat_id == chat.id,
                GroupMessage.sent_at >= start,
                GroupMessage.sent_at <= now,
            )
            .order_by(GroupMessage.sent_at.desc())
            .limit(35)
        )).all()
    if not rows:
        await message.reply_text(
            f"I couldn't find saved messages from this group in the last {hours} hours, Malik. "
            "That doesn't mean nothing happened; the history may be incomplete."
        )
        return True
    lines = [
        f"Recent saved messages from this group (last {hours} hours; newest first):",
        "I’m showing evidence rather than guessing at the meaning of the conversation.",
    ]
    for row in rows[:25]:
        label = row.display_name or f"User {row.user_id}"
        snippet = re.sub(r"\s+", " ", row.message_text).strip()[:220]
        lines.append(
            f"• {row.sent_at.astimezone(IST).strftime('%d %b %H:%M')} IST — "
            f"{label}: {snippet}"
        )
    if len(rows) > 25:
        lines.append(f"Showing 25 of {len(rows)} recent saved messages.")
    lines.append("Only messages XYRON saved are included; older or missed messages may be absent.")
    await message.reply_text("\n".join(lines))
    return True
