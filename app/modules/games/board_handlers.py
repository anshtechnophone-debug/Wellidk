"""Telegram commands for local RPS and Tic-Tac-Toe."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.modules.games.casual import (
    CasualGameError,
    CasualGameRepository,
    rps_transition,
    tictactoe_transition,
)


def _games(context: ContextTypes.DEFAULT_TYPE) -> CasualGameRepository:
    return CasualGameRepository(context.application.bot_data["sessions"])


async def _reply(update: Update, text: str) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(text)


def _board_text(board: list[str]) -> str:
    cells = [str(value) if value != " " else str(index + 1) for index, value in enumerate(board)]
    return "\n".join(" | ".join(cells[start : start + 3]) for start in (0, 3, 6))


async def rps_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user = update.effective_chat, update.effective_user
    if not chat or not user or len(context.args) != 1:
        await _reply(update, "Usage: /rps <rock|paper|scissors>")
        return
    repository = _games(context)
    try:
        transition = rps_transition(context.args[0])
        await repository.start(chat.id, user.id, "rps")
        turn = await repository.submit(chat.id, user.id, "rps", transition)
    except CasualGameError as exc:
        await _reply(update, str(exc))
        return
    player = turn.state["player_choice"]
    bot = turn.state["answer"]
    outcome = (
        "You win (+1 point)."
        if turn.status == "won"
        else "It's a tie."
        if turn.status == "tied"
        else "XYRON wins."
    )
    await _reply(update, f"You: {player}; XYRON: {bot}. {outcome}")


async def tictactoe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user = update.effective_chat, update.effective_user
    if not chat or not user:
        return
    try:
        await _games(context).start(chat.id, user.id, "tictactoe")
    except CasualGameError as exc:
        await _reply(update, str(exc))
        return
    await _reply(
        update, "Tic-Tac-Toe started. You are X. Use /tttmove <1-9>.\n" + _board_text([" "] * 9)
    )


async def tttmove_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user = update.effective_chat, update.effective_user
    if not chat or not user or len(context.args) != 1 or not context.args[0].isdecimal():
        await _reply(update, "Usage: /tttmove <1-9>")
        return
    try:
        turn = await _games(context).submit(
            chat.id, user.id, "tictactoe", tictactoe_transition(int(context.args[0]))
        )
    except CasualGameError as exc:
        await _reply(update, str(exc))
        return
    board = _board_text(list(turn.state["board"]))
    text = (
        "You win (+10 points)."
        if turn.status == "won"
        else "XYRON wins."
        if turn.status == "lost"
        else "It's a draw."
        if turn.status == "tied"
        else "Your turn."
    )
    await _reply(update, f"{board}\n{text}")


async def tttcancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user = update.effective_chat, update.effective_user
    if not chat or not user:
        return
    cancelled = await _games(context).cancel(chat.id, user.id, "tictactoe")
    await _reply(update, "Tic-Tac-Toe cancelled." if cancelled else "No active Tic-Tac-Toe game.")
