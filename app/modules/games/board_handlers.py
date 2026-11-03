"""Telegram handlers for RPS and multiplayer Tic-Tac-Toe."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.database.models import CasualGameScore, CasualGameSession
from app.modules.games.casual import CasualGameError, CasualGameRepository, rps_transition

GAME_TYPE = "tictactoe_multi"
GAME_TIMEOUT = timedelta(minutes=15)
WIN_LINES = ((0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6))


def _games(context: ContextTypes.DEFAULT_TYPE) -> CasualGameRepository:
    return CasualGameRepository(context.application.bot_data["sessions"])


async def _reply(update: Update, text: str) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(text)


def _board_text(board: list[str]) -> str:
    cells = [str(value) if value != " " else "·" for value in board]
    return "\n".join(" | ".join(cells[start : start + 3]) for start in (0, 3, 6))


def _keyboard(board: list[str], active: bool) -> InlineKeyboardMarkup:
    rows = []
    for start in (0, 3, 6):
        rows.append([
            InlineKeyboardButton(
                board[i] if board[i] != " " else str(i + 1),
                callback_data=f"tttm:{i}" if active and board[i] == " " else "tttm:noop",
            )
            for i in range(start, start + 3)
        ])
    return InlineKeyboardMarkup(rows)


def _won(board: list[str], mark: str) -> bool:
    return any(all(board[i] == mark for i in line) for line in WIN_LINES)


def _render(state: dict[str, object], status: str = "active") -> str:
    x = state.get("x_name", "Player X")
    o = state.get("o_name") or "Waiting for player O"
    turn = state.get("turn", "X")
    result = {
        "active": f"\nTurn: {'X' if turn == 'X' else 'O'}",
        "won": f"\n🏆 {state.get('winner_name', 'Winner')} wins!",
        "tied": "\nIt's a draw!",
        "cancelled": "\nGame cancelled.",
        "expired": "\nGame expired.",
    }.get(status, "")
    return f"⭕ **Tic-Tac-Toe**\n❌ X: {x}\n🔵 O: {o}\n\n{_board_text(list(state['board']))}{result}"


async def rps_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user = update.effective_chat, update.effective_user
    if not chat or not user or len(context.args) != 1:
        await _reply(update, "Usage: /rps <rock|paper|scissors>")
        return
    try:
        repo = _games(context)
        transition = rps_transition(context.args[0])
        await repo.start(chat.id, user.id, "rps")
        turn = await repo.submit(chat.id, user.id, "rps", transition)
    except CasualGameError as exc:
        await _reply(update, str(exc))
        return
    outcome = "You win (+1 point)." if turn.status == "won" else "It's a tie." if turn.status == "tied" else "XYRON wins."
    await _reply(update, f"You: {turn.state['player_choice']}; XYRON: {turn.state['answer']}. {outcome}")


async def tictactoe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user, message = update.effective_chat, update.effective_user, update.effective_message
    if not chat or not user or not message:
        return
    sessions = context.application.bot_data["sessions"]
    now = datetime.now(UTC)
    key = f"{chat.id}:tictactoe:multiplayer"
    async with sessions.begin() as db:
        game = await db.scalar(select(CasualGameSession).where(CasualGameSession.active_key == key).with_for_update())
        if game and game.expires_at <= now:
            game.status, game.active_key = "expired", None
            game = None
        if game is None:
            state = {"board": [" "] * 9, "x_id": user.id, "x_name": user.full_name, "o_id": None, "o_name": None, "turn": "X"}
            game = CasualGameSession(chat_id=chat.id, user_id=user.id, game_type=GAME_TYPE, active_key=key, state=state, status="active", created_at=now, expires_at=now + GAME_TIMEOUT)
            db.add(game)
            response = "created"
        else:
            state = dict(game.state)
            if state.get("x_id") == user.id or state.get("o_id") == user.id:
                response = "already"
            elif state.get("o_id") is None:
                state.update({"o_id": user.id, "o_name": user.full_name})
                game.state = state
                game.expires_at = now + GAME_TIMEOUT
                response = "joined"
            else:
                response = "full"
            state = dict(game.state)
    if response == "full":
        await _reply(update, "A Tic-Tac-Toe match is already in progress. Wait for it to finish.")
        return
    if response == "already":
        await _reply(update, "You're already in this match. Use the board buttons to play.")
        return
    if response == "created":
        text = _render(state).replace("**", "")
        text = "🎮 Tic-Tac-Toe lobby created!\n❌ X: " + user.full_name + "\nWaiting for a second player. They can join with /tictactoe."
    else:
        text = "🎮 Match started!\n" + _render(state).replace("**", "")
    board_message = await message.reply_text(text, reply_markup=_keyboard(list(state["board"]), response == "joined"))
    async with sessions.begin() as db:
        game = await db.scalar(select(CasualGameSession).where(CasualGameSession.active_key == key).with_for_update())
        if game:
            latest = dict(game.state)
            latest["board_message_id"] = board_message.message_id
            game.state = latest


async def ttt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.message or not query.from_user:
        return
    data = query.data or ""
    if data == "tttm:noop":
        await query.answer()
        return
    try:
        position = int(data.split(":", 1)[1])
    except (ValueError, IndexError):
        return
    chat_id = query.message.chat_id
    sessions = context.application.bot_data["sessions"]
    key = f"{chat_id}:tictactoe:multiplayer"
    now = datetime.now(UTC)
    error = None
    async with sessions.begin() as db:
        game = await db.scalar(select(CasualGameSession).where(CasualGameSession.active_key == key).with_for_update())
        if game is None or game.expires_at <= now:
            error = "This match has ended or expired. Start another with /tictactoe."
            state, status = None, "expired"
        else:
            state = dict(game.state)
            player_mark = "X" if query.from_user.id == state["x_id"] else "O" if query.from_user.id == state["o_id"] else None
            if state.get("o_id") is None:
                error = "Waiting for the second player to join."
            elif player_mark is None:
                error = "You're not a player in this match."
            elif player_mark != state["turn"]:
                error = "It's not your turn."
            elif not 0 <= position <= 8 or state["board"][position] != " ":
                error = "That square is unavailable."
            else:
                board = list(state["board"])
                board[position] = player_mark
                state["board"] = board
                if _won(board, player_mark):
                    status = "won"
                    state["winner_name"] = state["x_name"] if player_mark == "X" else state["o_name"]
                    game.status, game.active_key = "won", None
                    db.add(CasualGameScore(session_id=game.id, chat_id=chat_id, user_id=query.from_user.id, game_type="tictactoe", points=10, created_at=now))
                elif " " not in board:
                    status = "tied"
                    game.status, game.active_key = "tied", None
                else:
                    state["turn"] = "O" if player_mark == "X" else "X"
                    status = "active"
                game.state = state
                game.expires_at = now + GAME_TIMEOUT
    if error:
        await query.answer(error, show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(_render(state, status), reply_markup=_keyboard(list(state["board"]), status == "active"))


async def tttmove_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, "Tic-Tac-Toe now uses the inline board. Start or join with /tictactoe.")


async def tttcancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user = update.effective_chat, update.effective_user
    if not chat or not user:
        return
    key = f"{chat.id}:tictactoe:multiplayer"
    sessions = context.application.bot_data["sessions"]
    cancelled = False
    async with sessions.begin() as db:
        game = await db.scalar(select(CasualGameSession).where(CasualGameSession.active_key == key).with_for_update())
        if game and user.id in {game.state.get("x_id"), game.state.get("o_id")}:
            game.status, game.active_key = "cancelled", None
            cancelled = True
    await _reply(update, "Tic-Tac-Toe cancelled." if cancelled else "No active match to cancel.")


async def tttend_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """End the active match when a participant replies to its board with /end."""
    chat, user, message = update.effective_chat, update.effective_user, update.effective_message
    if not chat or not user or not message or not message.reply_to_message:
        await _reply(update, "Reply to the active Tic-Tac-Toe board with /end to end that match.")
        return
    target = message.reply_to_message
    key = f"{chat.id}:tictactoe:multiplayer"
    sessions = context.application.bot_data["sessions"]
    ended = False
    async with sessions.begin() as db:
        game = await db.scalar(select(CasualGameSession).where(CasualGameSession.active_key == key).with_for_update())
        if game:
            state = dict(game.state)
            players = {state.get("x_id"), state.get("o_id")}
            if user.id not in players:
                reason = "Only a player in this match can end it."
            elif state.get("board_message_id") != target.message_id or target.from_user is None or target.from_user.id != context.bot.id:
                reason = "Reply directly to the current Tic-Tac-Toe board message with /end."
            else:
                game.status = "cancelled"
                game.active_key = None
                state["ended_by"] = user.full_name
                game.state = state
                ended = True
                reason = ""
        else:
            reason = "There is no active Tic-Tac-Toe match to end."
    if ended:
        try:
            await context.bot.edit_message_text(
                chat_id=chat.id,
                message_id=target.message_id,
                text=_render(state, "cancelled") + f"\nEnded by: {user.full_name}",
                reply_markup=None,
            )
        except Exception:
            pass
        await _reply(update, "Tic-Tac-Toe match ended.")
    else:
        await _reply(update, reason)
