from telegram import InlineKeyboardButton

import app.core.keyboard_styles  # noqa: F401 — installs the global default styles


def test_inline_buttons_receive_a_native_color() -> None:
    button = InlineKeyboardButton("Help", callback_data="help")
    assert button.style in {"primary", "success", "danger"}


def test_destructive_buttons_are_red() -> None:
    button = InlineKeyboardButton("Delete", callback_data="delete:123")
    assert button.style == "danger"


def test_positive_buttons_are_green() -> None:
    button = InlineKeyboardButton("Join", callback_data="join")
    assert button.style == "success"


def test_explicit_button_style_is_preserved() -> None:
    button = InlineKeyboardButton("Custom", callback_data="custom", style="primary")
    assert button.style == "primary"
