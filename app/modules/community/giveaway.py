"""Seven-day group giveaway leaderboard and commands."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from html import escape

from telegram import Chat, Update
from telegram.ext import ContextTypes

from app.core.permissions import AuthorizationError, require_admin_access
from app.services.group_policy import GroupPolicyRepository

DURATION = timedelta(days=7)
TARGET_CREDITS = 9_800
PRIZES = (350, 200, 150, 0, 0)


def _repo(context: ContextTypes.DEFAULT_TYPE) -> GroupPolicyRepository:
    return GroupPolicyRepository(context.application.bot_data["sessions"])


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except (TypeError, ValueError):
        return None


def _remaining(end: datetime) -> str:
    seconds = max(0, int((end - _now()).total_seconds()))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60
    return f"{days}d {hours}h {minutes}m"


def _score(entry: dict) -> float:
    try:
        return max(0.0, float(entry.get("score", 0)))
    except (TypeError, ValueError):
        return 0.0


def _leaderboard(state: dict) -> list[tuple[int, dict]]:
    members = state.get("members", {})
    if not isinstance(members, dict):
        return []
    entries = [(int(user_id), data) for user_id, data in members.items() if isinstance(data, dict)]
    return sorted(entries, key=lambda item: (-_score(item[1]), str(item[1].get("name", "")).casefold(), item[0]))


async def giveaway_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle exact hashtag controls and count eligible text messages during the active week."""
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or chat.type not in {Chat.GROUP, Chat.SUPERGROUP} or not user or user.is_bot:
        return
    text = (message.text or "").strip()
    if not text:
        return
    folded = re.sub(r"\s+", " ", text).casefold()

    if folded in {"#giveaway start", "#giveaway end", "#price"}:
        state = (await _repo(context).get(chat.id)).get("giveaway", {})
        if not isinstance(state, dict):
            state = {}
        if folded in {"#giveaway start", "#giveaway end"}:
            try:
                await require_admin_access(
                    update, context, context.application.bot_data["settings"], None
                )
            except AuthorizationError as exc:
                await message.reply_text(str(exc))
                return
            if folded == "#giveaway start":
                if state.get("active") and (_parse_time(state.get("ends_at")) or _now()) > _now():
                    await message.reply_text("🎁 A giveaway is already running. Use #price to view the leaderboard.")
                    return
                started = _now()
                state = {
                    "active": True,
                    "started_at": started.isoformat(),
                    "ends_at": (started + DURATION).isoformat(),
                    "members": {},
                }
                await _repo(context).set_value(chat.id, "giveaway", state)
                await message.reply_text(
                    "🎉 <b>WEEKLY GIVEAWAY IS LIVE!</b>\n\n"
                    "⏳ Duration: <b>7 days</b> — the countdown starts now.\n"
                    "💬 Text messages count; replies earn full credit, standalone messages earn half credit.\n"
                    "🎯 Common target: <b>9,800 credited messages</b> for everyone.\n\n"
                    "🥇 1st: ₹350  •  🥈 2nd: ₹200  •  🥉 3rd: ₹150\n"
                    "📊 Send <b>#price</b> to see the top 5. Good luck!",
                    parse_mode="HTML",
                )
            else:
                if not state.get("active"):
                    await message.reply_text("There is no active giveaway to end.")
                    return
                state["active"] = False
                state["ended_at"] = _now().isoformat()
                await _repo(context).set_value(chat.id, "giveaway", state)
                await message.reply_text(
                    "🏁 <b>GIVEAWAY ENDED!</b>\nUse <b>#price</b> to view the final top 5 and calculated prizes.",
                    parse_mode="HTML",
                )
            return

        if not state or not state.get("started_at"):
            await message.reply_text("🎁 No giveaway has started yet. An admin can start one with <b>#giveaway start</b>.", parse_mode="HTML")
            return
        ends_at = _parse_time(state.get("ends_at")) or _now()
        if state.get("active") and _now() >= ends_at:
            state["active"] = False
            state["ended_at"] = ends_at.isoformat()
            await _repo(context).set_value(chat.id, "giveaway", state)
        entries = _leaderboard(state)
        if not entries:
            await message.reply_text(
                f"🎁 <b>WEEKLY GIVEAWAY LEADERBOARD</b>\n\n"
                f"{'⏳ Time left: ' + _remaining(ends_at) if state.get('active') else '🏁 Giveaway finished'}\n"
                "No eligible text messages have been counted yet.",
                parse_mode="HTML",
            )
            return
        active = bool(state.get("active"))
        header = "🎁 <b>WEEKLY GIVEAWAY</b>\n"
        header += f"⏳ Time left: <b>{_remaining(ends_at)}</b>\n" if active else "🏁 <b>FINAL RESULTS</b>\n"
        lines = [header, "━━━━━━━━━━━━━━━━━━"]
        medals = ("🥇", "🥈", "🥉", "4️⃣", "5️⃣")
        for index in range(5):
            if index >= len(entries):
                lines.append(f"{medals[index]} — <i>No participant yet</i>")
                continue
            user_id, data = entries[index]
            score = _score(data)
            name = escape(str(data.get("name") or "Member")[:80])
            mention = f'<a href="tg://user?id={user_id}">{name}</a>'
            prize_cap = PRIZES[index]
            prize = round(prize_cap * min(1.0, score / TARGET_CREDITS), 2) if index < 3 else 0
            prize_text = f"₹{prize:g}" if index < 3 else "₹0"
            lines.append(
                f"{medals[index]} <b>{index + 1}.</b> {mention}\n"
                f"   💬 <b>{score:,.1f}</b> credited messages  •  💰 <b>{prize_text}</b>"
            )
        lines.extend([
            "━━━━━━━━━━━━━━━━━━",
            f"🎯 Qualification target: <b>{TARGET_CREDITS:,}</b> credited messages",
            "💡 Replies = 1 credit; standalone text = 0.5 credit.",
            "🏆 Only the top 3 receive prizes. More messages change rank, not the prize cap.",
        ])
        await message.reply_text("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)
        return

    # The command/control messages themselves must never increase scores.
    if folded.startswith("#giveaway") or folded == "#price":
        return
    state = (await _repo(context).get(chat.id)).get("giveaway", {})
    if not isinstance(state, dict) or not state.get("active"):
        return
    ends_at = _parse_time(state.get("ends_at"))
    if not ends_at or _now() >= ends_at:
        state["active"] = False
        state["ended_at"] = ends_at.isoformat() if ends_at else _now().isoformat()
        await _repo(context).set_value(chat.id, "giveaway", state)
        return
    # Only actual text messages count: captions, voice notes, stickers and media are excluded.
    if not message.text or message.text.lstrip().startswith("/"):
        return
    if message.reply_to_message and message.reply_to_message.from_user:
        increment = 1.0
    else:
        increment = 0.5
    members = state.setdefault("members", {})
    key = str(user.id)
    entry = members.setdefault(key, {"name": user.full_name, "username": user.username, "score": 0.0})
    entry["name"] = user.full_name
    entry["username"] = user.username
    entry["score"] = round(_score(entry) + increment, 1)
    await _repo(context).set_value(chat.id, "giveaway", state)
