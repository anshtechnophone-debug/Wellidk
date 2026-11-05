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


def _link(user_id: int, name: str | None) -> str:
    # Telegram users may have no first/last name; never pass None to html.escape.
    safe_name = str(name or f"User {user_id}")
    return f'<a href="tg://user?id={user_id}">{escape(safe_name)}</a>'


async def family_remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only: remove family roles or one named/ID member from a replied user's family."""
    message, owner, chat = update.effective_message, update.effective_user, update.effective_chat
    if not message or not owner or not chat:
        return
    settings = context.application.bot_data.get("settings")
    if not settings or not settings.is_owner(owner.id):
        await message.reply_text("Only the bot owner can use /remove.")
        return
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.reply_text("Use /remove in a group by replying to the person's message.")
        return
    replied = message.reply_to_message
    if not replied or not replied.from_user:
        await message.reply_text("Reply to the person's message: /remove father, mother | @username | user_id")
        return

    tokens = [x.strip().casefold() for x in " ".join(context.args or []).replace(",", " ").split()]
    aliases = {
        "father": {"father"}, "mother": {"mother"}, "son": {"son"}, "daughter": {"daughter"},
        "brother": {"sibling"}, "sister": {"sibling"}, "sibling": {"sibling"},
        "friend": {"friend"}, "wife": {"wife"}, "husband": {"husband"},
        "spouse": {"wife", "husband"}, "parent": {"father", "mother"},
        "parents": {"father", "mother"}, "child": {"son", "daughter"},
        "children": {"son", "daughter"},
    }
    role_mode = bool(tokens) and all(x in aliases for x in tokens)
    username = None
    relative_id = None
    if role_mode:
        relations = set().union(*(aliases[x] for x in tokens))
    elif len(tokens) == 1 and tokens[0].startswith("@") and len(tokens[0]) > 1:
        relations = set()
        username = tokens[0][1:]
    elif len(tokens) == 1 and tokens[0].isdigit() and int(tokens[0]) > 0:
        relations = set()
        relative_id = int(tokens[0])
    else:
        await message.reply_text("Usage: /remove father, mother | @username | numeric_id (reply to the person)")
        return

    subject_id = replied.from_user.id
    if relative_id == subject_id:
        await message.reply_text("A person cannot be removed from their own family.")
        return

    from sqlalchemy import func, select
    from app.database.models import FamilyRelationship, KnownUser
    sessions = context.application.bot_data["sessions"]
    if username:
        async with sessions() as session:
            relative_id = await session.scalar(select(KnownUser.user_id).where(
                KnownUser.chat_id == chat.id,
                func.lower(KnownUser.username) == username.casefold(),
            ).limit(1))
        if relative_id is None:
            await message.reply_text(
                f"Couldn't find @{escape(username)} in this group's known users. "
                "Make sure XYRON has seen them in this group."
            )
            return
    if relative_id == subject_id:
        await message.reply_text("A person cannot be removed from their own family.")
        return

    reverse = {"father": "son", "son": "father", "mother": "daughter", "daughter": "mother",
               "wife": "husband", "husband": "wife", "sibling": "sibling", "friend": "friend"}
    async with sessions() as session:
        async with session.begin():
            query = select(FamilyRelationship).where(FamilyRelationship.user_id == subject_id)
            query = query.where(
                FamilyRelationship.relation.in_(relations) if role_mode
                else FamilyRelationship.related_user_id == relative_id
            )
            edges = list(await session.scalars(query))
            for edge in edges:
                reciprocal = await session.scalars(select(FamilyRelationship).where(
                    FamilyRelationship.user_id == edge.related_user_id,
                    FamilyRelationship.related_user_id == subject_id,
                    FamilyRelationship.relation == reverse.get(edge.relation, edge.relation),
                ))
                for other in list(reciprocal):
                    await session.delete(other)
                await session.delete(edge)
            names = sorted({edge.relation for edge in edges})
            removed_ids = sorted({edge.related_user_id for edge in edges})

    if not edges:
        label = ", ".join(sorted(relations)) if role_mode else "that user"
        await message.reply_text(
            f"No {escape(label)} relationship was found in {escape(replied.from_user.full_name)}'s family."
        )
        return

    if role_mode:
        member_names = ", ".join(escape(await _name(context, uid, chat.id)) for uid in removed_ids)
        await message.reply_text(
            "✅ <b>Family relationships removed.</b>\n"
            f"Roles: <b>{escape(', '.join(names))}</b>\n"
            f"Members: {member_names or '—'}\n"
            f"From {_link(subject_id, replied.from_user.full_name)}'s family.",
            parse_mode="HTML", disable_web_page_preview=True,
        )
    else:
        await message.reply_text(
            "✅ <b>Family relationship removed.</b>\n"
            f"{_link(subject_id, replied.from_user.full_name)} and "
            f"{_link(relative_id, await _name(context, relative_id, chat.id))} are no longer connected.\n"
            f"<i>Removed: {escape(', '.join(names))}</i>",
            parse_mode="HTML", disable_web_page_preview=True,
        )


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
        result = await _repo(context).add(user.id, target.id, command)
    except FamilyError as exc:
        await message.reply_text(str(exc), parse_mode="HTML")
        return

    if command in {"son", "daughter", "father", "mother"}:
        if command == "son":
            child, parent, child_role, parent_role = target, user, "son", "father"
        elif command == "daughter":
            child, parent, child_role, parent_role = target, user, "daughter", "mother"
        elif command == "father":
            child, parent, child_role, parent_role = user, target, "son", "father"
        else:
            child, parent, child_role, parent_role = user, target, "daughter", "mother"

        child_link = _link(child.id, child.username or child.full_name)
        parent_link = _link(parent.id, parent.username or parent.full_name)
        if result == "exists":
            confirmation = (
                f"✅ {child_link} is already connected as your {child_role}. "
                "No duplicate relationship was created."
            )
        else:
            confirmation = (
                f"✅ You have been connected as {parent_role} and {child_role}.\\n"
                f"Hey {child_role} {child_link}, meet your {parent_role} {parent_link}!"
            )
        try:
            await message.reply_text(confirmation, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            try:
                await context.bot.send_message(
                    chat_id=message.chat_id, text=confirmation,
                    parse_mode="HTML", disable_web_page_preview=True,
                )
            except Exception:
                import logging
                logging.getLogger(__name__).exception(
                    "Family relationship saved, but confirmation could not be delivered"
                )
        return

    labels = {"brother": "siblings", "sister": "siblings", "friend": "friends"}
    await message.reply_text(
        f"👨‍👩‍👧‍👦 <b>Family connection created!</b>\n"
        f"{_link(user.id, user.full_name)} and {_link(target.id, target.full_name)} "
        f"are now connected as {labels.get(command, 'family')}.",
        parse_mode="HTML", disable_web_page_preview=True)


async def _post_family_request(message, context, request_id: int, target_id: int, body: str) -> None:
    """Show a private-start link first; response buttons are enabled after /start."""
    sent = await message.reply_text(body, parse_mode="HTML", disable_web_page_preview=True)
    bot = await context.bot.get_me()
    payload = f"fr_{request_id}_{target_id}_{sent.chat_id}_{sent.message_id}"
    url = f"https://t.me/{bot.username}?start={payload}"
    await sent.edit_reply_markup(reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("➡️ Accept request — open XYRON DM", url=url)],
        [InlineKeyboardButton("Decline", callback_data=f"famreq:d:{request_id}")],
    ]))


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
    await _post_family_request(
        message, context, request_id, target.id,
        f"💍 <b>Marriage proposal</b>\n{_link(user.id, user.full_name)} has proposed to "
        f"{_link(target.id, target.full_name)} as their {command}.\n\n"
        "The intended person must open XYRON's DM and press Start before accepting. This proposal expires in 24 hours."
    )


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
    await _post_family_request(
        message, context, request_id, target.id,
        f"💔 <b>Divorce request</b>\n{_link(user.id, user.full_name)} has requested a mutual divorce from "
        f"{_link(target.id, target.full_name)}.\n\n"
        "The intended person must open XYRON's DM and press Start before accepting. Expires in 24 hours."
    )


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
        await context.bot.send_message(chat_id=requester_id, text=f"Your {verb} request was {outcome} by the other person.")
    except Exception:
        pass
    try:
        await context.bot.send_message(chat_id=target_id, text=f"You {outcome} the {verb} request.")
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
    grouped: dict[str, list[int]] = {}
    for edge in edges:
        grouped.setdefault(edge.relation, []).append(edge.related_user_id)

    father_ids = list(dict.fromkeys(grouped.get("father", [])))
    mother_ids = list(dict.fromkeys(grouped.get("mother", [])))
    spouse_ids = list(dict.fromkeys(grouped.get("wife", []) + grouped.get("husband", [])))

    ranking = await _repo(context).leaderboard("total", limit=1000000)
    world_rank = next((index for index, row in enumerate(ranking, 1) if row[0] == target.id), None)
    rank_text = f"🌍 World Rank: <b>#{world_rank:,}</b>" if world_rank else "🌍 World Rank: <b>Unranked</b>"

    spouse_text = (
        f"💍 {_link(target.id, target.full_name)} ❤️ "
        f"{_link(spouse_ids[0], await _name(context, spouse_ids[0], message.chat_id))}"
        if spouse_ids else "💍 Spouse: —"
    )
    father_text = (
        f"👨 Father: {_link(father_ids[0], await _name(context, father_ids[0], message.chat_id))}"
        if father_ids else "👨 Father: —"
    )
    mother_text = (
        f"👩 Mother: {_link(mother_ids[0], await _name(context, mother_ids[0], message.chat_id))}"
        if mother_ids else "👩 Mother: —"
    )

    sons_count = len(set(grouped.get("son", [])))
    daughters_count = len(set(grouped.get("daughter", [])))
    siblings_count = len(set(grouped.get("sibling", [])))
    friends_count = len(set(grouped.get("friend", [])))

    lines = [
        f"🌳 <b>{escape(target.full_name)}'s Family</b>",
        f"🏅 Level <b>{level}/25 · {title}</b>  •  <b>{count}</b> connections",
        rank_text,
        "",
        spouse_text,
        father_text,
        mother_text,
        f"👦 Sons: <b>{sons_count}</b>",
        f"👧 Daughters: <b>{daughters_count}</b>",
        f"🧑‍🤝‍🧑 Siblings: <b>{siblings_count}</b>",
        f"🤝 Friends: <b>{friends_count}</b>",
    ]
    if count == 0:
        lines.extend(["", "<i>No family connections yet. Reply to someone with a Family command to get started.</i>"])
    await message.reply_text("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


async def familytree_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await family_command(update, context)


_RANKS = {
    "sons": ("👦 Most Sons", "son"),
    "daughters": ("👧 Most Daughters", "daughter"),
    "total": ("❤️ Most Total Relationships", "total"),
    "marriage": ("💍 Longest Marriage", "marriage"),
}


async def _couple_page(context, user_id: int):
    repo = _repo(context)
    stats = await repo.couple_protection_stats(user_id)
    partner_id = stats["partner_id"]
    partner_name = await _name(context, partner_id)
    status = "✅ Already protected today" if stats["protected_today"] else "🟢 Available today"
    text = (
        "🛡️ <b>COUPLE PROTECTION</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"❤️ {_link(user_id, await _name(context, user_id))}  ×  {_link(partner_id, partner_name)}\n\n"
        f"🏆 <b>{stats['total_points']:,}</b> protection points\n"
        f"🙋 Your contribution: <b>{stats['your_points']:,}</b> points\n"
        f"💗 Partner's contribution: <b>{stats['partner_points']:,}</b> points\n\n"
        f"🛡️ Your daily protection: <b>{status}</b>\n"
        "<i>Protect your partner once a day to add 50 points to your couple score. "
        "If both partners protect each other, your couple earns 100 points that day.</i>"
    )
    button = "✅ Protected Today" if stats["protected_today"] else "🛡️ Protect Your Partner (+50)"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(button, callback_data="cp:done" if stats["protected_today"] else "cp:protect")],
    ])
    return text, keyboard


