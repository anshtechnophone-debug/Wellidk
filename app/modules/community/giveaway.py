"""Seven-day group giveaway leaderboard and participation flow."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from html import escape

from telegram import Chat, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
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
    return f"{days}d {hours}h {seconds // 60}m"


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


def _rules_text(group_title: str) -> str:
    return (
        f"🎁 <b>GIVEAWAY RULES — {escape(group_title)}</b>\n\n"
        "⏳ <b>Duration:</b> 7 days from the admin's #giveaway start command.\n"
        "🎯 <b>Common qualification target:</b> 9,800 credited text messages.\n\n"
        "💬 <b>Message credits</b>\n"
        "• Text reply to another message: <b>1 credit</b>\n"
        "• Standalone text message: <b>0.5 credit</b>\n"
        "• Stickers, voice notes, photos, videos, captions, and slash commands: <b>0 credits</b>\n"
        "• Only messages sent after you join and before the giveaway ends count.\n\n"
        "🏆 <b>Prizes</b>\n"
        "🥇 1st place: ₹350\n🥈 2nd place: ₹200\n🥉 3rd place: ₹150\n"
        "4th and 5th place: ₹0\n\n"
        "📊 <b>How ranking works</b>\n"
        "All participants share the same 9,800-credit target. Final positions are ranked by credited messages. "
        "The full prize is unlocked at the target; below the target, the prize is proportional to progress. "
        "More messages can change your position, but never increase that position's prize cap.\n\n"
        "Use <b>#price</b> in the group to view the top five. The giveaway closes automatically after seven days."
    )


async def giveaway_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return
    payload = query.data or ""
    if not payload.startswith("giveaway:join:"):
        return
    try:
        group_id = int(payload.rsplit(":", 1)[1])
    except ValueError:
        await query.answer("This join link is invalid.", show_alert=True)
        return
    repo = _repo(context)
    policy = await repo.get(group_id)
    state = policy.get("giveaway", {})
    if not isinstance(state, dict) or not state.get("active"):
        await query.answer("There is no active giveaway right now.", show_alert=True)
        return
    end = _parse_time(state.get("ends_at"))
    if not end or _now() >= end:
        state["active"] = False
        state["ended_at"] = end.isoformat() if end else _now().isoformat()
        await repo.set_value(group_id, "giveaway", state)
        await query.answer("This giveaway has ended.", show_alert=True)
        return
    try:
        await context.bot.get_chat_member(group_id, user.id)
    except TelegramError:
        await query.answer("Join the group first, then tap the button again.", show_alert=True)
        return
    members = state.setdefault("members", {})
    key = str(user.id)
    if key in members:
        await query.answer("You're already in the giveaway!", show_alert=True)
        return
    members[key] = {
        "name": user.full_name,
        "username": user.username,
        "score": 0.0,
        "joined_at": _now().isoformat(),
    }
    await repo.set_value(group_id, "giveaway", state)
    name = escape(user.full_name)
    mention = f'<a href="tg://user?id={user.id}">{name}</a>'
    try:
        await query.edit_message_text(
            f"✅ {mention} has joined the giveaway!\n\n📩 Please check your DM for the giveaway rules.",
            parse_mode="HTML",
            reply_markup=None,
            disable_web_page_preview=True,
        )
    except TelegramError:
        await query.answer("You're in! Please check your DM for the rules.")
    try:
        group = await context.bot.get_chat(group_id)
        group_title = group.title or "Group"
    except TelegramError:
        group_title = "Group"
    try:
        await context.bot.send_message(
            chat_id=user.id,
            text=_rules_text(group_title),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except TelegramError:
        # Joining is kept even when the user has not opened the bot in DM yet.
        await context.bot.send_message(
            chat_id=group_id,
            text=f"{mention}, you're registered! Please open XYRON's private chat and press Start so I can send you the rules.",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    await query.answer("You're in! Check your DM for the rules.")


async def giveaway_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle giveaway controls, invitations, and eligible text-message scoring."""
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or chat.type not in {Chat.GROUP, Chat.SUPERGROUP} or not user or user.is_bot:
        return
    text = (message.text or "").strip()
    if not text:
        return
    folded = re.sub(r"\s+", " ", text).casefold()
    repo = _repo(context)

    if folded == "#gw":
        state = (await repo.get(chat.id)).get("giveaway", {})
        if not isinstance(state, dict) or not state.get("active"):
            await message.reply_text("🎁 There isn't an active giveaway right now. Watch for the next one!")
            return
        ends_at = _parse_time(state.get("ends_at"))
        if not ends_at or _now() >= ends_at:
            state["active"] = False
            state["ended_at"] = ends_at.isoformat() if ends_at else _now().isoformat()
            await repo.set_value(chat.id, "giveaway", state)
            await message.reply_text("🏁 This giveaway has ended.")
            return
        members = state.get("members", {})
        if str(user.id) in members:
            await message.reply_text("✅ You're already registered for this giveaway. Send #price to check the leaderboard.")
            return
        button = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔵 Click here to join the giveaway", callback_data=f"giveaway:join:{chat.id}")
        ]])
        mention = f'<a href="tg://user?id={user.id}">{escape(user.full_name)}</a>'
        await message.reply_text(
            f"🎁 {mention}, you're invited!\n\n"
            "Tap the blue button below to join the weekly giveaway.\n"
            "📩 I'll send the rules to your DM after you join.",
            parse_mode="HTML",
            reply_markup=button,
            disable_web_page_preview=True,
        )
        return

    if folded in {"#giveaway start", "#giveaway end", "#price"}:
        state = (await repo.get(chat.id)).get("giveaway", {})
        if not isinstance(state, dict):
            state = {}
        if folded in {"#giveaway start", "#giveaway end"}:
            try:
                await require_admin_access(update, context, context.application.bot_data["settings"], None)
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
                await repo.set_value(chat.id, "giveaway", state)
                await message.reply_text(
                    "🎉 <b>WEEKLY GIVEAWAY IS LIVE!</b>\n\n"
                    "⏳ Duration: <b>7 days</b> — countdown and scoring start now.\n"
                    "📝 Send <b>#GW</b> to get your personal join button. Only joined participants earn credits.\n"
                    "🎯 Shared target: <b>9,800 credited messages</b>.\n\n"
                    "🥇 ₹350  •  🥈 ₹200  •  🥉 ₹150\n"
                    "📊 Send <b>#price</b> for the top five. Good luck!",
                    parse_mode="HTML",
                )
            else:
                if not state.get("active"):
                    await message.reply_text("There is no active giveaway to end.")
                    return
                state["active"] = False
                state["ended_at"] = _now().isoformat()
                await repo.set_value(chat.id, "giveaway", state)
                await message.reply_text("🏁 <b>GIVEAWAY ENDED!</b>\nUse <b>#price</b> to view the final top five.", parse_mode="HTML")
            return

        if not state or not state.get("started_at"):
            await message.reply_text("🎁 No giveaway has started yet. An admin can start one with <b>#giveaway start</b>.", parse_mode="HTML")
            return
        ends_at = _parse_time(state.get("ends_at")) or _now()
        if state.get("active") and _now() >= ends_at:
            state["active"] = False
            state["ended_at"] = ends_at.isoformat()
            await repo.set_value(chat.id, "giveaway", state)
        entries = _leaderboard(state)
        active = bool(state.get("active"))
        lines = [
            "🎁 <b>WEEKLY GIVEAWAY LEADERBOARD</b>",
            f"⏳ Time left: <b>{_remaining(ends_at)}</b>" if active else "🏁 <b>FINAL RESULTS</b>",
            "━━━━━━━━━━━━━━━━━━",
        ]
        medals = ("🥇", "🥈", "🥉", "4️⃣", "5️⃣")
        for index in range(5):
            if index >= len(entries):
                lines.append(f"{medals[index]} <i>Open spot</i> · Prize ₹{PRIZES[index]}")
                continue
            user_id, data = entries[index]
            score = _score(data)
            name = escape(str(data.get("name") or "Member")[:80])
            mention = f'<a href="tg://user?id={user_id}">{name}</a>'
            prize_cap = PRIZES[index]
            prize = round(prize_cap * min(1.0, score / TARGET_CREDITS), 2) if index < 3 else 0
            lines.append(f"{medals[index]} <b>{index + 1}. {mention}</b>\n   💬 {score:,.1f} credits · 💰 ₹{prize:g}")
        lines.extend([
            "━━━━━━━━━━━━━━━━━━",
            f"🎯 Shared full-prize target: <b>{TARGET_CREDITS:,} credits</b>",
            "💡 Replies = 1 credit · standalone text = 0.5 credit",
            "🏆 Only the top 3 win. More messages change rank, not prize caps.",
        ])
        await message.reply_text("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)
        return

    # Count only normal text messages by registered participants during the live window.
    if folded.startswith("#giveaway") or folded in {"#price", "#gw"}:
        return
    state = (await repo.get(chat.id)).get("giveaway", {})
    if not isinstance(state, dict) or not state.get("active"):
        return
    ends_at = _parse_time(state.get("ends_at"))
    if not ends_at or _now() >= ends_at:
        state["active"] = False
        state["ended_at"] = ends_at.isoformat() if ends_at else _now().isoformat()
        await repo.set_value(chat.id, "giveaway", state)
        return
    if not message.text or message.text.lstrip().startswith("/"):
        return
    members = state.get("members", {})
    key = str(user.id)
    if key not in members:
        return
    increment = 1.0 if message.reply_to_message and message.reply_to_message.from_user else 0.5
    entry = members[key]
    entry["name"] = user.full_name
    entry["username"] = user.username
    entry["score"] = round(_score(entry) + increment, 1)
    await repo.set_value(chat.id, "giveaway", state)
