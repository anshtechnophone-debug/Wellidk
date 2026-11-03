"""QuotLy-style compact Telegram quote stickers for XYRON."""

from __future__ import annotations

import io
import logging
import unicodedata

from PIL import Image, ImageDraw, ImageFont, ImageOps
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

WIDTH = 512
ACCENTS = ((244, 177, 104), (143, 176, 255), (120, 220, 174))
BUBBLES = ((49, 38, 55), (37, 46, 66), (32, 54, 51))


def _plain_unicode(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip()


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    filename = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    liberation = "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf"
    noto = "NotoSans-Bold.ttf" if bold else "NotoSans-Regular.ttf"
    candidates = (
        f"/usr/share/fonts/truetype/dejavu/{filename}",
        f"/usr/share/fonts/truetype/liberation2/{liberation}",
        f"/usr/share/fonts/truetype/noto/{noto}",
        f"/usr/share/fonts/opentype/noto/{noto}",
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if draw.textbbox((0, 0), candidate, font=font)[2] <= width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def _avatar(data: bytes | bytearray | None, size: int = 78) -> Image.Image | None:
    if not data:
        return None
    try:
        avatar = ImageOps.fit(Image.open(io.BytesIO(data)).convert("RGBA"), (size, size))
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
        avatar.putalpha(mask)
        return avatar
    except (OSError, ValueError):
        return None


def _render_sticker(
    quote: str,
    author: str,
    username: str | None,
    avatar_data: bytes | bytearray | None,
    theme_index: int,
) -> io.BytesIO:
    """Render a compact incoming-message bubble on a transparent WebP canvas."""
    theme_index %= len(ACCENTS)
    accent = ACCENTS[theme_index]
    bubble_color = BUBBLES[theme_index]
    quote = _plain_unicode(" ".join((quote or "[Empty message]").split()))
    author = _plain_unicode(author) or "Unknown user"
    avatar = _avatar(avatar_data)

    bubble_left, bubble_right = 56, WIDTH - 8
    text_left = bubble_left + 22
    text_width = bubble_right - text_left - 18
    name_font = _font(22, bold=True)
    message_font = _font(31, bold=False)

    probe = ImageDraw.Draw(Image.new("RGBA", (WIDTH, 512), (0, 0, 0, 0)))
    lines = _wrap(probe, quote, message_font, text_width)
    while len(lines) > 4 and message_font.size > 25:
        message_font = _font(message_font.size - 2, bold=False)
        lines = _wrap(probe, quote, message_font, text_width)
    if len(lines) > 4:
        lines = lines[:4]
        lines[-1] = lines[-1].rstrip(" .") + "…"

    name = author[:34]
    name_bbox = probe.textbbox((0, 0), name, font=name_font)
    name_height = name_bbox[3] - name_bbox[1]
    line_height = message_font.size + 7
    text_height = len(lines) * line_height

    # Compact padding and avatar proportions follow QuotLy's small message preview.
    top_pad, name_gap, bottom_pad = 13, 5, 13
    bubble_height = max(88, top_pad + name_height + name_gap + text_height + bottom_pad)
    bubble_height = min(250, bubble_height)
    canvas_height = bubble_height + 20
    bubble_top = 8
    bubble_bottom = bubble_top + bubble_height

    image = Image.new("RGBA", (WIDTH, canvas_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Rounded Telegram bubble with a small lower-left message tail.
    draw.rounded_rectangle(
        (bubble_left, bubble_top, bubble_right, bubble_bottom),
        radius=29,
        fill=bubble_color + (255,),
        outline=(91, 73, 91, 210),
        width=2,
    )
    draw.polygon(
        [
            (bubble_left + 5, bubble_bottom - 37),
            (bubble_left - 17, bubble_bottom - 13),
            (bubble_left + 25, bubble_bottom - 17),
        ],
        fill=bubble_color + (255,),
    )

    name_y = bubble_top + top_pad
    draw.text((text_left, name_y), name, font=name_font, fill=accent + (255,))

    message_y = name_y + name_height + name_gap
    for line in lines:
        draw.text(
            (text_left, message_y),
            line,
            font=message_font,
            fill=(248, 247, 250, 255),
        )
        message_y += line_height

    if avatar:
        avatar_y = bubble_top + (bubble_height - avatar.height) // 2
        image.alpha_composite(avatar, (0, avatar_y))

    output = io.BytesIO()
    output.name = "xyron_quote.webp"
    image.save(output, format="WEBP", lossless=True, method=6)
    output.seek(0)
    return output


async def quote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a QuotLy-style chat-bubble sticker for the replied-to message."""
    command_message = update.effective_message
    if not command_message:
        return

    source = command_message.reply_to_message
    if not source:
        await command_message.reply_text("Reply to a message and use /q. Try /q 2 or /q 3 for another style.")
        return
    if not source.from_user:
        await command_message.reply_text("I can only quote messages sent by a Telegram user.")
        return

    theme_index = 0
    if context.args:
        if len(context.args) != 1 or not context.args[0].isdigit() or not 1 <= int(context.args[0]) <= len(ACCENTS):
            await command_message.reply_text("Usage: /q [1-3] — reply to the message you want to quote.")
            return
        theme_index = int(context.args[0]) - 1

    user = source.from_user
    quote_text = source.text or source.caption or ""
    if not quote_text:
        if source.sticker:
            quote_text = "[Sticker]"
        elif source.photo:
            quote_text = "[Photo]"
        elif source.video:
            quote_text = "[Video]"
        elif source.animation:
            quote_text = "[GIF]"
        elif source.voice:
            quote_text = "[Voice message]"
        elif source.video_note:
            quote_text = "[Video message]"
        elif source.document:
            quote_text = f"[File: {source.document.file_name or 'document'}]"
        else:
            quote_text = "[Telegram message]"

    avatar_data: bytes | bytearray | None = None
    try:
        photos = await context.bot.get_user_profile_photos(user.id, limit=1)
        if photos.total_count and photos.photos:
            photo = photos.photos[0][-1]
            telegram_file = await context.bot.get_file(photo.file_id)
            avatar_data = await telegram_file.download_as_bytearray()
    except TelegramError:
        logger.debug("Could not fetch quote author avatar for user_id=%s", user.id, exc_info=True)

    try:
        sticker = _render_sticker(quote_text, user.full_name, user.username, avatar_data, theme_index)
        await command_message.reply_sticker(
            sticker=sticker,
            read_timeout=30,
            write_timeout=30,
            connect_timeout=15,
            pool_timeout=15,
        )
    except (OSError, TelegramError):
        logger.exception("Quote sticker generation failed")
        await command_message.reply_text("I couldn't generate that quote sticker. Please try again.")