async def cp_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user = update.effective_message, update.effective_user
    if not message or not user:
        return
    try:
        text, keyboard = await _couple_page(context, user.id)
    except FamilyError as exc:
        await message.reply_text(f"💍 {escape(str(exc))}", parse_mode="HTML")
        return
    await message.reply_text(text, parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True)


async def cp_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query, user = update.callback_query, update.effective_user
    if not query or not user or not query.data:
        return
    if query.data == "cp:done":
        await query.answer("You have already protected your partner today.", show_alert=True)
        return
    try:
        await _repo(context).protect_partner(user.id)
    except FamilyError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("🛡️ +50 protection points added!")
    if query.message:
        text, keyboard = await _couple_page(context, user.id)
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True)


async def familyrank_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    keyboard = [[InlineKeyboardButton(label, callback_data=f"famrank:choose:{key}")]
                for key, (label, _) in _RANKS.items()]
    await message.reply_text(
        "🏆 <b>FAMILY HALL OF FAME</b>\nChoose a leaderboard.",
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
    if action in {"top", "couples"}:
        if value not in {"global", "chat"}:
            await query.answer("Invalid scope.", show_alert=True)
            return
        chat_id = query.message.chat_id if query.message else None
        text = await _familytop_render(context, value, chat_id, couples=(action == "couples"))
        await query.answer()
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=await _familytop_keyboard(value), disable_web_page_preview=True)
        return
    if action == "menu":
        keyboard = [[InlineKeyboardButton(label, callback_data=f"famrank:choose:{key}")]
                    for key, (label, _) in _RANKS.items()]
        await query.answer()
        await query.edit_message_text("🏆 <b>FAMILY HALL OF FAME</b>\nChoose a leaderboard.",
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
        await query.edit_message_text(f"🏆 <b>{label}</b>\nChoose the ranking scope.",
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
            text = f"💍 <b>LONGEST MARRIAGES · {scope.upper()}</b>\nNo marriages yet."
        else:
            now = datetime.now(UTC)
            lines = [f"💍 <b>LONGEST MARRIAGES · {scope.upper()}</b>"]
            for i, (left, right, since) in enumerate(rows, 1):
                days = max(0, (now - since).days)
                lines.append(f"{['🥇','🥈','🥉'][i-1] if i <= 3 else str(i)+'.'} {_link(left, await _name(context, left, chat_id))} + {_link(right, await _name(context, right, chat_id))} — <b>{days} days</b>")
            text = "\n".join(lines)
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
            text = f"{_RANKS[category][0]} · {scope.upper()}\nNo connections yet."
        else:
            lines = [f"🏆 <b>FAMILY HALL OF FAME</b>\n{_RANKS[category][0]} · {scope.upper()}"]
            medals = ["🥇", "🥈", "🥉"]
            for i, (uid, score) in enumerate(rows[:10], 1):
                name = await _name(context, uid, chat_id)
                noun = "connection" if score == 1 else "connections"
                lines.append(f"{medals[i-1] if i <= 3 else str(i)+'.'} {_link(uid, name)}  <b>{score}</b> {noun}")
            text = "\n".join(lines)
    await query.answer()
    keyboard = [[InlineKeyboardButton("👦 Sons", callback_data="famrank:choose:sons"),
                 InlineKeyboardButton("👧 Daughters", callback_data="famrank:choose:daughters")],
                [InlineKeyboardButton("❤️ Total", callback_data="famrank:choose:total"),
                 InlineKeyboardButton("💍 Marriage", callback_data="famrank:choose:marriage")],
                [InlineKeyboardButton("🔄 Menu", callback_data="famrank:menu:all")]]
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard),
                                  disable_web_page_preview=True)


