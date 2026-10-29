"""Daily group chatter leaderboard rendered as a premium image."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta, timezone
from io import BytesIO
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont, ImageOps
from sqlalchemy import func, select
from telegram import Update
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from app.services.group_history import GroupMessage

logger = logging.getLogger(__name__)
LOCAL_TZ = ZoneInfo("Asia/Kolkata")

WIDTH = 1080
HEIGHT = 2180
BG_TOP = (10, 16, 37)
BG_BOTTOM = (34, 24, 65)
WHITE = (247, 249, 255)
MUTED = (170, 184, 211)
GOLD = (255, 207, 87)
SILVER = (205, 218, 239)
BRONZE = (224, 151, 99)
ACCENTS = (GOLD, SILVER, BRONZE)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    paths = (
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        )
        if bold
        else (
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


async def _avatar_bytes(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bytes | None:
    """Fetch a small Telegram avatar; missing or inaccessible photos use initials."""
    try:
        photos = await context.bot.get_user_profile_photos(user_id=user_id, limit=1)
        if not photos.photos:
            return None
        photo = photos.photos[0][-1]
        file = await context.bot.get_file(photo.file_id)
        return bytes(await file.download_as_bytearray())
    except (TelegramError, OSError, ValueError):
        logger.debug("Could not fetch /daily avatar for user_id=%s", user_id, exc_info=True)
        return None


def _draw_avatar(
    image: Image.Image,
    box: tuple[int, int, int, int],
    name: str,
    photo_bytes: bytes | None,
    accent: tuple[int, int, int],
    size_font: int = 26,
) -> None:
    x1, y1, x2, y2 = box
    size = x2 - x1
    avatar: Image.Image | None = None
    if photo_bytes:
        try:
            avatar = ImageOps.fit(
                Image.open(BytesIO(photo_bytes)).convert("RGB"),
                (size, size),
                method=Image.Resampling.LANCZOS,
            )
        except (OSError, ValueError):
            avatar = None
    if avatar is None:
        avatar = Image.new("RGB", (size, size), accent)
        adraw = ImageDraw.Draw(avatar)
        initials = "".join(part[0] for part in name.split()[:2] if part).upper()[:2] or "?"
        font = _font(size_font, True)
        bounds = adraw.textbbox((0, 0), initials, font=font)
        adraw.text(
            ((size - (bounds[2] - bounds[0])) / 2, (size - (bounds[3] - bounds[1])) / 2 - bounds[1]),
            initials,
            font=font,
            fill=(19, 25, 47),
        )
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    image.paste(avatar, (x1, y1), mask)
    ImageDraw.Draw(image).ellipse(box, outline=accent, width=max(3, size // 24))


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> str:
    text = text.strip() or "Member"
    if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
        return text
    while len(text) > 2 and draw.textbbox((0, 0), text + "…", font=font)[2] > max_width:
        text = text[:-1]
    return text.rstrip() + "…"


def _render_daily(
    rows: list[tuple[int, str | None, str | None, int]],
    avatars: dict[int, bytes | None],
    date_label: str,
    group_title: str,
) -> BytesIO:
    """Create a tall, mobile-first podium plus ten additional ranked members."""
    image = Image.new("RGB", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(image)

    # Smooth midnight gradient and soft decorative glow circles.
    for y in range(HEIGHT):
        ratio = y / (HEIGHT - 1)
        color = tuple(int(BG_TOP[i] * (1 - ratio) + BG_BOTTOM[i] * ratio) for i in range(3))
        draw.line((0, y, WIDTH, y), fill=color)
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((690, -180, 1250, 380), fill=(84, 67, 167, 42))
    gd.ellipse((-240, 300, 300, 850), fill=(29, 137, 177, 30))
    image = Image.alpha_composite(image.convert("RGBA"), glow).convert("RGB")
    draw = ImageDraw.Draw(image)

    title_font = _font(59, True)
    subtitle_font = _font(24)
    label_font = _font(22, True)
    name_font = _font(30, True)
    count_font = _font(24, True)
    row_name_font = _font(23, True)
    row_count_font = _font(21, True)
    tiny_font = _font(18, True)

    # Header.
    draw.rounded_rectangle((42, 36, WIDTH - 42, 224), radius=38, fill=(20, 28, 55), outline=(83, 86, 137), width=2)
    draw.text((WIDTH // 2, 64), "DAILY", font=title_font, fill=WHITE, anchor="mt")
    draw.text((WIDTH // 2, 137), "CHAT CHAMPIONS", font=label_font, fill=GOLD, anchor="mt")
    draw.text((WIDTH // 2, 181), f"{date_label}  •  INDIA / IST", font=subtitle_font, fill=MUTED, anchor="mt")

    safe_group = group_title.strip() or "THIS GROUP"
    safe_group = _fit_text(draw, safe_group.upper(), _font(19, True), 900)
    draw.text((WIDTH // 2, 252), safe_group, font=_font(19, True), fill=(143, 195, 245), anchor="mt")
    draw.text((WIDTH // 2, 294), "THE PODIUM", font=label_font, fill=WHITE, anchor="mt")

    # First place: centered, larger, and visually dominant.
    if rows:
        uid, display_name, username, count = rows[0]
        name = (display_name or (f"@{username}" if username else f"User {uid}")).strip()
        draw.rounded_rectangle((206, 340, 874, 648), radius=34, fill=(31, 39, 69), outline=GOLD, width=4)
        draw.rounded_rectangle((394, 358, 686, 406), radius=22, fill=GOLD)
        draw.text((540, 382), "♛  #1  CHAMPION", font=label_font, fill=(24, 29, 48), anchor="mm")
        _draw_avatar(image, (470, 423, 610, 563), name, avatars.get(uid), GOLD, 34)
        draw = ImageDraw.Draw(image)
        draw.text((540, 576), _fit_text(draw, name, name_font, 590), font=name_font, fill=WHITE, anchor="mt")
        draw.text((540, 620), f"{count:,} MESSAGES", font=count_font, fill=GOLD, anchor="mt")
    else:
        draw.rounded_rectangle((206, 340, 874, 648), radius=34, fill=(31, 39, 69), outline=GOLD, width=4)
        draw.text((540, 475), "NO CHAMPION YET", font=name_font, fill=WHITE, anchor="mm")

    # Second and third place side-by-side underneath the winner.
    podium_y1, podium_y2 = 674, 906
    side_cards = ((62, 500, 2), (580, 1018, 3))
    for x1, x2, rank in side_cards:
        index = rank - 1
        accent = ACCENTS[index]
        draw.rounded_rectangle((x1, podium_y1, x2, podium_y2), radius=28, fill=(27, 35, 62), outline=accent, width=3)
        draw.rounded_rectangle((x1 + 20, podium_y1 + 16, x2 - 20, podium_y1 + 56), radius=18, fill=accent)
        draw.text(((x1 + x2) // 2, podium_y1 + 36), f"#{rank}  " + ("RUNNER-UP" if rank == 2 else "THIRD PLACE"), font=tiny_font, fill=(24, 29, 48), anchor="mm")
        if len(rows) >= rank:
            uid, display_name, username, count = rows[index]
            name = (display_name or (f"@{username}" if username else f"User {uid}")).strip()
            _draw_avatar(image, ((x1 + x2) // 2 - 44, podium_y1 + 70, (x1 + x2) // 2 + 44, podium_y1 + 158), name, avatars.get(uid), accent, 24)
            draw = ImageDraw.Draw(image)
            center = (x1 + x2) // 2
            draw.text((center, podium_y1 + 166), _fit_text(draw, name, _font(21, True), x2 - x1 - 34), font=_font(21, True), fill=WHITE, anchor="mt")
            draw.text((center, podium_y1 + 199), f"{count:,} msgs", font=_font(22, True), fill=accent, anchor="mt")
        else:
            draw.text(((x1 + x2) // 2, podium_y1 + 130), "OPEN SPOT", font=name_font, fill=MUTED, anchor="mm")

    # Ten more ranked members, each with an avatar and total messages.
    draw = ImageDraw.Draw(image)
    draw.text((58, 938), "THE TOP 13  /  DAILY RANKINGS", font=label_font, fill=WHITE)
    draw.text((WIDTH - 58, 938), "MESSAGES", font=tiny_font, fill=MUTED, anchor="ra")
    start_y = 982
    row_height = 104
    for index in range(3, 13):
        y = start_y + (index - 3) * row_height
        draw.rounded_rectangle((48, y, WIDTH - 48, y + 88), radius=22, fill=(25, 33, 59), outline=(61, 69, 103), width=1)
        rank = index + 1
        draw.rounded_rectangle((62, y + 21, 116, y + 67), radius=14, fill=(51, 61, 91))
        draw.text((89, y + 44), f"{rank:02d}", font=_font(19, True), fill=MUTED, anchor="mm")
        if index < len(rows):
            uid, display_name, username, count = rows[index]
            name = (display_name or (f"@{username}" if username else f"User {uid}")).strip()
            _draw_avatar(image, (132, y + 13, 194, y + 75), name, avatars.get(uid), (105, 170, 222), 19)
            draw = ImageDraw.Draw(image)
            draw.text((211, y + 20), _fit_text(draw, name, row_name_font, 520), font=row_name_font, fill=WHITE)
            draw.text((211, y + 53), f"RANK #{rank}", font=tiny_font, fill=MUTED)
            draw.text((WIDTH - 76, y + 44), f"{count:,}", font=row_count_font, fill=(119, 222, 199), anchor="rm")
        else:
            draw.text((211, y + 30), "Waiting for a challenger", font=row_name_font, fill=MUTED)

    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((48, 2042, WIDTH - 48, 2130), radius=24, fill=(20, 28, 55), outline=(83, 86, 137), width=1)
    draw.text((WIDTH // 2, 2068), "KEEP TALKING. CLIMB THE RANKS.", font=label_font, fill=GOLD, anchor="mt")
    draw.text((WIDTH // 2, 2107), "POWERED BY XYRON", font=tiny_font, fill=MUTED, anchor="mt")

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    output.name = "xyron-daily-leaderboard.png"
    return output


async def daily_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show today's top three podium and the next ten members as an image."""
    message = update.effective_message
    chat = update.effective_chat
    if not message or not chat:
        return
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.reply_text("Use /daily inside a group to see its daily chatter leaderboard.")
        return

    sessions = context.application.bot_data.get("sessions")
    if sessions is None:
        await message.reply_text("The daily leaderboard is temporarily unavailable.")
        return

    now_local = datetime.now(LOCAL_TZ)
    start_local = datetime.combine(now_local.date(), time.min, tzinfo=LOCAL_TZ)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)

    statement = (
        select(
            GroupMessage.user_id,
            func.max(GroupMessage.display_name).label("display_name"),
            func.max(GroupMessage.username).label("username"),
            func.count(GroupMessage.id).label("message_count"),
        )
        .where(
            GroupMessage.chat_id == chat.id,
            GroupMessage.sent_at >= start_utc,
            GroupMessage.sent_at < end_utc,
        )
        .group_by(GroupMessage.user_id)
        .order_by(func.count(GroupMessage.id).desc(), GroupMessage.user_id.asc())
        .limit(13)
    )

    try:
        async with sessions() as session:
            rows = (await session.execute(statement)).all()
    except Exception:
        logger.exception("Could not build daily chatter leaderboard for chat_id=%s", chat.id)
        await message.reply_text("Couldn't load today's leaderboard. Please try again.")
        return

    if not rows:
        await message.reply_text("📊 No messages have been recorded in this group today yet.")
        return

    entries = [
        (int(user_id), display_name, username, int(count))
        for user_id, display_name, username, count in rows
    ]

    # Fetch avatars concurrently but cap parallel Bot API calls to avoid bursts.
    semaphore = asyncio.Semaphore(4)

    async def fetch_one(user_id: int) -> tuple[int, bytes | None]:
        async with semaphore:
            return user_id, await _avatar_bytes(context, user_id)

    try:
        avatar_results = await asyncio.gather(*(fetch_one(entry[0]) for entry in entries))
        avatars = dict(avatar_results)
        photo = _render_daily(
            entries,
            avatars,
            now_local.strftime("%d %b %Y"),
            chat.title or "This group",
        )
        await message.reply_photo(photo=photo, caption=f"🏆 Daily chat leaderboard · {now_local.strftime('%d %b %Y')} IST")
    except TelegramError:
        logger.exception("Could not send /daily leaderboard image for chat_id=%s", chat.id)
        await message.reply_text("Couldn't send the daily leaderboard image. Please try /daily again.")
    except Exception:
        logger.exception("Could not render /daily leaderboard image for chat_id=%s", chat.id)
        await message.reply_text("Couldn't build today's leaderboard image. Please try /daily again.")
