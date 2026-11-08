"""Per-user music playlists and song-choice preferences stored in the Telegram cache channel.

The channel is the source of truth; no playlist or preference rows are written to SQL.
A compact compressed payload is kept in one searchable channel message per Telegram user.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import zlib
from datetime import datetime, timezone

log = logging.getLogger("xyron.music.channel_store")


class ChannelMusicStore:
    VERSION = 1
    MAX_MESSAGE_CHARS = 3900

    def __init__(self, client_provider, channel_id: int | str):
        self.client_provider = client_provider
        self.channel_id = int(channel_id or 0)
        self._peer_cache = {}
        self._records: dict[int, dict] = {}
        self._message_ids: dict[int, int] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    def _lock_for(self, user_id: int) -> asyncio.Lock:
        return self._locks.setdefault(int(user_id), asyncio.Lock())

    def _client(self):
        client = self.client_provider()
        if client is None:
            raise RuntimeError("No connected Telegram music account is available.")
        return client

    async def _peer(self, client):
        if not self.channel_id:
            raise RuntimeError("MUSIC_CACHE_CHANNEL_ID is not configured.")
        key = id(client)
        if key in self._peer_cache:
            return self._peer_cache[key]
        marked_id = self.channel_id
        if marked_id <= -1000000000000:
            raw_id = -marked_id - 1000000000000
        else:
            raw_id = abs(marked_id)
            if marked_id > 0:
                marked_id = int("-100" + str(raw_id))
        try:
            peer = await client.get_input_entity(marked_id)
        except Exception:
            dialogs = await client.get_dialogs()
            entity = next(
                (item.entity for item in dialogs if getattr(item.entity, "id", None) == raw_id),
                None,
            )
            if entity is None:
                raise RuntimeError(
                    "The connected music account cannot access the configured music cache channel."
                )
            peer = await client.get_input_entity(entity)
        self._peer_cache[key] = peer
        return peer

    @staticmethod
    def _marker(user_id: int) -> str:
        return f"XYRON_MUSIC_USER_V1_{int(user_id)}"

    @staticmethod
    def _default(user_id: int) -> dict:
        return {
            "version": ChannelMusicStore.VERSION,
            "user_id": int(user_id),
            "playlist": [],
            "preferences": {},
            "updated_at": "",
        }

    def _encode(self, user_id: int, record: dict) -> str:
        payload = json.dumps(
            record, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        packed = base64.urlsafe_b64encode(zlib.compress(payload, 9)).decode("ascii")
        message = f"{self._marker(user_id)}\n{packed}"
        if len(message) > self.MAX_MESSAGE_CHARS:
            raise ValueError(
                "Your playlist data is too large to fit in its channel record. "
                "Remove a few tracks and try again."
            )
        return message

    def _decode(self, user_id: int, message: str) -> dict | None:
        marker = self._marker(user_id) + "\n"
        if not isinstance(message, str) or not message.startswith(marker):
            return None
        try:
            packed = base64.urlsafe_b64decode(message[len(marker):].encode("ascii"))
            record = json.loads(zlib.decompress(packed).decode("utf-8"))
            if not isinstance(record, dict) or int(record.get("user_id", 0)) != int(user_id):
                return None
            record.setdefault("playlist", [])
            record.setdefault("preferences", {})
            return record
        except Exception:
            log.exception("Could not decode channel playlist record for user %s", user_id)
            return None

    async def _find_message(self, client, peer, user_id: int):
        marker = self._marker(user_id)
        async for message in client.iter_messages(peer, search=marker, limit=5):
            if self._decode(user_id, getattr(message, "message", "") or "") is not None:
                return message
        return None

    async def get_user(self, user_id: int) -> dict:
        user_id = int(user_id)
        async with self._lock_for(user_id):
            if user_id in self._records:
                return self._records[user_id]
            client = self._client()
            peer = await self._peer(client)
            message = await self._find_message(client, peer, user_id)
            record = (
                self._decode(user_id, getattr(message, "message", "") or "")
                if message else None
            ) or self._default(user_id)
            if message:
                self._message_ids[user_id] = int(message.id)
            self._records[user_id] = record
            return record

    async def save_user(self, user_id: int, record: dict) -> dict:
        user_id = int(user_id)
        async with self._lock_for(user_id):
            client = self._client()
            peer = await self._peer(client)
            record["version"] = self.VERSION
            record["user_id"] = user_id
            record["updated_at"] = datetime.now(timezone.utc).isoformat()
            encoded = self._encode(user_id, record)
            message_id = self._message_ids.get(user_id)
            if message_id is None:
                existing = await self._find_message(client, peer, user_id)
                if existing:
                    message_id = int(existing.id)
                    self._message_ids[user_id] = message_id
            if message_id is not None:
                await client.edit_message(peer, message_id, encoded, link_preview=False)
            else:
                sent = await client.send_message(peer, encoded, link_preview=False)
                self._message_ids[user_id] = int(sent.id)
            self._records[user_id] = record
            return record
