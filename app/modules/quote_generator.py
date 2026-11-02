"""Transparent QuotLy-style Telegram quote stickers for XYRON."""

from __future__ import annotations

import io
import logging
import math

from PIL import Image, ImageDraw, ImageFont, ImageOps
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

SIZE = 512
MARGIN = 34
ACCENTS = ((132, 156, 255), (240, 174, 112), (111, 214, 173))


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
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
            if draw.textbbox((0, 0), candidate, font=font, stroke_width=1)[2] <= width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def _avatar(data: bytes | bytearray | None, size: int = 76) -> Image.Image | None:
    if not data:
        return None
    try:
        image = ImageOps.fit(Image.open(io.BytesIO(data)).convert("RGBA"), (size, size))
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
        image.putalpha(mask)
        return image
    except (OSError, ValueError):
        return None


def _render_sticker(
    quote: str,
    author: str,
    username: str | None,
    avatar_data: bytes | bytearray | None,
    theme_index: int,
) -> io.BytesIO:
    """Render a transparent 512px WebP sticker, not a rectangular image card."""
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    accent = ACCENTS[theme_index % len(ACCENTS)]

    # Keep stickers readable on both light and dark Telegram chat backgrounds.
    shadow = (0, 0, 0, 235)
    quote = " ".join((quote or "[Empty message]").split())[:520]
    quote = quote or "[Empty message]"

    chosen_font = _font(39, bold=True)
    lines = _wrap(draw, quote, chosen_font, SIZE - 2 * MARGIN - 18)
    while (len(lines) > 8 or any(draw.textbbox((0, 0), line, font=chosen_font)[2] > SIZE - 2 * MARGIN - 18 for line in lines)) and chosen_font.size > 25:
        chosen_font = _font(chosen_font.size - 2, bold=True)
        lines = _wrap(draw, quote, chosen_font, SIZE - 2 * MARGIN - 18)

    if len(lines) > 8:
        lines = lines[:8]
        lines[-1] = lines[-1].rstrip(" .") + "…"

    line_height = chosen_font.size + 12
    quote_height = len(lines) * line_height
    author_font = _font(25, bold=True)
    handle_font = _font(19)
    avatar = _avatar(avatar_data)

    footer_height = max(82 if avatar else 58, 72 if username else 42)
    total_height = quote_height + 24 + footer_height
    y = max(24, (SIZE - total_height) // 2)

    # A small opening quote mark gives the sticker a clear quote identity.
    draw.text(
        (MARGIN - 2, y - 20), "“",
        font=_font(82, bold=True), fill=accent + (255,),
        stroke_width=5, stroke_fill=shadow,
    )
    y += 44

    for line in lines:
        draw.text(
            (MARGIN, y), line,
            font=chosen_font, fill=(255, 255, 255, 255),
            stroke_width=5, stroke_fill=shadow,
        )
        y += line_height

    y += 25
    if avatar:
        image.alpha_composite(avatar, (MARGIN, y))
        text_x = MARGIN + 92
    else:
        text_x = MARGIN

    draw.text(
        (text_x, y + 5), author[:34],
        font=author_font, fill=accent + (255,),
        stroke_width=4, stroke_fill=shadow,
    )
    if username:
        draw.text(
            (text_x, y + 38), f"@{username[:32]}",
            font=handle_font, fill=(255, 255, 255, 245),
            stroke_width=3, stroke_fill=shadow,
        )

    # Telegram static stickers must have one side exactly 512px or less.
    output = io.BytesIO()
    output.name = "xyron_quote.webp"
    image.save(output, format="WEBP", lossless=True, method=6)
    output.seek(0)
    return output


async def quote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a transparent quote sticker for the message being replied to."""
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
