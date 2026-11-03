"""QuotLy-style group quote stickers for XYRON."""

from __future__ import annotations

import io
import logging
import os
import unicodedata

from PIL import Image, ImageDraw, ImageFont, ImageOps
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from app.modules.quote_features import quote_keyboard, save_group_quote

logger = logging.getLogger(__name__)
WIDTH = 512
ACCENTS = ((244, 177, 104), (143, 176, 255), (120, 220, 174))
BUBBLES = ((49, 38, 55), (37, 46, 66), (32, 54, 51))
EMOJI_FONT_PATHS = (
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/truetype/noto/NotoEmoji-Regular.ttf",
)


def _plain_unicode(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip()


def _font(size: int, bold: bool = False):
    filename = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    liberation = "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf"
    candidates = (
        f"/usr/share/fonts/truetype/dejavu/{filename}",
        f"/usr/share/fonts/truetype/liberation2/{liberation}",
        f"/usr/share/fonts/truetype/noto/{'NotoSans-Bold.ttf' if bold else 'NotoSans-Regular.ttf'}",
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _emoji_font():
    for path in EMOJI_FONT_PATHS:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=109)
            except OSError:
                continue
    return None


def _is_emoji_char(char: str) -> bool:
    cp = ord(char)
    return (
        0x1F000 <= cp <= 0x1FAFF
        or 0x2600 <= cp <= 0x27FF
        or 0x2300 <= cp <= 0x23FF
        or cp in (0x00A9, 0x00AE, 0x203C, 0x2049, 0x2122, 0x2139)
    )


def _clusters(text: str):
    result = []
    current = ""
    join_next = False
    regional_count = 0
    for char in text:
        cp = ord(char)
        is_modifier = 0xFE00 <= cp <= 0xFE0F or 0x1F3FB <= cp <= 0x1F3FF or cp == 0x20E3
        is_regional = 0x1F1E6 <= cp <= 0x1F1FF
        if not current:
            current = char
        elif is_modifier or join_next or char == "\u200d" or (is_regional and regional_count == 1):
            current += char
        else:
            result.append(current)
            current = char
        join_next = char == "\u200d"
        regional_count = regional_count + 1 if is_regional else 0
    if current:
        result.append(current)
    return result


def _draw_rich_text(image: Image.Image, xy: tuple[int, int], text: str, font, fill):
    """Draw normal text with color emoji glyphs when Noto Color Emoji is installed."""
    draw = ImageDraw.Draw(image)
    emoji_font = _emoji_font()
    x, y = xy
    for cluster in _clusters(text):
        if emoji_font and any(_is_emoji_char(ch) for ch in cluster):
            try:
                temp = Image.new("RGBA", (140, 140), (0, 0, 0, 0))
                ImageDraw.Draw(temp).text((8, 0), cluster, font=emoji_font, embedded_color=True)
                bbox = temp.getbbox()
                if bbox:
                    glyph = temp.crop(bbox)
                    target_h = max(20, int(font.size * 1.08))
                    target_w = max(18, int(glyph.width * target_h / max(1, glyph.height)))
                    glyph = glyph.resize((target_w, target_h), Image.Resampling.LANCZOS)
                    image.alpha_composite(glyph, (int(x), int(y + (font.size - target_h) // 2)))
                    x += target_w + 2
                    continue
            except (OSError, ValueError, TypeError):
                pass
        draw.text((int(x), y), cluster, font=font, fill=fill)
        try:
            x += draw.textlength(cluster, font=font)
        except AttributeError:
            x += font.getlength(cluster)
    return x


def _wrap(draw, text: str, font, width: int) -> list[str]:
    lines = []
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


def _avatar(data: bytes | bytearray | None, size: int = 50):
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


async def _user_avatar(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    try:
        photos = await context.bot.get_user_profile_photos(user_id, limit=1)
        if photos.total_count and photos.photos:
            file = await context.bot.get_file(photos.photos[0][-1].file_id)
            return await file.download_as_bytearray()
    except TelegramError:
        logger.debug("Could not fetch quote avatar for user_id=%s", user_id, exc_info=True)
    return None


def _render_sticker(quote: str, author: str, username: str | None, avatar_data, theme_index: int) -> io.BytesIO:
    theme_index %= len(ACCENTS)
    accent, bubble_color = ACCENTS[theme_index], BUBBLES[theme_index]
    quote = _plain_unicode(" ".join((quote or "[Empty message]").split()))
    author = _plain_unicode(author) or "Unknown user"
    avatar = _avatar(avatar_data)

    bubble_left, bubble_right = 58, WIDTH - 8
    text_left = bubble_left + 22
    text_width = bubble_right - text_left - 18
    name_font, message_font = _font(22, True), _font(31)
    probe = ImageDraw.Draw(Image.new("RGBA", (WIDTH, 512), (0, 0, 0, 0)))
    lines = _wrap(probe, quote, message_font, text_width)
    while len(lines) > 4 and message_font.size > 25:
        message_font = _font(message_font.size - 2)
        lines = _wrap(probe, quote, message_font, text_width)
    if len(lines) > 4:
        lines = lines[:4]
        lines[-1] = lines[-1].rstrip(" .") + "…"

    name_bbox = probe.textbbox((0, 0), author[:34], font=name_font)
    name_height = name_bbox[3] - name_bbox[1]
    line_height = message_font.size + 7
    bubble_height = max(88, 13 + name_height + 5 + len(lines) * line_height + 13)
    bubble_height = min(250, bubble_height)
    image = Image.new("RGBA", (WIDTH, bubble_height + 20), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    top, bottom = 8, 8 + bubble_height
    draw.rounded_rectangle((bubble_left, top, bubble_right, bottom), radius=29, fill=bubble_color + (255,), outline=(91, 73, 91, 210), width=2)
    draw.polygon([(bubble_left + 5, bottom - 37), (bubble_left - 14, bottom - 13), (bubble_left + 25, bottom - 17)], fill=bubble_color + (255,))

    name_y = top + 13
    _draw_rich_text(image, (text_left, name_y), author[:34], name_font, accent + (255,))
    message_y = name_y + name_height + 5
    for line in lines:
        _draw_rich_text(image, (text_left, message_y), line, message_font, (248, 247, 250, 255))
        message_y += line_height

    if avatar:
        image.alpha_composite(avatar, (2, top + (bubble_height - avatar.height) // 2))

    output = io.BytesIO()
    output.name = "xyron_quote.webp"
    image.save(output, format="WEBP", lossless=True, method=6)
    output.seek(0)
    return output


async def quote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    avatar_data = await _user_avatar(context, user.id)
    reply_markup = None
    if command_message.chat.type in {"group", "supergroup"}:
        sessions = context.application.bot_data.get("sessions")
        if sessions:
            quote_id = await save_group_quote(
                sessions,
                chat_id=command_message.chat_id,
                source_message_id=source.message_id,
                author_id=user.id,
                author_name=user.full_name,
                username=user.username,
                quote_text=quote_text,
            )
            reply_markup = quote_keyboard(quote_id)

    try:
        sticker = _render_sticker(quote_text, user.full_name, user.username, avatar_data, theme_index)
        await command_message.reply_sticker(
            sticker=sticker,
            reply_markup=reply_markup,
            read_timeout=30,
            write_timeout=30,
            connect_timeout=15,
            pool_timeout=15,
        )
    except (OSError, TelegramError):
        logger.exception("Quote sticker generation failed")
        await command_message.reply_text("I couldn't generate that quote sticker. Please try again.")
