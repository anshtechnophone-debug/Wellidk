"""Persistent group quote voting and automatic quote matching."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import BigInteger, ForeignKey, Integer, String, Text, UniqueConstraint, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import Forbidden, TelegramError
from telegram.ext import ContextTypes

from app.database.models import Base, UTCDateTime

logger = logging.getLogger(__name__)
STOP_WORDS = {"a","an","the","and","or","but","if","to","of","in","on","at","for","from","with","is","am","are","was","were","be","been","being","it","this","that","these","those","i","you","he","she","we","they","me","my","your","our","their","his","her","as","by","do","does","did","so","just","not","no","yes","have","has","had","will","would","can","could","should","what","when","where","who","why","how","very","too","than"}
MATCH_THRESHOLD = 0.40
AUTO_COOLDOWN_SECONDS = 300


class SavedGroupQuote(Base):
    __tablename__ = "saved_group_quotes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    source_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    author_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    author_name: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quote_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_auto_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class SavedGroupQuoteVote(Base):
    __tablename__ = "saved_group_quote_votes"
    __table_args__ = (UniqueConstraint("quote_id", "user_id", name="uq_group_quote_vote_user"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("saved_group_quotes.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    vote: Mapped[str] = mapped_column(String(4), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


def quote_keyboard(quote_id: int, likes: int = 0, dislikes: int = 0, start_url: str | None = None) -> InlineKeyboardMarkup:
    rows = [[
        InlineKeyboardButton(f"👍 {likes}", callback_data=f"qv:{quote_id}:up"),
        InlineKeyboardButton(f"👎 {dislikes}", callback_data=f"qv:{quote_id}:down"),
    ]]
    if start_url:
        rows.append([InlineKeyboardButton("Start XYRON to vote", url=start_url)])
    return InlineKeyboardMarkup(rows)


async def save_group_quote(sessions: async_sessionmaker[AsyncSession], *, chat_id: int, source_message_id: int, author_id: int, author_name: str, username: str | None, quote_text: str) -> int:
    async with sessions.begin() as session:
        quote = SavedGroupQuote(chat_id=chat_id, source_message_id=source_message_id, author_id=author_id, author_name=author_name[:255], username=username, quote_text=quote_text[:10000], created_at=datetime.now(UTC))
        session.add(quote)
        await session.flush()
        return quote.id


async def quote_vote_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query, chat, user = update.callback_query, update.effective_chat, update.effective_user
    if not query or not query.data or not chat or not user:
        return
    if chat.type not in {"group", "supergroup"}:
        await query.answer("Quote voting is available in groups only.", show_alert=True)
        return
    try:
        _, quote_raw, vote = query.data.split(":")
        quote_id = int(quote_raw)
        if vote not in {"up", "down"}:
            raise ValueError
    except (ValueError, TypeError):
        await query.answer("This quote vote is invalid.", show_alert=True)
        return
    try:
        await context.bot.send_chat_action(chat_id=user.id, action="typing")
    except Forbidden:
        try:
            bot = await context.bot.get_me()
            start_url = f"https://t.me/{bot.username}?start=quote_react_{quote_id}"
            await query.edit_message_reply_markup(reply_markup=quote_keyboard(quote_id, start_url=start_url))
        except TelegramError:
            logger.debug("Could not add quote onboarding button", exc_info=True)
        await query.answer("Open XYRON and press Start first. Then return here to vote.", show_alert=True)
        return
    sessions = context.application.bot_data.get("sessions")
    if sessions is None:
        await query.answer("Voting is temporarily unavailable.", show_alert=True)
        return
    async with sessions.begin() as session:
        quote = await session.get(SavedGroupQuote, quote_id)
        if not quote or quote.chat_id != chat.id:
            await query.answer("This quote is no longer available.", show_alert=True)
            return
        existing = await session.scalar(select(SavedGroupQuoteVote).where(SavedGroupQuoteVote.quote_id == quote_id, SavedGroupQuoteVote.user_id == user.id))
        if existing:
            if existing.vote == vote:
                await session.delete(existing)
            else:
                existing.vote = vote
                existing.updated_at = datetime.now(UTC)
        else:
            session.add(SavedGroupQuoteVote(quote_id=quote_id, user_id=user.id, vote=vote, updated_at=datetime.now(UTC)))
        await session.flush()
        likes = await session.scalar(select(func.count()).select_from(SavedGroupQuoteVote).where(SavedGroupQuoteVote.quote_id == quote_id, SavedGroupQuoteVote.vote == "up")) or 0
        dislikes = await session.scalar(select(func.count()).select_from(SavedGroupQuoteVote).where(SavedGroupQuoteVote.quote_id == quote_id, SavedGroupQuoteVote.vote == "down")) or 0
    try:
        await query.edit_message_reply_markup(reply_markup=quote_keyboard(quote_id, likes, dislikes))
    except Exception:
        logger.debug("Could not refresh quote vote buttons", exc_info=True)
    await query.answer("Vote updated.")


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[^\W_]+", text.casefold(), flags=re.UNICODE)
    return {word for word in words if len(word) >= 2 and word not in STOP_WORDS}


async def quote_auto_match_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user or chat.type not in {"group", "supergroup"}:
        return
    if message.text and message.text.lstrip().startswith("/"):
        return
    incoming = _tokens(message.text or message.caption or "")
    if len(incoming) < 2:
        return
    sessions = context.application.bot_data.get("sessions")
    if sessions is None:
        return
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=AUTO_COOLDOWN_SECONDS)
    async with sessions() as session:
        quotes = (await session.scalars(select(SavedGroupQuote).where(SavedGroupQuote.chat_id == chat.id).order_by(SavedGroupQuote.created_at.desc()).limit(500))).all()
    best = None
    best_score = 0.0
    for quote in quotes:
        quote_terms = _tokens(quote.quote_text)
        if len(quote_terms) < 2:
            continue
        shared = incoming & quote_terms
        if len(shared) < 2:
            continue
        score = len(shared) / min(len(incoming), len(quote_terms))
        if score >= MATCH_THRESHOLD and score > best_score and not (quote.last_auto_sent_at and quote.last_auto_sent_at > cutoff):
            best, best_score = quote, score
    if best is None:
        return
    async with sessions.begin() as session:
        current = await session.get(SavedGroupQuote, best.id)
        if not current or (current.last_auto_sent_at and current.last_auto_sent_at > cutoff):
            return
        current.last_auto_sent_at = now
        data = {"id": current.id, "author_id": current.author_id, "author_name": current.author_name, "username": current.username, "text": current.quote_text}
    try:
        from app.modules.quote_generator import _render_sticker, _user_avatar
        avatar_data = await _user_avatar(context, data["author_id"])
        sticker = _render_sticker(data["text"], data["author_name"], data["username"], avatar_data, 0)
        await context.bot.send_sticker(chat_id=chat.id, sticker=sticker, reply_to_message_id=message.message_id, allow_sending_without_reply=True, reply_markup=quote_keyboard(data["id"]))
    except Exception:
        logger.exception("Automatic saved-quote response failed for chat_id=%s", chat.id)
