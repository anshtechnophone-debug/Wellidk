"""Telegram commands for a local, per-user Word Chain challenge."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.modules.games.casual import CasualGameError, CasualGameRepository, wordchain_transition


def _games(context: ContextTypes.DEFAULT_TYPE) -> CasualGameRepository:
    return CasualGameRepository(context.application.bot_data["sessions"])


async def _reply(update: Update, text: str) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(text)


async def wordchain_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user = update.effective_chat, update.effective_user
    if not chat or not user:
        return
    try:
        state = await _games(context).start(chat.id, user.id, "wordchain")
    except CasualGameError as exc:
        await _reply(update, str(exc))
        return
    await _reply(
        update,
        f"Word Chain started with '{state['last_word']}'. "
        "Give a new valid word that starts with the previous word's last letter: /chain <word>. "
        "Complete 10 links before the 15-minute timer expires.",
    )


async def chain_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user = update.effective_chat, update.effective_user
    if not chat or not user or len(context.args) != 1:
        await _reply(update, "Usage: /chain <word>")
        return
    try:
        turn = await _games(context).submit(
            chat.id, user.id, "wordchain", wordchain_transition(context.args[0])
        )
    except CasualGameError as exc:
        await _reply(update, str(exc))
        return
    if turn.status == "won":
        text = f"Chain complete at 10 links. +{turn.points} points."
    else:
        next_letter = str(turn.state["last_word"])[-1]
        text = f"Link {turn.state['links']}/10 accepted. "
        text += f"Next word starts with '{next_letter}'."
    await _reply(update, text)


async def wordchaincancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user = update.effective_chat, update.effective_user
    if not chat or not user:
        return
    cancelled = await _games(context).cancel(chat.id, user.id, "wordchain")
    await _reply(update, "Word Chain cancelled." if cancelled else "No active Word Chain game.")
