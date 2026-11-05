"""Additional QuotLy-style quote retrieval commands for XYRON."""
from __future__ import annotations

import logging
import random
import re

from sqlalchemy import case, func, select
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from app.modules.quote_features import SavedGroupQuote, SavedGroupQuoteVote, quote_keyboard

logger = logging.getLogger(__name__)


async def _send_saved_quote(update: Update, context: ContextTypes.DEFAULT_TYPE, quote: SavedGroupQuote) -> None:
    message = update.effective_message
    if not message:
        return
    try:
        from app.modules.quote_generator import _render_sticker, _user_avatar
        avatar = await _user_avatar(context, quote.author_id)
        sticker = _render_sticker(quote.quote_text, quote.author_name, quote.username, avatar, 0)
        await message.reply_sticker(sticker=sticker, reply_markup=quote_keyboard(quote.id),
                                    read_timeout=30, write_timeout=30, connect_timeout=15, pool_timeout=15)
    except (OSError, TelegramError):
        logger.exception("Could not render saved quote id=%s", quote.id)
        await message.reply_text("I couldn't render that saved quote. Please try again.")


async def quote_random_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat = update.effective_message, update.effective_chat
    if not message or not chat:
        return
    if chat.type not in {"group", "supergroup"}:
        await message.reply_text("Use /qrand in a group where XYRON has saved quotes.")
        return
    sessions = context.application.bot_data.get("sessions")
    if sessions is None:
        await message.reply_text("Quote storage is temporarily unavailable.")
        return
    async with sessions() as session:
        rows = (await session.scalars(select(SavedGroupQuote).where(
            SavedGroupQuote.chat_id == chat.id).order_by(SavedGroupQuote.created_at.desc()).limit(250))).all()
    if not rows:
        await message.reply_text("No saved quotes yet. Reply to a message with /q to save one.")
        return
    await _send_saved_quote(update, context, random.choice(rows))


async def quote_top_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat = update.effective_message, update.effective_chat
    if not message or not chat:
        return
    if chat.type not in {"group", "supergroup"}:
        await message.reply_text("Use /qtop inside a group.")
        return
    sessions = context.application.bot_data.get("sessions")
    if sessions is None:
        await message.reply_text("Quote storage is temporarily unavailable.")
        return
    likes_expr = func.sum(case((SavedGroupQuoteVote.vote == "up", 1), else_=0))
    dislikes_expr = func.sum(case((SavedGroupQuoteVote.vote == "down", 1), else_=0))
    async with sessions() as session:
        rows = (await session.execute(select(
            SavedGroupQuote.id, SavedGroupQuote.author_name, SavedGroupQuote.quote_text,
            likes_expr.label("likes"), dislikes_expr.label("dislikes")
        ).outerjoin(SavedGroupQuoteVote, SavedGroupQuoteVote.quote_id == SavedGroupQuote.id)
          .where(SavedGroupQuote.chat_id == chat.id)
          .group_by(SavedGroupQuote.id, SavedGroupQuote.author_name, SavedGroupQuote.quote_text)
          .order_by((likes_expr - dislikes_expr).desc(), SavedGroupQuote.id.desc()).limit(10))).all()
    if not rows:
        await message.reply_text("No saved quotes yet. Reply to a message with /q to save one.")
        return
    lines = ["🏆 <b>Top group quotes</b>", ""]
    for rank, row in enumerate(rows, 1):
        quote_id, author, quote_text, likes, dislikes = row
        excerpt = " ".join((quote_text or "").split())
        if len(excerpt) > 115:
            excerpt = excerpt[:112].rstrip() + "…"
        lines.append(f"<b>{rank}.</b> “{_escape(excerpt)}”")
        lines.append(f"   — {_escape(author)} · 👍 {int(likes or 0)}  👎 {int(dislikes or 0)} · <code>#{quote_id}</code>")
    await message.reply_text("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


async def quote_recall_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat = update.effective_message, update.effective_chat
    if not message or not chat or not message.text:
        return
    match = re.match(r"^/q_(\d+)(?:@\w+)?(?:\s|$)", message.text.strip())
    if not match:
        return
    quote_id = int(match.group(1))
    sessions = context.application.bot_data.get("sessions")
    if sessions is None:
        await message.reply_text("Quote storage is temporarily unavailable.")
        return
    async with sessions() as session:
        quote = await session.scalar(select(SavedGroupQuote).where(
            SavedGroupQuote.id == quote_id, SavedGroupQuote.chat_id == chat.id))
    if not quote:
        await message.reply_text("That quote wasn't found in this chat.")
        return
    await _send_saved_quote(update, context, quote)


def _escape(value: str) -> str:
    return (value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
