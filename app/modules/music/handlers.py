"""XYRON Music plugin: Songsx search, styled player UI, and PyTgCalls playback."""

from __future__ import annotations

import asyncio
import html
import logging
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
from pytgcalls.types import StreamEnded

from app.modules.music.account_manager import AccountManager
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
        self.progress_tasks = {}
        self.locks = defaultdict(asyncio.Lock)
        self.search_cache = defaultdict(dict)
        self.http: aiohttp.ClientSession | None = None
        self._started = False
        self.calls.on_update(call_filters.stream_end())(self._on_stream_end)

    async def start(self):
        if self._started:
            return
        self.http = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=45),
            connector=aiohttp.TCPConnector(limit=30, ttl_dns_cache=300, keepalive_timeout=30),
        )
        try:
            count = await self.accounts.load_existing()
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

    async def _ensure_player_in_group(self, chat_id: int):
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
                chat_id, invite.invite_link, preferred_client=self.accounts.clients[0]
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
        remaining = max(0, duration - elapsed) if duration else 0
        esc = lambda value: html.escape(str(value or ""))
        title = esc(track.get("title", "Unknown title"))
        artist = esc(track.get("artist", "Unknown artist"))
        requester = esc(track.get("requested_by", "A group member"))
        username = self.public_username or "xyronrobot"
        if duration:
            timeline = (
                f"<code>{self._fmt_time(elapsed)}</code>  "
                f"{self._progress_bar(elapsed, duration)}  "
                f"<code>{self._fmt_time(duration)}</code>"
            )
            remaining_line = f"⏳ <b>{self._fmt_time(remaining)}</b> left"
        else:
            timeline = f"<code>{self._fmt_time(elapsed)}</code>  🔴 LIVE"
            remaining_line = "⏳ Live stream"
        status = "⏸ <b>PAUSED</b>" if track.get("paused") else "🟢 <b>NOW PLAYING</b>"
        requested_username = esc(track.get("requested_by_username", ""))
        return (
            f"🎧 <b>XYRON MUSIC</b>  ·  {status}\n"
            f"🎵 <b>{title}</b>\n"
            f"🎤 {artist}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"{timeline}\n{remaining_line}  ·  🔊 {track.get('volume', 100)}%\n"
            f"👤 <b>Requested by:</b> {requester} {requested_username}\n"
            f"✨ <a href=\"https://t.me/{username}\">@{username}</a>"
        )

    def _player_keyboard(self, chat_id):
        paused = bool(self.players.get(chat_id, {}).get("paused"))
        return {"inline_keyboard": [
            [
                {"text": "↻  REPLAY", "callback_data": "music:player:replay", "style": "success"},
                {"text": "▶  RESUME" if paused else "Ⅱ  PAUSE",
                 "callback_data": "music:player:resume" if paused else "music:player:pause",
                 "style": "success" if paused else "primary"},
            ],
            [
                {"text": "⏭  NEXT", "callback_data": "music:player:skip", "style": "primary"},
                {"text": "☷  QUEUE", "callback_data": "music:player:queue", "style": "primary"},
            ],
            [
                {"text": "－  VOLUME", "callback_data": "music:player:vol_down", "style": "primary"},
                {"text": "＋  VOLUME", "callback_data": "music:player:vol_up", "style": "primary"},
            ],
            [{"text": "■  STOP PLAYBACK", "callback_data": "music:player:stop", "style": "danger"}],
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
            await asyncio.sleep(3)

    async def _start_track(self, chat_id, track, status_message_id):
        audio_path = await self.source.download_selected(track)
        await self._ensure_player_in_group(chat_id)
        await self.calls.play(chat_id, str(audio_path))
        self.players[chat_id] = {
            "id": str(track["id"]),
            "title": track.get("name", "Unknown title"),
            "artist": track.get("artist_name", "Unknown artist"),
            "requested_by": track.get("requested_by", "A group member"),
            "requested_by_username": track.get("requested_by_username", ""),
            "duration": int(track.get("duration") or 0),
            "started_at": time.monotonic(),
            "message_id": status_message_id,
            "path": str(audio_path),
            "volume": 100,
            "paused": False,
            "paused_elapsed": 0,
        }
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
                try:
                    await self._start_track(chat_id, track, message_id)
                    return
                except Exception as exc:
                    log.exception("Could not start queued music track")
                    detail = str(exc)
                    if "no active group call" in detail.lower():
                        detail = "No active voice chat. Start the group's voice chat first."
                    elif "invite" in detail.lower() or "chat_admin_required" in detail.lower():
                        detail = f"Please make @xyronrobot an admin with Invite Users permission ({type(exc).__name__})."
                    await self.bot.send_message(
                        chat_id,
                        f"⚠️ Could not play <b>{html.escape(str(track.get('name', 'track')))}</b>: "
                        f"{html.escape(detail[:700])}",
                        parse_mode="HTML",
                    )
                    status_message_id = None
            await self._stop_progress(chat_id)
            self.players.pop(chat_id, None)
            try:
                await self.calls.leave_call(chat_id)
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
        self.players.pop(chat_id, None)
        await self._play_next(chat_id)

    async def play_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.effective_message
        if not await self._require_membership(update):
            return
        query = " ".join(context.args or []).strip()
        if not query:
            return await message.reply_text("Usage: /play <song name>")
        if update.effective_chat.type == Chat.PRIVATE:
            return await message.reply_text("Add @xyronrobot to a group, start its voice chat, then use /play there.")
        status = await message.reply_text(
            f"🔎 Finding tracks for <b>{html.escape(query)}</b>…", parse_mode="HTML"
        )
        try:
            results = (await self.source.search(query, self.accounts.clients))[:7]
            if not results:
                return await status.edit_text("Songsx returned no tracks. Try another title or artist.")
            user = update.effective_user
            requester = " ".join(x for x in (user.first_name, user.last_name) if x) if user else "Telegram user"
            username = f"@{user.username}" if user and user.username else ""
            for item in results:
                item["requested_by"] = requester
                item["requested_by_username"] = username
            if len(results) > 1:
                self.search_cache[update.effective_chat.id] = {
                    str(item["id"]): item for item in results
                }
                await self._edit_song_choices(update.effective_chat.id, status.message_id, results)
                return
            selected = results[0]
            self.queues[update.effective_chat.id].append(selected)
            if update.effective_chat.id not in self.players:
                await status.edit_text("⏳ Preparing your selected track and voice chat…")
                await self._play_next(update.effective_chat.id, status.message_id)
            else:
                await status.edit_text(
                    f"➕ Added to queue: <b>{html.escape(selected.get('name', 'Track'))}</b> "
                    f"— {html.escape(selected.get('artist_name', 'Unknown artist'))}",
                    parse_mode="HTML",
                )
        except Exception as exc:
            log.exception("Songsx search/play failed")
            try:
                await status.edit_text(
                    f"❌ Couldn't search Songsx or play that track: {type(exc).__name__}: {html.escape(str(exc)[:500])}",
                    parse_mode="HTML",
                )
            except Exception:
                pass

    async def choose_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if not await self._is_channel_member(query.from_user.id):
            await query.answer("Join @xyronschannel before selecting tracks.", show_alert=True)
            try:
                await self._api("sendMessage", {
                    "chat_id": query.from_user.id,
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
            return await query.answer("Search expired. Run /play again.", show_alert=True)
        await query.answer("Added to queue")
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
        if not await self._is_channel_member(query.from_user.id):
            return await query.answer("Join @xyronschannel before using music controls.", show_alert=True)
        chat = query.message.chat
        chat_id = chat.id
        if not await self._can_control(chat, query.from_user):
            return await query.answer("Only group admins or the bot owner can control playback.", show_alert=True)
        action = query.data.rsplit(":", 1)[-1]
        try:
            if action == "queue":
                upcoming = list(self.queues[chat_id])
                if not upcoming:
                    return await query.answer("The queue is empty.", show_alert=True)
                await query.answer()
                lines = [
                    f"{i}. {html.escape(str(item.get('name', 'Unknown')))} — "
                    f"{html.escape(str(item.get('artist_name', 'Unknown artist')))}"
                    for i, item in enumerate(upcoming[:15], 1)
                ]
                await self.bot.send_message(chat_id, "📜 <b>XYRON MUSIC QUEUE</b>\n" + "\n".join(lines), parse_mode="HTML")
                return
            if action == "replay":
                track = self.players.get(chat_id)
                if not track:
                    return await query.answer("Nothing is playing.", show_alert=True)
                await query.answer()
                await self.calls.leave_call(chat_id)
                await self._ensure_player_in_group(chat_id)
                await self.calls.play(chat_id, track["path"])
                track["started_at"] = time.monotonic()
                track["paused_elapsed"] = 0
                track["paused"] = False
            elif action == "pause":
                await query.answer()
                await self.calls.pause(chat_id)
                track = self.players.get(chat_id)
                if track:
                    track["paused_elapsed"] = int(time.monotonic() - track["started_at"])
                    track["paused"] = True
            elif action == "resume":
                await query.answer()
                await self.calls.resume(chat_id)
                track = self.players.get(chat_id)
                if track:
                    track["started_at"] = time.monotonic() - track.get("paused_elapsed", 0)
                    track["paused"] = False
            elif action == "skip":
                await query.answer("Skipping…")
                try:
                    await self.calls.leave_call(chat_id)
                except Exception:
                    pass
                await self._stop_progress(chat_id)
                self.players.pop(chat_id, None)
                await self._play_next(chat_id)
                return
            elif action == "stop":
                await query.answer("Stopping…")
                self.queues[chat_id].clear()
                await self._stop_progress(chat_id)
                self.players.pop(chat_id, None)
                try:
                    await self.calls.leave_call(chat_id)
                except Exception:
                    pass
                await self._edit_player_card(chat_id, query.message.message_id, "⏹ <b>Playback stopped.</b> Queue cleared.", False)
                return
            elif action in {"vol_down", "vol_up"}:
                track = self.players.get(chat_id)
                if not track:
                    return await query.answer("Nothing is playing.", show_alert=True)
                await query.answer()
                volume = max(1, min(200, int(track.get("volume", 100)) + (-10 if action == "vol_down" else 10)))
                await self.calls.change_volume_call(chat_id, volume)
                track["volume"] = volume
            await self._edit_player_card(chat_id, query.message.message_id, self._render_player(chat_id))
        except Exception as exc:
            log.exception("Music player control failed")
            try:
                await query.answer(f"Action failed: {type(exc).__name__}", show_alert=True)
            except Exception:
                pass

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
                    await self.calls.leave_call(chat_id)
                except Exception:
                    pass
                await self._stop_progress(chat_id)
                self.players.pop(chat_id, None)
                await self._play_next(chat_id)
                await message.reply_text("⏭ Skipped.")
            elif action == "stop":
                self.queues[chat_id].clear()
                await self._stop_progress(chat_id)
                track = self.players.pop(chat_id, None)
                try:
                    await self.calls.leave_call(chat_id)
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
            return await message.reply_text("Usage: /volume <1-200>")
        chat_id = update.effective_chat.id
        if chat_id not in self.players:
            return await message.reply_text("Nothing is playing right now.")
        volume = max(1, min(200, int(context.args[0])))
        try:
            await self.calls.change_volume_call(chat_id, volume)
            self.players[chat_id]["volume"] = volume
            await message.reply_text(f"🔊 Volume set to {volume}%.")
        except Exception as exc:
            log.exception("Music volume change failed")
            await message.reply_text(f"❌ Could not change volume: {type(exc).__name__}")

    async def music_help_command(self, update, context):
        await update.effective_message.reply_text(
            "🎧 <b>XYRON MUSIC</b>\n\n"
            "• /play &lt;song name&gt; — search Songsx and play\n"
            "• /pause — pause playback\n• /resume — resume playback\n"
            "• /skip — skip current track\n• /stop — stop and clear queue\n"
            "• /queue — show upcoming tracks\n• /np — show player card\n"
            "• /volume &lt;1-200&gt; — set volume\n"
            "• /addaccount — connect a voice-player account (owner only)\n"
            "• /cancelaccount — cancel QR account linking (owner only)\n"
            "• /accounts — list connected accounts (owner only)\n\n"
            "Start the group's voice chat before using /play. The bot needs permission to invite its voice-player account.",
            parse_mode="HTML",
        )

    async def subscription_callback(self, update, context):
        query = update.callback_query
        if await self._is_channel_member(query.from_user.id):
            await query.answer("Membership confirmed!", show_alert=True)
            try:
                await query.message.edit_text(
                    "✅ You're verified!\n\nAdd @xyronrobot to your group and use /play song name."
                )
            except Exception:
                pass
        else:
            await query.answer("Join @xyronschannel first, then try again.", show_alert=True)

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
        CallbackQueryHandler(plugin.subscription_callback, pattern=r"^music:subscription$")
    )
