from app.modules.games.handlers import (
    build_wordseek_inline_keyboard,
    build_wordseek_rich_feedback,
)


def test_wordseek_fallback_uses_disabled_colored_tiles_without_images():
    keyboard = build_wordseek_inline_keyboard(
        ("crane",), ("🟩🟨🟥🟥🟨",)
    )
    row = keyboard["inline_keyboard"][0]

    assert len(row) == 5
    assert row[0] == {"text": "C", "disabled": {}, "style": "success"}
    assert row[1] == {"text": "🟨 R", "disabled": {}}
    assert row[2] == {"text": "A", "disabled": {}, "style": "danger"}
    assert all(tile["disabled"] == {} for tile in row)


def test_wordseek_rich_feedback_uses_real_zero_width_unicode():
    rendered = build_wordseek_rich_feedback(4, ("able",), ("🟩🟨🟥🟥",))
    zero_width = ("\u200b", "\u200c", "\u200d", "\u2060", "\ufeff")

    assert any(char in rendered for char in zero_width)
    assert "\\u200b" not in rendered
    assert "A" in rendered
