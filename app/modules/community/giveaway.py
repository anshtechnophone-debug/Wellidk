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
    """Prefer broad Unicode fonts so Telegram display names don't become tofu boxes."""
    paths = (
        (
            "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        )
        if bold
        else (
            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        )
    )
    for path in paths:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _display_name(value: object) -> str:
    """Normalize decorative Unicode and remove unsupported emoji glyphs from raster text."""
    name = unicodedata.normalize("NFKC", str(value or "")).strip()
    name = "".join(char for char in name if char.isprintable() and unicodedata.category(char) not in {"So", "Cs"})
    return name or "Member"


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
    """Render a premium mobile leaderboard with the real Vice Cream NFT artwork."""
    import base64
import unicodedata

    width, height = 1080, 1350
    image = Image.new("RGB", (width, height), (9, 13, 31))
    draw = ImageDraw.Draw(image)
    top, bottom = (10, 16, 38), (35, 19, 57)
    for y in range(height):
        ratio = y / (height - 1)
        color = tuple(int(top[i] * (1 - ratio) + bottom[i] * ratio) for i in range(3))
        draw.line((0, y, width, y), fill=color)
    draw.ellipse((-250, -330, 570, 460), fill=(25, 43, 91))
    draw.ellipse((660, 860, 1340, 1510), fill=(65, 30, 86))
    draw = ImageDraw.Draw(image)

    title_font = _load_font(58, True)
    eyebrow_font = _load_font(20, True)
    subtitle_font = _load_font(25, True)
    rank_font = _load_font(23, True)
    name_font = _load_font(39, True)
    points_font = _load_font(34, True)
    prize_font = _load_font(25, True)
    small_font = _load_font(18, True)
    white, muted = (248, 249, 255), (166, 180, 207)
    gold, silver, bronze = (255, 211, 105), (211, 222, 241), (224, 157, 116)

    # Header: compact, with stronger hierarchy and more breathing room.
    draw.rounded_rectangle((42, 35, width - 42, 220), radius=40, fill=(17, 25, 51), outline=(78, 91, 137), width=2)
    draw.text((width // 2, 62), "XYRON PRESENTS", font=eyebrow_font, fill=(147, 169, 222), anchor="mt")
    draw.text((width // 2, 94), "GIVEAWAY", font=title_font, fill=white, anchor="mt")
    status = ("LIVE  •  " + _remaining(ends_at) + " LEFT") if active else "GIVEAWAY RESULTS"
    draw.text((width // 2, 177), status, font=subtitle_font, fill=(126, 237, 181) if active else gold, anchor="mt")
    draw.text((width // 2, 255), "TOP PLAYERS  •  HIGHEST POINTS WIN", font=eyebrow_font, fill=muted, anchor="mm")

    # Actual Vice Cream artwork cropped from the supplied NFT screenshot.
    nft_bytes = base64.b64decode("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")
    nft_image = Image.open(BytesIO(nft_bytes)).convert("RGB")
    nft_image = ImageOps.fit(nft_image, (230, 310), method=Image.Resampling.LANCZOS)

    # Grand-prize card gets a distinct, larger composition.
    y1, y2 = 292, 752
    draw.rounded_rectangle((42, y1, width - 42, y2), radius=38, fill=(18, 27, 53), outline=gold, width=4)
    draw.rounded_rectangle((60, y1 + 18, width - 60, y1 + 27), radius=5, fill=gold)
    draw.rounded_rectangle((74, y1 + 47, 390, y1 + 98), radius=20, fill=(57, 49, 67), outline=gold, width=1)
    draw.text((232, y1 + 72), "1ST  •  NFT GRAND PRIZE", font=rank_font, fill=gold, anchor="mm")

    art_box = (78, y1 + 120, 338, y1 + 445)
    draw.rounded_rectangle(art_box, radius=28, fill=(84, 145, 66), outline=(255, 222, 139), width=4)
    art_mask = Image.new("L", nft_image.size, 0)
    ImageDraw.Draw(art_mask).rounded_rectangle((0, 0, 229, 309), radius=22, fill=255)
    image.paste(nft_image, (93, y1 + 128), art_mask)

    if entries:
        user_id, data = entries[0]
        name = _display_name(data.get("name"))
        score = _score(data)
    else:
        user_id, name, score = None, "OPEN SPOT", 0.0

    info_x = 375
    draw.text((info_x, y1 + 158), "GRAND PRIZE WINNER", font=small_font, fill=(177, 190, 218))
    # Clip long names without squeezing the entire card.
    shown_name = name
    while shown_name and draw.textbbox((0, 0), shown_name, font=name_font)[2] > 600:
        shown_name = shown_name[:-2].rstrip() + "…"
    draw.text((info_x, y1 + 203), shown_name, font=name_font, fill=white)
    draw.text((info_x, y1 + 270), f"{score:g} POINTS" if user_id is not None else "— POINTS", font=points_font, fill=(126, 237, 181) if user_id is not None else muted)
    draw.rounded_rectangle((info_x, y1 + 338, 975, y1 + 410), radius=22, fill=(54, 46, 62), outline=(138, 111, 67), width=1)
    draw.text(((info_x + 975) // 2, y1 + 374), "VICE CREAM  •  NFT GIFT", font=prize_font, fill=gold, anchor="mm")
    draw.text(((info_x + 975) // 2, y1 + 430), "ONE-OF-A-KIND TELEGRAM GIFT", font=small_font, fill=(183, 193, 215), anchor="mm")

    # Compact silver and bronze runner-up cards.
    cards = (
        (790, 1005, 2, silver, "2ND  •  RUNNER-UP", "₹175"),
        (1025, 1240, 3, bronze, "3RD  •  THIRD PLACE", "₹75"),
    )
    for top_y, bottom_y, rank, accent, label, prize in cards:
        draw.rounded_rectangle((42, top_y, width - 42, bottom_y), radius=30, fill=(18, 27, 53), outline=accent, width=3)
        draw.rounded_rectangle((62, top_y + 15, width - 62, top_y + 23), radius=4, fill=accent)
        draw.rounded_rectangle((72, top_y + 39, 385, top_y + 83), radius=17, fill=(45, 48, 73), outline=accent, width=1)
        draw.text((228, top_y + 61), label, font=rank_font, fill=accent, anchor="mm")
        if len(entries) >= rank:
            user_id, data = entries[rank - 1]
            name = str(data.get("name") or "Member").strip() or "Member"
            score = _score(data)
            photo_bytes = avatars.get(user_id)
        else:
            user_id, name, score, photo_bytes = None, "OPEN SPOT", 0.0, None
        _draw_avatar(image, (78, top_y + 99, 180, top_y + 201), name, photo_bytes, rank)
        draw = ImageDraw.Draw(image)
        shown_name = name
        while shown_name and draw.textbbox((0, 0), shown_name, font=name_font)[2] > 440:
            shown_name = shown_name[:-2].rstrip() + "…"
        draw.text((210, top_y + 105), shown_name, font=name_font, fill=white)
        draw.text((210, top_y + 153), f"{score:g} POINTS" if user_id is not None else "— POINTS", font=points_font, fill=(126, 237, 181) if user_id is not None else muted)
        draw.rounded_rectangle((710, top_y + 119, 985, top_y + 184), radius=18, fill=(42, 47, 70))
        draw.text((847, top_y + 151), prize, font=prize_font, fill=accent, anchor="mm")

    draw.text((width // 2, 1280), "KEEP CHATTING. CLIMB THE LEADERBOARD.", font=eyebrow_font, fill=white, anchor="mm")
    draw.text((width // 2, 1315), "POWERED BY XYRON", font=eyebrow_font, fill=(139, 151, 184), anchor="mm")
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
