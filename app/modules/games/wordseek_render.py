"""In-memory PNG renderer for WordSeek boards.

All generated images are held in BytesIO and are never written to disk.
"""
from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from app.services.image_fonts import font_for_text


WIDTH = 900
BG_TOP = (12, 20, 35)
BG_BOTTOM = (24, 35, 54)
GREEN = (35, 190, 112)
BLUE = (45, 139, 205)
RED = (205, 83, 91)
TILE_EMPTY = (38, 50, 69)
WHITE = (245, 248, 255)
MUTED = (157, 174, 195)


def _font(size: int, bold: bool = False):
    return font_for_text("WORDSEEK", size, bold)


def render_wordseek_board(
    length: int,
    guesses: tuple[str, ...],
    feedbacks: tuple[str, ...],
    max_guesses: int = 30,
) -> BytesIO:
    """Build a cumulative, copy-resistant board PNG entirely in memory."""
    count = len(guesses)
    header_h = 205
    row_h = 104
    footer_h = 120
    height = header_h + max(1, count) * row_h + footer_h
    image = Image.new("RGB", (WIDTH, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        t = y / max(1, height - 1)
        color = tuple(round(BG_TOP[i] * (1 - t) + BG_BOTTOM[i] * t) for i in range(3))
        draw.line((0, y, WIDTH, y), fill=color)

    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((30, 26, WIDTH - 30, 170), radius=30, fill=(20, 31, 49), outline=(65, 88, 116), width=2)
    title = f"WORDSEEK  ·  {length}-LETTER MODE"
    draw.text((58, 48), title, font=_font(34, True), fill=WHITE)
    draw.text((60, 108), f"{count}/{max_guesses} GUESSES", font=_font(25, True), fill=(178, 199, 224))
    remaining = max(0, max_guesses - count)
    draw.rounded_rectangle((WIDTH - 255, 100, WIDTH - 58, 145), radius=18, fill=(34, 49, 69))
    draw.text((WIDTH - 156, 122), f"{remaining} LEFT", font=_font(20, True), fill=(117, 220, 169), anchor="mm")

    gap = 12
    tile = min(112, (WIDTH - 100 - (length - 1) * gap) // length)
    total = length * tile + (length - 1) * gap
    x0 = (WIDTH - total) // 2
    for row, (word, feedback) in enumerate(zip(guesses, feedbacks)):
        y0 = header_h + row * row_h
        for col, letter in enumerate(word.upper()):
            x = x0 + col * (tile + gap)
            y = y0 + 4
            code = feedback[col] if col < len(feedback) else "🟥"
            fill = GREEN if code == "🟩" else BLUE if code == "🟨" else RED
            draw.rounded_rectangle((x, y, x + tile, y + tile - 4), radius=16, fill=fill, outline=(105, 125, 150), width=2)
            draw.text((x + tile / 2, y + (tile - 4) / 2), letter, font=_font(48, True), fill=WHITE, anchor="mm")

    legend_y = header_h + count * row_h + 28
    legend = ((GREEN, "CORRECT"), (BLUE, "WRONG SPOT"), (RED, "NOT IN WORD"))
    item_w = 260
    start_x = (WIDTH - item_w * 3) // 2
    for index, (color, label) in enumerate(legend):
        x = start_x + index * item_w
        draw.rounded_rectangle((x, legend_y, x + 24, legend_y + 24), radius=7, fill=color)
        draw.text((x + 35, legend_y + 12), label, font=_font(15, True), fill=MUTED, anchor="lm")
    draw.text((WIDTH // 2, height - 30), "XYRON  •  KEEP GUESSING", font=_font(17, True), fill=(124, 151, 184), anchor="mm")

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    output.name = "wordseek-board.png"
    return output
