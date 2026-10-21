"""Telegram commands for locally executed games."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.modules.games.wordseek import WordGameError, WordSeekRepository


def _games(context: ContextTypes.DEFAULT_TYPE) -> WordSeekRepository:
    return WordSeekRepository(context.application.bot_data["sessions"])


async def wordseek_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user:
        return
    if len(context.args) > 1:
        await message.reply_text("Usage: /wordseek [4|5|6]")
        return
    try:
        length = int(context.args[0]) if context.args else 5
        await _games(context).start(chat.id, user.id, length)
    except (ValueError, WordGameError) as exc:
        await message.reply_text(str(exc))
        return
    await message.reply_text(
        f"Word Seek started for you: guess the {length}-letter word in 6 tries. "
        "Use /guess <word>. Green means right letter and place, yellow means present, "
        "grey means absent. Game expires in 15 minutes."
    )


async def guess_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user:
        return
    if len(context.args) != 1:
        await message.reply_text("Usage: /guess <word>")
        return
    try:
        result = await _games(context).guess(chat.id, user.id, context.args[0])
    except WordGameError as exc:
        await message.reply_text(str(exc))
        return
    if result.state == "won":
        await message.reply_text(
            f"{result.feedback}\nSolved: {result.answer}. You earned {result.points} points."
        )
    elif result.state == "lost":
        await message.reply_text(
            f"{result.feedback}\nOut of guesses. The word was {result.answer}."
        )
    else:
        await message.reply_text(f"{result.feedback}\n{result.guesses_left} guess(es) remain.")


async def wordcancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user:
        return
    cancelled = await _games(context).cancel(chat.id, user.id)
    await message.reply_text("Word Seek cancelled." if cancelled else "You have no active game.")


async def wordrank_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat = update.effective_message, update.effective_chat
    if not message or not chat:
        return
    leaderboard = await _games(context).leaderboard(chat.id)
    if not leaderboard:
        await message.reply_text("No Word Seek scores in this chat yet.")
        return
    rows = [
        f"{position}. {user_id}: {points} points"
        for position, (user_id, points) in enumerate(leaderboard, 1)
    ]
    await message.reply_text("Word Seek leaderboard\n" + "\n".join(rows))
