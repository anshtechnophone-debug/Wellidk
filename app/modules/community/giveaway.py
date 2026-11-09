"""Seven-day group giveaway leaderboard and participation flow."""

from __future__ import annotations

import base64
import logging
import re
import unicodedata
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
PRIZE_LABELS = ("VICE CREAM NFT", "₹175", "₹75")
MINIMUM_POINTS = (9000, 6500, 5000)


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
        "🏆 <b>Prize targets:</b> 1st — 9,000 points (Vice Cream NFT) · 2nd — 6,500 points (₹175) · 3rd — 5,000 points (₹75).\n"
        "Your rank depends on your points. Use <b>/prize</b> in the group to check the leaderboard.\\n"
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


def _fit_font(draw: ImageDraw.ImageDraw, text: str, max_width: int, size: int, bold: bool = True) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Shrink text until it fits its designated panel."""
    current = size
    while current > 13:
        font = _load_font(current, bold)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
        current -= 1
    return _load_font(13, bold)


def _render_leaderboard(state: dict, entries: list[tuple[int, dict]], ends_at: datetime, active: bool, avatars: dict[int, bytes | None]) -> BytesIO:
    """Draw a complete leaderboard poster in Pillow; no background artwork is required."""
    width, height = 1080, 1720
    image = Image.new("RGB", (width, height), (12, 15, 32))
    pixels = image.load()

    # Subtle vertical navy-to-indigo gradient, drawn directly by Python.
    for y in range(height):
        t = y / max(1, height - 1)
        color = (
            int(12 + 13 * t),
            int(15 + 5 * t),
            int(32 + 28 * t),
        )
        for x in range(width):
            pixels[x, y] = color

    draw = ImageDraw.Draw(image)
    white = (247, 247, 250)
    muted = (171, 181, 204)
    gold = (255, 218, 133)
    silver = (221, 231, 246)
    bronze = (224, 166, 126)
    accents = (gold, silver, bronze)

    # Decorative glow rings keep the poster polished without using a template.
    for bounds, outline in (
        ((-180, 80, 300, 560), (43, 47, 81)),
        ((820, 40, 1260, 480), (39, 43, 77)),
        ((780, 1000, 1190, 1410), (37, 42, 75)),
    ):
        draw.ellipse(bounds, outline=outline, width=5)

    title_font = _load_font(58, True)
    sub_font = _load_font(24, True)
    small_font = _load_font(21, False)
    draw.text((width // 2, 76), "XYRON", font=_load_font(27, True), fill=(151, 164, 205), anchor="mm")
    draw.text((width // 2, 145), "WEEKLY GIVEAWAY", font=title_font, fill=white, anchor="mm")
    draw.text(
        (width // 2, 198),
        f"{_remaining(ends_at)} LEFT" if active else "GIVEAWAY ENDED · FINAL RESULTS",
        font=sub_font,
        fill=(123, 239, 183) if active else gold,
        anchor="mm",
    )

    # NFT feature panel: original Pillow artwork, not a supplied background image.
    nft_box = (110, 245, 970, 440)
    draw.rounded_rectangle(nft_box, radius=34, fill=(36, 31, 45), outline=(181, 155, 111), width=3)
    draw.rounded_rectangle((132, 265, 360, 420), radius=25, fill=(235, 225, 198), outline=(255, 245, 222), width=2)
    nft_bytes = base64.b64decode("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")
    nft_image = Image.open(BytesIO(nft_bytes)).convert("RGB")
    nft_image = ImageOps.fit(nft_image, (208, 140), method=Image.Resampling.LANCZOS)
    nft_mask = Image.new("L", (208, 140), 0)
    ImageDraw.Draw(nft_mask).rounded_rectangle((0, 0, 207, 139), radius=18, fill=255)
    image.paste(nft_image, (142, 273), nft_mask)
    draw.text((395, 300), "1ST PRIZE", font=_load_font(23, True), fill=gold)
    draw.text((395, 345), "VICE CREAM NFT", font=_fit_font(draw, "VICE CREAM NFT", 520, 42, True), fill=(255, 244, 220))
    draw.text((395, 392), "MINIMUM TARGET  ·  9,000 POINTS", font=_load_font(20, True), fill=(222, 211, 185))

    # Two smaller prize labels sit below the NFT panel.
    prize_y = 475
    prize_cards = (
        ((110, prize_y, 520, prize_y + 112), silver, "2ND PRIZE", "₹175", "6,500 POINTS"),
        ((560, prize_y, 970, prize_y + 112), bronze, "3RD PRIZE", "₹75", "5,000 POINTS"),
    )
    for box, accent, label, prize, target in prize_cards:
        draw.rounded_rectangle(box, radius=25, fill=(25, 30, 51), outline=accent, width=2)
        x1, y1, x2, y2 = box
        draw.text((x1 + 24, y1 + 28), label, font=_load_font(19, True), fill=accent)
        draw.text((x1 + 24, y1 + 72), prize, font=_load_font(35, True), fill=white, anchor="lm")
        draw.text((x2 - 22, y1 + 76), target, font=_fit_font(draw, target, 190, 17, True), fill=muted, anchor="rm")

    draw.text((width // 2, 635), "TOP 3 LEADERBOARD", font=_load_font(29, True), fill=white, anchor="mm")

    # Equal-width podium cards keep every PFP and name aligned on the same grid.
    card_y, card_h, card_w, gap = 685, 480, 270, 28
    left = (width - (3 * card_w + 2 * gap)) // 2
    slots = []
    for index in range(3):
        x1 = left + index * (card_w + gap)
        slots.append((x1, card_y, x1 + card_w, card_y + card_h))

    for index, box in enumerate(slots):
        rank = index + 1
        accent = accents[index]
        x1, y1, x2, y2 = box
        fill = (35, 34, 49) if rank == 1 else (25, 31, 50)
        draw.rounded_rectangle(box, radius=28, fill=fill, outline=accent, width=3)
        draw.text(((x1 + x2) // 2, y1 + 42), ("1ST", "2ND", "3RD")[index], font=_load_font(30, True), fill=accent, anchor="mm")

        if index < len(entries):
            user_id, data = entries[index]
            name = str(data.get("name") or "Member").strip() or "Member"
            score = _score(data)
            photo_bytes = avatars.get(user_id)
        else:
            user_id, name, score, photo_bytes = None, "OPEN SPOT", 0.0, None

        avatar_size = 142
        avatar_x = (x1 + x2 - avatar_size) // 2
        avatar_y = y1 + 82
        _draw_avatar(image, (avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size), name, photo_bytes, rank)
        draw = ImageDraw.Draw(image)

        name = re.sub(r"\s+", " ", name)
        name_font = _fit_font(draw, name, card_w - 38, 24, True)
        draw.text(((x1 + x2) // 2, y1 + 270), name, font=name_font, fill=white, anchor="mm")

        score_text = f"{score:,.1f}" if user_id is not None else "—"
        draw.text(((x1 + x2) // 2, y1 + 326), score_text, font=_load_font(39, True), fill=accent, anchor="mm")
        draw.text(((x1 + x2) // 2, y1 + 365), "POINTS", font=_load_font(17, True), fill=muted, anchor="mm")
        target_text = f"TARGET  {MINIMUM_POINTS[index]:,}"
        draw.rounded_rectangle((x1 + 24, y2 - 78, x2 - 24, y2 - 30), radius=18, fill=(17, 21, 37), outline=accent, width=1)
        draw.text(((x1 + x2) // 2, y2 - 54), target_text, font=_fit_font(draw, target_text, card_w - 56, 17, True), fill=accent, anchor="mm")

    # Five additional participants appear below the podium; only ranks 1–3 win.
    draw.text((width // 2, 1205), "MORE LEADERS  ·  RANKS 4–8", font=_load_font(25, True), fill=white, anchor="mm")
    for rank in range(4, 9):
        row_y = 1240 + (rank - 4) * 67
        draw.rounded_rectangle((110, row_y, 970, row_y + 54), radius=17, fill=(25, 31, 50), outline=(63, 73, 103), width=1)
        draw.text((145, row_y + 27), f"#{rank}", font=_load_font(22, True), fill=muted, anchor="lm")
        if rank - 1 < len(entries):
            user_id, data = entries[rank - 1]
            name = re.sub(r"\s+", " ", str(data.get("name") or "Member").strip() or "Member")
            score = _score(data)
        else:
            name, score = "OPEN SPOT", 0.0
        draw.text((225, row_y + 27), name, font=_fit_font(draw, name, 490, 21, True), fill=white, anchor="lm")
        score_text = f"{score:,.1f} pts" if rank - 1 < len(entries) else "— pts"
        draw.text((930, row_y + 27), score_text, font=_fit_font(draw, score_text, 190, 20, True), fill=muted, anchor="rm")

    draw.text((width // 2, 1615), "REPLY  +1 POINT   ·   STANDALONE TEXT  +0.5 POINT", font=_fit_font(draw, "REPLY  +1 POINT   ·   STANDALONE TEXT  +0.5 POINT", 950, 19, True), fill=muted, anchor="mm")
    draw.text((width // 2, 1655), "JOIN FIRST · ONLY MESSAGES AFTER JOINING COUNT", font=_load_font(17, True), fill=(137, 150, 178), anchor="mm")
    draw.text((width // 2, 1695), "XYRON  •  7-DAY COMMUNITY GIVEAWAY", font=_load_font(17, True), fill=(111, 125, 157), anchor="mm")

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    output.name = "xyron-giveaway.png"
    return output

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
        entries = _leaderboard(state)[:8]
        avatars: dict[int, bytes | None] = {}
        for user_id, _data in entries[:3]:
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
