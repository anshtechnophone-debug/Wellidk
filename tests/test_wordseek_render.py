from io import BytesIO

from PIL import Image

from app.modules.games.wordseek_render import render_wordseek_board


def test_wordseek_board_is_generated_in_memory_as_png():
    board = render_wordseek_board(
        4,
        ("king", "dead", "epic"),
        ("🟥🟨🟥🟥", "🟥🟥🟩🟥", "🟥🟩🟩🟥"),
    )
    assert isinstance(board, BytesIO)
    assert board.name == "wordseek-board.png"
    assert board.getvalue().startswith(b"\\x89PNG\\r\\n\\x1a\\n")
    board.seek(0)
    with Image.open(board) as image:
        assert image.format == "PNG"
        assert image.width == 900
        assert image.height > 500


def test_wordseek_board_supports_all_word_lengths():
    for length in (4, 5, 6):
        word = "a" * length
        board = render_wordseek_board(length, (word,), ("🟩" * length,))
        board.seek(0)
        with Image.open(board) as image:
            assert image.size[0] == 900
