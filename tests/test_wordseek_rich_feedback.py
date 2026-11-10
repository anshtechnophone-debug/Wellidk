from app.modules.games.handlers import build_wordseek_inline_keyboard


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
