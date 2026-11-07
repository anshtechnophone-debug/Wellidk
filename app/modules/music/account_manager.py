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
        self._login_lock = asyncio.Lock()
        self._login_tasks = {}

    def _read_store(self):
        candidates = [self.store_path]
        if self.legacy_store:
            candidates.append(self.legacy_store)
        for candidate in candidates:
            try:
                if not candidate.is_file():
                    continue
                payload = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    if candidate != self.store_path:
                        log.warning("Found legacy player-account store at %s; migrating a copy", candidate)
                    return payload
            except PermissionError:
                if candidate == self.legacy_store:
                    log.warning("Skipping inaccessible legacy player-account store: %s", candidate)
                    continue
                log.exception("Could not access player-account store: %s", candidate)
                raise RuntimeError(f"Player-account store is unreadable: {candidate}")
            except (OSError, json.JSONDecodeError):
                log.exception("Could not read player-account store: %s", candidate)
                raise RuntimeError(f"Player-account store is unreadable: {candidate}")
        return {}

    def _write_store(self):
        temporary = self.store_path.with_suffix(".tmp")
        payload = json.dumps(self.accounts, indent=2, sort_keys=True)
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
        # Check the active store first. If it already exists, there is no reason
        # to inspect the retired service's legacy path (which may be inaccessible).
        if self.accounts and not failed and self.legacy_store and not self.store_path.exists():
            try:
                legacy_exists = self.legacy_store.is_file()
            except OSError as exc:
                log.warning("Skipping inaccessible legacy player-account store %s: %s", self.legacy_store, exc)
                legacy_exists = False
            if legacy_exists:
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
        user_id = update.effective_user.id
        current = self._login_tasks.get(user_id)
        if current and not current.done():
            return await update.effective_message.reply_text(
                "An account connection is already running. Use /cancelaccount to stop it."
            )
        if self._login_lock.locked():
            return await update.effective_message.reply_text("An account connection is already in progress.")
        await update.effective_message.reply_text(
            "Starting secure account linking. Send /cancelaccount at any time to stop it."
        )
        task = asyncio.create_task(self._login_worker(update, context, user_id))
        self._login_tasks[user_id] = task

    async def _login_worker(self, update, context, user_id):
        try:
            async with self._login_lock:
                await self._run_qr_login(update, context)
        except asyncio.CancelledError:
            log.info("Music account login cancelled by owner %s", user_id)
        finally:
            if self._login_tasks.get(user_id) is asyncio.current_task():
                self._login_tasks.pop(user_id, None)

    async def handle_cancel_account(self, update, context):
        message = update.effective_message
        if not self._is_owner(update):
            return await message.reply_text("⛔ This command is available only to the configured bot owner.")
        user_id = update.effective_user.id
        task = self._login_tasks.get(user_id)
        if not task or task.done():
            return await message.reply_text("There is no active account-linking process to cancel.")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await message.reply_text("🛑 Account linking cancelled. Temporary QR messages are being removed.")

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
            self.accounts[account_id] = record
            self.clients.append(player)
            try:
                self._write_store()
            except Exception:
                self.accounts.pop(account_id, None)
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
        except asyncio.CancelledError:
            try:
                await status.edit_text("🛑 Account linking cancelled. No new account was saved.")
            except Exception:
                log.debug("Could not update cancelled music login status", exc_info=True)
            raise
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
        if not self.accounts:
            return await message.reply_text("No voice-player accounts are connected. Use /addaccount.")
        rows = []
        for account_id, record in self.accounts.items():
            name = f"@{record['username']}" if record.get("username") else record.get("first_name", "Telegram user")
            rows.append(f"• {name} — <code>{account_id}</code>")
        await message.reply_text(
            "🎧 <b>XYRON MUSIC — CONNECTED ACCOUNTS</b>\n\n" + "\n".join(rows),
            parse_mode="HTML",
        )

    async def shutdown(self):
        login_tasks = list(self._login_tasks.values())
        for task in login_tasks:
            if not task.done():
                task.cancel()
        if login_tasks:
            await asyncio.gather(*login_tasks, return_exceptions=True)
        self._login_tasks.clear()
        for client in reversed(self.clients):
            try:
                if client.is_connected():
                    await client.disconnect()
            except Exception:
                log.exception("Error disconnecting a music player account")
