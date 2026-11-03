"""Telegram handlers for the global Family game."""
from __future__ import annotations

from datetime import UTC, datetime
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from app.modules.games.family import FamilyError, FamilyRepository


def _repo(context):
    return FamilyRepository(context.application.bot_data["sessions"])


async def _name(context, user_id: int, chat_id: int | None = None) -> str:
    try:
        user = await context.bot.get_chat(user_id)
        if user.full_name:
            return user.full_name
    except Exception:
        pass
    if chat_id:
        try:
            member = await context.bot.get_chat_member(chat_id, user_id)
            return member.user.full_name
        except Exception:
            pass
    return f"User {user_id}"


def _link(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{escape(name)}</a>'


async def family_relationship_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user = update.effective_message, update.effective_user
    if not message or not user or not message.reply_to_message or not message.reply_to_message.from_user:
        if message:
            await message.reply_text("Reply to the person's message with this command.")
        return
    if update.effective_chat and update.effective_chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.reply_text("Family relationships can only be created in groups.")
        return
    target = message.reply_to_message.from_user
    command = (message.text or "").split()[0].split("@")[0].lstrip("/").casefold()
    try:
        await _repo(context).add(user.id, target.id, command)
    except FamilyError as exc:
        await message.reply_text(str(exc), parse_mode="HTML")
        return
    labels = {"son": "son", "daughter": "daughter", "father": "father", "mother": "mother",
              "brother": "sibling", "sister": "sibling", "friend": "friend"}
    await message.reply_text(
        f"👨‍👩‍👧‍👦 <b>Family updated!</b>\n{_link(user.id, user.full_name)} and "
        f"{_link(target.id, target.full_name)} are now connected as {labels.get(command, 'family')}.",
        parse_mode="HTML", disable_web_page_preview=True)


async def marriage_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if not message or not user or not chat:
        return
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP) or not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply_text("Reply to the person's message in a group with /wife or /husband.")
        return
    target = message.reply_to_message.from_user
    command = (message.text or "").split()[0].split("@")[0].lstrip("/").lower()
    try:
        request_id = await _repo(context).create_request(user.id, target.id, "marriage", command, chat.id)
    except FamilyError as exc:
        await message.reply_text(str(exc), parse_mode="HTML")
        return
    bot = await context.bot.get_me()
    start_url = f"https://t.me/{bot.username}?start=family_req_{request_id}_{target.id}"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Open XYRON privately", url=start_url)],
        [InlineKeyboardButton("✅ I've started — Accept", callback_data=f"famreq:a:{request_id}"),
         InlineKeyboardButton("Decline", callback_data=f"famreq:d:{request_id}")],
    ])
    await message.reply_text(
        f"💍 <b>Marriage proposal</b>\n{_link(user.id, user.full_name)} has proposed to "
        f"{_link(target.id, target.full_name)} as their {command}.\n\n"
        "Only the intended person can respond. This proposal expires in 24 hours.",
        parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True)


async def divorce_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if not message or not user or not chat:
        return
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP) or not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply_text("Reply to your spouse's message with /divorce.")
        return
    target = message.reply_to_message.from_user
    try:
        request_id = await _repo(context).create_request(user.id, target.id, "divorce", None, chat.id)
    except FamilyError as exc:
        await message.reply_text(str(exc), parse_mode="HTML")
        return
    bot = await context.bot.get_me()
    start_url = f"https://t.me/{bot.username}?start=family_req_{request_id}_{target.id}"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Open XYRON privately", url=start_url)],
        [InlineKeyboardButton("✅ I've started — Accept", callback_data=f"famreq:a:{request_id}"),
         InlineKeyboardButton("Decline", callback_data=f"famreq:d:{request_id}")],
    ])
    await message.reply_text(
        f"💔 <b>Divorce request</b>\n{_link(user.id, user.full_name)} has requested a mutual divorce from "
        f"{_link(target.id, target.full_name)}.\n\nOnly the spouse can respond. Expires in 24 hours.",
        parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True)


