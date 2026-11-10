"""Telegram commands and plain-message handling for WordSeek."""

from __future__ import annotations

from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.modules.games.wordseek import WordGameError, WordSeekRepository
from app.modules.games.wordseek_daily import DailyWordError, DailyWordResult, WordSeekDailyRepository


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
    from app.modules.games.wordseek_render import render_wordseek_board

    caption = f"👤 <b>{escape(user.full_name)}</b> guessed · {len(result.guesses)}/30"
    if result.state == "won":
        caption += (
            "\n\n🎉 <b>Excellent! You found the word!</b>"
            f"\n⭐ Added {result.points} points to the leaderboard."
            f"\nStart again: /new{len(value)}"
        )
    elif result.state == "lost":
        caption += (
            f"\n\nThe word was <b>{escape(result.answer.upper() if result.answer else '')}</b>."
            f"\nTry again: /new{len(value)}"
        )

    board_image = render_wordseek_board(
        len(value), result.guesses, result.feedbacks, max_guesses=30
    )
    await message.reply_photo(
        photo=board_image,
        caption=caption,
        parse_mode="HTML",
    )


async def guess_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /guess <word>")
        return
    await _submit_guess(update, context, context.args[0])


def _daily_games(context: ContextTypes.DEFAULT_TYPE) -> WordSeekDailyRepository:
    return WordSeekDailyRepository(context.application.bot_data["sessions"])


async def _send_daily_board(message, user, result: DailyWordResult) -> None:
    from app.modules.games.wordseek_render import render_wordseek_board

    caption = f"📅 <b>DAILY WORDSEEK</b> · {len(result.guesses)}/6 guesses"
    if result.state == "won":
        caption += f"\n\n🎉 <b>Solved in {len(result.guesses)}/6!</b>"
    elif result.state == "lost":
        caption += f"\n\nThe word was <b>{escape(result.answer or '') .upper()}</b>."
    else:
        caption += f"\n\n{result.guesses_left} guesses left. Send your next 5-letter word."
    if result.state in {"won", "lost"}:
        caption += "\n\n<b>Your share grid</b>\n<code>" + "\n".join(result.feedbacks) + "</code>"
    board = render_wordseek_board(5, result.guesses, result.feedbacks, max_guesses=6)
    await message.reply_photo(photo=board, caption=caption, parse_mode="HTML")


async def dailyword_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user:
        return
    if chat.type != "private":
        await message.reply_text("Today's daily puzzle is personal. Open XYRON's private chat and use /dailyword.")
        return
    result = await _daily_games(context).start(user.id)
    if result.guesses:
        await _send_daily_board(message, user, result)
    else:
        await message.reply_text(
            "📅 <b>WORDSEEK DAILY</b>\n\n"
            "One shared 5-letter puzzle for everyone. You get 6 guesses, and your progress is saved.\n"
            "Send a 5-letter word to make your first guess.\n\n"
            "🟩 Correct spot   🟨 Wrong spot   🟥 Not in word",
            parse_mode="HTML",
        )


async def dailyword_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or chat.type != "private" or not user or not message.text:
        return
    value = message.text.strip()
    if not value.isalpha() or len(value) != 5:
        return
    try:
        result = await _daily_games(context).guess(user.id, value)
    except DailyWordError as exc:
        if "Start today's puzzle first" in str(exc):
            return
        await message.reply_text(str(exc))
        return
    await _send_daily_board(message, user, result)


async def wordstats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return
    stats = await _daily_games(context).stats(user.id)
    distribution = stats["distribution"]
    bars = []
    for attempts in range(1, 7):
        count = distribution[str(attempts)]
        bars.append(f"{attempts}: {'🟩' * min(count, 12) if count else '—'} {count}")
    await message.reply_text(
        "📊 <b>YOUR WORDSEEK DAILY STATS</b>\n\n"
        f"Played: <b>{stats['played']}</b>\n"
        f"Won: <b>{stats['wins']}</b>\n"
        f"Win rate: <b>{stats['win_rate']}%</b>\n"
        f"Current streak: <b>{stats['current_streak']}</b>\n"
        f"Best streak: <b>{stats['best_streak']}</b>\n\n"
        "<b>Guess distribution</b>\n" + "\n".join(bars),
        parse_mode="HTML",
    )


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
