"""Telegram interfaces for persistent non-AI casual games."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.modules.games.casual import (
    CasualGameError,
    CasualGameRepository,
    hangman_transition,
    number_transition,
    scramble_transition,
)


def _games(context: ContextTypes.DEFAULT_TYPE) -> CasualGameRepository:
    return CasualGameRepository(context.application.bot_data["sessions"])


async def _reply(update: Update, text: str) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(text)


async def hangman_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user:
        return
    try:
        state = await _games(context).start(chat.id, user.id, "hangman")
    except CasualGameError as exc:
        await _reply(update, str(exc))
        return
    await _reply(
        update, f"Hangman started. Word: {'_' * len(str(state['answer']))}. Use /letter <a-z>."
    )


async def letter_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user or len(context.args) != 1:
        await _reply(update, "Usage: /letter <a-z>")
        return
    try:
        turn = await _games(context).submit(
            chat.id, user.id, "hangman", hangman_transition(context.args[0])
        )
    except CasualGameError as exc:
        await _reply(update, str(exc))
        return
    answer = str(turn.state["answer"])
    guessed = set(turn.state["guessed"])
    mask = " ".join(letter if letter in guessed else "_" for letter in answer)
    misses = ", ".join(turn.state["misses"]) or "none"
    if turn.status == "won":
        text = f"{mask}\nSolved: {answer}. +{turn.points} points."
    elif turn.status == "lost":
        text = f"{mask}\nNo guesses left. The word was {answer}."
    else:
        text = f"{mask}\nMisses: {misses} ({6 - len(turn.state['misses'])} left)."
    await _reply(update, text)


async def hangcancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _cancel(update, context, "hangman")


async def scramble_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user:
        return
    try:
        state = await _games(context).start(chat.id, user.id, "scramble")
    except CasualGameError as exc:
        await _reply(update, str(exc))
        return
    await _reply(update, f"Unscramble: {state['scrambled']}. Use /unscramble <word> (3 tries).")


async def unscramble_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user or len(context.args) != 1:
        await _reply(update, "Usage: /unscramble <word>")
        return
    try:
        turn = await _games(context).submit(
            chat.id, user.id, "scramble", scramble_transition(context.args[0])
        )
    except CasualGameError as exc:
        await _reply(update, str(exc))
        return
    if turn.status == "won":
        text = f"Correct: {turn.state['answer']} (+{turn.points} points)."
    elif turn.status == "lost":
        text = f"No tries left. The word was {turn.state['answer']}."
    else:
        text = f"Not quite. {3 - int(turn.state['attempts'])} try/tries remain."
    await _reply(update, text)


async def scramblecancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _cancel(update, context, "scramble")


async def numbergame_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user:
        return
    try:
        await _games(context).start(chat.id, user.id, "number")
    except CasualGameError as exc:
        await _reply(update, str(exc))
        return
    await _reply(update, "Number Guess started (1-100, 7 tries). Use /numberguess <number>.")


async def numberguess_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user or len(context.args) != 1:
        await _reply(update, "Usage: /numberguess <1-100>")
        return
    try:
        guess = int(context.args[0])
        turn = await _games(context).submit(chat.id, user.id, "number", number_transition(guess))
    except (ValueError, CasualGameError) as exc:
        await _reply(update, str(exc) or "Guess a number from 1 to 100.")
        return
    answer = int(turn.state["answer"])
    if turn.status == "won":
        text = f"Correct! {answer} (+{turn.points} points)."
    elif turn.status == "lost":
        text = f"No guesses left. The number was {answer}."
    else:
        text = f"Try {turn.state['last_hint']}. {7 - int(turn.state['attempts'])} guess(es) remain."
    await _reply(update, text)


async def numbercancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _cancel(update, context, "number")


async def _cancel(update: Update, context: ContextTypes.DEFAULT_TYPE, game_type: str) -> None:
    chat, user = update.effective_chat, update.effective_user
    if not chat or not user:
        return
    cancelled = await _games(context).cancel(chat.id, user.id, game_type)
    await _reply(update, f"{game_type.title()} cancelled." if cancelled else "No active game.")


async def gamerank_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return
    game_type = context.args[0].casefold() if len(context.args) == 1 else ""
    if game_type not in {"hangman", "scramble", "number"}:
        await _reply(update, "Usage: /gamerank <hangman|scramble|number>")
        return
    board = await _games(context).leaderboard(chat.id, game_type)
    if not board:
        await _reply(update, f"No {game_type} scores in this chat yet.")
        return
    lines = [f"{rank}. {user_id}: {points}" for rank, (user_id, points) in enumerate(board, 1)]
    await _reply(update, f"{game_type.title()} leaderboard\n" + "\n".join(lines))
