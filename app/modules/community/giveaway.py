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
    entries = _leaderboard(state)[:3]
    avatars = {uid: await _profile_photo_bytes(context, uid) for uid, _ in entries}
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
