"""Persistent locally scored quiz, true/false, riddle, and arithmetic rounds."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.modules.games.casual import (
    CasualGameError,
    CasualGameRepository,
    question_transition,
)


def _games(context: ContextTypes.DEFAULT_TYPE) -> CasualGameRepository:
    return CasualGameRepository(context.application.bot_data["sessions"])


async def _reply(update: Update, text: str) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(text)


async def _start_question_game(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    game_type: str,
    label: str,
    instructions: str,
) -> None:
    chat, user = update.effective_chat, update.effective_user
    if not chat or not user:
        return
    try:
        state = await _games(context).start(chat.id, user.id, game_type)
    except CasualGameError as exc:
        await _reply(update, str(exc))
        return
    await _reply(update, f"{label}: {state['prompt']}\n{instructions}")


async def _answer_question(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    game_type: str,
    usage: str,
) -> None:
    chat, user = update.effective_chat, update.effective_user
    if not chat or not user or len(context.args) != 1:
        await _reply(update, f"Usage: {usage}")
        return
    try:
        turn = await _games(context).submit(
            chat.id,
            user.id,
            game_type,
            question_transition(context.args[0]),
        )
    except CasualGameError as exc:
        await _reply(update, str(exc))
        return
    answer = turn.state["answer"]
    if turn.status == "won":
        text = f"Correct. Answer: {answer}. +{turn.points} points."
    elif turn.status == "lost":
        text = f"No tries left. The answer was {answer}."
    else:
        text = "Not quite. One final try."
    await _reply(update, text)


async def trivia_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _start_question_game(
        update, context, "trivia", "Trivia", "Answer with /triviaanswer <a|b|c>."
    )


async def triviaanswer_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _answer_question(update, context, "trivia", "/triviaanswer <a|b|c>")


async def truefalse_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _start_question_game(
        update, context, "truefalse", "True or False", "Answer with /tfanswer <true|false>."
    )


async def tfanswer_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _answer_question(update, context, "truefalse", "/tfanswer <true|false>")


async def riddle_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _start_question_game(
        update, context, "riddle", "Riddle", "Answer with /riddleanswer <word>."
    )


async def riddleanswer_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _answer_question(update, context, "riddle", "/riddleanswer <word>")


async def quickmath_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _start_question_game(
        update, context, "quickmath", "Quick Math", "Answer with /mathanswer <number>."
    )


async def mathanswer_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _answer_question(update, context, "quickmath", "/mathanswer <number>")


async def questioncancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user = update.effective_chat, update.effective_user
    if not chat or not user or len(context.args) != 1:
        await _reply(update, "Usage: /questioncancel <trivia|truefalse|riddle|quickmath>")
        return
    game_type = context.args[0].casefold()
    if game_type not in {"trivia", "truefalse", "riddle", "quickmath"}:
        await _reply(update, "Unknown question game.")
        return
    cancelled = await _games(context).cancel(chat.id, user.id, game_type)
    await _reply(update, "Game cancelled." if cancelled else "No active game.")
