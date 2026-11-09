"""Seven-day group giveaway leaderboard and participation flow."""

from __future__ import annotations

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
PRIZE_LABELS = ("VICE CREAM NFT", "TELEGRAM PREMIUM", "150 TELEGRAM STARS")
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
        "🏆 <b>Prize targets:</b> 1st — 9,000 points (Vice Cream NFT) · 2nd — 6,500 points (Telegram Premium) · 3rd — 5,000 points (150 Telegram Stars).\n"
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
    """Render the navy-indigo global leaderboard with text-only prizes and aligned avatars."""
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

    # Text-only grand-prize panel. No NFT artwork or embedded image data.
    nft_box = (110, 245, 970, 440)
    draw.rounded_rectangle(nft_box, radius=34, fill=(36, 31, 45), outline=(181, 155, 111), width=3)
    draw.rounded_rectangle((132, 265, 948, 420), radius=25, fill=(29, 29, 48), outline=(112, 94, 119), width=2)
    draw.text((width // 2, 300), "1ST PRIZE", font=_load_font(23, True), fill=gold, anchor="mm")
    draw.text((width // 2, 348), "VICE CREAM NFT", font=_fit_font(draw, "VICE CREAM NFT", 740, 48, True), fill=(255, 244, 220), anchor="mm")
    draw.text((width // 2, 395), "MINIMUM TARGET  ·  9,000 POINTS", font=_load_font(20, True), fill=(222, 211, 185), anchor="mm")

    # Two smaller prize labels sit below the NFT panel.
    prize_y = 475
    prize_cards = (
        ((110, prize_y, 520, prize_y + 112), silver, "2ND PRIZE", "TELEGRAM PREMIUM", "6,500 POINTS"),
        ((560, prize_y, 970, prize_y + 112), bronze, "3RD PRIZE", "150 TELEGRAM STARS", "5,000 POINTS"),
    )
    for box, accent, label, prize, target in prize_cards:
        draw.rounded_rectangle(box, radius=25, fill=(25, 30, 51), outline=accent, width=2)
        x1, y1, x2, y2 = box
        draw.text((x1 + 24, y1 + 28), label, font=_load_font(19, True), fill=accent)
        draw.text((x1 + 24, y1 + 72), prize, font=_fit_font(draw, prize, x2 - x1 - 48, 27, True), fill=white, anchor="lm")
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

async def _global_state(context: ContextTypes.DEFAULT_TYPE) -> dict:
    state = (await _repo(context).get(0)).get("global_giveaway", {})
    return state if isinstance(state, dict) else {}


async def _save_global_state(context: ContextTypes.DEFAULT_TYPE, state: dict) -> None:
    await _repo(context).set_value(0, "global_giveaway", state)


async def _channel_member(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    channel = getattr(context.application.bot_data["settings"], "music_channel", "@xyronschannel")
    try:
        member = await context.bot.get_chat_member(channel, user_id)
        return member.status in {"member", "administrator", "creator"} or bool(getattr(member, "is_member", False))
    except TelegramError:
        return False


async def _eligible_group(context: ContextTypes.DEFAULT_TYPE, chat: Chat) -> bool:
    cache = context.application.bot_data.setdefault("giveaway_group_cache", {})
    now = _now().timestamp()
    cached = cache.get(chat.id)
    if cached and now - cached[0] < 300:
        return bool(cached[1])
    try:
        count = await context.bot.get_chat_member_count(chat.id)
        bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)
        ok = count >= 30 and bot_member.status in {"member", "administrator", "creator"}
    except TelegramError:
        ok = False
    cache[chat.id] = (now, ok)
    return ok


async def _send_global_board(message, context, state: dict) -> None:
    ends = _parse_time(state.get("ends_at")) or _now()
    active = bool(state.get("active")) and _now() < ends
    entries = _leaderboard(state)[:8]
    avatars = {uid: await _profile_photo_bytes(context, uid) for uid, _ in entries[:3]}
    await message.reply_photo(
        photo=_render_leaderboard(state, entries, ends, active, avatars),
        caption="🎁 GLOBAL GIVEAWAY PRIZE LEADERBOARD · Reply /reset prize to clear points",
    )


async def giveaway_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query, user = update.callback_query, update.effective_user
    if not query or not user or (query.data or "") != "giveaway:join:0":
        return
    state = await _global_state(context)
    ends = _parse_time(state.get("ends_at"))
    if not state.get("active") or not ends or _now() >= ends:
        await query.answer("This giveaway has ended.", show_alert=True)
        return
    if not await _channel_member(context, user.id):
        channel = getattr(context.application.bot_data["settings"], "music_channel", "@xyronschannel")
        channel_url = "https://t.me/" + str(channel).lstrip("@")
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Join XYRON Channel", url=channel_url)],
            [InlineKeyboardButton("✅ Verify Membership", callback_data="giveaway:join:0")],
        ])
        await query.answer("Join XYRON's channel first.", show_alert=True)
        try:
            await context.bot.send_message(user.id, "Join the official XYRON channel, then tap Verify.", reply_markup=keyboard)
        except TelegramError:
            pass
        return
    members = state.setdefault("members", {})
    key = str(user.id)
    if key not in members:
        members[key] = {
            "name": user.full_name, "username": user.username, "score": 0.0,
            "joined_at": _now().isoformat(), "groups": [], "risk": 0, "flags": [],
            "recent": [], "last_text": "", "last_at": 0, "messages": 0, "replies": 0,
            "distinct_replies": [], "alerted": False, "channel_ok": True,
            "channel_checked_at": _now().timestamp(),
        }
        await _save_global_state(context, state)
    await query.answer("You're registered for the global giveaway!", show_alert=True)
    try:
        await context.bot.send_message(user.id, "✅ Registered! Your eligible messages in EVERY XYRON group with 30+ members count toward one global score. Stay subscribed to XYRON's channel.")
    except TelegramError:
        pass
    try:
        await query.edit_message_text(f"✅ {escape(user.full_name)} is registered for XYRON's global giveaway.", parse_mode="HTML")
    except TelegramError:
        pass


async def global_giveaway_dm_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if not message or not user or not chat or chat.type != Chat.PRIVATE:
        return
    settings = context.application.bot_data["settings"]
    if not settings.is_owner(user.id):
        await message.reply_text("🔒 These giveaway controls are owner-only.")
        return
    args = [x.casefold() for x in context.args]
    state = await _global_state(context)
    if message.text and message.text.split("@", 1)[0].split()[0].casefold() == "/prize":
        if not state:
            await message.reply_text("No global giveaway has started yet.")
        else:
            try:
                await _send_global_board(message, context, state)
            except TelegramError:
                logger.exception("Could not send global giveaway leaderboard in owner DM")
                await message.reply_text("Could not render the global leaderboard.")
        return
    action = args[0] if args else "status"
    if action == "start":
        started = _now()
        state = {"active": True, "started_at": started.isoformat(), "ends_at": (started + DURATION).isoformat(), "members": {}, "scope": "all_eligible_groups"}
        await _save_global_state(context, state)
        await message.reply_text("🎉 GLOBAL GIVEAWAY STARTED\nDuration: 7 days\nGroups: all XYRON groups with 30+ members\nEntry: XYRON channel subscription + one-time registration\nScoring: reply +1 · standalone text +0.5\nAll groups share one leaderboard.")
    elif action == "end":
        if not state.get("active"):
            await message.reply_text("There is no active global giveaway.")
            return
        state["active"] = False
        state["ended_at"] = _now().isoformat()
        await _save_global_state(context, state)
        await message.reply_text("🏁 Global giveaway ended. Use /prize for final results.")
    elif action in {"reset", "clear"}:
        for entry in state.get("members", {}).values():
            if isinstance(entry, dict):
                entry["score"] = 0.0
        await _save_global_state(context, state)
        await message.reply_text("✅ Global giveaway points reset.")
    elif action in {"suspicious", "fraud"}:
        flagged = [(uid, data) for uid, data in state.get("members", {}).items() if isinstance(data, dict) and int(data.get("risk", 0)) >= 20]
        flagged.sort(key=lambda item: -int(item[1].get("risk", 0)))
        if not flagged:
            await message.reply_text("No accounts currently meet the suspicious-activity review threshold.")
            return
        lines = ["🔎 GLOBAL GIVEAWAY REVIEW"]
        for uid, data in flagged[:15]:
            lines.append(f"\n{data.get('name', uid)} · ID {uid}\nRisk {data.get('risk', 0)}/100 · Messages {data.get('messages', 0)}\nSignals: {', '.join(data.get('flags', [])[-4:])}")
        await message.reply_text("\n".join(lines))
    else:
        ends = _parse_time(state.get("ends_at")) or _now()
        await message.reply_text(f"GLOBAL GIVEAWAY: {'LIVE' if state.get('active') and _now() < ends else 'ENDED'}\nParticipants: {len(state.get('members', {}))}\nEnds: {ends:%d %b %Y, %H:%M UTC}\nCommands: /gw start, /gw end, /gw suspicious, /gw reset, /prize")


async def giveaway_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or chat.type not in {Chat.GROUP, Chat.SUPERGROUP} or not user or user.is_bot:
        return
    text = (message.text or "").strip()
    if not text:
        return
    command = re.sub(r"\s+", " ", text.casefold()).strip()
    command = re.sub(r"^/(gw|prize)(?:@\w+)?", lambda m: "/" + m.group(1), command)
    state = await _global_state(context)
    if command == "/gw":
        if not state.get("active"):
            await message.reply_text("🎁 No global giveaway is live right now.")
        elif str(user.id) in state.get("members", {}):
            await message.reply_text("✅ You're already registered. Your messages across all eligible XYRON groups count together.")
        else:
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🎁 Join Global Giveaway", callback_data="giveaway:join:0")]])
            await message.reply_text("🎁 <b>XYRON GLOBAL GIVEAWAY</b>\nRegister once. Messages in EVERY XYRON group with 30+ members count toward one global score.\nXYRON channel membership is required.", parse_mode="HTML", reply_markup=keyboard)
        return
    if command == "/prize":
        if not state:
            await message.reply_text("No global giveaway has started yet.")
        else:
            try:
                await _send_global_board(message, context, state)
            except TelegramError:
                logger.exception("Could not send global giveaway leaderboard")
                await message.reply_text("Couldn't send the leaderboard. Please try again.")
        return
    if text.startswith("/") or command.startswith("#giveaway") or not state.get("active"):
        return
    ends = _parse_time(state.get("ends_at"))
    if not ends or _now() >= ends:
        state["active"] = False
        state["ended_at"] = ends.isoformat() if ends else _now().isoformat()
        await _save_global_state(context, state)
        return
    if not await _eligible_group(context, chat):
        return
    entry = state.get("members", {}).get(str(user.id))
    if not isinstance(entry, dict):
        return
    now = _now().timestamp()
    checked = float(entry.get("channel_checked_at", 0) or 0)
    if now - checked > 3600:
        entry["channel_checked_at"] = now
        entry["channel_ok"] = await _channel_member(context, user.id)
        if not entry["channel_ok"]:
            await _save_global_state(context, state)
            return
    elif entry.get("channel_ok") is False:
        return
    raw = text
    normalized = re.sub(r"\s+", " ", raw).casefold()
    recent = [x for x in entry.get("recent", []) if now - float(x.get("at", 0)) <= 120]
    flags = list(entry.get("flags", []))
    risk = int(entry.get("risk", 0))
    repeated = normalized == entry.get("last_text") and now - float(entry.get("last_at", 0)) < 120
    if repeated:
        risk = min(100, risk + 12)
        flags.append("Repeated identical messages")
    if len(recent) >= 12:
        risk = min(100, risk + 8)
        flags.append("High message rate")
    reply = message.reply_to_message
    genuine_reply = bool(reply and reply.from_user and not reply.from_user.is_bot and reply.from_user.id != user.id)
    if genuine_reply:
        entry["replies"] = int(entry.get("replies", 0)) + 1
        peers = list(entry.get("distinct_replies", []))
        if reply.from_user.id not in peers:
            peers.append(reply.from_user.id)
        entry["distinct_replies"] = peers[-50:]
    if int(entry.get("messages", 0)) >= 18 and int(entry.get("replies", 0)) == 0:
        risk = min(100, risk + 10)
        flags.append("Extended activity without replying to others")
    if int(entry.get("messages", 0)) >= 30 and len(entry.get("distinct_replies", [])) < 2:
        risk = min(100, risk + 5)
        flags.append("Conversation limited to fewer than two other members")
    isolated_activity = int(entry.get("messages", 0)) >= 18 and int(entry.get("replies", 0)) == 0
    rapid_unanswered = len(recent) >= 12 and not genuine_reply
    if repeated or isolated_activity or rapid_unanswered:
        increment = 0.0
    else:
        increment = 1.0 if genuine_reply else 0.5
    entry["score"] = round(_score(entry) + increment, 1)
    entry["messages"] = int(entry.get("messages", 0)) + 1
    entry["groups"] = list(dict.fromkeys([*entry.get("groups", []), chat.id]))
    entry["recent"] = (recent + [{"at": now, "text": normalized[:160], "group": chat.id}])[-30:]
    entry["last_text"] = normalized[:500]
    entry["last_at"] = now
    entry["name"], entry["username"], entry["risk"] = user.full_name, user.username, risk
    entry["flags"] = list(dict.fromkeys(flags))[-12:]
    await _save_global_state(context, state)
    if risk >= 30 and not entry.get("alerted"):
        entry["alerted"] = True
        await _save_global_state(context, state)
        for owner_id in context.application.bot_data["settings"].owner_ids:
            try:
                await context.bot.send_message(owner_id, f"⚠️ GIVEAWAY REVIEW\nUser: {user.full_name} ({user.id})\nGroup: {chat.title} ({chat.id})\nRisk: {risk}/100\nSignals: {', '.join(entry['flags'][-4:])}\nReview only; no automatic disqualification.")
            except TelegramError:
                pass
