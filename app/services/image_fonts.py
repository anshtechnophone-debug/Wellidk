"""Shared Unicode-aware font selection for every Pillow renderer.

Install fonts-noto-core, fonts-noto-cjk and fonts-noto-color-emoji in runtime
images. Select a script-appropriate Noto family for user-provided text so
names in Indic, Arabic and CJK scripts do not become missing-glyph boxes.
"""
from __future__ import annotations

import unicodedata
from functools import lru_cache

from PIL import ImageFont

_FONT_FILES = {
    "default": ("NotoSans-Bold.ttf", "NotoSans-Regular.ttf"),
    "devanagari": ("NotoSansDevanagari-Bold.ttf", "NotoSansDevanagari-Regular.ttf"),
    "bengali": ("NotoSansBengali-Bold.ttf", "NotoSansBengali-Regular.ttf"),
    "gurmukhi": ("NotoSansGurmukhi-Bold.ttf", "NotoSansGurmukhi-Regular.ttf"),
    "gujarati": ("NotoSansGujarati-Bold.ttf", "NotoSansGujarati-Regular.ttf"),
    "tamil": ("NotoSansTamil-Bold.ttf", "NotoSansTamil-Regular.ttf"),
    "telugu": ("NotoSansTelugu-Bold.ttf", "NotoSansTelugu-Regular.ttf"),
    "kannada": ("NotoSansKannada-Bold.ttf", "NotoSansKannada-Regular.ttf"),
    "malayalam": ("NotoSansMalayalam-Bold.ttf", "NotoSansMalayalam-Regular.ttf"),
    "arabic": ("NotoSansArabic-Bold.ttf", "NotoSansArabic-Regular.ttf"),
    "thai": ("NotoSansThai-Bold.ttf", "NotoSansThai-Regular.ttf"),
    "cjk": ("NotoSansCJK-Bold.ttc", "NotoSansCJK-Regular.ttc"),
}

_SCRIPT_RANGES = (
    ("devanagari", 0x0900, 0x097F),
    ("bengali", 0x0980, 0x09FF),
    ("gurmukhi", 0x0A00, 0x0A7F),
    ("gujarati", 0x0A80, 0x0AFF),
    ("tamil", 0x0B80, 0x0BFF),
    ("telugu", 0x0C00, 0x0C7F),
    ("kannada", 0x0C80, 0x0CFF),
    ("malayalam", 0x0D00, 0x0D7F),
    ("thai", 0x0E00, 0x0E7F),
    ("cjk", 0x2E80, 0x9FFF),
    ("cjk", 0xF900, 0xFAFF),
    ("arabic", 0x0600, 0x06FF),
    ("arabic", 0x0750, 0x077F),
)

def script_for_text(text: str) -> str:
    """Pick the best single font family for the dominant non-Latin script."""
    counts: dict[str, int] = {}
    for char in str(text or ""):
        cp = ord(char)
        for script, start, end in _SCRIPT_RANGES:
            if start <= cp <= end:
                counts[script] = counts.get(script, 0) + 1
                break
    return max(counts, key=counts.get) if counts else "default"

@lru_cache(maxsize=256)
def _font_path(filename: str) -> str | None:
    import os
    candidates = (
        f"/usr/share/fonts/truetype/noto/{filename}",
        f"/usr/share/fonts/opentype/noto/{filename}",
    )
    return next((path for path in candidates if os.path.isfile(path)), None)

@lru_cache(maxsize=256)
def _load(path: str, size: int):
    return ImageFont.truetype(path, size=size)

def font_for_text(text: str, size: int, bold: bool = False):
    """Return a Noto font suited to the text's script, with safe Latin fallback."""
    index = 0 if bold else 1
    script = script_for_text(text)
    families = (_FONT_FILES[script], _FONT_FILES["default"]) if script != "default" else (_FONT_FILES["default"],)
    for family in families:
        path = _font_path(family[index])
        if path:
            try:
                return _load(path, size)
            except OSError:
                continue
    # Keep the application usable on minimal hosts; Docker/VPS setup installs Noto.
    return ImageFont.load_default()
