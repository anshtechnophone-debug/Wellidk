"""Seven-day group giveaway leaderboard and participation flow."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from html import escape
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont, ImageOps

from telegram import Chat, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from app.core.permissions import AuthorizationError, require_admin_access
from app.services.group_policy import GroupPolicyRepository

DURATION = timedelta(days=7)
PRIZE_LABELS = ("NFT GIFT · VICE CREAM", "₹175", "₹75")


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
        "💬 <b>Earn points</b>\n"
        "• Text reply: <b>+1 point</b>\n"
        "• Standalone text: <b>+0.5 point</b>\n"
        "• Media, stickers, captions and commands: <b>0 points</b>\n\n"
        "✅ Join first. Only messages sent after joining count.\n"
        "🏆 Your rank depends on your points. Use <b>/prize</b> in the group.\n"
        "⏳ The giveaway ends after 7 days."
    )

logger = logging.getLogger(__name__)


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a widely available system font, with a safe Pillow fallback."""
    paths = (
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf")
        if bold else
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf")
    )
    for path in paths:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


async def _profile_photo_bytes(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bytes | None:
    """Fetch the user's small Telegram avatar, if the Bot API exposes one."""
    try:
        photos = await context.bot.get_user_profile_photos(user_id=user_id, limit=1)
        if not photos.photos:
            return None
        photo = photos.photos[0][-1]
        telegram_file = await context.bot.get_file(photo.file_id)
        return bytes(await telegram_file.download_as_bytearray())
    except (TelegramError, OSError, ValueError):
        logger.debug("Could not fetch giveaway avatar for user_id=%s", user_id, exc_info=True)
        return None


def _draw_avatar(canvas: Image.Image, box: tuple[int, int, int, int], name: str, photo_bytes: bytes | None, rank: int) -> None:
    """Draw a circular profile picture or a matching initials fallback."""
    x1, y1, x2, y2 = box
    size = x2 - x1
    avatar = None
    if photo_bytes:
        try:
            source = Image.open(BytesIO(photo_bytes)).convert("RGB")
            avatar = ImageOps.fit(source, (size, size), method=Image.Resampling.LANCZOS)
        except (OSError, ValueError):
            avatar = None
    if avatar is None:
        colors = ((236, 190, 82), (171, 185, 205), (190, 130, 87))
        avatar = Image.new("RGB", (size, size), colors[rank - 1])
        draw = ImageDraw.Draw(avatar)
        initials = "".join(part[0] for part in name.split()[:2] if part)[:2].upper() or "?"
        font = _load_font(30, True)
        bounds = draw.textbbox((0, 0), initials, font=font)
        draw.text(((size - (bounds[2] - bounds[0])) / 2, (size - (bounds[3] - bounds[1])) / 2 - bounds[1]), initials, fill=(20, 25, 42), font=font)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    canvas.paste(avatar, (x1, y1), mask)
    border = ImageDraw.Draw(canvas)
    ring = ((255, 208, 91), (205, 216, 235), (205, 143, 94))[rank - 1]
    border.ellipse(box, outline=ring, width=5)


def _render_leaderboard(state: dict, entries: list[tuple[int, dict]], ends_at: datetime, active: bool, avatars: dict[int, bytes | None]) -> BytesIO:
    """Render a premium, mobile-first top-three points leaderboard."""
    width, height = 1080, 1350
    image = Image.new("RGB", (width, height), (8, 12, 29))
    draw = ImageDraw.Draw(image)
    top, bottom = (8, 13, 31), (31, 20, 54)
    for y in range(height):
        ratio = y / (height - 1)
        color = tuple(int(top[i] * (1 - ratio) + bottom[i] * ratio) for i in range(3))
        draw.line((0, y, width, y), fill=color)
    draw.ellipse((-230, -330, 560, 460), fill=(24, 39, 82))
    draw.ellipse((650, 820, 1320, 1500), fill=(54, 27, 79))
    draw = ImageDraw.Draw(image)

    title_font = _load_font(58, True)
    subtitle_font = _load_font(25)
    eyebrow_font = _load_font(20, True)
    rank_font = _load_font(23, True)
    name_font = _load_font(37, True)
    points_font = _load_font(31, True)
    prize_font = _load_font(25, True)
    footer_font = _load_font(20, True)
    white, muted = (248, 249, 255), (166, 180, 207)
    gold, silver, bronze = (255, 211, 105), (211, 222, 241), (224, 157, 116)
    accents = (gold, silver, bronze)

    draw.rounded_rectangle((42, 38, width - 42, 238), radius=42, fill=(17, 25, 51), outline=(78, 91, 137), width=2)
    draw.text((width // 2, 66), "XYRON PRESENTS", font=eyebrow_font, fill=(147, 169, 222), anchor="mt")
    draw.text((width // 2, 101), "GIVEAWAY", font=title_font, fill=white, anchor="mt", stroke_width=1, stroke_fill=(102, 91, 151))
    status = ("LIVE  •  " + _remaining(ends_at) + " LEFT") if active else "GIVEAWAY RESULTS"
    draw.text((width // 2, 184), status, font=subtitle_font, fill=(126, 237, 181) if active else gold, anchor="mt")
    draw.text((width // 2, 270), "TOP PLAYERS  •  HIGHEST POINTS WIN", font=eyebrow_font, fill=muted, anchor="mm")

    labels = ("1ST  •  GRAND PRIZE", "2ND  •  RUNNER-UP", "3RD  •  THIRD PLACE")
    for index in range(3):
        rank, y1 = index + 1, 310 + index * 315
        accent = accents[index]
        draw.rounded_rectangle((42, y1, width - 42, y1 + 282), radius=34, fill=(17, 25, 49), outline=accent, width=3)
        draw.rounded_rectangle((58, y1 + 17, width - 58, y1 + 26), radius=5, fill=accent)
        draw.rounded_rectangle((72, y1 + 48, 390, y1 + 98), radius=20, fill=(45, 48, 73), outline=accent, width=1)
        draw.text((231, y1 + 73), labels[index], font=rank_font, fill=accent, anchor="mm")
        if index < len(entries):
            user_id, data = entries[index]
            name = str(data.get("name") or "Member").strip() or "Member"
            score, photo_bytes = _score(data), avatars.get(user_id)
        else:
            user_id, name, score, photo_bytes = None, "OPEN SPOT", 0.0, None
        _draw_avatar(image, (78, y1 + 120, 202, y1 + 244), name, photo_bytes, rank)
        draw = ImageDraw.Draw(image)
        while name and draw.textbbox((0, 0), name, font=name_font)[2] > 690:
            name = name[:-2].rstrip() + "…"
        draw.text((230, y1 + 133), name, font=name_font, fill=white)
        draw.text((230, y1 + 187), f"{score:g} POINTS" if user_id is not None else "— POINTS", font=points_font, fill=(126, 237, 181) if user_id is not None else muted)
        draw.rounded_rectangle((72, y1 + 250, width - 72, y1 + 270), radius=10, fill=(36, 44, 70))
        draw.text((width // 2, y1 + 260), PRIZE_LABELS[index], font=prize_font, fill=accent, anchor="mm")

    draw.text((width // 2, 1280), "KEEP CHATTING. CLIMB THE LEADERBOARD.", font=footer_font, fill=white, anchor="mm")
    draw.text((width // 2, 1317), "POWERED BY XYRON", font=eyebrow_font, fill=(139, 151, 184), anchor="mm")
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    output.name = "xyron-giveaway-leaderboard.png"
    return output


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
            "💬 Earn points by chatting after you join.\n"
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
                    "💬 Reply = 1 point · Standalone text = 0.5 point\n"
                    "✅ Join with <b>/gw</b> before chatting to earn points.\n"
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
        active = bool(state.get("active"))
        entries = _leaderboard(state)[:3]
        avatars: dict[int, bytes | None] = {}
        for user_id, _data in entries:
            avatars[user_id] = await _profile_photo_bytes(context, user_id)
        photo = _render_leaderboard(state, entries, ends_at, active, avatars)
        try:
            await message.reply_photo(photo=photo)
        except TelegramError:
            logger.exception("Could not send giveaway leaderboard image for chat_id=%s", chat.id)
            await message.reply_text("Couldn't send the leaderboard image. Please try /prize again.")
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
