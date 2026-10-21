"""Single-attempt local Anagram challenge commands."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.modules.games.casual import CasualGameError, CasualGameRepository, anagram_transition


async def anagram_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user = update.effective_chat, update.effective_user
    if not chat or not user:
        return
    try:
        state = await CasualGameRepository(context.application.bot_data["sessions"]).start(
            chat.id, user.id, "anagram"
        )
    except CasualGameError as exc:
        await update.effective_message.reply_text(str(exc))
        return
    await update.effective_message.reply_text(
        f"Anagram challenge: {state['scrambled']}. One answer: /anagramanswer <word>."
    )


async def anagramanswer_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user = update.effective_chat, update.effective_user
    if not chat or not user or len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /anagramanswer <word>")
        return
    try:
        turn = await CasualGameRepository(context.application.bot_data["sessions"]).submit(
            chat.id, user.id, "anagram", anagram_transition(context.args[0])
        )
    except CasualGameError as exc:
        await update.effective_message.reply_text(str(exc))
        return
    if turn.status == "won":
        text = f"Correct: {turn.state['answer']} (+{turn.points} points)."
    else:
        text = f"Incorrect. The word was {turn.state['answer']}."
    await update.effective_message.reply_text(text)
