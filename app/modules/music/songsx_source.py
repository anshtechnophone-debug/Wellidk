"""Songsx-backed music search with durable Telegram-channel audio caching."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import unicodedata
from pathlib import Path

from telethon.tl.functions.messages import GetBotCallbackAnswerRequest
from telethon.tl.types import PeerChannel

log = logging.getLogger("xyron.music.songsx")


class SongsxSource:
    def __init__(
        self, bot_username: str, cache_channel_id: int | str, media_dir: Path,
        legacy_index: Path | None = None, local_cache_max_mb: int = 512,
    ):
        self.bot_username = (bot_username or "Songsx_bot").lstrip("@")
        self.raw_cache_channel_id = int(cache_channel_id or 0)
        self.media_dir = Path(media_dir)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.media_dir / "songsx_cache.json"
        self.legacy_index = Path(legacy_index) if legacy_index else None
        self.local_cache_max_bytes = int(local_cache_max_mb) * 1024 * 1024
        self.index = self._load_index()
        self.pending: dict[str, dict] = {}
        self._lock = asyncio.Lock()
        self._peer_cache = {}

    def _load_index(self) -> dict:
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError):
            if self.legacy_index and self.legacy_index.is_file():
                try:
                    payload = json.loads(self.legacy_index.read_text(encoding="utf-8"))
                    if isinstance(payload, dict):
                        log.info("Loaded legacy Songsx cache index from %s", self.legacy_index)
                        return payload
                except (OSError, json.JSONDecodeError):
                    log.exception("Could not read legacy Songsx cache index")
                    raise
            log.warning("Songsx cache index is unavailable; starting with an empty index")
            return {}

    def _save_index(self) -> None:
        temporary = self.index_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self.index, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.index_path)

    @staticmethod
    def _key(value: str) -> str:
        value = unicodedata.normalize("NFKC", str(value or "")).casefold()
        return re.sub(r"[^\w]+", " ", value, flags=re.UNICODE).strip()

    @staticmethod
    def _is_audio(message) -> bool:
        if getattr(message, "audio", None) or getattr(message, "voice", None):
            return True
        document = getattr(message, "document", None)
        mime = getattr(document, "mime_type", "") if document else ""
        return bool(mime and mime.lower().startswith("audio/"))

    async def _cache_peer(self, client):
        if not self.raw_cache_channel_id:
            raise RuntimeError("MUSIC_CACHE_CHANNEL_ID is not configured.")
        cache_key = (id(client), self.raw_cache_channel_id)
        if cache_key in self._peer_cache:
            return self._peer_cache[cache_key]
        marked_id = self.raw_cache_channel_id
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
                (d.entity for d in dialogs if getattr(d.entity, "id", None) == raw_id),
                None,
            )
            peer = await client.get_input_entity(entity or PeerChannel(raw_id))
        self._peer_cache[cache_key] = peer
        return peer

    async def _wait_for_keyboard(self, client, peer, after_id: int, timeout: float = 20):
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            messages = await client.get_messages(peer, limit=12)
            for message in sorted(messages, key=lambda item: item.id):
                if message.id > after_id and message.buttons:
                    return message
            await asyncio.sleep(0.35)
        raise TimeoutError("Songsx did not return selectable tracks in time.")

    async def _restore_from_channel(self, client, record: dict) -> str | None:
        path = Path(record.get("path", ""))
        if path.is_file() and path.stat().st_size > 10_000:
            os.utime(path, None)
            return str(path)
        message_id = record.get("cache_message_id")
        if not message_id:
            return None
        try:
            peer = await self._cache_peer(client)
            message = await client.get_messages(peer, ids=int(message_id))
            if not message or not self._is_audio(message):
                return None
            safe_name = re.sub(
                r"[^a-zA-Z0-9_-]+", "_", record.get("title", "track")
            )[:55] or "track"
            ext = getattr(getattr(message, "file", None), "ext", None) or ".mp3"
            destination = self.media_dir / f"cached_{message_id}_{safe_name}{ext}"
            if not destination.is_file() or destination.stat().st_size < 10_000:
                temporary = destination.with_suffix(destination.suffix + ".part")
                temporary.unlink(missing_ok=True)
                downloaded = await client.download_media(message, file=str(temporary))
                if not downloaded or not temporary.is_file() or temporary.stat().st_size < 10_000:
                    temporary.unlink(missing_ok=True)
                    return None
                temporary.replace(destination)
            record["path"] = str(destination)
            os.utime(destination, None)
            self._save_index()
            return str(destination)
        except Exception:
            log.exception("Could not restore cached track from the music cache channel")
            return None

    async def search(self, query: str, clients: list) -> list[dict]:
        if not clients:
            raise RuntimeError("No voice-player account is connected. Use /addaccount first.")
        key = self._key(query)
        async with self._lock:
            cached = self.index.get(key)
            if cached:
                restored = await self._restore_from_channel(clients[0], cached)
                if restored:
                    return [{
                        "id": "cache_" + secrets.token_hex(5),
                        "name": cached.get("title", query),
                        "artist_name": cached.get("artist", "Unknown artist"),
                        "duration": int(cached.get("duration", 0) or 0),
                        "_source": "songsx",
                        "_cached_path": restored,
                        "source_url": f"https://t.me/{self.bot_username}",
                    }]

            client = clients[0]
            peer = await client.get_entity("@" + self.bot_username)
            sent = await client.send_message(peer, query)
            keyboard = await self._wait_for_keyboard(client, peer, sent.id)
            choices = []
            for row in keyboard.buttons or []:
                for button in row:
                    data = getattr(button, "data", None)
                    label = (getattr(button, "text", "") or "").strip()
                    if not data or not label:
                        continue
                    if " — " in label:
                        title, artist = label.split(" — ", 1)
                    elif " - " in label:
                        title, artist = label.split(" - ", 1)
                    else:
                        title, artist = label, "Unknown artist"
                    token = secrets.token_urlsafe(7).replace("-", "_")
                    self.pending[token] = {
                        "client": client, "peer": peer, "message_id": keyboard.id,
                        "button_data": data, "query": query,
                        "title": title.strip(), "artist": artist.strip(),
                    }
                    choices.append({
                        "id": token, "name": title.strip(),
                        "artist_name": artist.strip(), "duration": 0,
                        "_source": "songsx",
                        "source_url": f"https://t.me/{self.bot_username}",
                    })
                    if len(choices) >= 7:
                        break
                if len(choices) >= 7:
                    break
            if not choices:
                raise RuntimeError("Songsx returned no selectable song buttons.")
            return choices

    def prune_local_cache(self, protected_paths=()) -> int:
        """Bound local audio storage; Telegram-channel copies remain restorable."""
        protected = {str(Path(path).resolve()) for path in protected_paths if path}
        cache_paths = {}
        for record in self.index.values():
            if not record.get("cache_message_id"):
                continue
            path = Path(record.get("path", ""))
            try:
                if path.is_file() and path.stat().st_size > 0:
                    cache_paths[str(path.resolve())] = path
            except OSError:
                continue
        entries = []
        total_bytes = 0
        for resolved, path in cache_paths.items():
            try:
                stat = path.stat()
                total_bytes += stat.st_size
                entries.append((stat.st_mtime, stat.st_size, resolved, path))
            except OSError:
                continue
        removed = 0
        for _, size, resolved, path in sorted(entries):
            if total_bytes <= self.local_cache_max_bytes:
                break
            if resolved in protected:
                continue
            try:
                path.unlink()
                total_bytes -= size
                removed += 1
            except OSError:
                log.warning("Could not prune cached audio file %s", path, exc_info=True)
        if removed:
            log.info("Pruned %s local music cache file(s); Telegram channel copies remain available", removed)
        return removed
    async def download_selected(self, track: dict) -> str:
        cached_path = track.get("_cached_path")
        if cached_path and Path(cached_path).is_file():
            return str(cached_path)
        token = str(track.get("id", ""))
        context = self.pending.get(token)
        if not context:
            raise RuntimeError("This song selection expired. Run /play again.")
        async with self._lock:
            client, peer = context["client"], context["peer"]
            baseline = await client.get_messages(peer, limit=1)
            baseline_id = baseline[0].id if baseline else context["message_id"]
            await client(GetBotCallbackAnswerRequest(
                peer=peer, msg_id=context["message_id"], data=context["button_data"]
            ))
            deadline = asyncio.get_running_loop().time() + 30
            audio_message = None
            while asyncio.get_running_loop().time() < deadline:
                selected = await client.get_messages(peer, ids=context["message_id"])
                if selected and self._is_audio(selected):
                    audio_message = selected
                    break
                recent = await client.get_messages(peer, limit=15)
                audio_message = next(
                    (item for item in sorted(recent, key=lambda item: item.id)
                     if item.id > baseline_id and self._is_audio(item)),
                    None,
                )
                if audio_message:
                    break
                await asyncio.sleep(0.35)
            if not audio_message:
                raise TimeoutError("Songsx did not send the selected audio in time.")
            file_info = getattr(audio_message, "file", None)
            size = getattr(file_info, "size", 0) or 0
            if size > 100 * 1024 * 1024:
                raise RuntimeError("The selected audio exceeds the 100 MB safety limit.")
            ext = getattr(file_info, "ext", None) or ".mp3"
            safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", context["title"])[:55] or "track"
            destination = self.media_dir / f"songsx_{audio_message.id}_{safe_name}{ext}"
            temporary = destination.with_suffix(destination.suffix + ".part")
            temporary.unlink(missing_ok=True)
            downloaded = await client.download_media(audio_message, file=str(temporary))
            if not downloaded or not temporary.is_file() or temporary.stat().st_size < 10_000:
                temporary.unlink(missing_ok=True)
                raise RuntimeError("Audio download was incomplete.")
            temporary.replace(destination)

            duration = int(getattr(getattr(audio_message, "audio", None), "duration", 0) or 0)
            cache_message = None
            try:
                cache_peer = await self._cache_peer(client)
                cache_message = await client.send_file(
                    cache_peer, str(destination),
                    caption=(
                        f"{context['title']} — {context['artist']}\n"
                        f"Search: {context['query']}\n"
                        f"Source: @{self.bot_username}"
                    )[:900],
                    force_document=False,
                )
            except Exception:
                log.exception("Track played, but saving it to the cache channel failed")

            record = {
                "path": str(destination), "title": context["title"],
                "artist": context["artist"], "duration": duration,
                "cache_message_id": getattr(cache_message, "id", None),
            }
            for alias in (context["query"], context["title"], f"{context['title']} {context['artist']}"):
                alias_key = self._key(alias)
                if alias_key:
                    self.index[alias_key] = record
            self._save_index()
            self.pending.pop(token, None)
            track["duration"] = duration
            track["name"] = context["title"]
            track["artist_name"] = context["artist"]
            return str(destination)
