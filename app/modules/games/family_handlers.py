"""Telegram handlers for the global Family game."""
from __future__ import annotations

from html import escape

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from app.modules.games.family import FamilyError, FamilyRepository, LEVELS


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
        await message.reply_text(str(exc))
        return
    labels = {
        "son": "son", "daughter": "daughter", "father": "father", "mother": "mother",
        "brother": "brother/sister", "sister": "brother/sister", "friend": "friend",
    }
    actor_name = escape(user.full_name)
    target_name = escape(target.full_name)
    await message.reply_text(f"👨‍👩‍👧‍👦 <b>Family updated!</b>\n{actor_name} and {target_name} are now connected as {labels.get(command, 'family')}.", parse_mode="HTML")


async def family_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, message = update.effective_user, update.effective_message
    if not user or not message:
        return
    edges, count, level, title = await _repo(context).profile(user.id)
    grouped = {}
    for edge in edges:
        grouped.setdefault(edge.relation, []).append(edge.related_user_id)
    lines = [f"🌳 <b>{escape(user.full_name)}'s Family</b>", f"Level: <b>{level}/25 · {title}</b>", f"Global relationships: <b>{count:,}</b>"]
    for relation, ids in grouped.items():
        names = [escape(await _name(context, uid, update.effective_chat.id if update.effective_chat else None)) for uid in ids[:8]]
        suffix = f" +{len(ids)-8} more" if len(ids) > 8 else ""
        lines.append(f"• <b>{escape(relation.title())}</b> ({len(ids)}): {', '.join(names)}{suffix}")
    if not edges:
        lines.append("No relationships yet. Reply to someone with /son, /daughter, /father, /mother, /brother, /sister or /friend.")
    await message.reply_text("\n".join(lines), parse_mode="HTML")


async def familytree_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await family_command(update, context)


async def familyrank_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await family_command(update, context)


async def familytop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    rows = await _repo(context).leaderboard()
    if not rows:
        await message.reply_text("No Family relationships yet.")
        return
    lines = ["🏆 <b>Global Family leaderboard</b>"]
    for index, (user_id, total) in enumerate(rows, 1):
        name = escape(await _name(context, user_id, update.effective_chat.id if update.effective_chat else None))
        lines.append(f'{index}. <a href="tg://user?id={user_id}">{name}</a> — {total} connections')
    await message.reply_text("\n".join(lines), parse_mode="HTML")


async def familyhelp_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(
            "🌳 FAMILY — GLOBAL GAME\n\n"
            "Reply to a user's message: /son, /daughter, /father, /mother, /brother, /sister or /friend.\n"
            "Parents and children are permanent; each user can have one father and one mother. "
            "Siblings merge into one shared sibling group. Friends are unlimited.\n\n"
            "/family — your profile\n/familytree — your connections\n"
            "/familyrank — your level\n/familytop — global leaderboard"
        )
