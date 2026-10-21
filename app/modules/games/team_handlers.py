"""Group-wide Word Chain-style cooperative word guessing."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.modules.games.casual import CasualGameError, CasualGameRepository, teamword_transition


def _games(context: ContextTypes.DEFAULT_TYPE) -> CasualGameRepository:
    return CasualGameRepository(context.application.bot_data["sessions"])


async def _reply(update: Update, text: str) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(text)


async def teamword_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user = update.effective_chat, update.effective_user
    if not chat or not user:
        return
    try:
        await _games(context).start(chat.id, user.id, "teamword")
    except CasualGameError as exc:
        await _reply(update, str(exc))
        return
    await _reply(
        update,
        "Team Word started for this chat. Everyone may contribute up to 8 guesses. "
        "Length: 5. Use /teamguess <word>.",
    )


async def teamguess_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user = update.effective_chat, update.effective_user
    if not chat or not user or len(context.args) != 1:
        await _reply(update, "Usage: /teamguess <five-letter word>")
        return
    try:
        turn = await _games(context).submit(
            chat.id,
            user.id,
            "teamword",
            teamword_transition(context.args[0], user.id),
        )
    except CasualGameError as exc:
        await _reply(update, str(exc))
        return
    if turn.status == "won":
        text = f"{turn.state['feedback']}\n"
        text += f"Team solved it: {turn.state['answer']}. +{turn.points} points to solver."
    elif turn.status == "lost":
        text = f"{turn.state['feedback']}\nOut of guesses. The word was {turn.state['answer']}."
    else:
        remaining = 8 - len(turn.state["guesses"])
        text = f"{turn.state['feedback']}\n{remaining} team guess(es) remain."
    await _reply(update, text)


async def teamwordcancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user = update.effective_chat, update.effective_user
    if not chat or not user:
        return
    cancelled = await _games(context).cancel(chat.id, user.id, "teamword")
    await _reply(update, "Team Word cancelled." if cancelled else "No active Team Word game.")
