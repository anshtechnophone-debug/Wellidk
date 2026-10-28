"""Daily group chatter leaderboard."""

from __future__ import annotations

import html
import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from telegram import Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from app.services.group_history import GroupMessage

logger = logging.getLogger(__name__)
LOCAL_TZ = ZoneInfo("Asia/Kolkata")


async def daily_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the top 50 members by messages sent today in the current group."""
    message = update.effective_message
    chat = update.effective_chat
    if not message or not chat:
        return
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.reply_text("Use /daily inside a group to see its daily chatter leaderboard.")
        return

    sessions = context.application.bot_data.get("sessions")
    if sessions is None:
        await message.reply_text("The daily leaderboard is temporarily unavailable.")
        return

    now_local = datetime.now(LOCAL_TZ)
    start_local = datetime.combine(now_local.date(), time.min, tzinfo=LOCAL_TZ)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)

    statement = (
        select(
            GroupMessage.user_id,
            func.max(GroupMessage.display_name).label("display_name"),
            func.max(GroupMessage.username).label("username"),
            func.count(GroupMessage.id).label("message_count"),
        )
        .where(
            GroupMessage.chat_id == chat.id,
            GroupMessage.sent_at >= start_utc,
            GroupMessage.sent_at < end_utc,
        )
        .group_by(GroupMessage.user_id)
        .order_by(func.count(GroupMessage.id).desc(), GroupMessage.user_id.asc())
        .limit(50)
    )

    try:
        async with sessions() as session:
            rows = (await session.execute(statement)).all()
    except Exception:
        logger.exception("Could not build daily chatter leaderboard for chat_id=%s", chat.id)
        await message.reply_text("Couldn't load today's leaderboard. Please try again.")
        return

    if not rows:
        await message.reply_text("📊 No messages have been recorded in this group today yet.")
        return

    lines = [
        f"📊 <b>DAILY TOP CHATTERS</b> — {html.escape(now_local.strftime('%d %b %Y'))}",
        "<i>Top 50 members by messages sent today (IST)</i>",
        "",
    ]
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    for rank, row in enumerate(rows, start=1):
        user_id, display_name, username, count = row
        label = (display_name or (f"@{username}" if username else f"User {user_id}")).strip()
        safe_label = html.escape(label)
        linked_name = f'<a href="tg://user?id={int(user_id)}">{safe_label}</a>'
        lines.append(f"{medals.get(rank, f'{rank}.')} {linked_name} — <b>{int(count):,}</b>")

    # Telegram messages are limited to 4096 characters; send the heading and
    # entries in safe chunks while keeping HTML tags balanced per message.
    heading = "\n".join(lines[:3])
    entries = lines[4:]
    chunks: list[str] = []
    current = heading
    for entry in entries:
        candidate = f"{current}\n{entry}"
        if len(candidate) > 3800 and current != heading:
            chunks.append(current)
            current = f"📊 <b>DAILY TOP CHATTERS (continued)</b>\n{entry}"
        else:
            current = candidate
    if current:
        chunks.append(current)

    for chunk in chunks:
        await message.reply_text(chunk, parse_mode="HTML", disable_web_page_preview=True)