async def _familytop_keyboard(scope: str = "global") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(("✓ " if scope == "global" else "") + "🌐 Global", callback_data="famrank:top:global"),
            InlineKeyboardButton(("✓ " if scope == "chat" else "") + "💬 This Chat", callback_data="famrank:top:chat"),
        ],
        [InlineKeyboardButton("💍 Couple Top", callback_data="famrank:couples:global")],
    ])


async def _familytop_render(context, scope: str, chat_id: int | None, couples: bool = False) -> str:
    repo = _repo(context)
    if couples:
        rows = await repo.top_couples(limit=10000)
        if scope == "chat" and chat_id:
            from sqlalchemy import select
            from app.database.models import KnownUser
            async with context.application.bot_data["sessions"]() as session:
                ids = set(await session.scalars(
                    select(KnownUser.user_id).where(KnownUser.chat_id == chat_id)
                ))
            rows = [row for row in rows if row[0] in ids and row[1] in ids]
        rows = rows[:15]
        heading = f"💍 <b>COUPLE TOP 15</b>  ·  {'GLOBAL' if scope == 'global' else 'THIS CHAT'}"
        if not rows:
            return heading + "\n\nNo couples yet."
        lines = [heading, "<i>Ranked by couple protection points</i>", ""]
        medals = ["🥇", "🥈", "🥉"]
        for i, (left, right, points) in enumerate(rows, 1):
            rank = medals[i - 1] if i <= 3 else f"<b>{i}.</b>"
            left_name = await _name(context, left, chat_id)
            right_name = await _name(context, right, chat_id)
            # Keep names and scores on separate lines so long Telegram
            # usernames cannot make the Couple Top leaderboard look crowded.
            lines.append(
                f"{rank} {_link(left, left_name)} ❤️ {_link(right, right_name)}"
                f"\n   🛡️ <b>{points:,} protection points</b>"
            )
        return "\n".join(lines)

    rows = await repo.leaderboard("total", limit=10000)
    if scope == "chat" and chat_id:
        from sqlalchemy import select
        from app.database.models import KnownUser
        async with context.application.bot_data["sessions"]() as session:
            ids = set(await session.scalars(
                select(KnownUser.user_id).where(KnownUser.chat_id == chat_id)
            ))
        rows = [row for row in rows if row[0] in ids]
    rows = rows[:15]
    heading = f"🏆 <b>FAMILY TOP 15</b>  ·  {'GLOBAL' if scope == 'global' else 'THIS CHAT'}"
    if not rows:
        return heading + "\n\nNo family relationships yet. Be the first to build your family!"
    medals = ["🥇", "🥈", "🥉"]
    lines = [heading, "<i>Members with the most family connections</i>", ""]
    for i, (uid, score) in enumerate(rows, 1):
        rank = medals[i - 1] if i <= 3 else f"<b>{i}.</b>"
        name = await _name(context, uid, chat_id)
        noun = "connection" if score == 1 else "connections"
        lines.append(f"{rank} {_link(uid, name)}  —  <b>{score:,}</b> {noun}")
    return "\n".join(lines)


