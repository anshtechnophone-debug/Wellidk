from app.modules.games.handlers import build_wordseek_rich_feedback


def test_wordseek_feedback_uses_colored_rich_buttons_without_images_or_emoji():
    rendered = build_wordseek_rich_feedback(
        5,
        ("crane", "slate"),
        ("🟩🟨🟥🟥🟨", "🟩🟩🟩🟩🟩"),
    )

    assert "<tg-button type=\"disabled\" style=\"success\">" in rendered
    assert "<tg-button type=\"disabled\" style=\"danger\">" in rendered
    assert "<tg-button type=\"disabled\">" in rendered
    assert "WORDSEEK · 5-LETTER MODE" in rendered
    assert "2/30 guesses" in rendered
    assert "<img" not in rendered
    assert "<tg-photo" not in rendered
    assert "🟩" not in rendered
    assert "🟨" not in rendered
    assert "🟥" not in rendered


def test_wordseek_feedback_adds_invisible_noise_around_letters():
    rendered = build_wordseek_rich_feedback(4, ("able",), ("🟩🟨🟥🟥",))
    zero_width = ("\u200b", "\u200c", "\u200d", "\u2060", "\ufeff")

    assert any(char in rendered for char in zero_width)
    assert "A" in rendered
    assert "B" in rendered
    assert "L" in rendered
    assert "E" in rendered
