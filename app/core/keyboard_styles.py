"""Apply consistent native Telegram colors to every XYRON inline button.

Telegram supports only three native styles: blue (primary), green (success),
and red (danger). This keeps explicit styles intact and gives every otherwise
unstyled button a stable color based on its label/action.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton

_DANGER_WORDS = (
    "cancel", "close", "delete", "remove", "ban", "kick", "mute",
    "deny", "reject", "stop", "reset", "revoke", "leave", "disable",
)
_SUCCESS_WORDS = (
    "join", "start", "verify", "confirm", "approve", "yes", "save",
    "submit", "accept", "unmute", "unban", "unlock", "continue",
    "enable", "vote", "claim", "collect", "play", "create", "allow",
)


def _choose_style(label: str) -> str:
    normalized = label.casefold()
    words = set(normalized.replace("—", " ").replace("-", " ").replace("_", " ").split())

    if words.intersection(_DANGER_WORDS):
        return "danger"
    if words.intersection(_SUCCESS_WORDS):
        return "success"

    # Stable across restarts; distributes neutral buttons across all three colors.
    styles = ("primary", "success", "danger")
    return styles[sum(ord(char) for char in normalized) % len(styles)]


def install_inline_button_styles() -> None:
    """Set a native color on every InlineKeyboardButton created by XYRON."""
    button_class = InlineKeyboardButton
    if getattr(button_class, "_xyron_color_styles_installed", False):
        return

    original_init = button_class.__init__

    def colorful_init(self, *args, **kwargs):
        if kwargs.get("style") is None:
            text = kwargs.get("text")
            if text is None and args:
                text = args[0]
            parts = (
                str(text or ""),
                str(kwargs.get("callback_data") or ""),
                str(kwargs.get("url") or ""),
            )
            kwargs["style"] = _choose_style(" ".join(parts))
        original_init(self, *args, **kwargs)

    button_class.__init__ = colorful_init
    button_class._xyron_color_styles_installed = True
