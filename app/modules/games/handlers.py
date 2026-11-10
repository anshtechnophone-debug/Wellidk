"""Telegram commands and plain-message handling for WordSeek."""

from __future__ import annotations

from html import escape
import logging
import secrets

import httpx

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)
# HTTP request logs include full Bot API URLs, which contain the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)

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
        "The starter or a group admin can use /wordcancel.",
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


_ZERO_WIDTH_CHARS = ("\\u200b", "\\u200c", "\\u200d", "\\u2060", "\\ufeff")
_TILE_STYLES = {"🟩": "success", "🟨": None, "🟥": "danger"}


def _zero_width_noise(minimum: int = 1, maximum: int = 6) -> str:
    count = secrets.randbelow(maximum - minimum + 1) + minimum
    return "".join(secrets.choice(_ZERO_WIDTH_CHARS) for _ in range(count))


def build_wordseek_rich_feedback(
    length: int,
    guesses: tuple[str, ...],
    feedbacks: tuple[str, ...],
    max_guesses: int = 30,
    ending_text: str = "",
) -> str:
    """Build WordSeek's text-only colored-tile layout using Telegram Rich Messages."""
    rows = []
    for guess, feedback in zip(guesses, feedbacks):
        buttons = []
        for letter, state in zip(guess.upper(), feedback):
            style = _TILE_STYLES.get(state)
            style_attr = f' style="{style}"' if style else ""
            label = f"{_zero_width_noise()}{escape(letter)}{_zero_width_noise()}"
            buttons.append(f'<tg-button type="disabled"{style_attr}>{label}</tg-button>')
        rows.append(f"<p>{''.join(buttons)}</p>")

    header = (
        f"<h3>WORDSEEK · {length}-LETTER MODE</h3>"
        f"<p><i>{len(guesses)}/{max_guesses} guesses</i></p>"
    )
    legend = (
        "<p><b>Green:</b> correct position · "
        "<b>Default:</b> correct letter, wrong position · "
        "<b>Red:</b> letter not in the word</p>"
    )
    return header + "".join(rows) + legend + (f"<p>{ending_text}</p>" if ending_text else "")


async def _send_wordseek_rich_message(
    message, context: ContextTypes.DEFAULT_TYPE, html_text: str
) -> None:
    """Call Bot API 10.3 sendRichMessage, which PTB 22.8 does not wrap yet."""
    payload = {
        "chat_id": message.chat_id,
        "rich_message": {"html": html_text},
        "reply_parameters": {"message_id": message.message_id},
    }
    if message.message_thread_id is not None:
        payload["message_thread_id"] = message.message_thread_id

    url = f"{context.bot.base_url}{context.bot.token}/sendRichMessage"
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        result = response.json()
    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Telegram rejected the rich message"))


def build_wordseek_inline_keyboard(
    guesses: tuple[str, ...], feedbacks: tuple[str, ...]
) -> dict[str, list[list[dict[str, object]]]]:
    """Build non-interactive, colored letter tiles using standard Telegram keyboards."""
    rows: list[list[dict[str, object]]] = []
    styles = {"🟩": "success", "🟥": "danger"}
    for guess, feedback in zip(guesses, feedbacks):
        row: list[dict[str, object]] = []
        for letter, state in zip(guess.upper(), feedback):
            tile: dict[str, object] = {"text": letter, "disabled": {}}
            style = styles.get(state)
            if style:
                tile["style"] = style
            elif state == "🟨":
                # Telegram has no yellow button style; retain a visible yellow tile glyph.
                tile["text"] = f"🟨 {letter}"
            row.append(tile)
        if row:
            rows.append(row)
    return {"inline_keyboard": rows}


def _plain_wordseek_fallback(
    length: int, guesses: tuple[str, ...], feedbacks: tuple[str, ...], ending_text: str = ""
) -> str:
    lines = [
        f"<b>WORDSEEK · {length}-LETTER MODE</b>",
        f"<i>{len(guesses)}/30 guesses</i>",
        "<i>Green: correct · Yellow: wrong position · Red: not in word</i>",
    ]
    if ending_text:
        lines.append(ending_text)
    return "\n".join(lines)


async def _send_wordseek_keyboard_fallback(
    message, context: ContextTypes.DEFAULT_TYPE, text: str,
    guesses: tuple[str, ...], feedbacks: tuple[str, ...],
) -> None:
    payload = {
        "chat_id": message.chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": build_wordseek_inline_keyboard(guesses, feedbacks),
        "reply_parameters": {"message_id": message.message_id},
    }
    if message.message_thread_id is not None:
        payload["message_thread_id"] = message.message_thread_id

    url = f"{context.bot.base_url}{context.bot.token}/sendMessage"
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(url, json=payload)
    try:
        result = response.json()
    except ValueError:
        result = {}
    if not response.is_success or not result.get("ok"):
        description = result.get("description", "unknown Telegram API error")
        raise RuntimeError(
            f"Telegram colored-tile fallback failed (HTTP {response.status_code}): {description}"
        )


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

    ending_text = ""
    if result.state == "won":
        ending_text = (
            f"<b>Excellent! {escape(user.full_name)} found the word.</b> "
            f"Added {result.points} points to the leaderboard. Start again with /new{len(value)}."
        )
    elif result.state == "lost":
        answer = escape(result.answer.upper() if result.answer else "")
        ending_text = f"Game over. The word was <b>{answer}</b>. Try again with /new{len(value)}."

    rich_html = build_wordseek_rich_feedback(
        len(value), result.guesses, result.feedbacks, max_guesses=30, ending_text=ending_text
    )
    try:
        await _send_wordseek_rich_message(message, context, rich_html)
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        # Never log the exception itself: HTTP client exceptions can contain the bot token URL.
        logger.warning(
            "Telegram Rich Message unavailable (%s); using colored inline-keyboard tiles",
            type(exc).__name__,
        )
        fallback = _plain_wordseek_fallback(
            len(value), result.guesses, result.feedbacks, ending_text=ending_text
        )
        await _send_wordseek_keyboard_fallback(
            message, context, fallback, result.guesses, result.feedbacks
        )


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
    is_admin = False
    if chat.type in ("group", "supergroup"):
        try:
            member = await context.bot.get_chat_member(chat.id, user.id)
            is_admin = member.status in ("administrator", "creator")
        except Exception:
            is_admin = False
    cancelled = await _games(context).cancel(chat.id, user.id, is_admin=is_admin)
    if cancelled:
        await message.reply_text("Word Seek cancelled.")
    else:
        await message.reply_text("No active game to cancel, or only the starter and group admins can cancel it.")


