from __future__ import annotations

import json

from app.modules.music.handlers import MusicPlugin
from app.modules.music.songsx_source import SongsxSource


def test_songsx_search_key_normalizes_case_and_punctuation(tmp_path):
    source = SongsxSource("Songsx_bot", 4363096287, tmp_path)
    assert source._key("  A.R. Rahman — Dil Se! ") == "a r rahman dil se"


def test_songsx_source_reads_legacy_cache_index(tmp_path):
    legacy = tmp_path / "old" / "songsx_cache.json"
    legacy.parent.mkdir()
    legacy.write_text(json.dumps({"dil se": {"title": "Dil Se", "artist": "A. R. Rahman"}}))
    source = SongsxSource("Songsx_bot", 4363096287, tmp_path / "new", legacy_index=legacy)
    assert source.index["dil se"]["title"] == "Dil Se"


def test_player_progress_bar_is_bounded():
    assert MusicPlugin._progress_bar(0, 100, width=10) == "●" + "─" * 10
    assert MusicPlugin._progress_bar(100, 100, width=10) == "━" * 10 + "●"
    assert MusicPlugin._progress_bar(50, 0) == "🔴 LIVE"


def test_player_keyboard_uses_native_color_styles():
    plugin = MusicPlugin.__new__(MusicPlugin)
    plugin.players = {}
    keyboard = plugin._player_keyboard(-100123)
    buttons = [button for row in keyboard["inline_keyboard"] for button in row]
    assert all(button.get("style") in {"primary", "success", "danger"} for button in buttons)
    assert any(button["callback_data"] == "music:player:pause" for button in buttons)
