"""Telegram commands and inline multiplayer Tic-Tac-Toe."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from app.database.models import (
    BroadcastRecipient,
    CasualGameScore,
    CasualGameSession,
    GlobalUser,
)
from app.modules.games.casual import CasualGameError, CasualGameRepository, rps_transition

_TTT_ACTIVE_KEY = lambda chat_id: f"{chat_id}:tictactoe"
_TTT_TIMEOUT = timedelta(minutes=15)


def _games(context: ContextTypes.DEFAULT_TYPE) -> CasualGameRepository:
    return CasualGameRepository(context.application.bot_data["sessions"])


async def _reply(update: Update, text: str) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(text)


def _winner(board: list[str], mark: str) -> bool:
    lines = (
        (0, 1, 2), (3, 4, 5), (6, 7, 8),
        (0, 3, 6), (1, 4, 7), (2, 5, 8),
        (0, 4, 8), (2, 4, 6),
    )
    return any(all(board[index] == mark for index in line) for line in lines)


def _mark_for(state: dict, user_id: int) -> str | None:
    if user_id == state.get("player1_id"):
        return "X"
    if user_id == state.get("player2_id"):
        return "O"
    return None


def _ttt_text(game: CasualGameSession) -> str:
    state = dict(game.state)
    p1 = state.get("player1_name", "Player 1")
    p2 = state.get("player2_name") or "Waiting for an opponent…"
    if game.status == "active" and state.get("player2_id"):
        turn_mark = _mark_for(state, int(state["turn"]))
        turn_name = p1 if turn_mark == "X" else p2
        status = f"🎯 Turn: {turn_name} ({turn_mark})"
    elif game.status == "won":
        status = f"🏆 {state.get('winner_name', 'Player')} wins!"
    elif game.status == "tied":
        status = "🤝 It's a draw!"
    elif game.status == "cancelled":
        status = "Game cancelled."
    else:
        status = "Tap Join to play against the challenger."
    return (
        "🎮 **TIC-TAC-TOE**\n\n"
        f"❎ X — {p1}\n"
        f"⭕ O — {p2}\n\n"
        f"{status}"
    )


def _ttt_keyboard(game: CasualGameSession) -> InlineKeyboardMarkup | None:
    state = dict(game.state)
    if game.status == "waiting":
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("🎮 Join Game", callback_data=f"ttt:join:{game.id}")
        ]])
    if game.status != "active" or not state.get("player2_id"):
        return None
    board = list(state["board"])
    rows = []
    for start in (0, 3, 6):
        rows.append([
            InlineKeyboardButton(
                board[index] if board[index] != " " else "▫️",
                callback_data=f"ttt:move:{game.id}:{index}",
            )
            for index in range(start, start + 3)
        ])
    return InlineKeyboardMarkup(rows)


async def _get_active_ttt(session, chat_id: int):
    return await session.scalar(
        select(CasualGameSession).where(
            CasualGameSession.active_key == _TTT_ACTIVE_KEY(chat_id),
            CasualGameSession.game_type == "tictactoe",
        )
    )


async def _private_start_seen(sessions, user_id: int) -> bool:
    async with sessions() as session:
        global_user = await session.get(GlobalUser, user_id)
        if global_user and global_user.seen_in_private:
            return True
        return await session.get(BroadcastRecipient, user_id) is not None


async def _join_game(sessions, game_id: int, user_id: int, full_name: str):
    now = datetime.now(UTC)
    async with sessions.begin() as session:
        game = await session.get(CasualGameSession, game_id)
        if not game or game.game_type != "tictactoe" or game.status != "waiting":
            return None, "This Tic-Tac-Toe invitation has expired or is no longer waiting."
        if game.expires_at <= now:
            game.status = "expired"
            game.active_key = None
            return None, "This Tic-Tac-Toe invitation has expired."
        state = dict(game.state)
        if user_id == int(state["player1_id"]):
            return None, "You started this game. Wait for another player to join."
        if state.get("player2_id"):
            return None, "Someone has already joined this game."
        state["player2_id"] = user_id
        state["player2_name"] = full_name
        state["turn"] = int(state["player1_id"])
        game.state = state
        game.status = "active"
        return game.id, None


async def _refresh_ttt_message(context, game_id: int) -> None:
    sessions = context.application.bot_data["sessions"]
    async with sessions() as session:
        game = await session.get(CasualGameSession, game_id)
        if not game:
            return
        state = dict(game.state)
        message_id = state.get("message_id")
        chat_id = game.chat_id
        text = _ttt_text(game)
        markup = _ttt_keyboard(game)
    if message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=int(message_id),
                text=text,
                reply_markup=markup,
                parse_mode="Markdown",
            )
        except TelegramError:
            pass


async def tictactoe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user, message = update.effective_chat, update.effective_user, update.effective_message
    if not chat or not user or not message:
        return
    if chat.type not in {"group", "supergroup"}:
        await message.reply_text("Start Tic-Tac-Toe in a group so another member can join.")
        return

    sessions = context.application.bot_data["sessions"]
    now = datetime.now(UTC)
    async with sessions.begin() as session:
        old = await _get_active_ttt(session, chat.id)
        if old and old.expires_at > now:
            await message.reply_text("A Tic-Tac-Toe game is already active in this group.")
            return
        if old:
            old.status = "expired"
            old.active_key = None
        game = CasualGameSession(
            chat_id=chat.id,
            user_id=user.id,
            game_type="tictactoe",
            active_key=_TTT_ACTIVE_KEY(chat.id),
            state={
                "player1_id": user.id,
                "player1_name": user.full_name,
                "player2_id": None,
                "player2_name": None,
                "turn": user.id,
                "board": [" "] * 9,
            },
            status="waiting",
            created_at=now,
            expires_at=now + _TTT_TIMEOUT,
        )
        session.add(game)
        await session.flush()
        game_id = game.id

    async with sessions() as session:
        game = await session.get(CasualGameSession, game_id)
        text = _ttt_text(game)
        markup = _ttt_keyboard(game)
    sent = await message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
    async with sessions.begin() as session:
        game = await session.get(CasualGameSession, game_id)
        if game:
            state = dict(game.state)
            state["message_id"] = sent.message_id
            game.state = state


async def ttt_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not query.data or not user:
        return
    parts = query.data.split(":")
    if len(parts) < 3:
        await query.answer("This game button is invalid.", show_alert=True)
        return
    action, game_id = parts[1], int(parts[2])
    sessions = context.application.bot_data["sessions"]

    if action == "join":
        if not await _private_start_seen(sessions, user.id):
            me = await context.bot.get_me()
            url = f"https://t.me/{me.username}?start=tttj_{game_id}"
            await query.answer("Start XYRON once in private, then you'll be added automatically.")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"{user.mention_html()}, start XYRON to join this game, then return here.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("Open XYRON", url=url)
                ]]),
                reply_to_message_id=query.message.message_id,
            )
            return
        joined_id, error = await _join_game(sessions, game_id, user.id, user.full_name)
        if error:
            await query.answer(error, show_alert=True)
            return
        await query.answer("You're in! You are O.")
        await _refresh_ttt_message(context, joined_id)
        return

    if action != "move" or len(parts) != 4:
        await query.answer("Unknown game action.", show_alert=True)
        return
    position = int(parts[3])
    if not 0 <= position <= 8:
        await query.answer("Invalid square.", show_alert=True)
        return

    now = datetime.now(UTC)
    result_text = None
    async with sessions.begin() as session:
        game = await session.get(CasualGameSession, game_id)
        if not game or game.game_type != "tictactoe" or game.status != "active":
            result_text = "This game is no longer active."
        elif game.expires_at <= now:
            game.status = "expired"
            game.active_key = None
            result_text = "This game has expired."
        else:
            state = dict(game.state)
            mark = _mark_for(state, user.id)
            if mark is None:
                result_text = "You're not a player in this game."
            elif int(state["turn"]) != user.id:
                result_text = "It's not your turn."
            else:
                board = list(state["board"])
                if board[position] != " ":
                    result_text = "That square is already taken."
                else:
                    board[position] = mark
                    state["board"] = board
                    if _winner(board, mark):
                        game.status = "won"
                        game.active_key = None
                        state["winner_name"] = state["player1_name"] if mark == "X" else state["player2_name"]
                        session.add(CasualGameScore(
                            session_id=game.id,
                            chat_id=game.chat_id,
                            user_id=user.id,
                            game_type="tictactoe",
                            points=10,
                            created_at=now,
                        ))
                    elif " " not in board:
                        game.status = "tied"
                        game.active_key = None
                    else:
                        state["turn"] = int(state["player2_id"] if mark == "X" else state["player1_id"])
                    game.state = state
    if result_text:
        await query.answer(result_text, show_alert=True)
        return
    await query.answer()
    await _refresh_ttt_message(context, game_id)


async def join_ttt_from_start(update: Update, context: ContextTypes.DEFAULT_TYPE, game_id: int) -> None:
    message, user = update.effective_message, update.effective_user
    if not message or not user:
        return
    joined_id, error = await _join_game(
        context.application.bot_data["sessions"], game_id, user.id, user.full_name
    )
    if error:
        await message.reply_text(error)
        return
    await message.reply_text("✅ You're in! Return to the group — you're O, and X goes first.")
    await _refresh_ttt_message(context, joined_id)


async def tttmove_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, "Use the Tic-Tac-Toe inline buttons to make your move.")


async def tttcancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user = update.effective_chat, update.effective_user
    if not chat or not user:
        return
    sessions = context.application.bot_data["sessions"]
    async with sessions.begin() as session:
        game = await _get_active_ttt(session, chat.id)
        if not game:
            cancelled = False
        else:
            state = dict(game.state)
            if user.id not in {state.get("player1_id"), state.get("player2_id")}:
                cancelled = False
            else:
                game.status = "cancelled"
                game.active_key = None
                cancelled = True
                game_id = game.id
    if not cancelled:
        await _reply(update, "No active Tic-Tac-Toe game you can cancel.")
        return
    await _refresh_ttt_message(context, game_id)
    await _reply(update, "Tic-Tac-Toe cancelled.")


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