def _ranking_keyboard(period: str = "all", scope: str = "chat", length: int = 4) -> InlineKeyboardMarkup:
    periods = [("day", "Daily"), ("week", "Weekly"), ("month", "Monthly"), ("all", "All time")]
    scopes = [("chat", "This chat"), ("global", "Global")]
    lengths = [(4, "4 letter"), (5, "5 letter"), (6, "6 letter")]
    def row(items, selected, prefix):
        return [InlineKeyboardButton(("✓ " if value == selected else "") + label,
                                     callback_data=f"wr:{prefix}:{value}") for value, label in items]
    return InlineKeyboardMarkup([
        row(periods, period, "p"),
        row(scopes, scope, "s"),
        row(lengths, length, "l"),
    ])


async def _ranking_name(context, chat_id: int, user_id: int) -> str:
    """Resolve a leaderboard user to a real display name when Telegram/history knows it."""
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.user.full_name:
            return member.user.full_name
    except Exception:
        pass
    sessions = context.application.bot_data.get("sessions")
    if sessions is not None:
        try:
            from sqlalchemy import select
            from app.services.group_history import GroupMessage

            async with sessions() as db:
                row = await db.execute(
                    select(GroupMessage.display_name, GroupMessage.username)
                    .where(
                        GroupMessage.chat_id == chat_id,
                        GroupMessage.user_id == user_id,
                    )
                    .order_by(GroupMessage.sent_at.desc())
                    .limit(1)
                )
                found = row.first()
            if found:
                display_name, username = found
                if display_name:
                    return display_name
                if username:
                    return f"@{username}"
        except Exception:
            pass
    return f"User {user_id}"


async def _ranking_text(context, chat_id: int, period: str, scope: str, length: int) -> str:
    rows = await _games(context).leaderboard(
        chat_id if scope == "chat" else None, period=period, length=length
    )
    title = (
        f"🏆 <b>WordSeek leaderboard</b>\n"
        f"📅 {dict(day='Daily', week='Weekly', month='Monthly', all='All time')[period]}"
        f"  ·  🌐 {'This chat' if scope == 'chat' else 'Global'}"
        f"  ·  🔤 {length}-letter"
    )
    if not rows:
        return title + "\n\nNo scores for these filters yet."
    names = []
    for position, (user_id, points) in enumerate(rows, 1):
        label = await _ranking_name(context, chat_id, user_id)
        names.append(
            f'{position}. <a href="tg://user?id={user_id}">{escape(label)}</a> — <b>{points}</b> points'
        )
    return title + "\n\n" + "\n".join(names)


async def _send_wordrank(target, context, period="all", scope="chat", length=4):
    chat = target.chat
    if not chat:
        return
    context.user_data["wordrank_filters"] = (period, scope, length)
    body = await _ranking_text(context, chat.id, period, scope, length)
    await target.reply_text(
        body,
        parse_mode="HTML",
        reply_markup=_ranking_keyboard(period, scope, length),
    )


async def wordstate_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user:
        return
    state = await _games(context).player_state(None, user.id)
    rank = f"#{state['rank']} of {state['players']}" if state["rank"] is not None else "Unranked"
    await message.reply_text(
        "🧩 <b>YOUR WORDSEEK STATE</b>\n\n"
        f"🏆 Global rank: <b>{rank}</b>\n"
        f"⭐ Total points: <b>{state['points']}</b>\n"
        f"✅ Words solved: <b>{state['wins']}</b>\n\n"
        "Use /wordrank to open the full leaderboard.",
        parse_mode="HTML",
    )


async def wordrank_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_wordrank(update.effective_message, context)


async def wordrank_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()
    parts = query.data.split(":")
    if len(parts) != 3 or parts[0] != "wr":
        return
    period, scope, length = context.user_data.get("wordrank_filters", ("all", "chat", 4))
    if parts[1] == "p" and parts[2] in {"day", "week", "month", "all"}:
        period = parts[2]
    elif parts[1] == "s" and parts[2] in {"chat", "global"}:
        scope = parts[2]
    elif parts[1] == "l" and parts[2] in {"4", "5", "6"}:
        length = int(parts[2])
    else:
        return
    context.user_data["wordrank_filters"] = (period, scope, length)
    chat = update.effective_chat
    if not chat:
        return
    body = await _ranking_text(context, chat.id, period, scope, length)
    await query.edit_message_text(
        body,
        parse_mode="HTML",
        reply_markup=_ranking_keyboard(period, scope, length),
    )
