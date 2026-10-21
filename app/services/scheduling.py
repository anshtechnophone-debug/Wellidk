"""Restart-safe worker for expiring stored moderation actions."""

from __future__ import annotations

import asyncio
import logging

from telegram import ChatMember, ChatPermissions
from telegram.error import TelegramError
from telegram.ext import Application

from app.core.permissions import bounded_permissions
from app.services.moderation import ModerationRepository

logger = logging.getLogger(__name__)


async def expiration_worker(application: Application, stop_event: asyncio.Event) -> None:
    repository = ModerationRepository(application.bot_data["sessions"])
    while not stop_event.is_set():
        for action in await repository.due_actions():
            try:
                settings = application.bot_data["settings"]
                owner = await application.bot.get_chat_member(action.chat_id, settings.owner_id)
                bot_member = await application.bot.get_chat_member(
                    action.chat_id, application.bot.id
                )
                if settings.owner_admin_required and owner.status not in {
                    ChatMember.ADMINISTRATOR,
                    ChatMember.OWNER,
                }:
                    if action.attempts == 0:
                        try:
                            await application.bot.send_message(
                                settings.owner_id,
                                f"XYRON could not expire a temporary moderation action in "
                                f"group {action.chat_id}: you are no longer an administrator.",
                            )
                        except TelegramError:
                            pass
                    await repository.note_expiration_failure(action.id)
                    continue
                if bot_member.status not in {
                    ChatMember.ADMINISTRATOR,
                    ChatMember.OWNER,
                } or not getattr(bot_member, "can_restrict_members", False):
                    await repository.note_expiration_failure(action.id)
                    continue
                if action.action == "unban":
                    await application.bot.unban_chat_member(
                        action.chat_id, action.target_id, only_if_banned=True
                    )
                    await repository.record(
                        action.chat_id, 0, action.target_id, "auto_unban", "temporary ban expired"
                    )
                elif action.action == "unmute":
                    group = await application.bot.get_chat(action.chat_id)
                    group_permissions = getattr(group, "permissions", None)
                    if group_permissions is None:
                        await repository.note_expiration_failure(action.id)
                        continue
                    defaults = {
                        name: bool(getattr(group_permissions, name, False))
                        for name in ChatPermissions.__slots__
                    }
                    await application.bot.restrict_chat_member(
                        action.chat_id,
                        action.target_id,
                        permissions=bounded_permissions(
                            action.original_permissions or defaults, defaults
                        ),
                    )
                    await repository.forget_permissions(action.chat_id, action.target_id)
                else:
                    logger.error(
                        "Unknown temporary action %s for record %s", action.action, action.id
                    )
                await repository.delete_temporary(action.id)
            except TelegramError:
                await repository.note_expiration_failure(action.id)
                logger.exception(
                    "Temporary action %s failed; it remains queued for retry", action.id
                )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=15)
        except TimeoutError:
            continue
