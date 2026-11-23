from __future__ import annotations

import asyncio

from app.modules.music.songsx_source import SongsxSource


def test_source_cleans_stale_playback_files_on_startup(tmp_path):
    playback_dir = tmp_path / ".playback_tmp"
    playback_dir.mkdir()
    (playback_dir / "crashed-session.mp3").write_bytes(b"stale")

    SongsxSource("Songsx_bot", 4363096287, tmp_path)

    assert list(playback_dir.iterdir()) == []


def test_cleanup_refuses_to_delete_files_outside_playback_directory(tmp_path):
    source = SongsxSource("Songsx_bot", 4363096287, tmp_path)
    persistent_file = tmp_path / "songsx-cache.mp3"
    persistent_file.write_bytes(b"keep")

    removed = asyncio.run(source.cleanup_playback_file(str(persistent_file)))

    assert removed is False
    assert persistent_file.exists()


def test_cleanup_deletes_temporary_playback_file(tmp_path):
    source = SongsxSource("Songsx_bot", 4363096287, tmp_path)
    temporary_file = source.playback_dir / "playback-track.mp3"
    temporary_file.write_bytes(b"temporary")

    removed = asyncio.run(source.cleanup_playback_file(str(temporary_file)))

    assert removed is True
    assert not temporary_file.exists()
