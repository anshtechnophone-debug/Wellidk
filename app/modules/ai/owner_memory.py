"""Persistent owner sleep sessions and evidence-based history queries.

This module is deliberately observe-only: abuse-word matches are candidates,
not proof, and this module never performs a moderation action.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import func, select
from telegram import Chat, Update
from telegram.ext import ContextTypes

from app.database.models import ModerationEvent, OwnerSleepSession
from app.services.group_history import GroupMessage

# Normalize repeated letters and separators before matching common English and
# Hindi/Hinglish abusive terms. This is a candidate filter, not a final verdict.
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
    for term in _CANDIDATE_TERMS:
        # Short forms require boundaries in the original text to reduce false positives.
        if len(term) <= 2:
            if re.search(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", text.casefold()):
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
    """Handle explicit sleep/awake and history questions for the configured owner."""
    message = update.effective_message
    chat = update.effective_chat
    if not message or not chat or not _owner(update, context):
        return False
    if chat.type not in {Chat.GROUP, Chat.SUPERGROUP}:
        return False
    sessions = context.application.bot_data.get("sessions")
    if sessions is None:
        return False
    text = request.strip().casefold()
    now = datetime.now(UTC)

    sleep_phrase = bool(re.search(
        r"\b(?:i'?m going to sleep|i am going to sleep|i'?m sleeping|"
        r"going to sleep now|main sone ja raha|main sone jaa raha|"
        r"main sone ja rha|xyron sleep mode)\b", text
    ))
    awake_phrase = bool(re.search(
        r"\b(?:i'?m awake|i am awake|i woke up|i'?m up now|"
        r"main uth gaya|main uth gya|xyron awake)\b", text
    ))
    if sleep_phrase:
        async with sessions.begin() as session:
            active = await session.scalar(select(OwnerSleepSession).where(
                OwnerSleepSession.owner_id == update.effective_user.id,
                OwnerSleepSession.ended_at.is_(None)
            ).order_by(OwnerSleepSession.started_at.desc()))
            if active:
                await message.reply_text(f"Sleep session already active since {active.started_at.isoformat()}.")
            else:
                session.add(OwnerSleepSession(owner_id=update.effective_user.id, started_at=now))
                await message.reply_text("Sleep session started. I'll search saved group messages from now until you say you're awake.")
        return True
    if awake_phrase:
        async with sessions.begin() as session:
            active = await session.scalar(select(OwnerSleepSession).where(
                OwnerSleepSession.owner_id == update.effective_user.id,
                OwnerSleepSession.ended_at.is_(None)
            ).order_by(OwnerSleepSession.started_at.desc()))
            if not active:
                await message.reply_text("No active sleep session was saved, so I haven't changed anything.")
            else:
                active.ended_at = now
                await message.reply_text(f"Sleep session closed. Saved interval: {active.started_at.isoformat()} to {now.isoformat()}.")
        return True

    asks_sleep_abuse = bool(re.search(
        r"(?:who|anyone|kisi|kisne|kaun|kon).{0,60}(?:abuse|abused|gaali|gali|insult|abusive)|"
        r"(?:abuse|abused|gaali|gali|insult).{0,60}(?:while i was asleep|when i was asleep|during my sleep|mere sote|sleep session)",
        text
    ))
    if asks_sleep_abuse:
        async with sessions() as session:
            active = await session.scalar(select(OwnerSleepSession).where(
                OwnerSleepSession.owner_id == update.effective_user.id,
                OwnerSleepSession.ended_at.is_(None)
            ).order_by(OwnerSleepSession.started_at.desc()))
            if active:
                start, end = active.started_at, now
            else:
                last = await session.scalar(select(OwnerSleepSession).where(
                    OwnerSleepSession.owner_id == update.effective_user.id,
                    OwnerSleepSession.ended_at.is_not(None)
                ).order_by(OwnerSleepSession.started_at.desc()))
                if not last:
                    await message.reply_text("I don't have a saved sleep session yet. Tell me 'I'm going to sleep' first.")
                    return True
                start, end = last.started_at, last.ended_at or now
            rows = (await session.scalars(select(GroupMessage).where(
                GroupMessage.sent_at >= start, GroupMessage.sent_at <= end
            ).order_by(GroupMessage.sent_at.asc()).limit(1000))).all()
        matches = [row for row in rows if _candidate(row.message_text)]
        if not matches:
            await message.reply_text(
                f"No messages matched the abusive-language candidate filter between {start.isoformat()} and {end.isoformat()}. "
                "This searches only messages XYRON saved; it does not prove that no abuse occurred."
            )
            return True
        lines = [f"Possible abusive-language matches ({len(matches)}). These are unconfirmed keyword matches:"]
        for row in matches[-20:]:
            snippet = row.message_text.replace("\n", " ")[:180]
            lines.append(f"• {row.sent_at.isoformat()} | group {row.chat_id} | {row.display_name} (ID {row.user_id}) | msg {row.message_id}: {snippet}")
        if len(matches) > 20:
            lines.append(f"Showing latest 20 of {len(matches)} matches.")
        lines.append("AI confirmation is not available until the provider authentication error is fixed; no punishment was applied.")
        await message.reply_text("\n".join(lines))
        return True

    asks_today = bool(re.search(r"(?:how many|kitne|count).{0,40}(?:muted|mute kiya|mutes today)|(?:muted|mute).{0,30}(?:today|aaj).{0,20}(?:how many|kitne|count)", text))
    asks_last = bool(re.search(r"(?:last|latest|most recent|abhi).{0,40}(?:muted|mute).{0,30}(?:id|member|user|who|kaun|kon)|(?:muted|mute).{0,30}(?:last|latest|abhi).{0,20}(?:id|who|kaun|kon)", text))
    if asks_today or asks_last:
        # UTC day boundaries keep counts consistent with the database timestamps.
        day_start = datetime.combine(now.date(), time.min, tzinfo=UTC)
        async with sessions() as session:
            statement = select(ModerationEvent).where(
                ModerationEvent.created_at >= day_start,
                ModerationEvent.action.in_({"mute", "tmute", "warn_limit_mute", "auto_mute"})
            ).order_by(ModerationEvent.created_at.desc())
            rows = list((await session.scalars(statement)).all())
        if asks_last:
            if rows:
                event = rows[0]
                await message.reply_text(f"Latest recorded mute today: user ID {event.target_id}, group {event.chat_id}, action {event.action}, at {event.created_at.isoformat()}.")
            else:
                await message.reply_text("No mute action is recorded in the database for today (UTC).")
        else:
            await message.reply_text(f"XYRON's moderation log contains {len(rows)} mute action(s) today (UTC). This is based on recorded events, not a live Telegram-wide scan.")
        return True
    return False
