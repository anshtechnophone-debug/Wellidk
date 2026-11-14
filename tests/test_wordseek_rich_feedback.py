from app.modules.games.handlers import _format_wordseek_board


def test_wordseek_rich_board_renders_colored_callback_tiles():
    rendered = _format_wordseek_board(4, ("able",), ("🟩🟨🟥🟥",))
    assert "<h2>WordSeek</h2>" in rendered
    assert 'style="success"' in rendered
    assert 'style="danger"' in rendered
    assert 'data="ws:hint:🟩:A"' in rendered
    assert 'data="ws:hint:🟨:B"' in rendered
    assert "<tg-button" in rendered


def test_wordseek_rich_board_uses_real_zero_width_unicode():
    rendered = _format_wordseek_board(4, ("able",), ("🟩🟨🟥🟥",))
    zero_width = ("\u200b", "\u200c", "\u200d", "\u2060", "\ufeff")
    assert any(char in rendered for char in zero_width)
    assert "\\u200b" not in rendered
    assert "A" in rendered
