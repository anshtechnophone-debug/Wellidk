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


def _rules_text(group_title: str, started: datetime | None = None, ends: datetime | None = None) -> str:
    start_label = started.strftime("%d %b %Y, %H:%M UTC") if started else "At launch"
    end_label = ends.strftime("%d %b %Y, %H:%M UTC") if ends else "7 days after launch"
    return (
        f"🎁 <b>{escape(group_title)} — GIVEAWAY RULES</b>\n\n"
        f"🗓 <b>Starts:</b> {start_label}\n"
        f"🏁 <b>Ends:</b> {end_label}\n\n"
        "💬 <b>Earn credits</b>\n"
        "• Text reply: <b>+1 credit</b>\n"
        "• Standalone text: <b>+0.5 credit</b>\n"
        "• Media, stickers, captions and commands: <b>0 credits</b>\n\n"
        "✅ Join first. Only messages sent after joining count.\n"
        "🏆 Your rank depends on your credits. Use <b>/prize</b> in the group.\n"
        "⏳ The giveaway ends after 7 days."
    )

async def giveaway_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Register giveaway participants and provide private rules or a start link."""
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return
    action = (query.data or "").split(":")
    if len(action) != 3 or action[0] != "giveaway" or action[1] not in {"join", "rules"}:
        return
    try:
        group_id = int(action[2])
    except ValueError:
        await query.answer("This giveaway button is invalid.", show_alert=True)
        return

    repo = _repo(context)
    policy = await repo.get(group_id)
    state = policy.get("giveaway", {})
    if not isinstance(state, dict):
        await query.answer("This giveaway is unavailable.", show_alert=True)
        return

    end = _parse_time(state.get("ends_at"))
    if not state.get("active") or not end or _now() >= end:
        if state.get("active"):
            state["active"] = False
            state["ended_at"] = end.isoformat() if end else _now().isoformat()
            await repo.set_value(group_id, "giveaway", state)
        await query.answer("This giveaway has ended.", show_alert=True)
        return

    try:
        member = await context.bot.get_chat_member(group_id, user.id)
        if member.status not in {"member", "administrator", "creator"} and not bool(getattr(member, "is_member", False)):
            await query.answer("Join the group first, then tap Join Giveaway.", show_alert=True)
            return
        group = await context.bot.get_chat(group_id)
    except TelegramError:
        await query.answer("I couldn't verify your group membership. Please try again.", show_alert=True)
        return

    if action[1] == "rules":
        try:
            await context.bot.send_message(
                chat_id=user.id,
                text=_rules_text(group.title or "Group", _parse_time(state.get("started_at")), end),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            await query.answer("Giveaway rules sent to your DM.")
        except TelegramError:
            await query.answer("Open the rules link and press Start in XYRON.", show_alert=True)
        return

    members = state.setdefault("members", {})
    key = str(user.id)
    if key not in members:
        members[key] = {
            "name": user.full_name,
            "username": user.username,
            "score": 0.0,
            "joined_at": _now().isoformat(),
        }
        await repo.set_value(group_id, "giveaway", state)

    name = escape(user.full_name)
    mention = f'<a href="tg://user?id={user.id}">{name}</a>'
    started = _parse_time(state.get("started_at"))
    try:
        await context.bot.send_message(
            chat_id=user.id,
            text=_rules_text(group.title or "Group", started, end),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        result_text = (
            f"✅ {mention} has joined the giveaway!\n"
            "📩 Giveaway rules have been sent to your DM."
        )
        await query.edit_message_text(
            result_text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        await query.answer("You're in! Check your private chat for the rules.")
    except TelegramError:
        me = await context.bot.get_me()
        rules_url = f"https://t.me/{me.username}?start=gw_{group_id}"
        result_text = (
            f"✅ {mention} has joined the giveaway!\n"
            f'📜 <a href="{rules_url}">Please click here to see the giveaway rules.</a>\n'
            "<i>Open XYRON and press Start to receive them privately.</i>"
        )
        try:
            await query.edit_message_text(
                result_text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except TelegramError:
            pass
        await query.answer("You're registered! Tap the rules link and press Start in XYRON.")


async def giveaway_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle giveaway controls, invitations, and eligible text-message scoring."""
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or chat.type not in {Chat.GROUP, Chat.SUPERGROUP} or not user or user.is_bot:
        return
    text = (message.text or "").strip()
    if not text:
        return
    folded = re.sub(r"\s+", " ", text).casefold()
    command_text = re.sub(r"^/(gw|prize)(?:@\w+)?", lambda match: "/" + match.group(1), folded)
    command_text = re.sub(r"\s+", " ", command_text).strip()
    repo = _repo(context)

    if command_text == "/gw":
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
            await message.reply_text("✅ You're already registered for this giveaway. Send /prize to check the leaderboard.")
            return
        me = await context.bot.get_me()
        button = InlineKeyboardMarkup([[
            InlineKeyboardButton("Join Giveaway", callback_data=f"giveaway:join:{chat.id}")
        ]])
        mention = f'<a href="tg://user?id={user.id}">{escape(user.full_name)}</a>'
        await message.reply_text(
            f"🎁 <b>WEEKLY GIVEAWAY</b>\n\n"
            f"{mention}, join here to register and receive the rules privately.\n"
            "💬 Earn credits by chatting after you join.\n"
            "🏆 Use /prize in the group to check your progress.",
            parse_mode="HTML",
            reply_markup=button,
            disable_web_page_preview=True,
        )
        return

    if command_text in {"/gw start", "/gw end", "/prize"}:
        state = (await repo.get(chat.id)).get("giveaway", {})
        if not isinstance(state, dict):
            state = {}
        is_start = command_text == "/gw start"
        is_end = command_text == "/gw end"
        if is_start or is_end:
            if is_start:
                try:
                    actor = await context.bot.get_chat_member(chat.id, user.id)
                except TelegramError:
                    await message.reply_text("I couldn't verify the group owner's identity. Please try again.")
                    return
                if actor.status != "creator":
                    await message.reply_text("🔒 Only the group owner can start the giveaway.")
                    return
                if state.get("active") and (_parse_time(state.get("ends_at")) or _now()) > _now():
                    await message.reply_text("🎁 A giveaway is already running. Use /prize to view the leaderboard.")
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
                    "🎉 <b>WEEKLY GIVEAWAY IS LIVE!</b>\n"
                    f"🗓 <b>From:</b> {started.strftime('%d %b %Y, %H:%M UTC')}\n"
                    f"🏁 <b>Until:</b> {(started + DURATION).strftime('%d %b %Y, %H:%M UTC')}\n"
                    "⏳ Duration: <b>7 days</b>\n\n"
                    "💬 Reply = 1 credit · Standalone text = 0.5 credit\n"
                    "✅ Join with <b>/gw</b> before chatting to earn credits.\n"
                    "📊 Use <b>/prize</b> to see the leaderboard.",
                    parse_mode="HTML",
                )
            else:
                try:
                    await require_admin_access(update, context, context.application.bot_data["settings"], None)
                except AuthorizationError as exc:
                    await message.reply_text(str(exc))
                    return
                if not state.get("active"):
                    await message.reply_text("There is no active giveaway to end.")
                    return
                state["active"] = False
                state["ended_at"] = _now().isoformat()
                await repo.set_value(chat.id, "giveaway", state)
                await message.reply_text(
                    "🏁 <b>GIVEAWAY ENDED!</b>\nUse <b>/prize</b> to view the final top five.",
                    parse_mode="HTML",
                )
            return

        if not state or not state.get("started_at"):
            await message.reply_text("🎁 No giveaway has started yet. Only the group owner can start one with <b>/gw start</b>.", parse_mode="HTML")
            return
        ends_at = _parse_time(state.get("ends_at")) or _now()
        if state.get("active") and _now() >= ends_at:
            state["active"] = False
            state["ended_at"] = ends_at.isoformat()
            await repo.set_value(chat.id, "giveaway", state)
        entries = _leaderboard(state)
        active = bool(state.get("active"))
        lines = [
            "🏆 <b>WEEKLY GIVEAWAY</b>",
            f"🟢 <b>LIVE</b> · {_remaining(ends_at)} left" if active else "🏁 <b>GIVEAWAY ENDED · FINAL RANKING</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "<b>   NAMES             SCORE              PRIZE</b>",
        ]
        medals = ("🥇", "🥈", "🥉", "4️⃣", "5️⃣")
        for index in range(5):
            prize = f"₹{PRIZES[index]:,}" if PRIZES[index] > 0 else "—"
            if index >= len(entries):
                lines.append(f"{medals[index]} <i>{index + 1}. Open spot</i>  ·  — credits  ·  {prize}")
                continue
            user_id, data = entries[index]
            score = _score(data)
            name = escape(str(data.get("name") or "Member")[:24])
            mention = f'<a href="tg://user?id={user_id}">{name}</a>'
            lines.append(
                f"{medals[index]} <b>{index + 1}. {mention}</b>  💬 <b>{score:,.1f}</b> credits  ·  <b>{prize}</b>"
            )
        lines.extend([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "💬 Reply = 1 credit · Standalone text = 0.5 credit",
            "✅ Only messages sent after joining count.",
            "🔄 Keep chatting to improve your rank.",
        ])
        await message.reply_text("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)
        return

    # Count only normal text messages by registered participants during the live window.
    if command_text.startswith(("/gw", "/prize", "#giveaway")) or command_text in {"/prize", "/gw"}:
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
