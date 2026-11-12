"""Multi-account PyTgCalls pool; one Telethon session per active voice chat."""

from __future__ import annotations

import asyncio

from telethon.errors import UserAlreadyParticipantError
from telethon.tl.functions.messages import ImportChatInviteRequest
from pytgcalls import PyTgCalls
from pytgcalls.types import AudioQuality, MediaStream


class MultiCallPool:
    def __init__(self, clients=None):
        self.clients = list(clients or [])
        self.instances = [PyTgCalls(client) for client in self.clients]
        self.chat_to_slot = {}
        self.slot_to_chat = {}
        self._allocation_lock = asyncio.Lock()
        self._client_lock = asyncio.Lock()
        self._update_handlers = []
        self._started = False
        self._handoff_handler = None
        self._searching_clients = set()

    def set_handoff_handler(self, handler):
        self._handoff_handler = handler

    async def claim_search_clients(self):
        """Reserve every currently free account for one parallel search request."""
        async with self._allocation_lock:
            clients = [
                client for index, client in enumerate(self.clients)
                if index not in self.slot_to_chat and client not in self._searching_clients
            ]
            self._searching_clients.update(clients)
            return clients

    async def release_search_clients(self, clients):
        async with self._allocation_lock:
            for client in clients or ():
                self._searching_clients.discard(client)

    def is_client_busy(self, client):
        try:
            return self.clients.index(client) in self.slot_to_chat
        except ValueError:
            return True

    def _register_update_handler(self, instance, update_filter, handler):
        async def wrapped(_, update, _handler=handler):
            await _handler(self, update)
        instance.on_update(update_filter)(wrapped)

    def on_update(self, update_filter):
        def decorator(handler):
            self._update_handlers.append((update_filter, handler))
            for instance in self.instances:
                self._register_update_handler(instance, update_filter, handler)
            return handler
        return decorator

    async def add_client(self, client):
        async with self._client_lock:
            if client in self.clients:
                return self.instances[self.clients.index(client)]
            instance = PyTgCalls(client)
            for update_filter, handler in self._update_handlers:
                self._register_update_handler(instance, update_filter, handler)
            if self._started:
                await instance.start()
            self.clients.append(client)
            self.instances.append(instance)
            return instance

    async def remove_client(self, client):
        """Remove a just-added account safely if its persistent save fails."""
        async with self._client_lock:
            if client not in self.clients:
                return
            index = self.clients.index(client)
            chat_id = self.slot_to_chat.pop(index, None)
            if chat_id is not None:
                self.chat_to_slot.pop(chat_id, None)
                try:
                    await self.instances[index].leave_call(chat_id)
                except Exception:
                    pass
            instance = self.instances.pop(index)
            self.clients.pop(index)
            self._searching_clients.discard(client)
            if self._started:
                try:
                    await instance.stop()
                except Exception:
                    pass
            self.chat_to_slot = {
                chat: slot - 1 if slot > index else slot
                for chat, slot in self.chat_to_slot.items()
            }
            self.slot_to_chat = {
                slot - 1 if slot > index else slot: chat
                for slot, chat in self.slot_to_chat.items()
            }

    async def start(self):
        started = []
        try:
            for instance in self.instances:
                await instance.start()
                started.append(instance)
            self._started = True
        except Exception:
            for instance in reversed(started):
                try:
                    await instance.stop()
                except Exception:
                    pass
            raise

    async def stop(self):
        for instance in reversed(self.instances):
            try:
                await instance.stop()
            except Exception:
                pass
        self._started = False
        self.chat_to_slot.clear()
        self.slot_to_chat.clear()
        self._searching_clients.clear()

    async def _instance_for(self, chat_id, allocate=False, preferred_client=None):
        slot = self.chat_to_slot.get(chat_id)
        if slot is not None:
            return self.instances[slot]
        if not allocate:
            raise RuntimeError("No voice-player session is assigned to this group.")
        async with self._allocation_lock:
            slot = self.chat_to_slot.get(chat_id)
            if slot is not None:
                return self.instances[slot]
            if preferred_client in self.clients:
                preferred = self.clients.index(preferred_client)
                if preferred not in self.slot_to_chat:
                    self.chat_to_slot[chat_id] = preferred
                    self.slot_to_chat[preferred] = chat_id
                    self._searching_clients.discard(preferred_client)
                    return self.instances[preferred]
            for index, instance in enumerate(self.instances):
                client = self.clients[index]
                if index not in self.slot_to_chat and client not in self._searching_clients:
                    self.chat_to_slot[chat_id] = index
                    self.slot_to_chat[index] = chat_id
                    return instance
        raise RuntimeError("All voice-player accounts are currently busy. Please try again shortly.")

    async def ensure_member(self, chat_id, invite_link, preferred_client=None):
        await self._instance_for(chat_id, allocate=True, preferred_client=preferred_client)
        client = self.clients[self.chat_to_slot[chat_id]]
        invite_hash = invite_link.rstrip("/").split("/")[-1].split("?")[0].lstrip("+")
        if not invite_hash or invite_hash.lower() == "joinchat":
            raise RuntimeError("Telegram returned an invalid group invite link.")
        me = await client.get_me()
        try:
            await client(ImportChatInviteRequest(invite_hash))
        except UserAlreadyParticipantError:
            pass
        await client.get_entity(chat_id)
        return me

    @staticmethod
    def _build_media_stream(source, volume=100, start_at=0):
        """Build an audio-only stream with local FFmpeg gain (no Telegram admin rights)."""
        volume = max(0, min(200, int(volume)))
        start_at = max(0.0, float(start_at or 0))
        gain = volume / 100
        ffmpeg_parameters = (
            f"--audio -instart -ss {start_at:.3f} "
            f"-inmid -af volume={gain:.2f}"
        )
        return MediaStream(
            source,
            AudioQuality.STUDIO,
            audio_flags=MediaStream.Flags.REQUIRED,
            video_flags=MediaStream.Flags.IGNORE,
            ffmpeg_parameters=ffmpeg_parameters,
        )

    async def play(self, chat_id, source, volume=100, start_at=0):
        instance = await self._instance_for(chat_id, allocate=True)
        try:
            await instance.play(
                chat_id, self._build_media_stream(source, volume, start_at)
            )
        except Exception:
            await self.leave_call(chat_id)
            raise

    async def pause(self, chat_id):
        await (await self._instance_for(chat_id)).pause(chat_id)

    async def resume(self, chat_id):
        await (await self._instance_for(chat_id)).resume(chat_id)

    async def change_volume_call(self, chat_id, volume, source, position=0):
        """Change playback gain locally and seek back to the current position.

        PyTgCalls' Telegram participant-volume API requires call-management
        privileges. Replacing the FFmpeg-backed stream avoids that permission
        requirement and changes only XYRON's outgoing audio.
        """
        await self.play(chat_id, source, volume=volume, start_at=position)

    async def leave_call(self, chat_id):
        slot = self.chat_to_slot.get(chat_id)
        if slot is None:
            return
        try:
            await self.instances[slot].leave_call(chat_id)
        finally:
            self.chat_to_slot.pop(chat_id, None)
            self.slot_to_chat.pop(slot, None)
