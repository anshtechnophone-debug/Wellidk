"""Owner-only Telethon QR account manager with backwards-compatible session migration."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
from pathlib import Path

import qrcode
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

log = logging.getLogger("xyron.music.accounts")


class AccountManager:
    def __init__(self, bot, call_pool, api_id, api_hash, owner_id, data_dir, legacy_store=None):
        self.bot = bot
        self.call_pool = call_pool
        self.api_id = int(api_id)
        self.api_hash = api_hash
        self.owner_id = int(owner_id or 0)
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.data_dir.chmod(0o700)
        except OSError:
            log.warning("Could not restrict music data directory permissions")
        self.store_path = self.data_dir / "player_accounts.json"
        self.legacy_store = Path(legacy_store) if legacy_store else None
        self.clients = []
        self.accounts = {}
        self._stored_accounts = {}
        self._login_lock = asyncio.Lock()

    def _read_store(self):
        """Merge new and legacy stores without discarding any saved account."""
        merged = {}
        candidates = [self.store_path]
        if self.legacy_store and self.legacy_store != self.store_path:
            candidates.append(self.legacy_store)
        for candidate in candidates:
            if not candidate.is_file():
                continue
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("Player-account store must contain a JSON object")
                # Prefer the new store if the same account exists in both places.
                for account_id, record in payload.items():
                    merged.setdefault(str(account_id), record)
                if candidate != self.store_path:
                    log.info("Found legacy player-account store at %s; preserving its entries", candidate)
            except (OSError, json.JSONDecodeError, ValueError):
                log.exception("Could not read player-account store: %s", candidate)
                raise RuntimeError(f"Player-account store is unreadable: {candidate}")
        return merged

    def _write_store(self):
        temporary = self.store_path.with_suffix(".tmp")
        self._stored_accounts.update(self.accounts)
        payload = json.dumps(self._stored_accounts, indent=2, sort_keys=True)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.store_path)
            self.store_path.chmod(0o600)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            temporary.unlink(missing_ok=True)
            raise

    def _is_owner(self, update):
        user = update.effective_user
        return bool(self.owner_id and user and user.id == self.owner_id)

    async def load_existing(self):
        stored = self._read_store()
        self._stored_accounts = dict(stored)
        failed = []
        for account_id, record in stored.items():
            try:
                session = record["session"]
                if not isinstance(session, str) or not session:
                    raise ValueError("Missing player session")
                client = TelegramClient(StringSession(session), self.api_id, self.api_hash)
                await client.start()
                await self.call_pool.add_client(client)
                self.clients.append(client)
                self.accounts[str(account_id)] = record
                log.info("Loaded saved music player account %s", account_id)
            except Exception:
                failed.append(str(account_id))
                log.exception("Could not load saved music player account %s", account_id)
        if self.accounts and not failed and self.legacy_store and self.legacy_store.is_file() and not self.store_path.exists():
            self._write_store()
            log.info("Migrated %s existing player account(s) without deleting the legacy file", len(self.accounts))
        if failed:
            log.error("Failed to load %s saved player account(s); source store was preserved", len(failed))
        return len(self.clients)

    async def handle_add_account(self, update, context):
        if not self.owner_id:
            return await update.effective_message.reply_text("Music owner ID is not configured. Ask the bot owner to set MUSIC_OWNER_ID.")
        if not self._is_owner(update):
            return await update.effective_message.reply_text("⛔ This command is available only to the configured bot owner.")
        if len(self.call_pool.clients) >= 10:
            return await update.effective_message.reply_text("The total voice-player account limit is 10.")
        if self._login_lock.locked():
            return await update.effective_message.reply_text("An account connection is already in progress.")
        async with self._login_lock:
            await self._run_qr_login(update, context)

    async def _run_qr_login(self, update, context):
        message = update.effective_message
        client = TelegramClient(StringSession(), self.api_id, self.api_hash)
        status = await message.reply_text(
            "🔐 <b>Connect a voice-player account</b>\n\n"
            "Scan the temporary QR code using Telegram → Settings → Devices → Link Desktop Device.\n\n"
            "<b>Never forward the QR code.</b> It grants access to your Telegram account.",
            parse_mode="HTML",
        )
        qr_message_ids = []
        try:
            await client.connect()
            qr = await client.qr_login()
            deadline = asyncio.get_running_loop().time() + 150
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                image = qrcode.make(qr.url)
                buffer = io.BytesIO()
                buffer.name = "xyron-music-login.png"
                image.save(buffer, format="PNG")
                buffer.seek(0)
                sent = await context.bot.send_photo(
                    chat_id=message.chat_id,
                    photo=buffer,
                    caption="Scan from Telegram → Settings → Devices → Link Desktop Device. A refreshed QR will be sent if this one expires.",
                )
                qr_message_ids.append(sent.message_id)
                try:
                    await qr.wait(timeout=min(25, remaining))
                    break
                except asyncio.TimeoutError:
                    try:
                        await context.bot.delete_messages(message.chat_id, qr_message_ids)
                    except Exception:
                        log.debug("Could not delete expired QR messages", exc_info=True)
                    qr_message_ids.clear()
                    await qr.recreate()
            me = await client.get_me()
            account_id = str(me.id)
            if account_id in self.accounts:
                await status.edit_text("That Telegram account is already connected.")
                return
            session_string = StringSession.save(client.session)
            record = {
                "username": me.username or "",
                "first_name": me.first_name or "",
                "session": session_string,
            }
            await client.disconnect()
            player = TelegramClient(StringSession(session_string), self.api_id, self.api_hash)
            await player.start()
            try:
                await self.call_pool.add_client(player)
            except Exception:
                await player.disconnect()
                raise
            previous_stored_record = self._stored_accounts.get(account_id)
            self.accounts[account_id] = record
            self._stored_accounts[account_id] = record
            self.clients.append(player)
            try:
                self._write_store()
            except Exception:
                self.accounts.pop(account_id, None)
                if previous_stored_record is None:
                    self._stored_accounts.pop(account_id, None)
                else:
                    self._stored_accounts[account_id] = previous_stored_record
                self.clients.remove(player)
                await self.call_pool.remove_client(player)
                await player.disconnect()
                raise
            display = f"@{me.username}" if me.username else (me.first_name or account_id)
            await status.edit_text(
                f"✅ <b>Voice-player account connected</b>\n\n"
                f"Account: {display}\nTelegram ID: <code>{account_id}</code>\n"
                "Use /accounts to view connected players.",
                parse_mode="HTML",
            )
        except SessionPasswordNeededError:
            await status.edit_text("Telegram requires an additional verification step. For safety, XYRON does not collect account passwords; this login was not saved.")
        except asyncio.TimeoutError:
            await status.edit_text("⌛ QR login timed out. Run /addaccount to try again.")
        except Exception as exc:
            log.exception("Music player QR login failed")
            await status.edit_text(f"❌ Could not connect the account: {type(exc).__name__}")
        finally:
            if qr_message_ids:
                try:
                    await context.bot.delete_messages(message.chat_id, qr_message_ids)
                except Exception:
                    log.debug("Could not remove temporary QR messages", exc_info=True)
            if client.is_connected():
                await client.disconnect()

    async def handle_accounts(self, update, context):
        message = update.effective_message
        if not self._is_owner(update):
            return await message.reply_text("⛔ Owner only.")
        if not self._stored_accounts:
            return await message.reply_text("No voice-player accounts are saved. Use /addaccount.")
        rows = []
        for account_id, record in self._stored_accounts.items():
            name = f"@{record['username']}" if record.get("username") else record.get("first_name", "Telegram user")
            state = "🟢 Connected" if account_id in self.accounts else "🟡 Saved; reconnect needed"
            rows.append(f"• {name} — <code>{account_id}</code> · {state}")
        await message.reply_text(
            "🎧 <b>XYRON MUSIC — CONNECTED ACCOUNTS</b>\n\n" + "\n".join(rows),
            parse_mode="HTML",
        )

    async def shutdown(self):
        for client in reversed(self.clients):
            try:
                if client.is_connected():
                    await client.disconnect()
            except Exception:
                log.exception("Error disconnecting a music player account")
