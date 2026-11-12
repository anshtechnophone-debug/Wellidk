"""XYRON Music plugin: Songsx search, styled player UI, and PyTgCalls playback."""

from __future__ import annotations

import asyncio
import html
import logging
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
from telegram import Chat, Update
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
)
from pytgcalls import filters as call_filters
from pytgcalls.types import ChatUpdate, StreamEnded

from app.modules.music.account_manager import AccountManager
from app.modules.music.channel_store import ChannelMusicStore
from app.modules.music.player_art import make_player_cover
from app.modules.music.player_pool import MultiCallPool
from app.modules.music.songsx_source import SongsxSource

log = logging.getLogger("xyron.music")


class MusicPlugin:
    def __init__(self, application: Application, settings):
        self.application = application
        self.bot = application.bot
        self.settings = settings
        self.owner_id = int(getattr(settings, "music_owner_id", None) or settings.owner_id)
        self.public_username = ""
        self.channel = getattr(settings, "music_channel", "@xyronschannel")
        self.cache_channel_id = getattr(settings, "music_cache_channel_id", 4363096287)
        self.data_dir = Path(getattr(settings, "music_data_dir", "./data/music"))
        self.media_dir = self.data_dir / "media"
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.calls = MultiCallPool()
        self.calls.set_handoff_handler(self._handle_player_handoff)
        self.accounts = AccountManager(
            bot=self.bot,
            call_pool=self.calls,
            api_id=settings.music_api_id,
            api_hash=settings.music_api_hash,
            owner_id=self.owner_id,
            data_dir=self.data_dir,
            legacy_store=settings.music_legacy_accounts_file,
        )
        self.source = SongsxSource(
            bot_username=settings.music_songsx_bot,
            cache_channel_id=self.cache_channel_id,
            media_dir=self.media_dir,
            legacy_index=Path(settings.music_legacy_cache_index),
        )
        self.queues = defaultdict(deque)
        self.players = {}
        self.starting_tracks = {}
        self.playlist_sessions = {}
        self.user_store = None
        self.progress_tasks = {}
        self.locks = defaultdict(asyncio.Lock)
        self._intentional_leave_until = {}
        self.search_cache = defaultdict(dict)
        self.http: aiohttp.ClientSession | None = None
        self._started = False
        self.calls.on_update(call_filters.stream_end())(self._on_stream_end)
        self.calls.on_update(
            call_filters.chat_update(ChatUpdate.Status.LEFT_CALL)
        )(self._on_call_left)

    async def start(self):
        if self._started:
            return
        self.http = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=45),
            connector=aiohttp.TCPConnector(limit=30, ttl_dns_cache=300, keepalive_timeout=30),
        )
        try:
            count = await self.accounts.load_existing()
            if self.accounts.clients:
                self.user_store = ChannelMusicStore(
                    client_provider=lambda: self.accounts.clients[0],
                    channel_id=self.cache_channel_id,
                )
            await self.calls.start()
            me = await self.bot.get_me()
            self.public_username = me.username or "xyronrobot"
            self._started = True
            log.info("XYRON Music started with %s voice-player account(s)", count)
        except Exception:
            await self.shutdown()
            raise

    async def shutdown(self):
        for task in list(self.progress_tasks.values()):
            task.cancel()
        if self.progress_tasks:
            await asyncio.gather(*self.progress_tasks.values(), return_exceptions=True)
        self.progress_tasks.clear()
        try:
            await self.calls.stop()
        except Exception:
            log.exception("Could not stop all PyTgCalls instances cleanly")
        await self.accounts.shutdown()
        if self.http and not self.http.closed:
            await self.http.close()
        self._started = False

    def is_active(self, chat_id: int) -> bool:
        return bool(self.players.get(chat_id) or self.queues.get(chat_id))

    async def _api(self, method: str, payload: dict, photo=None):
        if not self.http or self.http.closed:
            raise RuntimeError("Music HTTP client is not ready.")
        token = self.settings.bot_token
        url = f"https://api.telegram.org/bot{token}/{method}"
        if photo is None:
            async with self.http.post(url, json=payload) as response:
                data = await response.json()
        else:
            form = aiohttp.FormData()
            for key, value in payload.items():
                import json
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False)
                form.add_field(key, str(value))
            photo.seek(0)
            form.add_field(
                "photo", photo.read(), filename="xyron_music_player.png",
                content_type="image/png",
            )
            async with self.http.post(url, data=form) as response:
                data = await response.json()
        if not data.get("ok"):
            raise RuntimeError(
                f"Telegram {method} failed: {data.get('description', 'unknown error')}"
            )
        return data["result"]

    def _subscription_keyboard(self):
        return {"inline_keyboard": [
            [{"text": "📢  JOIN XYRON CHANNEL", "url": "https://t.me/xyronschannel", "style": "primary"}],
            [{"text": "✅  CONFIRM MEMBERSHIP", "callback_data": "music:subscription", "style": "success"}],
        ]}

    async def _is_channel_member(self, user_id: int) -> bool:
        try:
            member = await self.bot.get_chat_member(self.channel, user_id)
            return member.status in {
                ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.MEMBER,
            } or bool(getattr(member, "is_member", False))
        except Exception:
            log.exception("Could not verify music channel membership for user %s", user_id)
            return False

    async def _require_membership(self, update: Update) -> bool:
        if not self._started:
            if update.effective_message:
                await update.effective_message.reply_text("🎵 XYRON Music is not ready yet. Please check the bot status or try again shortly.")
            return False
        user = update.effective_user
        message = update.effective_message
        if not user or not message:
            return False
        if await self._is_channel_member(user.id):
            return True
        try:
            await self._api("sendMessage", {
                "chat_id": user.id,
                "text": (
                    "🔒 <b>One quick step before playing music</b>\n\n"
                    "Join <b>XYRON's official channel</b>, then tap Confirm Membership. "
                    "Once verified, you can use /play in your groups."
                ),
                "parse_mode": "HTML",
                "reply_markup": self._subscription_keyboard(),
                "disable_web_page_preview": True,
            })
            await message.reply_text(
                "🔒 Please join @xyronschannel and confirm membership in my private chat. "
                "I've sent you the verification button."
            )
        except Exception:
            await message.reply_text(
                "🔒 Open @xyronrobot in private chat, then join @xyronschannel and confirm membership."
            )
        return False

    async def _can_control(self, chat, user) -> bool:
        if not user:
            return False
        if user.id == self.owner_id:
            return True
        if chat.type == Chat.PRIVATE:
            return False
        try:
            member = await self.bot.get_chat_member(chat.id, user.id)
            return member.status in {ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR}
        except Exception:
            log.exception("Could not verify music control permissions")
            return False

    async def _reject_control(self, update: Update) -> bool:
        if not await self._require_membership(update):
            return True
        if not await self._can_control(update.effective_chat, update.effective_user):
            await update.effective_message.reply_text(
                "⛔ Only group admins or the configured XYRON owner can control playback."
            )
            return True
        return False

    async def _ensure_player_in_group(self, chat_id: int, preferred_client=None):
        if chat_id >= 0:
            raise RuntimeError("Music playback is available in groups, not private chats.")
        if not self.accounts.clients:
            raise RuntimeError("No voice-player account is connected. Ask the owner to use /addaccount.")
        invite = await self.bot.create_chat_invite_link(
            chat_id,
            member_limit=1,
            expire_date=datetime.now(timezone.utc) + timedelta(minutes=5),
            name="XYRON Music voice player",
        )
        try:
            return await self.calls.ensure_member(
                chat_id, invite.invite_link, preferred_client=preferred_client
            )
        finally:
            try:
                await self.bot.revoke_chat_invite_link(chat_id, invite.invite_link)
            except Exception:
                log.debug("Could not revoke temporary music invite", exc_info=True)

    @staticmethod
    def _fmt_time(seconds):
        seconds = max(0, int(seconds or 0))
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    @staticmethod
    def _progress_bar(position, duration, width=20):
        if duration <= 0:
            return "🔴 LIVE"
        filled = min(width, int(width * position / duration))
        return "━" * filled + "●" + "─" * (width - filled)

    def _render_player(self, chat_id):
        track = self.players.get(chat_id)
        if not track:
            return "🎵 Nothing is playing right now."
        duration = int(track.get("duration") or 0)
        elapsed_now = (
            track.get("paused_elapsed", 0) if track.get("paused")
            else int(time.monotonic() - track["started_at"])
        )
        elapsed = min(duration, int(elapsed_now)) if duration else int(elapsed_now)
        esc = lambda value: html.escape(str(value or ""))
        title = esc(track.get("title", "Unknown title"))
        artist = esc(track.get("artist", "Unknown artist"))
        requester = esc(track.get("requested_by", "A group member"))
        if duration:
            timeline = (
                f"<code>{self._fmt_time(elapsed)}</code>  "
                f"{self._progress_bar(elapsed, duration, width=12)}  "
                f"<code>{self._fmt_time(duration)}</code>"
            )
        else:
            timeline = f"<code>{self._fmt_time(elapsed)}</code>  🔴 LIVE"
        status = "⏸ Paused" if track.get("paused") else "🟢 Playing"
        loading = " · ⬇️ buffering" if not track.get("_stream_ready", True) else ""
        return (
            f"🎧 <b>XYRON MUSIC</b> · {status}{loading}\n"
            f"🎵 <b>{title}</b>\n"
            f"🎤 {artist}\n"
            f"{timeline}\n"
            f"👤 {requester}"
        )

    def _player_keyboard(self, chat_id):
        paused = bool(self.players.get(chat_id, {}).get("paused"))
        return {"inline_keyboard": [
            [
                {
                    "text": "▶ Resume" if paused else "Ⅱ Pause",
                    "callback_data": "music:player:resume" if paused else "music:player:pause",
                },
                {"text": "⏭ Next", "callback_data": "music:player:skip"},
            ],
            [
                {"text": "♫ Playlist", "callback_data": "music:playlist:open"},
                {"text": "■ Stop", "callback_data": "music:player:stop"},
            ],
        ]}

    async def _send_player_card(self, chat_id, cover, caption):
        return await self._api("sendPhoto", {
            "chat_id": chat_id, "caption": caption, "parse_mode": "HTML",
            "reply_markup": self._player_keyboard(chat_id),
        }, photo=cover)

    async def _edit_player_card(self, chat_id, message_id, caption, with_keyboard=True):
        payload = {
            "chat_id": chat_id, "message_id": message_id,
            "caption": caption, "parse_mode": "HTML",
            "reply_markup": self._player_keyboard(chat_id) if with_keyboard else {"inline_keyboard": []},
        }
        return await self._api("editMessageCaption", payload)

    async def _edit_song_choices(self, chat_id, message_id, results):
        rows = []
        for index, item in enumerate(results[:7]):
            title = str(item.get("name", "Unknown"))[:35]
            artist = str(item.get("artist_name", "Unknown artist"))[:20]
            rows.append([{
                "text": f"♫  {title} — {artist}",
                "callback_data": f"music:choose:{item['id']}",
                "style": "primary" if index % 2 == 0 else "success",
            }])
        await self._api("editMessageText", {
            "chat_id": chat_id, "message_id": message_id,
            "text": "🎼 <b>CHOOSE YOUR TRACK</b>\nSelect a song to add it to the group player:",
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": rows},
            "disable_web_page_preview": True,
        })

    async def _leave_call(self, chat_id):
        """Leave deliberately without treating our own leave event as VC shutdown."""
        self._intentional_leave_until[chat_id] = time.monotonic() + 3
        try:
            await self.calls.leave_call(chat_id)
        except Exception:
            self._intentional_leave_until.pop(chat_id, None)
            raise

    async def _stop_progress(self, chat_id):
        task = self.progress_tasks.pop(chat_id, None)
        if task and task is not asyncio.current_task():
            task.cancel()

    async def _update_progress(self, chat_id):
        while chat_id in self.players:
            track = self.players.get(chat_id)
            if not track:
                break
            try:
                await self._edit_player_card(
                    chat_id, track["message_id"], self._render_player(chat_id)
                )
            except Exception:
                log.debug("Player progress refresh failed for chat %s", chat_id, exc_info=True)
            await asyncio.sleep(10)

    def _on_music_stream_ready(self, chat_id, track_id, task):
        try:
            final_path = task.result()
        except Exception:
            log.warning("A music stream could not be fully cached", exc_info=True)
            return
        current = self.players.get(chat_id)
        if current and str(current.get("id")) == str(track_id):
            current["path"] = final_path
            current["_stream_ready"] = True

    async def _cancel_track_stream(self, track):
        task = track.get("_stream_task") if track else None
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _start_track(self, chat_id, track, status_message_id):
        await self._ensure_player_in_group(
            chat_id, preferred_client=track.get("_music_client")
        )
        stream = await self.source.prepare_selected_stream(track)
        stream_task = stream.get("task")
        try:
            await self.calls.play(chat_id, stream["path"])
        except Exception:
            if stream_task:
                await self._cancel_track_stream({"_stream_task": stream_task})
            raise
        self.players[chat_id] = {
            "id": str(track["id"]),
            "title": track.get("name", "Unknown title"),
            "artist": track.get("artist_name", "Unknown artist"),
            "requested_by": track.get("requested_by", "A group member"),
            "requested_by_username": track.get("requested_by_username", ""),
            "duration": int(stream.get("duration") or track.get("duration") or 0),
            "started_at": time.monotonic(),
            "message_id": status_message_id,
            "path": stream["path"],
            "volume": 100,
            "paused": False,
            "paused_elapsed": 0,
            "_stream_ready": stream_task is None,
            "_stream_task": stream_task,
            "_playlist_item_id": track.get("_playlist_item_id"),
        }
        if stream_task:
            stream_task.add_done_callback(
                lambda task: self._on_music_stream_ready(
                    chat_id, track["id"], task
                )
            )
        cover = make_player_cover(
            title=self.players[chat_id]["title"],
            artist=self.players[chat_id]["artist"],
            requested_by=self.players[chat_id]["requested_by"],
            bot_username=self.public_username,
        )
        sent = await self._send_player_card(chat_id, cover, self._render_player(chat_id))
        self.players[chat_id]["message_id"] = sent["message_id"]
        try:
            await self.bot.delete_message(chat_id, status_message_id)
        except Exception:
            pass
        await self._stop_progress(chat_id)
        self.progress_tasks[chat_id] = asyncio.create_task(
            self._update_progress(chat_id), name=f"xyron-music-progress-{chat_id}"
        )

    async def _play_next(self, chat_id, status_message_id=None):
        async with self.locks[chat_id]:
            while self.queues[chat_id]:
                track = self.queues[chat_id].popleft()
                message_id = status_message_id or track.get("status_message_id")
                if not message_id:
                    sent = await self.bot.send_message(chat_id, "⏳ Loading next track…")
                    message_id = sent.message_id
                self.starting_tracks[chat_id] = track
                try:
                    await self._start_track(chat_id, track, message_id)
                    return
                except Exception:
                    log.exception("Could not start queued music track")
                    await self.bot.send_message(
                        chat_id,
                        f"⚠️ Could not start <b>{html.escape(str(track.get('name', 'track')))}</b>. "
                        "Please check the voice chat and try again.",
                        parse_mode="HTML",
                    )
                    status_message_id = None
                finally:
                    if self.starting_tracks.get(chat_id) is track:
                        self.starting_tracks.pop(chat_id, None)
            await self._stop_progress(chat_id)
            self.players.pop(chat_id, None)
            self.playlist_sessions.pop(chat_id, None)
            try:
                await self._leave_call(chat_id)
            except Exception:
                pass
            if status_message_id:
                try:
                    await self.bot.edit_message_text(
                        chat_id, status_message_id, "🏁 Queue finished. I left the voice chat."
                    )
                except Exception:
                    pass

    async def _handle_player_handoff(self, old_chat_id: int, new_chat_id: int):
        """Clear stale UI/state when every authorized voice account is already busy."""
        await self._stop_progress(old_chat_id)
        old_track = self.players.pop(old_chat_id, None)
        self.queues[old_chat_id].clear()
        self.playlist_sessions.pop(old_chat_id, None)
        if old_track:
            try:
                await self._edit_player_card(
                    old_chat_id, old_track["message_id"],
                    "🔀 <b>XYRON Music moved to another group's voice chat.</b> Playback stopped here.",
                    with_keyboard=False,
                )
            except Exception:
                pass
        try:
            await self.bot.send_message(
                old_chat_id,
                "🔀 XYRON Music moved to another group's voice chat. Use /play to start music here again.",
            )
        except Exception:
            log.debug("Could not notify previous music group %s", old_chat_id, exc_info=True)

    async def _on_stream_end(self, _, update: StreamEnded):
        chat_id = update.chat_id
        await self._stop_progress(chat_id)
        # The stream reader has reached EOF; let the local cache writer finish
        # and hand off the finished file to the independent channel uploader.
        self.players.pop(chat_id, None)
        await self._play_next(chat_id)

    async def _on_call_left(self, _, update: ChatUpdate):
        """Drop queued tracks when an administrator ends the group's voice chat."""
        chat_id = update.chat_id
        intentional_until = self._intentional_leave_until.get(chat_id, 0)
        if intentional_until >= time.monotonic():
            self._intentional_leave_until.pop(chat_id, None)
            return
        self._intentional_leave_until.pop(chat_id, None)
        await self.calls.release_slot(chat_id)
        await self._stop_progress(chat_id)
        track = self.players.pop(chat_id, None)
        await self._cancel_track_stream(track)
        self.queues[chat_id].clear()
        self.playlist_sessions.pop(chat_id, None)
        if track:
            try:
                await self._edit_player_card(
                    chat_id,
                    track["message_id"],
                    "⏹ <b>Voice chat ended. Playback and the old queue were cleared.</b>",
                    with_keyboard=False,
                )
            except Exception:
                log.debug("Could not update ended voice-chat player card", exc_info=True)

    @staticmethod
    def _preference_key(chat_id: int, query: str) -> str:
        return f"{int(chat_id)}:{SongsxSource._key(query)}"

    async def _remember_choice(self, chat_id: int, user, query_text: str, track: dict):
        """Remember one choice per user/group/search; update it instead of appending duplicates."""
        if not self.user_store or not user or not query_text:
            return
        try:
            record = await self.user_store.get_user(user.id)
            preferences = record.setdefault("preferences", {})
            key = self._preference_key(chat_id, query_text)
            title = str(track.get("name", "Unknown title"))[:120]
            artist = str(track.get("artist_name", "Unknown artist"))[:100]
            previous = preferences.get(key, {})
            if (
                previous.get("title") == title
                and previous.get("artist") == artist
                and int(previous.get("chosen_by", 0)) == int(user.id)
            ):
                return
            preferences[key] = {
                "chat_id": int(chat_id),
                "query": str(query_text)[:120],
                "title": title,
                "artist": artist,
                "duration": int(track.get("duration") or 0),
                "chosen_by": int(user.id),
                "chosen_by_name": str(user.full_name)[:100],
                "selected_at": datetime.now(timezone.utc).isoformat(),
            }
            while len(preferences) > 80:
                preferences.pop(next(iter(preferences)))
            await self.user_store.save_user(user.id, record)
        except Exception:
            log.exception("Could not save music choice in the cache channel for user %s", user.id)

    @staticmethod
    def _playlist_text(record: dict, page: int = 0) -> str:
        playlist = record.get("playlist", [])
        if not playlist:
            return (
                "🎼 <b>YOUR XYRON PLAYLIST</b>\n\n"
                "Your playlist is empty. Start a song in this group, open /myplaylist, "
                "then tap <b>ADD TO MY PLAYLIST</b> here."
            )
        page_size = 8
        page_count = max(1, (len(playlist) + page_size - 1) // page_size)
        page = max(0, min(int(page), page_count - 1))
        start = page * page_size
        lines = [
            f"<b>{start + index + 1}.</b> {html.escape(str(item.get('title', 'Unknown title')))}"
            f" — {html.escape(str(item.get('artist', 'Unknown artist')))}"
            for index, item in enumerate(playlist[start:start + page_size])
        ]
        return (
            "🎼 <b>YOUR XYRON PLAYLIST</b>\n"
            f"🎵 <b>{len(playlist)}</b> saved track(s)\n\n"
            + "\n".join(lines)
            + f"\n\n<code>Page {page + 1}/{page_count}</code>"
        )

    @staticmethod
    def _playlist_keyboard(record: dict, page: int = 0) -> dict:
        playlist = record.get("playlist", [])
        page_size = 8
        page_count = max(1, (len(playlist) + page_size - 1) // page_size)
        page = max(0, min(int(page), page_count - 1))
        rows = [
            [
                {"text": "▶  PLAY PLAYLIST", "callback_data": "music:playlist:play", "style": "success"},
                {"text": "■  STOP PLAYLIST", "callback_data": "music:playlist:stop", "style": "danger"},
            ],
            [{"text": "➕  ADD TO MY PLAYLIST", "callback_data": "music:playlist:add_current", "style": "success"}],
        ]
        for item in playlist[page * page_size:(page + 1) * page_size]:
            title = str(item.get("title", "Track"))[:32]
            artist = str(item.get("artist", ""))[:18]
            rows.append([{
                "text": f"✖  {title} — {artist}",
                "callback_data": f"music:playlist:delete:{item['item_id']}",
                "style": "danger",
            }])
        if page_count > 1:
            nav = []
            if page > 0:
                nav.append({"text": "⬅ PREVIOUS", "callback_data": f"music:playlist:page:{page - 1}", "style": "primary"})
            if page + 1 < page_count:
                nav.append({"text": "NEXT ➡", "callback_data": f"music:playlist:page:{page + 1}", "style": "primary"})
            rows.append(nav)
        return {"inline_keyboard": rows}

    async def _send_playlist_view(self, chat_id: int, record: dict, page: int = 0):
        return await self._api("sendMessage", {
            "chat_id": chat_id,
            "text": self._playlist_text(record, page),
            "parse_mode": "HTML",
            "reply_markup": self._playlist_keyboard(record, page),
            "disable_web_page_preview": True,
        })

    async def my_playlist_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._require_membership(update):
            return
        chat = update.effective_chat
        if chat.type == Chat.PRIVATE:
            return await update.effective_message.reply_text(
                "Open a group where XYRON Music is enabled and use /myplaylist there."
            )
        if not self.user_store:
            return await update.effective_message.reply_text(
                "Playlist storage is unavailable. The music cache channel is not connected."
            )
        try:
            record = await self.user_store.get_user(update.effective_user.id)
            await self._send_playlist_view(chat.id, record)
        except Exception as exc:
            log.exception("Could not open user playlist")
            await update.effective_message.reply_text(
                f"Could not load your playlist from the music cache channel: {type(exc).__name__}"
            )

    async def _start_user_playlist(self, chat_id: int, user, record: dict):
        playlist = list(record.get("playlist", []))
        if not playlist:
            raise RuntimeError("Your playlist is empty. Start a song, open /myplaylist, and tap ADD TO MY PLAYLIST.")
        if not self.accounts.clients:
            raise RuntimeError("No voice-player account is connected.")
        playable = []
        for item in playlist:
            title = str(item.get("title", "")).strip()
            artist = str(item.get("artist", "")).strip()
            if not title:
                continue
            results = await self.source.search(f"{title} {artist}".strip(), self.accounts.clients)
            expected_title = SongsxSource._key(title)
            expected_artist = SongsxSource._key(artist)
            track = next(
                (
                    candidate for candidate in results
                    if SongsxSource._key(candidate.get("name", "")) == expected_title
                    and SongsxSource._key(candidate.get("artist_name", "")) == expected_artist
                ),
                results[0] if results else None,
            )
            if not track:
                log.warning("Playlist track could not be resolved: %s — %s", title, artist)
                continue
            track["requested_by"] = user.full_name
            track["requested_by_username"] = f"@{user.username}" if user.username else ""
            track["_playlist_item_id"] = item["item_id"]
            playable.append(track)
        if not playable:
            raise RuntimeError("None of the saved tracks could be loaded from the music cache channel.")
        old_track = self.players.get(chat_id)
        await self._stop_progress(chat_id)
        # Clear old queued work before leaving the call so a stream-end event
        # cannot race in and restart a stale queue.
        self.queues[chat_id].clear()
        self.playlist_sessions.pop(chat_id, None)
        if old_track:
            try:
                await self._edit_player_card(
                    chat_id, old_track["message_id"],
                    "🔁 <b>Switching to a personal playlist…</b>", False,
                )
            except Exception:
                pass
        try:
            await self._leave_call(chat_id)
        except Exception:
            pass
        self.players.pop(chat_id, None)
        self.queues[chat_id].extend(playable)
        self.playlist_sessions[chat_id] = {
            "owner_id": int(user.id),
            "item_ids": {item.get("_playlist_item_id") for item in playable},
        }
        status = await self.bot.send_message(
            chat_id,
            f"🎼 Starting {html.escape(user.first_name)}'s playlist ({len(playable)} tracks)…",
            parse_mode="HTML",
        )
        await self._play_next(chat_id, status.message_id)

    async def _stop_user_playlist(self, chat_id: int, user_id: int):
        session = self.playlist_sessions.get(chat_id)
        if not session or int(session.get("owner_id", 0)) != int(user_id):
            raise PermissionError("You do not own the playlist currently playing in this group.")
        item_ids = set(session.get("item_ids", set()))
        self.queues[chat_id] = deque(
            item for item in self.queues[chat_id]
            if item.get("_playlist_item_id") not in item_ids
        )
        current = self.players.get(chat_id)
        current_is_playlist = bool(current and current.get("_playlist_item_id") in item_ids)
        self.playlist_sessions.pop(chat_id, None)
        if current_is_playlist:
            await self._stop_progress(chat_id)
            self.players.pop(chat_id, None)
            try:
                await self._leave_call(chat_id)
            except Exception:
                pass
            try:
                await self._edit_player_card(
                    chat_id, current["message_id"],
                    "⏹ <b>Your playlist has been stopped.</b>", False,
                )
            except Exception:
                pass
            if self.queues[chat_id]:
                await self._play_next(chat_id)

    async def _callback_notice(self, query, text: str) -> None:
        """Show follow-up feedback in chat after the callback has been acknowledged."""
        try:
            if query.message:
                await query.message.reply_text(text)
        except Exception:
            log.debug("Could not send callback feedback", exc_info=True)

    async def playlist_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user = query.from_user
        # Acknowledge before membership, cache-channel, or playlist I/O.
        await query.answer("Working…")
        if not await self._is_channel_member(user.id):
            await self._callback_notice(query, "Join @xyronschannel before using playlists.")
            return
        chat = query.message.chat
        if chat.type == Chat.PRIVATE:
            await self._callback_notice(query, "Use playlist controls in a group.")
            return
        if not self.user_store:
            await self._callback_notice(query, "Playlist storage is unavailable.")
            return
        parts = query.data.split(":")
        action = parts[2] if len(parts) > 2 else ""
        chat_id = chat.id
        try:
            record = await self.user_store.get_user(user.id)
            if action == "open":
                await self._send_playlist_view(chat_id, record)
                return
            if action == "add_current":
                current = self.players.get(chat_id)
                if not current:
                    await self._callback_notice(query, "Nothing is playing right now.")
                    return
                playlist = record.setdefault("playlist", [])
                title = str(current.get("title", "Unknown title"))
                artist = str(current.get("artist", "Unknown artist"))
                if any(
                    SongsxSource._key(item.get("title", "")) == SongsxSource._key(title)
                    and SongsxSource._key(item.get("artist", "")) == SongsxSource._key(artist)
                    for item in playlist
                ):
                    await self._callback_notice(query, "This song is already in your playlist.")
                    return
                if len(playlist) >= 100:
                    await self._callback_notice(query, "Your playlist has reached its 100-track limit.")
                    return
                playlist.append({
                    "item_id": secrets.token_hex(4),
                    "title": title[:120],
                    "artist": artist[:100],
                    "duration": int(current.get("duration") or 0),
                    "added_at": datetime.now(timezone.utc).isoformat(),
                })
                await self.user_store.save_user(user.id, record)
                await self._callback_notice(query, "Added to your personal playlist! 🎵")
                return
            if action == "delete" and len(parts) == 4:
                item_id = parts[3]
                before = len(record.get("playlist", []))
                record["playlist"] = [
                    item for item in record.get("playlist", [])
                    if item.get("item_id") != item_id
                ]
                if len(record["playlist"]) == before:
                    await self._callback_notice(query, "That track is no longer in your playlist.")
                    return
                await self.user_store.save_user(user.id, record)
                session = self.playlist_sessions.get(chat_id)
                current = self.players.get(chat_id)
                if (
                    session
                    and int(session.get("owner_id", 0)) == int(user.id)
                    and (not current or current.get("_playlist_item_id") != item_id)
                ):
                    session.get("item_ids", set()).discard(item_id)
                self.queues[chat_id] = deque(
                    item for item in self.queues[chat_id]
                    if item.get("_playlist_item_id") != item_id
                )
                await query.edit_message_text(
                    self._playlist_text(record),
                    parse_mode="HTML",
                    reply_markup=self._playlist_keyboard(record),
                    disable_web_page_preview=True,
                )
                return
            if action == "page" and len(parts) == 4:
                page = max(0, int(parts[3]))
                await query.edit_message_text(
                    self._playlist_text(record, page),
                    parse_mode="HTML",
                    reply_markup=self._playlist_keyboard(record, page),
                    disable_web_page_preview=True,
                )
                return
            if action == "play":
                await self._start_user_playlist(chat_id, user, record)
                return
            if action == "stop":
                await self._stop_user_playlist(chat_id, user.id)
                await self._callback_notice(query, "Your playlist has been stopped.")
                return
            await self._callback_notice(query, "Unknown playlist action.")
        except PermissionError as exc:
            await self._callback_notice(query, str(exc))
        except Exception as exc:
            log.exception("Playlist action failed")
            await self._callback_notice(query, f"Playlist action failed: {type(exc).__name__}")

    async def play_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.effective_message
        if not await self._require_membership(update):
            return
        query_text = " ".join(context.args or []).strip()
        if not query_text:
            return await message.reply_text("Usage: /play <song name>")
        if update.effective_chat.type == Chat.PRIVATE:
            return await message.reply_text("Add @xyronrobot to a group, start its voice chat, then use /play there.")
        chat_id = update.effective_chat.id
        user = update.effective_user
        status = await message.reply_text(
            f"🔎 Finding <b>{html.escape(query_text)}</b>…", parse_mode="HTML"
        )
        search_clients = await self.calls.claim_search_clients()
        selected = None
        if not search_clients:
            return await status.edit_text(
                "⏳ All music players are busy right now. Please try again shortly."
            )
        try:
            selected = await self.source.search_first(query_text, search_clients)
            if not selected:
                return await status.edit_text(
                    "🎵 I couldn't find that track. Try another song title or artist."
                )
            await self.source.resolve_selected(selected)
            selected["requested_by"] = user.full_name if user else "Telegram user"
            selected["requested_by_username"] = (
                f"@{user.username}" if user and user.username else ""
            )
            selected["_search_query"] = query_text

            def same_track(left, title, artist):
                left_title = left.get("title", left.get("name", ""))
                left_artist = left.get("artist", left.get("artist_name", ""))
                return (
                    SongsxSource._key(left_title) == SongsxSource._key(title)
                    and SongsxSource._key(left_artist) == SongsxSource._key(artist)
                )

            title = selected.get("name", "")
            artist = selected.get("artist_name", "")
            current = self.players.get(chat_id)
            starting = self.starting_tracks.get(chat_id)
            if (
                (current and same_track(current, title, artist))
                or (starting and same_track(starting, title, artist))
                or any(same_track(item, title, artist) for item in self.queues[chat_id])
            ):
                return await status.edit_text("🎵 That track is already playing or queued.")

            if user and self.user_store:
                asyncio.create_task(
                    self._remember_choice(chat_id, user, query_text, selected)
                )

            self.queues[chat_id].append(selected)
            if current or self.locks[chat_id].locked():
                await status.edit_text(
                    f"➕ Added <b>{html.escape(title)}</b> to the queue.",
                    parse_mode="HTML",
                )
                return
            await status.edit_text("▶️ Starting playback…")
            await self._play_next(chat_id, status.message_id)
        except Exception:
            log.exception("Music request could not be completed")
            try:
                await status.edit_text(
                    "⚠️ Couldn't load that track. Please try another search."
                )
            except Exception:
                pass
        finally:
            self.source.discard_selection(selected)
            await self.calls.release_search_clients(search_clients)



    async def choose_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user = query.from_user
        # Membership verification and preference persistence can take longer than
        # Telegram's callback acknowledgement window.
        await query.answer("Adding track…")
        if not await self._is_channel_member(user.id):
            await self._callback_notice(query, "Join @xyronschannel before selecting tracks.")
            try:
                await self._api("sendMessage", {
                    "chat_id": user.id,
                    "text": "Join @xyronschannel and confirm membership to use music.",
                    "reply_markup": self._subscription_keyboard(),
                })
            except Exception:
                pass
            return
        chat_id = query.message.chat_id
        token = query.data.rsplit(":", 1)[-1]
        track = self.search_cache.get(chat_id, {}).get(token)
        if not track:
            await self._callback_notice(query, "Search expired. Run /play again.")
            return
        track["requested_by"] = user.full_name
        track["requested_by_username"] = f"@{user.username}" if user.username else ""
        await self._remember_choice(chat_id, user, track.get("_search_query", ""), track)
        self.queues[chat_id].append(track)
        if chat_id not in self.players:
            await query.edit_message_text("⏳ Preparing your selected track…")
            await self._play_next(chat_id, query.message.message_id)
        else:
            await query.edit_message_text(
                f"➕ Added to queue: <b>{html.escape(track.get('name', 'Track'))}</b> "
                f"— {html.escape(track.get('artist_name', 'Unknown artist'))}",
                parse_mode="HTML",
            )

    async def player_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer("Processing…")
        if not await self._is_channel_member(query.from_user.id):
            await self._callback_notice(query, "Join @xyronschannel before using music controls.")
            return
        chat = query.message.chat
        chat_id = chat.id
        if not await self._can_control(chat, query.from_user):
            await self._callback_notice(
                query, "Only group admins or the bot owner can control playback."
            )
            return
        action = query.data.rsplit(":", 1)[-1]
        try:
            if action == "queue":
                upcoming = list(self.queues[chat_id])
                if not upcoming:
                    await self._callback_notice(query, "The queue is empty.")
                    return
                lines = [
                    f"{i}. {html.escape(str(item.get('name', 'Unknown')))} — "
                    f"{html.escape(str(item.get('artist_name', 'Unknown artist')))}"
                    for i, item in enumerate(upcoming[:15], 1)
                ]
                await self.bot.send_message(
                    chat_id, "📜 <b>XYRON MUSIC QUEUE</b>\n" + "\n".join(lines),
                    parse_mode="HTML",
                )
                return
            if action == "replay":
                track = self.players.get(chat_id)
                if not track:
                    await self._callback_notice(query, "Nothing is playing.")
                    return
                if not track.get("_stream_ready", True):
                    await self._callback_notice(query, "Track is still loading. Try again shortly.")
                    return
                await self._leave_call(chat_id)
                await self._ensure_player_in_group(chat_id)
                await self.calls.play(chat_id, track["path"], volume=track.get("volume", 100))
                track["started_at"] = time.monotonic()
                track["paused_elapsed"] = 0
                track["paused"] = False
            elif action == "pause":
                await self.calls.pause(chat_id)
                track = self.players.get(chat_id)
                if track:
                    track["paused_elapsed"] = int(time.monotonic() - track["started_at"])
                    track["paused"] = True
            elif action == "resume":
                await self.calls.resume(chat_id)
                track = self.players.get(chat_id)
                if track:
                    track["started_at"] = time.monotonic() - track.get("paused_elapsed", 0)
                    track["paused"] = False
            elif action == "skip":
                await self._leave_call(chat_id)
                await self._stop_progress(chat_id)
                old_track = self.players.pop(chat_id, None)
                await self._cancel_track_stream(old_track)
                await self._play_next(chat_id)
                return
            elif action == "stop":
                self.queues[chat_id].clear()
                self.playlist_sessions.pop(chat_id, None)
                await self._stop_progress(chat_id)
                old_track = self.players.pop(chat_id, None)
                await self._cancel_track_stream(old_track)
                await self._leave_call(chat_id)
                await self._edit_player_card(
                    chat_id, query.message.message_id,
                    "⏹ <b>Playback stopped.</b> Queue cleared.", False,
                )
                return
            elif action in {"vol_down", "vol_up"}:
                track = self.players.get(chat_id)
                if not track:
                    await self._callback_notice(query, "Nothing is playing.")
                    return
                if not track.get("_stream_ready", True):
                    await self._callback_notice(query, "Track is still loading. Try again shortly.")
                    return
                volume = max(
                    0, min(200, int(track.get("volume", 100))
                           + (-10 if action == "vol_down" else 10))
                )
                elapsed = (
                    int(track.get("paused_elapsed", 0)) if track.get("paused")
                    else max(0, int(time.monotonic() - track["started_at"]))
                )
                await self.calls.change_volume_call(chat_id, volume, track["path"], elapsed)
                if track.get("paused"):
                    await self.calls.pause(chat_id)
                else:
                    track["started_at"] = time.monotonic() - elapsed
                track["volume"] = volume
                await self._callback_notice(query, f"Volume: {volume}%")
            await self._edit_player_card(
                chat_id, query.message.message_id, self._render_player(chat_id)
            )
        except RuntimeError as exc:
            if "No voice-player session is assigned to this group" in str(exc):
                track = self.players.pop(chat_id, None)
                await self._stop_progress(chat_id)
                await self._cancel_track_stream(track)
                try:
                    await self._edit_player_card(
                        chat_id,
                        query.message.message_id,
                        "⏹ <b>Playback has already ended. Start a new track with /play.</b>",
                        with_keyboard=False,
                    )
                except Exception:
                    log.debug("Could not refresh stale music controls", exc_info=True)
                await self._callback_notice(query, "Playback has ended. Use /play to start again.")
                return
            log.exception("Music player control failed")
            await self._callback_notice(query, f"Action failed: {type(exc).__name__}")
        except Exception as exc:
            log.exception("Music player control failed")
            await self._callback_notice(query, f"Action failed: {type(exc).__name__}")

    async def _control_command(self, update, action):
        if await self._reject_control(update):
            return
        chat_id = update.effective_chat.id
        message = update.effective_message
        try:
            if action == "pause":
                await self.calls.pause(chat_id)
                track = self.players.get(chat_id)
                if track:
                    track["paused_elapsed"] = int(time.monotonic() - track["started_at"])
                    track["paused"] = True
                await message.reply_text("⏸ Playback paused.")
            elif action == "resume":
                await self.calls.resume(chat_id)
                track = self.players.get(chat_id)
                if track:
                    track["started_at"] = time.monotonic() - track.get("paused_elapsed", 0)
                    track["paused"] = False
                await message.reply_text("▶️ Playback resumed.")
            elif action == "skip":
                try:
                    await self._leave_call(chat_id)
                except Exception:
                    pass
                await self._stop_progress(chat_id)
                old_track = self.players.pop(chat_id, None)
                await self._cancel_track_stream(old_track)
                await self._play_next(chat_id)
                await message.reply_text("⏭ Skipped.")
            elif action == "stop":
                self.queues[chat_id].clear()
                self.playlist_sessions.pop(chat_id, None)
                await self._stop_progress(chat_id)
                track = self.players.pop(chat_id, None)
                await self._cancel_track_stream(track)
                try:
                    await self._leave_call(chat_id)
                except Exception:
                    pass
                if track:
                    try:
                        await self._edit_player_card(chat_id, track["message_id"], "⏹ <b>Playback stopped.</b> Queue cleared.", False)
                    except Exception:
                        pass
                await message.reply_text("⏹ Stopped playback and cleared the queue.")
            elif action == "queue":
                upcoming = list(self.queues[chat_id])
                if not upcoming:
                    return await message.reply_text("📭 The queue is empty.")
                lines = [
                    f"{i}. {html.escape(str(x.get('name')))} — {html.escape(str(x.get('artist_name')))}"
                    for i, x in enumerate(upcoming[:15], 1)
                ]
                await message.reply_text("📜 <b>UP NEXT</b>\n" + "\n".join(lines), parse_mode="HTML")
            elif action == "np":
                track = self.players.get(chat_id)
                if not track:
                    return await message.reply_text("🎵 Nothing is playing.")
                cover = make_player_cover(
                    title=track.get("title", "Unknown title"),
                    artist=track.get("artist", "Unknown artist"),
                    requested_by=track.get("requested_by", "A group member"),
                    bot_username=self.public_username,
                )
                await self._send_player_card(chat_id, cover, self._render_player(chat_id))
        except Exception as exc:
            log.exception("Music command %s failed", action)
            await message.reply_text(f"❌ {action.title()} failed: {type(exc).__name__}")

    async def pause_command(self, update, context):
        await self._control_command(update, "pause")

    async def resume_command(self, update, context):
        await self._control_command(update, "resume")

    async def skip_command(self, update, context):
        await self._control_command(update, "skip")

    async def stop_command(self, update, context):
        await self._control_command(update, "stop")

    async def queue_command(self, update, context):
        if not await self._require_membership(update):
            return
        chat_id = update.effective_chat.id
        upcoming = list(self.queues[chat_id])
        if not upcoming:
            return await update.effective_message.reply_text("📭 The queue is empty.")
        lines = [
            f"{i}. {html.escape(str(x.get('name')))} — {html.escape(str(x.get('artist_name')))}"
            for i, x in enumerate(upcoming[:15], 1)
        ]
        await update.effective_message.reply_text(
            "📜 <b>UP NEXT</b>\\n" + "\\n".join(lines), parse_mode="HTML"
        )

    async def np_command(self, update, context):
        if not await self._require_membership(update):
            return
        track = self.players.get(update.effective_chat.id)
        if not track:
            return await update.effective_message.reply_text("🎵 Nothing is playing.")
        cover = make_player_cover(
            title=track.get("title", "Unknown title"),
            artist=track.get("artist", "Unknown artist"),
            requested_by=track.get("requested_by", "A group member"),
            bot_username=self.public_username,
        )
        await self._send_player_card(
            update.effective_chat.id, cover, self._render_player(update.effective_chat.id)
        )

    async def volume_command(self, update, context):
        if await self._reject_control(update):
            return
        message = update.effective_message
        if not context.args or not context.args[0].isdigit():
            return await message.reply_text("Usage: /volume <0-200>")
        chat_id = update.effective_chat.id
        if chat_id not in self.players:
            return await message.reply_text("Nothing is playing right now.")
        if not self.players[chat_id].get("_stream_ready", True):
            return await message.reply_text("Track is still loading. Try again shortly.")
        volume = max(0, min(200, int(context.args[0])))
        try:
            track = self.players[chat_id]
            elapsed = (
                int(track.get("paused_elapsed", 0)) if track.get("paused")
                else max(0, int(time.monotonic() - track["started_at"]))
            )
            await self.calls.change_volume_call(
                chat_id, volume, track["path"], elapsed
            )
            if track.get("paused"):
                await self.calls.pause(chat_id)
            else:
                track["started_at"] = time.monotonic() - elapsed
            track["volume"] = volume
            await message.reply_text(f"🔊 Volume set to {volume}%.")
        except Exception as exc:
            log.exception("Music volume change failed")
            await message.reply_text(f"❌ Could not change volume: {type(exc).__name__}")

    async def music_help_command(self, update, context):
        await update.effective_message.reply_text(
            "🎧 <b>XYRON MUSIC</b>\n\n"
            "• /play &lt;song name&gt; — play a song\n"
            "• /myplaylist — open, play, or manage your personal playlist\n"
            "• /pause — pause playback\n• /resume — resume playback\n"
            "• /skip — skip current track\n• /stop — stop and clear queue\n"
            "• /queue — show upcoming tracks\n• /np — show player card\n"
            "• /volume &lt;0-200&gt; — set volume\n"
            "• /addaccount — connect a voice-player account (owner only)\n"
            "• /cancelaccount — cancel QR account linking (owner only)\n"
            "• /accounts — list connected accounts (owner only)\n\n"
            "Start the group's voice chat before using /play. The bot needs permission to invite its voice-player account.",
            parse_mode="HTML",
        )

    async def subscription_callback(self, update, context):
        query = update.callback_query
        await query.answer("Checking membership…")
        if await self._is_channel_member(query.from_user.id):
            try:
                await query.message.edit_text(
                    "✅ You're verified!\n\nAdd @xyronrobot to your group and use /play song name."
                )
            except Exception:
                pass
        else:
            try:
                await query.message.edit_text(
                    "❌ Membership not found. Join @xyronschannel, then tap Confirm Membership again."
                )
            except Exception:
                pass

    async def add_account_command(self, update, context):
        if update.effective_chat.type != Chat.PRIVATE:
            return await update.effective_message.reply_text("For account security, use /addaccount in XYRON's private chat.")
        await self.accounts.handle_add_account(update, context)

    async def cancel_account_command(self, update, context):
        if update.effective_chat.type != Chat.PRIVATE:
            return await update.effective_message.reply_text(
                "For account security, use /cancelaccount in XYRON's private chat."
            )
        await self.accounts.handle_cancel_account(update, context)

    async def myid_command(self, update, context):
        user = update.effective_user
        if user:
            await update.effective_message.reply_text(f"Your Telegram user ID: <code>{user.id}</code>", parse_mode="HTML")

    async def accounts_command(self, update, context):
        if update.effective_chat.type != Chat.PRIVATE:
            return await update.effective_message.reply_text("Use /accounts in XYRON's private chat.")
        await self.accounts.handle_accounts(update, context)


def register_music(application: Application):
    settings = application.bot_data["settings"]
    if not getattr(settings, "music_enabled", False):
        log.info("Music plugin is disabled; set MUSIC_ENABLED=true and configure MUSIC_API_ID/MUSIC_API_HASH to enable it")
        return
    plugin = MusicPlugin(application, settings)
    application.bot_data["music_plugin"] = plugin
    commands = {
        "play": plugin.play_command,
        "myplaylist": plugin.my_playlist_command,
        "pause": plugin.pause_command,
        "resume": plugin.resume_command,
        "skip": plugin.skip_command,
        "queue": plugin.queue_command,
        "np": plugin.np_command,
        "volume": plugin.volume_command,
        "musichelp": plugin.music_help_command,
        "addaccount": plugin.add_account_command,
        "newaccount": plugin.add_account_command,
        "cancelaccount": plugin.cancel_account_command,
        "accounts": plugin.accounts_command,
        "myid": plugin.myid_command,
    }
    for name, callback in commands.items():
        application.add_handler(CommandHandler(name, callback))
    application.add_handler(
        CallbackQueryHandler(plugin.choose_callback, pattern=r"^music:choose:[A-Za-z0-9_-]+$")
    )
    application.add_handler(
        CallbackQueryHandler(plugin.player_callback, pattern=r"^music:player:(?:replay|pause|resume|skip|stop|queue|vol_down|vol_up)$")
    )
    application.add_handler(
        CallbackQueryHandler(plugin.playlist_callback, pattern=r"^music:playlist:(?:open|add_current|play|stop|delete:[A-Za-z0-9]+|page:\d+)$")
    )
    application.add_handler(
        CallbackQueryHandler(plugin.subscription_callback, pattern=r"^music:subscription$")
    )