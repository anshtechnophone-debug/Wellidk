"""Per-user music playlists and preferences stored in Telegram channel messages.

The channel is the source of truth; no playlist or preference rows are written to SQL.
Each user's compressed JSON record is split into searchable channel-message chunks.
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
    CHUNK_SIZE = 3500
    MAX_PARTS = 100

    def __init__(self, client_provider, channel_id: int | str):
        self.client_provider = client_provider
        self.channel_id = int(channel_id or 0)
        self._peer_cache = {}
        self._records: dict[int, dict] = {}
        self._message_ids: dict[int, dict[int, int]] = {}
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

    def _encode(self, user_id: int, record: dict) -> list[str]:
        payload = json.dumps(
            record, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        packed = base64.urlsafe_b64encode(zlib.compress(payload, 9)).decode("ascii")
        chunks = [
            packed[index:index + self.CHUNK_SIZE]
            for index in range(0, len(packed), self.CHUNK_SIZE)
        ] or [""]
        if len(chunks) > self.MAX_PARTS:
            raise ValueError("Your music data is too large to store in the cache channel.")
        total = len(chunks)
        marker = self._marker(user_id)
        return [
            f"{marker}_PART_{index}_OF_{total}\n{chunk}"
            for index, chunk in enumerate(chunks, 1)
        ]

    def _parse_part(self, user_id: int, message: str):
        if not isinstance(message, str):
            return None
        header, separator, chunk = message.partition("\n")
        prefix = self._marker(user_id) + "_PART_"
        if not separator or not header.startswith(prefix):
            return None
        try:
            part_text, total_text = header[len(prefix):].split("_OF_", 1)
            part, total = int(part_text), int(total_text)
            if not (1 <= part <= total <= self.MAX_PARTS):
                return None
            return part, total, chunk
        except (TypeError, ValueError):
            return None

    def _decode(self, user_id: int, parts: dict[int, str], total: int) -> dict | None:
        if total < 1 or len(parts) != total or any(index not in parts for index in range(1, total + 1)):
            return None
        try:
            packed = "".join(parts[index] for index in range(1, total + 1))
            payload = base64.urlsafe_b64decode(packed.encode("ascii"))
            record = json.loads(zlib.decompress(payload).decode("utf-8"))
            if not isinstance(record, dict) or int(record.get("user_id", 0)) != int(user_id):
                return None
            record.setdefault("playlist", [])
            record.setdefault("preferences", {})
            return record
        except Exception:
            log.exception("Could not decode channel playlist record for user %s", user_id)
            return None

    async def _find_messages(self, client, peer, user_id: int):
        marker = self._marker(user_id)
        found: dict[int, int] = {}
        chunks: dict[int, str] = {}
        total = 0
        async for message in client.iter_messages(peer, search=marker, limit=200):
            parsed = self._parse_part(user_id, getattr(message, "message", "") or "")
            if not parsed:
                continue
            part, message_total, chunk = parsed
            if total and total != message_total:
                continue
            total = message_total
            found[part] = int(message.id)
            chunks[part] = chunk
        return found, chunks, total

    async def get_user(self, user_id: int) -> dict:
        user_id = int(user_id)
        async with self._lock_for(user_id):
            if user_id in self._records:
                return self._records[user_id]
            client = self._client()
            peer = await self._peer(client)
            message_ids, chunks, total = await self._find_messages(client, peer, user_id)
            record = self._decode(user_id, chunks, total) or self._default(user_id)
            self._message_ids[user_id] = message_ids
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
            messages = self._encode(user_id, record)
            existing_ids = self._message_ids.get(user_id)
            if existing_ids is None:
                existing_ids, _, _ = await self._find_messages(client, peer, user_id)
            saved_ids = {}
            for part, message_text in enumerate(messages, 1):
                message_id = existing_ids.get(part)
                if message_id is not None:
                    await client.edit_message(peer, message_id, message_text, link_preview=False)
                    saved_ids[part] = message_id
                else:
                    sent = await client.send_message(peer, message_text, link_preview=False)
                    saved_ids[part] = int(sent.id)
            stale_ids = [
                message_id for part, message_id in existing_ids.items()
                if part > len(messages)
            ]
            if stale_ids:
                await client.delete_messages(peer, stale_ids)
            self._message_ids[user_id] = saved_ids
            self._records[user_id] = record
            return record
