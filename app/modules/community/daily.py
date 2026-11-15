"""Daily group chatter leaderboard rendered as a premium image."""

from __future__ import annotations

import logging
import unicodedata
from datetime import datetime
from io import BytesIO
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select
from telegram import Update
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from app.services.image_fonts import font_for_text
from app.database.models import DailyMessageCount

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
    return font_for_text("XYRON", size, bold)


def _draw_avatar(
    image: Image.Image,
    box: tuple[int, int, int, int],
    name: str,
    accent: tuple[int, int, int],
    size_font: int = 26,
) -> None:
    x1, y1, x2, y2 = box
    size = x2 - x1
    avatar = Image.new("RGB", (size, size), accent)
    adraw = ImageDraw.Draw(avatar)
    initials = "".join(part[0] for part in name.split()[:2] if part).upper()[:2] or "?"
    font = font_for_text(name, size_font, True)
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
        name = unicodedata.normalize("NFKC", (display_name or (f"@{username}" if username else f"User {uid}")).strip())
        draw.rounded_rectangle((206, 340, 874, 648), radius=34, fill=(31, 39, 69), outline=GOLD, width=4)
        draw.rounded_rectangle((394, 358, 686, 406), radius=22, fill=GOLD)
        draw.text((540, 382), "♛  #1  CHAMPION", font=label_font, fill=(24, 29, 48), anchor="mm")
        _draw_avatar(image, (470, 423, 610, 563), name, GOLD, 34)
        draw = ImageDraw.Draw(image)
        name_font = font_for_text(name, 30, True)
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
            _draw_avatar(image, ((x1 + x2) // 2 - 44, podium_y1 + 70, (x1 + x2) // 2 + 44, podium_y1 + 158), name, accent, 24)
            draw = ImageDraw.Draw(image)
            center = (x1 + x2) // 2
            side_name_font = font_for_text(name, 21, True)
            draw.text((center, podium_y1 + 166), _fit_text(draw, name, side_name_font, x2 - x1 - 34), font=side_name_font, fill=WHITE, anchor="mt")
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
            _draw_avatar(image, (132, y + 13, 194, y + 75), name, (105, 170, 222), 19)
            draw = ImageDraw.Draw(image)
            row_name_font = font_for_text(name, 23, True)
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
    # Counts are pre-aggregated by group, user, and India-local day.
    # /daily performs one small indexed read and no history scan.
    try:
        async with sessions() as session:
            statement = (
                select(
                    DailyMessageCount.user_id,
                    DailyMessageCount.display_name,
                    DailyMessageCount.username,
                    DailyMessageCount.message_count,
                )
                .where(
                    DailyMessageCount.chat_id == chat.id,
                    DailyMessageCount.day == now_local.date(),
                )
                .order_by(
                    DailyMessageCount.message_count.desc(),
                    DailyMessageCount.user_id.asc(),
                )
                .limit(13)
            )
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

    try:
        photo = _render_daily(
            entries,
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
