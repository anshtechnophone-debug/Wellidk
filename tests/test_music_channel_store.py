from __future__ import annotations

import hashlib

from app.modules.music.channel_store import ChannelMusicStore


def test_channel_store_round_trips_large_chunked_playlist():
    store = ChannelMusicStore(client_provider=lambda: None, channel_id=-1001234567890)
    record = store._default(987654321)
    record["playlist"] = [
        {
            "item_id": f"{index:08x}",
            "title": hashlib.sha256(f"title-{index}".encode()).hexdigest() * 2,
            "artist": hashlib.sha256(f"artist-{index}".encode()).hexdigest(),
            "duration": 210 + index,
        }
        for index in range(100)
    ]
    record["preferences"] = {
        f"-100123:{index}": {
            "query": f"query-{index}",
            "title": f"chosen-track-{index}",
            "artist": f"artist-{index}",
            "chosen_by": 987654321,
        }
        for index in range(80)
    }

    messages = store._encode(987654321, record)

    assert len(messages) > 1
    chunks = {}
    total = 0
    for message in messages:
        parsed = store._parse_part(987654321, message)
        assert parsed is not None
        generation, part, total, chunk = parsed
        assert generation
        chunks[part] = chunk

    restored = store._decode(987654321, chunks, total)
    assert restored == record


def test_channel_store_records_are_scoped_to_telegram_user():
    store = ChannelMusicStore(client_provider=lambda: None, channel_id=123456789)
    record = store._default(111)
    encoded = store._encode(111, record)

    assert store._parse_part(111, encoded[0]) is not None
    assert store._parse_part(222, encoded[0]) is None
