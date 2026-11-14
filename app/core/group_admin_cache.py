"""Shared, short-lived Telegram group administrator snapshots.

A single getChatAdministrators request replaces repeated per-user membership
lookups for the sender, configured owner, and bot. Failed refreshes are never
treated as an empty or trusted administrator list.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from telegram.error import TelegramError

logger = logging.getLogger(__name__)

ADMIN_CACHE_TTL_SECONDS = 30
ADMIN_LOOKUP_TIMEOUT_SECONDS = 6


class GroupAdminLookupError(RuntimeError):
    """Telegram administrator state could not be verified safely."""


def invalidate_group_admin_cache(bot_data: dict[str, Any], chat_id: int) -> None:
    """Invalidate one group's administrator snapshot after membership changes."""
    cache = bot_data.get("_xyron_group_admin_cache")
    if isinstance(cache, dict):
        cache.pop(chat_id, None)


async def get_group_admins(bot, bot_data: dict[str, Any], chat_id: int):
    """Return {user_id: ChatMember} from a lock-protected 30-second cache."""
    cache = bot_data.setdefault("_xyron_group_admin_cache", {})
    locks = bot_data.setdefault("_xyron_group_admin_locks", {})
    lock = locks.setdefault(chat_id, asyncio.Lock())

    now = time.monotonic()
    cached = cache.get(chat_id)
    if cached and cached[0] > now:
        return cached[1]

    async with lock:
        now = time.monotonic()
        cached = cache.get(chat_id)
        if cached and cached[0] > now:
            return cached[1]

        try:
            members = await asyncio.wait_for(
                bot.get_chat_administrators(chat_id),
                timeout=ADMIN_LOOKUP_TIMEOUT_SECONDS,
            )
        except (TimeoutError, TelegramError) as exc:
            logger.warning(
                "Group administrator lookup failed: chat_id=%s error=%s",
                chat_id,
                exc,
            )
            raise GroupAdminLookupError(
                f"Could not verify administrators for group {chat_id}"
            ) from exc
        except Exception as exc:
            logger.exception(
                "Unexpected group administrator lookup failure: chat_id=%s",
                chat_id,
            )
            raise GroupAdminLookupError(
                f"Could not verify administrators for group {chat_id}"
            ) from exc

        admins = {
            int(member.user.id): member
            for member in members
            if getattr(member, "user", None) is not None
        }
        cache[chat_id] = (time.monotonic() + ADMIN_CACHE_TTL_SECONDS, admins)
        return admins