async def family_request_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not update.effective_user:
        return
    parts = query.data.split(":")
    if len(parts) != 3 or parts[1] not in {"a", "d"}:
        await query.answer("Invalid request.", show_alert=True)
        return
    try:
        kind, requester_id, target_id, status = await _repo(context).resolve_request(
            int(parts[2]), update.effective_user.id, parts[1] == "a")
    except FamilyError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("Response recorded.")
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    verb = ("marriage" if kind == "marriage" else "divorce")
    outcome = "accepted" if status == "accepted" else "declined"
    try:
        await context.bot.send_message(
            chat_id=requester_id,
            text=f"Your {verb} request was {outcome} by the other person.")
    except Exception:
        pass
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=f"You {outcome} the {verb} request.")
    except Exception:
        pass
    if query.message:
        await query.message.reply_text(f"💍 {verb.title()} request {outcome}.")


async def family_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, message = update.effective_user, update.effective_message
    if not user or not message:
        return
    target = message.reply_to_message.from_user if message.reply_to_message and message.reply_to_message.from_user else user
    edges, count, level, title, sibling_ids = await _repo(context).profile(target.id)
    grouped = {}
    for edge in edges:
        if edge.relation != "sibling":
            grouped.setdefault(edge.relation, []).append(edge.related_user_id)
    if sibling_ids:
        grouped["siblings"] = sibling_ids
    lines = [f"🌳 <b>{escape(target.full_name)}'s Family</b>",
             f"🏅 Level <b>{level}/25 · {title}</b>  •  <b>{count}</b> connections"]
    labels = {"son": "Sons", "daughter": "Daughters", "father": "Father", "mother": "Mother",
              "wife": "Wife", "husband": "Husband", "friend": "Friends", "siblings": "Siblings"}
    order = ["wife", "husband", "father", "mother", "son", "daughter", "siblings", "friend"]
    for relation in order:
        ids = grouped.get(relation, [])
        if relation in {"wife", "husband", "father", "mother"}:
            name = await _name(context, ids[0], update.effective_chat.id if update.effective_chat else None) if ids else "—"
            lines.append(f"• <b>{labels[relation]}:</b> {_link(ids[0], name) if ids else '—'}")
        else:
            names = [_link(uid, await _name(context, uid, update.effective_chat.id if update.effective_chat else None)) for uid in ids]
            lines.append(f"• <b>{labels[relation]} ({len(ids)}):</b> {', '.join(names) if names else '—'}")
    if not edges:
        lines.append("\nNo relationships yet. Reply to someone with a Family command to get started.")
    await message.reply_text("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


async def familytree_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await family_command(update, context)


_RANKS = {
    "sons": ("👦 Most Sons", "son"),
    "daughters": ("👧 Most Daughters", "daughter"),
    "total": ("❤️ Most Total Relationships", "total"),
    "marriage": ("💍 Longest Marriage", "marriage"),
}


async def familyrank_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    keyboard = [[InlineKeyboardButton(label, callback_data=f"famrank:choose:{key}")]
                for key, (label, _) in _RANKS.items()]
    await message.reply_text(
        "🏆 <b>FAMILY HALL OF FAME</b>\\nChoose a leaderboard.",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def familyrank_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    parts = query.data.split(":")
    if len(parts) < 3:
        await query.answer("Invalid leaderboard.", show_alert=True)
        return
    _, action, value = parts[:3]
    if action == "menu":
        keyboard = [[InlineKeyboardButton(label, callback_data=f"famrank:choose:{key}")]
                    for key, (label, _) in _RANKS.items()]
        await query.answer()
        await query.edit_message_text("🏆 <b>FAMILY HALL OF FAME</b>\\nChoose a leaderboard.",
                                      parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    if action == "choose":
        if value not in _RANKS:
            await query.answer("Invalid category.", show_alert=True)
            return
        label = _RANKS[value][0]
        keyboard = [[InlineKeyboardButton("This Chat", callback_data=f"famrank:show:{value}:chat"),
                     InlineKeyboardButton("Global", callback_data=f"famrank:show:{value}:global")],
                    [InlineKeyboardButton("⬅️ Back", callback_data="famrank:menu:all")]]
        await query.answer()
        await query.edit_message_text(f"🏆 <b>{label}</b>\\nChoose the ranking scope.",
                                      parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    if action != "show" or len(parts) != 4:
        await query.answer("Invalid leaderboard.", show_alert=True)
        return
    category, scope = value, parts[3]
    if category not in _RANKS or scope not in {"global", "chat"}:
        await query.answer("Invalid leaderboard.", show_alert=True)
        return
    chat_id = query.message.chat_id if query.message else None
    if category == "marriage":
        rows = await _repo(context).longest_marriages(limit=10000)
        if scope == "chat" and chat_id:
            try:
                from sqlalchemy import select
                from app.database.models import KnownUser
                async with context.application.bot_data["sessions"]() as session:
                    ids = set(await session.scalars(select(KnownUser.user_id).where(KnownUser.chat_id == chat_id)))
                rows = [row for row in rows if row[0] in ids and row[1] in ids]
            except Exception:
                rows = []
        if not rows:
            text = f"💍 <b>LONGEST MARRIAGES · {scope.upper()}</b>\\nNo marriages yet."
        else:
            now = datetime.now(UTC)
            lines = [f"💍 <b>LONGEST MARRIAGES · {scope.upper()}</b>"]
            for i, (left, right, since) in enumerate(rows, 1):
                days = max(0, (now - since).days)
                lines.append(f"{['🥇','🥈','🥉'][i-1] if i <= 3 else str(i)+'.'} {_link(left, await _name(context, left, chat_id))} + {_link(right, await _name(context, right, chat_id))} — <b>{days} days</b>")
            text = "\\n".join(lines)
    else:
        rows = await _repo(context).leaderboard(category, limit=10000)
        if scope == "chat" and chat_id:
            try:
                from sqlalchemy import select
                from app.database.models import KnownUser
                async with context.application.bot_data["sessions"]() as session:
                    ids = set(await session.scalars(select(KnownUser.user_id).where(KnownUser.chat_id == chat_id)))
                rows = [row for row in rows if row[0] in ids]
            except Exception:
                rows = []
        if not rows:
            text = f"{_RANKS[category][0]} · {scope.upper()}\\nNo connections yet."
        else:
            lines = [f"🏆 <b>FAMILY HALL OF FAME</b>\\n{_RANKS[category][0]} · {scope.upper()}"]
            medals = ["🥇", "🥈", "🥉"]
            for i, (uid, score) in enumerate(rows[:10], 1):
                name = await _name(context, uid, chat_id)
                noun = "connection" if score == 1 else "connections"
                lines.append(f"{medals[i-1] if i <= 3 else str(i)+'.'} {_link(uid, name)}  <b>{score}</b> {noun}")
            text = "\\n".join(lines)
    await query.answer()
    keyboard = [[InlineKeyboardButton("👦 Sons", callback_data="famrank:choose:sons"),
                 InlineKeyboardButton("👧 Daughters", callback_data="famrank:choose:daughters")],
                [InlineKeyboardButton("❤️ Total", callback_data="famrank:choose:total"),
                 InlineKeyboardButton("💍 Marriage", callback_data="famrank:choose:marriage")],
                [InlineKeyboardButton("🔄 Menu", callback_data="famrank:menu:all")]]
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard),
                                  disable_web_page_preview=True)


async def familytop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await familyrank_command(update, context)


async def familyhelp_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(
            "🌳 FAMILY — GLOBAL GAME\n\n"
            "Reply to a user's message: /son, /daughter, /father, /mother, /brother, /sister or /friend.\n"
            "Reply with /wife or /husband to propose marriage; the other person must accept. "
            "Reply with /divorce to request a mutual divorce. Requests expire after 24 hours.\n\n"
            "/family — profile (or reply to view another member)\n/familyrank — interactive leaderboards\n"
            "/familyhelp — commands")
