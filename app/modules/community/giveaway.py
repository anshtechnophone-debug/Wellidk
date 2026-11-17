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
from app.services.image_fonts import font_for_text

DURATION = timedelta(days=7)
PRIZE_LABELS = ("TELEGRAM PREMIUM", "₹100 INR", "30 TELEGRAM STARS")
MINIMUM_POINTS = (11000, 10000, 9000)


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
        "🏆 <b>Prize targets:</b> 1st — 11,000 points (Telegram Premium) · 2nd — 10,000 points (₹100 INR) · 3rd — 9,000 points (30 Telegram Stars).\n"
        "Only messages in this group count toward this group's leaderboard. Use <b>/prize</b> to check it.\n"
        "👑 <b>Hosted by:</b> @afkvoid\n"
        "⏳ The giveaway ends after 7 days."
    )

logger = logging.getLogger(__name__)


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Prefer broad Unicode fonts so Telegram display names don't become tofu boxes."""
    return font_for_text("XYRON", size, bold)


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
        font = font_for_text(text, current, bold)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
        current -= 1
    return font_for_text(text, 13, bold)


def _render_leaderboard(state: dict, entries: list[tuple[int, dict]], ends_at: datetime, active: bool, avatars: dict[int, bytes | None]) -> BytesIO:
    """Render the navy-indigo group leaderboard with text-only prizes and aligned avatars."""
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
    draw.text((width // 2, 145), "GROUP GIVEAWAY", font=title_font, fill=white, anchor="mm")
    draw.text(
        (width // 2, 198),
        f"{_remaining(ends_at)} LEFT" if active else "GIVEAWAY ENDED · FINAL RESULTS",
        font=sub_font,
        fill=(123, 239, 183) if active else gold,
        anchor="mm",
    )
    draw.text((width // 2, 229), _display_name(state.get("group_title", "XYRON GROUP"))[:42], font=_fit_font(draw, _display_name(state.get("group_title", "XYRON GROUP"))[:42], 800, 18, True), fill=(190, 198, 222), anchor="mm")

    # Text-only grand-prize panel. No NFT artwork or embedded image data.
    prize_box = (110, 245, 970, 440)
    draw.rounded_rectangle(prize_box, radius=34, fill=(36, 31, 45), outline=(181, 155, 111), width=3)
    draw.rounded_rectangle((132, 265, 948, 420), radius=25, fill=(29, 29, 48), outline=(112, 94, 119), width=2)
    draw.text((width // 2, 300), "1ST PRIZE", font=_load_font(23, True), fill=gold, anchor="mm")
    draw.text((width // 2, 348), "TELEGRAM PREMIUM", font=_fit_font(draw, "TELEGRAM PREMIUM", 740, 48, True), fill=(255, 244, 220), anchor="mm")
    draw.text((width // 2, 395), "MINIMUM TARGET  ·  11,000 POINTS", font=_load_font(20, True), fill=(222, 211, 185), anchor="mm")

    # Two smaller prize labels sit below the NFT panel.
    prize_y = 475
    prize_cards = (
        ((110, prize_y, 520, prize_y + 142), silver, "2ND PRIZE", "₹100 INR", "CASH PRIZE"),
        ((560, prize_y, 970, prize_y + 142), bronze, "3RD PRIZE", "30 TELEGRAM STARS", "STAR PRIZE"),
    )
    for box, accent, label, prize, target in prize_cards:
        draw.rounded_rectangle(box, radius=25, fill=(25, 30, 51), outline=accent, width=2)
        x1, y1, x2, y2 = box
        draw.text((x1 + 24, y1 + 29), label, font=_load_font(19, True), fill=accent)
        draw.text((x1 + 24, y1 + 70), prize, font=_fit_font(draw, prize, x2 - x1 - 48, 25, True), fill=white, anchor="lm")
        draw.text(((x1 + x2) // 2, y1 + 116), target, font=_fit_font(draw, target, x2 - x1 - 44, 17, True), fill=muted, anchor="mm")

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
            name = _display_name(data.get("name"))
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
            name = re.sub(r"\s+", " ", _display_name(data.get("name")) )
            score = _score(data)
        else:
            name, score = "OPEN SPOT", 0.0
        draw.text((225, row_y + 27), name, font=_fit_font(draw, name, 490, 21, True), fill=white, anchor="lm")
        score_text = f"{score:,.1f} pts" if rank - 1 < len(entries) else "— pts"
        draw.text((930, row_y + 27), score_text, font=_fit_font(draw, score_text, 190, 20, True), fill=muted, anchor="rm")

    draw.text((width // 2, 1615), "REPLY  +1 POINT   ·   STANDALONE TEXT  +0.5 POINT", font=_fit_font(draw, "REPLY  +1 POINT   ·   STANDALONE TEXT  +0.5 POINT", 950, 19, True), fill=muted, anchor="mm")
    draw.text((width // 2, 1655), "JOIN FIRST · ONLY MESSAGES AFTER JOINING COUNT", font=_load_font(17, True), fill=(137, 150, 178), anchor="mm")
    draw.text((width // 2, 1695), "XYRON  •  7-DAY GROUP GIVEAWAY", font=_load_font(17, True), fill=(111, 125, 157), anchor="mm")

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    output.name = "xyron-giveaway.png"
    return output

    return output

async def _giveaway_state(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> dict:
    # Giveaway is global across every eligible group. The canonical record
    # lives in GroupPolicy(chat_id=0).global_giveaway.
    state = (await _repo(context).get(0)).get("global_giveaway", {})
    return state if isinstance(state, dict) else {}


async def _save_giveaway_state(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, state: dict
) -> None:
    # Ignore chat_id intentionally: all groups share the same persisted state.
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


async def _send_giveaway_board(message, context, state: dict) -> None:
    ends = _parse_time(state.get("ends_at")) or _now()
    active = bool(state.get("active")) and _now() < ends
    entries = _leaderboard(state)[:8]
    avatars = {uid: await _profile_photo_bytes(context, uid) for uid, _ in entries[:3]}
    await message.reply_photo(
        photo=_render_leaderboard(state, entries, ends, active, avatars),
        caption="🎁 GIVEAWAY PRIZE LEADERBOARD",
    )


def _giveaway_channel_keyboard(
    context: ContextTypes.DEFAULT_TYPE, group_id: int
) -> InlineKeyboardMarkup:
    channel = getattr(context.application.bot_data["settings"], "music_channel", "@xyronschannel")
    channel_url = "https://t.me/" + str(channel).lstrip("@")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join XYRON Channel", url=channel_url)],
        [InlineKeyboardButton("✅ Joined — Confirm", callback_data=f"giveaway:join:{group_id}")],
    ])


async def _prompt_giveaway_channel(
    context: ContextTypes.DEFAULT_TYPE, user_id: int, group_id: int
) -> None:
    await context.bot.send_message(
        user_id,
        "⚠️ Your giveaway points in this group are paused because you are not a member of @xyronschannel.\n\n"
        "Join the channel, then tap Joined — Confirm. Your existing points will be kept.",
        reply_markup=_giveaway_channel_keyboard(context, group_id),
    )


async def giveaway_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query, user = update.callback_query, update.effective_user
    if not query or not user:
        return
    data = (query.data or "").split(":")
    if len(data) != 3 or data[:2] != ["giveaway", "join"]:
        return
    try:
        group_id = int(data[2])
    except ValueError:
        await query.answer("This giveaway link is invalid.", show_alert=True)
        return
    state = await _giveaway_state(context, group_id)
    ends = _parse_time(state.get("ends_at"))
    if not state.get("active") or not ends or _now() >= ends:
        await query.answer("The global giveaway has ended.", show_alert=True)
        return
    if not await _channel_member(context, user.id):
        await query.answer("Join XYRON's channel first, then confirm.", show_alert=True)
        try:
            await _prompt_giveaway_channel(context, user.id, group_id)
        except TelegramError:
            pass
        return
    members = state.setdefault("members", {})
    key = str(user.id)
    entry = members.get(key)
    if not isinstance(entry, dict):
        entry = {
            "name": user.full_name, "username": user.username, "score": 0.0,
            "joined_at": _now().isoformat(), "risk": 0, "flags": [],
            "recent": [], "last_text": "", "last_at": 0, "messages": 0, "replies": 0,
            "distinct_replies": [], "alerted": False,
        }
        members[key] = entry
        confirmation = f"✅ You're registered for the giveaway in {state.get('group_title', 'this group')}!"
    else:
        confirmation = "✅ Channel membership confirmed. Your global giveaway points are active again."
    entry["channel_ok"] = True
    entry["channel_checked_at"] = _now().timestamp()
    entry["channel_prompted"] = False
    entry["name"], entry["username"] = user.full_name, user.username
    await _save_giveaway_state(context, group_id, state)
    await query.answer("Membership confirmed. Point counting is active.", show_alert=True)
    try:
        await query.edit_message_text(confirmation)
    except TelegramError:
        try:
            await context.bot.send_message(user.id, confirmation)
        except TelegramError:
            pass


async def global_giveaway_dm_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle public group participation and owner-only controls for that group."""
    message, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if not message or not user or not chat:
        return
    settings = context.application.bot_data["settings"]
    if chat.type == Chat.PRIVATE:
        if not settings.is_owner(user.id):
            await message.reply_text("🔒 Giveaway controls are bot-owner-only.")
        else:
            await message.reply_text(
                "Run /gw start inside the group where you want its giveaway to begin. "
                "All eligible groups share one global giveaway and leaderboard."
            )
        return
    if chat.type not in {Chat.GROUP, Chat.SUPERGROUP}:
        return

    command_name = (message.text or "").split(maxsplit=1)[0].split("@", 1)[0].casefold()
    state = await _giveaway_state(context, chat.id)
    if command_name == "/prize":
        # The public leaderboard is global: read the persisted global giveaway
        # record from GroupPolicy(chat_id=0), not the current group's policy.
        global_policy = await _repo(context).get(0)
        global_state = global_policy.get("global_giveaway")
        if not isinstance(global_state, dict) or not global_state.get("members"):
            # Backward-compatible fallback for deployments that still have a
            # group-local record, without ever overwriting the global database.
            global_state = state if isinstance(state, dict) else {}
        if not global_state or not global_state.get("members"):
            await message.reply_text("🎁 The giveaway leaderboard is being updated. Please try again shortly.")
        else:
            try:
                await _send_giveaway_board(message, context, global_state)
            except TelegramError:
                logger.exception("Could not send global giveaway leaderboard for chat_id=%s", chat.id)
                await message.reply_text("Couldn't load the saved giveaway leaderboard. Please try again.")
        return

    action = context.args[0].casefold() if context.args else "status"
    is_owner = settings.is_owner(user.id)
    if action == "status" and not is_owner:
        if not state.get("active"):
            await message.reply_text("🎁 No global giveaway is live right now. The bot owner can start it with /gw start.")
        elif str(user.id) in state.get("members", {}):
            await message.reply_text("✅ You're already registered for the global giveaway. Eligible messages across groups count.")
        else:
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "🎁 Join Global Giveaway",
                    callback_data=f"giveaway:join:{chat.id}",
                )
            ]])
            await message.reply_text(
                "🎁 <b>THIS GROUP'S GIVEAWAY</b>\n"
                "Register here. Only eligible messages in this group count toward this group's score.\n"
                "XYRON channel membership is required.",
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        return
    if not is_owner:
        await message.reply_text("🔒 Giveaway controls are bot-owner-only.")
        return

    if action == "start":
        if state.get("active") and (_parse_time(state.get("ends_at")) or _now()) > _now():
            await message.reply_text(
                "🎉 The GLOBAL giveaway is already live!\n"
                "All eligible groups share the same participants and leaderboard.\n"
                "Use /gw to join and /prize to view the global leaderboard."
            )
            return
        started = _now()
        # Preserve all saved participants and scores when starting/restarting.
        state.update({
            "active": True,
            "started_at": started.isoformat(),
            "ends_at": (started + DURATION).isoformat(),
            "group_id": 0,
            "group_title": "XYRON Global Giveaway",
        })
        state.setdefault("members", {})
        await _save_giveaway_state(context, 0, state)
        await message.reply_text(
            "🎉 GLOBAL GIVEAWAY STARTED\n"
            "Duration: 7 days\n"
            "Participants and points: shared across all eligible groups\n"
            "Entry: XYRON channel subscription + one-time registration\n"
            "Scoring: reply +1 · standalone text +0.5\n"
            "Use /gw to join and /prize to view the global leaderboard."
        )
    elif action == "end":
        if not state.get("active"):
            await message.reply_text("There is no active giveaway in this group.")
            return
        state["active"] = False
        state["ended_at"] = _now().isoformat()
        await _save_giveaway_state(context, chat.id, state)
        await message.reply_text("🏁 This group's giveaway has ended. Use /prize for its final results.")
    elif action in {"reset", "clear"}:
        if not state:
            await message.reply_text("No giveaway has started in this group.")
            return
        for entry in state.get("members", {}).values():
            if isinstance(entry, dict):
                entry["score"] = 0.0
        await _save_giveaway_state(context, chat.id, state)
        await message.reply_text("✅ This group's giveaway points have been reset.")
    elif action in {"suspicious", "fraud"}:
        flagged = [
            (uid, data) for uid, data in state.get("members", {}).items()
            if isinstance(data, dict) and int(data.get("risk", 0)) >= 20
        ]
        flagged.sort(key=lambda item: -int(item[1].get("risk", 0)))
        if not flagged:
            await message.reply_text("No accounts currently meet this group's suspicious-activity review threshold.")
            return
        lines = ["🔎 THIS GROUP'S GIVEAWAY REVIEW"]
        for uid, data in flagged[:15]:
            lines.append(
                f"\n{data.get('name', uid)} · ID {uid}\n"
                f"Risk {data.get('risk', 0)}/100 · Messages {data.get('messages', 0)}\n"
                f"Signals: {', '.join(data.get('flags', [])[-4:])}"
            )
        await message.reply_text("\n".join(lines))
    elif action == "status":
        ends = _parse_time(state.get("ends_at"))
        if not state:
            await message.reply_text("🎁 No giveaway has started in this group. The bot owner can run /gw start here.")
        else:
            live = bool(state.get("active")) and bool(ends) and _now() < ends
            status = "LIVE" if live else "ENDED"
            end_text = f"{ends:%d %b %Y, %H:%M UTC}" if ends else "unknown"
            await message.reply_text(
                f"THIS GROUP'S GIVEAWAY: {status}\n"
                f"Participants: {len(state.get('members', {}))}\nEnds: {end_text}"
            )
    else:
        await message.reply_text("Usage: /gw start · /gw end · /gw reset · /gw suspicious")


async def giveaway_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or chat.type not in {Chat.GROUP, Chat.SUPERGROUP} or not user or user.is_bot:
        return
    text = (message.text or "").strip()
    if not text:
        return
    command = re.sub(r"\s+", " ", text.casefold()).strip()
    state = await _giveaway_state(context, chat.id)
    if text.startswith("/") or command.startswith("#giveaway") or not state.get("active"):
        return
    ends = _parse_time(state.get("ends_at"))
    if not ends or _now() >= ends:
        state["active"] = False
        state["ended_at"] = ends.isoformat() if ends else _now().isoformat()
        await _save_giveaway_state(context, chat.id, state)
        return
    if not await _eligible_group(context, chat):
        return
    entry = state.get("members", {}).get(str(user.id))
    if not isinstance(entry, dict) or entry.get("disqualified"):
        return
    now = _now().timestamp()
    # Re-check membership on every scored message so leaving the channel pauses
    # points immediately; retain the score and require an explicit DM confirmation.
    channel_ok = await _channel_member(context, user.id)
    entry["channel_checked_at"] = now
    entry["channel_ok"] = channel_ok
    if not channel_ok:
        if not entry.get("channel_prompted"):
            entry["channel_prompted"] = True
            await _save_giveaway_state(context, chat.id, state)
            try:
                await _prompt_giveaway_channel(context, user.id, chat.id)
            except TelegramError:
                logger.info("Could not send channel rejoin prompt to giveaway participant %s", user.id)
        else:
            await _save_giveaway_state(context, chat.id, state)
        return
    entry["channel_prompted"] = False
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
    entry["recent"] = (recent + [{"at": now, "text": normalized[:160], "group": chat.id}])[-30:]
    entry["last_text"] = normalized[:500]
    entry["last_at"] = now
    entry["name"], entry["username"], entry["risk"] = user.full_name, user.username, risk
    entry["flags"] = list(dict.fromkeys(flags))[-12:]

    # Require several independent, sustained signals before automatic disqualification.
    # A risk score alone is never treated as proof.
    certain_fraud = (
        risk >= 90
        and int(entry.get("messages", 0)) >= 40
        and repeated
        and rapid_unanswered
        and isolated_activity
        and len(entry.get("distinct_replies", [])) < 2
    )
    if certain_fraud:
        entry["disqualified"] = True
        entry["disqualified_at"] = _now().isoformat()
        entry["disqualification_reason"] = (
            "Repeated identical messages, sustained rapid unanswered activity, "
            "and no genuine conversation with other members."
        )
        entry["score"] = 0.0
        await _save_giveaway_state(context, chat.id, state)
        notice = (
            f"🚫 GIVEAWAY DISQUALIFICATION\n"
            f"{user.full_name} (ID {user.id}) has been disqualified from this group's giveaway "
            "after multiple strong anti-abuse signals in this group's giveaway."
        )
        try:
            await context.bot.send_message(chat.id, notice)
        except TelegramError:
            logger.exception("Could not announce giveaway disqualification in group %s", chat.id)
        for owner_id in context.application.bot_data["settings"].owner_ids:
            try:
                await context.bot.send_message(
                    owner_id,
                    f"🚫 GROUP GIVEAWAY — DISQUALIFIED\n"
                    f"User: {user.full_name} (@{user.username or 'no_username'}) · ID {user.id}\n"
                    f"Group: {chat.title} ({chat.id})\n"
                    f"Risk: {risk}/100\n"
                    f"Evidence: {entry['disqualification_reason']}\n"
                    "The participant has been blocked from earning further points."
                )
            except TelegramError:
                logger.exception("Could not DM owner about disqualified giveaway participant %s", user.id)
        return

    await _save_giveaway_state(context, chat.id, state)
    if risk >= 30 and not entry.get("alerted"):
        entry["alerted"] = True
        await _save_giveaway_state(context, chat.id, state)
        for owner_id in context.application.bot_data["settings"].owner_ids:
            try:
                await context.bot.send_message(
                    owner_id,
                    f"⚠️ GROUP GIVEAWAY — SUSPICIOUS ACTIVITY\n"
                    f"User: {user.full_name} (@{user.username or 'no_username'}) · ID {user.id}\n"
                    f"Group: {chat.title} ({chat.id})\n"
                    f"Risk: {risk}/100\n"
                    f"Signals: {', '.join(entry['flags'][-4:])}\n"
                    "Review requested only. The participant has NOT been disqualified."
                )
            except TelegramError:
                logger.exception("Could not DM owner about suspicious giveaway participant %s", user.id)