async def familytop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    text = await _familytop_render(context, "global", message.chat_id)
    await message.reply_text(
        text, parse_mode="HTML", reply_markup=await _familytop_keyboard("global"),
        disable_web_page_preview=True,
    )


async def familyhelp_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(
            "🌳 FAMILY — GLOBAL GAME\n\n"
            "Reply to a user's message: /son, /daughter, /father, /mother, /brother, /sister or /friend.\n"
            "Reply with /wife or /husband to propose marriage; the other person must accept. "
            "Reply with /divorce to request a mutual divorce. Requests expire after 24 hours.\n\n"
            "/family — profile (or reply to view another member)\n/familyrank — interactive leaderboards\n"
            "/familyhelp — commands")


async def familyreset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user = update.effective_message, update.effective_user
    if not message or not user:
        return
    settings = context.application.bot_data.get("settings")
    owner_ids = set(getattr(settings, "owner_ids", []) or [])
    if user.id not in owner_ids:
        await message.reply_text("Only the bot owner can reset Family data.")
        return
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚠️ Yes, reset Family", callback_data=f"famreset:confirm:{user.id}"),
         InlineKeyboardButton("Cancel", callback_data=f"famreset:cancel:{user.id}")]
    ])
    await message.reply_text(
        "⚠️ <b>Reset all Family data?</b>\n\n"
        "This deletes only Family relationship records (parents, children, siblings, friends and spouses).\n\n"
        "Family requests, users, groups, bot admins, commands, moderation records, other games and settings will remain untouched.",
        parse_mode="HTML", reply_markup=keyboard,
    )


async def familyreset_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not user or not query.data:
        return
    parts = query.data.split(":")
    if len(parts) != 3 or parts[1] not in {"confirm", "cancel"}:
        await query.answer("Invalid action.", show_alert=True)
        return
    settings = context.application.bot_data.get("settings")
    owner_ids = set(getattr(settings, "owner_ids", []) or [])
    try:
        initiator_id = int(parts[2])
    except ValueError:
        await query.answer("Invalid confirmation.", show_alert=True)
        return
    if user.id not in owner_ids or user.id != initiator_id:
        await query.answer("Only the owner who started this reset can confirm it.", show_alert=True)
        return
    if parts[1] == "cancel":
        await query.answer("Reset cancelled.")
        await query.edit_message_text("Family reset cancelled. No data was changed.")
        return
    relationships = await _repo(context).reset_relationships()
    await query.answer("Family relationships reset.")
    await query.edit_message_text(
        "✅ <b>Family relationship reset complete.</b>\n\n"
        f"Relationships deleted: <b>{relationships}</b>\n\n"
        "Family requests and all other XYRON data remain untouched.",
        parse_mode="HTML",
    )
