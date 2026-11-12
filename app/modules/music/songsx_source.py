"""Songsx-backed music search with durable Telegram-channel audio caching."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import errno
import re
import secrets
import unicodedata
from pathlib import Path

from telethon.tl.functions.messages import GetBotCallbackAnswerRequest
from telethon.tl.types import PeerChannel

log = logging.getLogger("xyron.music.songsx")


class SongsxSource:
    def __init__(self, bot_username: str, cache_channel_id: int | str, media_dir: Path, legacy_index: Path | None = None):
        self.bot_username = (bot_username or "Songsx_bot").lstrip("@")
        self.raw_cache_channel_id = int(cache_channel_id or 0)
        self.media_dir = Path(media_dir)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.media_dir / "songsx_cache.json"
        self.legacy_index = Path(legacy_index) if legacy_index else None
        self.index = self._load_index()
        self.pending: dict[str, dict] = {}
        self._lock = asyncio.Lock()
        self._client_locks = {}
        self._peer_cache = {}
        self._background_uploads = set()

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
            self._save_index()
            return str(destination)
        except Exception:
            log.exception("Could not restore cached track from the music cache channel")
            return None

    def _client_lock(self, client):
        return self._client_locks.setdefault(id(client), asyncio.Lock())

    async def _search_one(self, query: str, client):
        """Search from one free account and keep its bot conversation exclusive until selection."""
        lock = self._client_lock(client)
        await lock.acquire()
        try:
            peer = await client.get_entity("@" + self.bot_username)
            sent = await client.send_message(peer, query)
            keyboard = await self._wait_for_keyboard(client, peer, sent.id, timeout=15)
            selected = next(
                (
                    (button, (getattr(button, "text", "") or "").strip())
                    for row in (keyboard.buttons or [])
                    for button in row
                    if getattr(button, "data", None) and (getattr(button, "text", "") or "").strip()
                ),
                None,
            )
            if not selected:
                lock.release()
                return None
            button, label = selected
            if " — " in label:
                title, artist = label.split(" — ", 1)
            elif " - " in label:
                title, artist = label.split(" - ", 1)
            else:
                title, artist = label, "Unknown artist"
            token = secrets.token_urlsafe(9).replace("-", "_")
            self.pending[token] = {
                "client": client,
                "peer": peer,
                "message_id": keyboard.id,
                "button_data": button.data,
                "query": query,
                "title": title.strip(),
                "artist": artist.strip(),
                "lock": lock,
            }
            return {
                "id": token,
                "name": title.strip(),
                "artist_name": artist.strip(),
                "duration": 0,
                "_source": "songsx",
                "_music_client": client,
            }
        except BaseException:
            if lock.locked():
                lock.release()
            raise

    async def _cached_channel_message(self, client, record):
        message_id = record.get("cache_message_id")
        if not message_id:
            return None
        try:
            peer = await self._cache_peer(client)
            message = await client.get_messages(peer, ids=int(message_id))
            return message if message and self._is_audio(message) else None
        except Exception:
            log.debug("Cached channel track could not be opened", exc_info=True)
            return None

    async def _find_cached_channel_track(self, client, query: str):
        """Find an exact title match in the cache channel even if the local index is missing."""
        try:
            peer = await self._cache_peer(client)
            query_key = self._key(query)
            async for message in client.iter_messages(peer, search=query, limit=30):
                if not self._is_audio(message):
                    continue
                caption = (getattr(message, "message", "") or "").strip()
                first_line = caption.splitlines()[0] if caption else ""
                if " — " in first_line:
                    title, artist = first_line.split(" — ", 1)
                elif " - " in first_line:
                    title, artist = first_line.split(" - ", 1)
                else:
                    title, artist = first_line, "Unknown artist"
                title_key = self._key(title)
                full_key = self._key(f"{title} {artist}")
                if query_key not in {title_key, full_key}:
                    continue
                duration = int(
                    getattr(getattr(message, "audio", None), "duration", 0) or 0
                )
                return {
                    "id": "cache_" + secrets.token_hex(5),
                    "name": title or query,
                    "artist_name": artist or "Unknown artist",
                    "duration": duration,
                    "_source": "cache",
                    "_audio_message": message,
                    "_music_client": client,
                    "_stream_context": {
                        "title": title or query,
                        "artist": artist or "Unknown artist",
                        "query": "cache",
                        "cache_message_id": int(message.id),
                    },
                }
        except Exception:
            log.debug("Cache-channel search failed for a free account", exc_info=True)
        return None

    async def search_first(self, query: str, clients: list) -> dict | None:
        """Return the first matching result from all supplied free accounts in parallel.

        Exact cache hits are resolved before any external search is started.
        """
        if not clients:
            raise RuntimeError("No free voice-player account is currently available.")
        key = self._key(query)
        cached = self.index.get(key)
        if cached and key in {
            self._key(cached.get("title", "")),
            self._key(f"{cached.get('title', '')} {cached.get('artist', '')}"),
        }:
            for client in clients:
                local_path = Path(cached.get("path", ""))
                if local_path.is_file() and local_path.stat().st_size > 10_000:
                    return {
                        "id": "cache_" + secrets.token_hex(5),
                        "name": cached.get("title", query),
                        "artist_name": cached.get("artist", "Unknown artist"),
                        "duration": int(cached.get("duration", 0) or 0),
                        "_source": "cache",
                        "_cached_path": str(local_path),
                        "_music_client": client,
                    }
                cached_message = await self._cached_channel_message(client, cached)
                if cached_message:
                    return {
                        "id": "cache_" + secrets.token_hex(5),
                        "name": cached.get("title", query),
                        "artist_name": cached.get("artist", "Unknown artist"),
                        "duration": int(cached.get("duration", 0) or 0),
                        "_source": "cache",
                        "_audio_message": cached_message,
                        "_music_client": client,
                        "_stream_context": {
                            "title": cached.get("title", query),
                            "artist": cached.get("artist", "Unknown artist"),
                            "query": "cache",
                            "cache_message_id": int(cached.get("cache_message_id")),
                        },
                    }
            for client in clients:
                cached_track = await self._find_cached_channel_track(client, query)
                if cached_track:
                    return cached_track
        else:
            for client in clients:
                cached_track = await self._find_cached_channel_track(client, query)
                if cached_track:
                    return cached_track

        tasks = [asyncio.create_task(self._search_one(query, client)) for client in clients]
        winner = None
        try:
            for completed in asyncio.as_completed(tasks):
                try:
                    result = await completed
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.debug("A music account search did not return a result", exc_info=True)
                    continue
                if result:
                    winner = result
                    break
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, dict) and result is not winner:
                    context = self.pending.pop(str(result.get("id", "")), None)
                    if context:
                        lock = context.get("lock")
                        if lock and lock.locked():
                            lock.release()
        return winner

    async def _upload_to_cache(self, client, destination: Path, record: dict, context: dict):
        try:
            cache_peer = await self._cache_peer(client)
            cache_message = await client.send_file(
                cache_peer,
                str(destination),
                caption=f"{context['title']} — {context['artist']}"[:900],
                force_document=False,
            )
            record["cache_message_id"] = getattr(cache_message, "id", None)
            self._save_index()
        except Exception:
            log.info("Background music cache upload failed", exc_info=True)

    @staticmethod
    def _write_all(pipe, data):
        view = memoryview(data)
        while view:
            written = pipe.write(view)
            if not written:
                raise BrokenPipeError("The audio stream reader closed.")
            view = view[written:]

    async def _stream_audio_to_fifo(
        self, client, audio_message, fifo_path: Path, temporary: Path,
        destination: Path, context: dict, duration: int,
    ) -> str:
        descriptor = None
        try:
            while descriptor is None:
                try:
                    descriptor = os.open(
                        str(fifo_path), os.O_WRONLY | os.O_NONBLOCK
                    )
                except OSError as exc:
                    if exc.errno != errno.ENXIO:
                        raise
                    await asyncio.sleep(0.05)
            os.set_blocking(descriptor, True)
            with os.fdopen(descriptor, "wb", buffering=0) as pipe:
                descriptor = None
                pipe_available = True
                with temporary.open("wb") as saved:
                    async for chunk in client.iter_download(
                        audio_message.media, request_size=512 * 1024
                    ):
                        if not chunk:
                            continue
                        saved.write(chunk)
                        if pipe_available:
                            try:
                                await asyncio.to_thread(self._write_all, pipe, chunk)
                            except (BrokenPipeError, OSError) as exc:
                                if isinstance(exc, OSError) and exc.errno not in {
                                    errno.EPIPE, errno.EBADF
                                }:
                                    raise
                                pipe_available = False
                                log.warning(
                                    "Voice player closed the live audio stream; "
                                    "finishing the cache download for recovery."
                                )
                if temporary.stat().st_size < 10_000:
                    raise RuntimeError("The audio stream was incomplete.")
            temporary.replace(destination)
            record = {
                "path": str(destination),
                "title": context["title"],
                "artist": context["artist"],
                "duration": duration,
                "cache_message_id": context.get("cache_message_id"),
            }
            for alias in (
                context["title"],
                f"{context['title']} {context['artist']}",
            ):
                alias_key = self._key(alias)
                if alias_key:
                    self.index[alias_key] = record
            self._save_index()

            # The local file is ready for replay. Channel upload runs independently
            # so a stream-end/stop event cannot cancel the cache operation.
            if not record["cache_message_id"]:
                upload_task = asyncio.create_task(
                    self._upload_to_cache(client, destination, record, context),
                    name=f"xyron-music-cache-upload-{destination.stem}",
                )
                self._background_uploads.add(upload_task)
                upload_task.add_done_callback(self._background_uploads.discard)
            return str(destination)
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            temporary.unlink(missing_ok=True)
            fifo_path.unlink(missing_ok=True)

    def discard_selection(self, track: dict | None):
        """Release an unconsumed search result if the requesting command is cancelled."""
        if not track:
            return
        context = self.pending.pop(str(track.get("id", "")), None)
        if context:
            lock = context.get("lock")
            if lock and lock.locked():
                lock.release()

    async def resolve_selected(self, track: dict) -> dict:
        """Click the first result and retain its Telegram message without downloading it."""
        if track.get("_cached_path") or track.get("_audio_message"):
            return track
        token = str(track.get("id", ""))
        context = self.pending.get(token)
        if not context:
            raise RuntimeError("The selected track is no longer available. Please try again.")
        client, peer = context["client"], context["peer"]
        lock = context.get("lock")
        if lock is None:
            lock = self._client_lock(client)
            await lock.acquire()
        try:
            baseline = await client.get_messages(peer, limit=1)
            baseline_id = baseline[0].id if baseline else context["message_id"]
            await client(GetBotCallbackAnswerRequest(
                peer=peer, msg_id=context["message_id"], data=context["button_data"]
            ))
            # Songsx may take longer to replace its result keyboard with the
            # selected audio message during busy periods.
            deadline = asyncio.get_running_loop().time() + 40
            audio_message = None
            while asyncio.get_running_loop().time() < deadline:
                selected = await client.get_messages(peer, ids=context["message_id"])
                if selected and self._is_audio(selected):
                    audio_message = selected
                    break
                recent = await client.get_messages(peer, limit=30)
                audio_message = next(
                    (
                        item for item in sorted(recent, key=lambda item: item.id)
                        if item.id > baseline_id and self._is_audio(item)
                    ),
                    None,
                )
                if audio_message:
                    break
                await asyncio.sleep(0.2)
            if not audio_message:
                raise TimeoutError("The selected track could not be prepared.")
        finally:
            if lock.locked():
                lock.release()
            self.pending.pop(token, None)

        track["_audio_message"] = audio_message
        track["_music_client"] = client
        track["_stream_context"] = {
            "title": context["title"],
            "artist": context["artist"],
            "query": context.get("query", ""),
        }
        track["name"] = context["title"]
        track["artist_name"] = context["artist"]
        track["duration"] = int(
            getattr(getattr(audio_message, "audio", None), "duration", 0) or 0
        )
        return track

    async def prepare_selected_stream(self, track: dict) -> dict:
        """Create a FIFO so playback starts while the selected Telegram audio is downloaded."""
        cached_path = track.get("_cached_path")
        if cached_path and Path(cached_path).is_file():
            return {
                "path": str(cached_path),
                "duration": int(track.get("duration", 0) or 0),
                "task": None,
                "final_path": str(cached_path),
            }

        await self.resolve_selected(track)
        audio_message = track["_audio_message"]
        client = track["_music_client"]
        context = track["_stream_context"]
        file_info = getattr(audio_message, "file", None)
        size = getattr(file_info, "size", 0) or 0
        if size > 100 * 1024 * 1024:
            raise RuntimeError("The selected audio is too large to play.")
        duration = int(track.get("duration", 0) or 0)
        safe_name = re.sub(
            r"[^a-zA-Z0-9_-]+", "_", context["title"]
        )[:55] or "track"
        unique = secrets.token_hex(8)
        fifo_path = self.media_dir / f"stream_{unique}.fifo"
        temporary = self.media_dir / f"stream_{unique}.part"
        destination = self.media_dir / (
            f"songsx_{getattr(audio_message, 'id', unique)}_{safe_name}"
            f"{getattr(file_info, 'ext', None) or '.mp3'}"
        )
        os.mkfifo(fifo_path, 0o600)
        task = asyncio.create_task(
            self._stream_audio_to_fifo(
                client, audio_message, fifo_path, temporary,
                destination, context, duration,
            ),
            name=f"xyron-music-stream-{unique}",
        )
        return {
            "path": str(fifo_path),
            "duration": duration,
            "task": task,
            "final_path": str(destination),
        }

    async def search(self, query: str, clients: list) -> list[dict]:
        if not clients:
            raise RuntimeError("No voice-player account is connected. Use /addaccount first.")
        key = self._key(query)
        async with self._lock:
            cached = self.index.get(key)
            if cached:
                # Only reuse an exact title/title+artist cache hit. A raw search
                # phrase must never become a global shortcut to another user's choice.
                if key != self._key(
                    f"{cached.get('title', '')} {cached.get('artist', '')}"
                ):
                    cached = None
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
                        "_music_client": client,
                        "source_url": f"https://t.me/{self.bot_username}",
                    })
                    if len(choices) >= 7:
                        break
                if len(choices) >= 7:
                    break
            if not choices:
                raise RuntimeError("Songsx returned no selectable song buttons.")
            return choices

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
            # Cache by the resolved track identity, not the user's broad search phrase.
            # Per-user search preferences live separately in the Telegram cache channel. 
            for alias in (context["title"], f"{context['title']} {context['artist']}"):
                alias_key = self._key(alias)
                if alias_key:
                    self.index[alias_key] = record
            self._save_index()
            self.pending.pop(token, None)
            track["duration"] = duration
            track["name"] = context["title"]
            track["artist_name"] = context["artist"]
            return str(destination)
