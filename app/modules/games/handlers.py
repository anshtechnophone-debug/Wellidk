"""Telegram commands and plain-message handling for WordSeek."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.modules.games.wordseek import WordGameError, WordSeekRepository


def _games(context: ContextTypes.DEFAULT_TYPE) -> WordSeekRepository:
    return WordSeekRepository(context.application.bot_data["sessions"])


async def _start(update: Update, context: ContextTypes.DEFAULT_TYPE, length: int) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user:
        return
    try:
        game = await _games(context).start(chat.id, user.id, length)
    except WordGameError as exc:
        await message.reply_text(str(exc))
        return
    await message.reply_text(
        f"🧩 <b>WORD SEEK · {length} LETTERS</b>\n\n"
        f"Guess the hidden {length}-letter word in up to 30 attempts.\n"
        "🟩 Correct spot   🟨 Wrong spot   🟥 Not in word\n"
        "Everyone in this chat can play: type a guess directly in the chat.\n"
        "Use /wordcancel to stop.",
        parse_mode="HTML",
    )


async def wordseek_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) > 1:
        await update.effective_message.reply_text("Usage: /wordseek [4|5|6]")
        return
    try:
        length = int(context.args[0]) if context.args else 5
    except ValueError:
        await update.effective_message.reply_text("Choose /new4, /new5 or /new6.")
        return
    await _start(update, context, length)


async def new4_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _start(update, context, 4)


async def new5_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _start(update, context, 5)


async def new6_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _start(update, context, 6)


async def _submit_guess(update: Update, context: ContextTypes.DEFAULT_TYPE, value: str) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user:
        return
    try:
        result = await _games(context).guess(chat.id, user.id, value)
    except WordGameError as exc:
        if "No active Word Seek game" in str(exc):
            return
        await message.reply_text(str(exc))
        return
    rows = [f"{feedback}  <b>{word.upper()}</b>"
            for feedback, word in zip(result.feedbacks, result.guesses, strict=True)]
    board = f"<b>{len(value)}-letter mode · {len(result.guesses)}/30</b>\n" + "\n".join(rows)
    if result.state == "won":
        board += f"\n\n🎉 <b>Excellent! You found {result.answer.upper()}!</b>\n⭐ Added {result.points} points to the leaderboard.\nStart again: /new{len(value)}"
    elif result.state == "lost":
        board += f"\n\nThe word was <b>{result.answer.upper()}</b>.\nTry again: /new{len(value)}"
    await message.reply_text(f"👤 {user.full_name} guessed:\n" + board, parse_mode="HTML")


async def guess_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /guess <word>")
        return
    await _submit_guess(update, context, context.args[0])


async def wordseek_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.text or message.text.startswith("/"):
        return
    value = message.text.strip()
    if value.isalpha() and len(value) in (4, 5, 6):
        await _submit_guess(update, context, value)


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
    rows = [f"{position}. <code>{user_id}</code> — {points} points"
            for position, (user_id, points) in enumerate(leaderboard, 1)]
    await message.reply_text("<b>🏆 WordSeek leaderboard</b>\n" + "\n".join(rows),
                             parse_mode="HTML")
