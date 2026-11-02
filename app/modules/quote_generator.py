"""QuotLy-style quote cards generated locally with Pillow."""

from __future__ import annotations

import io
import logging

from PIL import Image, ImageDraw, ImageFont, ImageOps
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

WIDTH = 1080
PADDING = 88
THEMES = (
    {"bg": (19, 24, 42), "panel": (31, 39, 63), "accent": (132, 156, 255), "text": (247, 248, 255), "muted": (173, 184, 211)},
    {"bg": (28, 23, 21), "panel": (48, 39, 34), "accent": (240, 174, 112), "text": (255, 247, 238), "muted": (205, 183, 163)},
    {"bg": (17, 37, 34), "panel": (27, 56, 50), "accent": (111, 214, 173), "text": (240, 255, 248), "muted": (165, 204, 188)},
)


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


def _wrap_pixels(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def _avatar_bytes(data: bytes | bytearray | None) -> Image.Image | None:
    if not data:
        return None
    try:
        return ImageOps.fit(Image.open(io.BytesIO(data)).convert("RGB"), (112, 112), centering=(0.5, 0.35))
    except (OSError, ValueError):
        return None


def _render_quote(quote: str, author: str, username: str | None,
                  avatar_data: bytes | bytearray | None, theme_index: int) -> io.BytesIO:
    theme = THEMES[theme_index % len(THEMES)]
    image = Image.new("RGB", (WIDTH, 900), theme["bg"])
    draw = ImageDraw.Draw(image)
    title_font = _font(29, bold=True)
    quote_font = _font(47)
    author_font = _font(30, bold=True)
    handle_font = _font(23)

    draw.rounded_rectangle((38, 38, WIDTH - 38, 862), radius=42, fill=theme["panel"])
    draw.rounded_rectangle((PADDING, 104, PADDING + 9, 202), radius=5, fill=theme["accent"])
    draw.text((PADDING + 34, 112), "XYRON  /  QUOTE", font=title_font, fill=theme["accent"])

    quote = quote.strip()[:1800] or "[This message has no text]"
    lines = _wrap_pixels(draw, quote, quote_font, WIDTH - 2 * PADDING - 30)
    line_height = 66
    if len(lines) > 6:
        lines = lines[:6]
        lines[-1] = lines[-1].rstrip(" .") + "…"
    quote_top = 260
    for index, line in enumerate(lines):
        draw.text((PADDING, quote_top + index * line_height), line, font=quote_font, fill=theme["text"])

    footer_y = max(quote_top + len(lines) * line_height + 48, 748)
    avatar = _avatar_bytes(avatar_data)
    avatar_x = PADDING
    if avatar:
        mask = Image.new("L", (112, 112), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, 111, 111), fill=255)
        image.paste(avatar, (avatar_x, footer_y), mask)
    else:
        draw.ellipse((avatar_x, footer_y, avatar_x + 112, footer_y + 112), fill=theme["accent"])
        initials = "".join(part[0] for part in author.split()[:2]).upper() or "?"
        draw.text((avatar_x + 31, footer_y + 30), initials, font=_font(39, bold=True), fill=theme["bg"])

    text_x = avatar_x + 145
    draw.text((text_x, footer_y + 8), author[:48], font=author_font, fill=theme["text"])
    if username:
        draw.text((text_x, footer_y + 55), f"@{username[:48]}", font=handle_font, fill=theme["muted"])
    draw.text((WIDTH - 220, 806), "XYRON", font=_font(22, bold=True), fill=theme["muted"])

    output = io.BytesIO()
    output.name = "xyron_quote.png"
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


async def quote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Create a quote card from the message this command replies to."""
    command_message = update.effective_message
    if not command_message:
        return
    source = command_message.reply_to_message
    if not source:
        await command_message.reply_text("Reply to a message and use /q. Try /q 2 or /q 3 for another theme.")
        return
    if not source.from_user:
        await command_message.reply_text("I can only quote messages sent by a Telegram user.")
        return

    theme_index = 0
    if context.args:
        if len(context.args) != 1 or not context.args[0].isdigit() or not 1 <= int(context.args[0]) <= len(THEMES):
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
            file = await context.bot.get_file(photo.file_id)
            avatar_data = await file.download_as_bytearray()
    except TelegramError:
        logger.debug("Could not fetch quote author avatar for user_id=%s", user.id, exc_info=True)

    try:
        card = _render_quote(quote_text, user.full_name, user.username, avatar_data, theme_index)
        await command_message.reply_photo(
            photo=card,
            caption="Quoted with XYRON",
            read_timeout=30,
            write_timeout=30,
            connect_timeout=15,
            pool_timeout=15,
        )
    except (OSError, TelegramError):
        logger.exception("Quote card generation failed")
        await command_message.reply_text("I couldn't generate that quote card. Please try again.")
