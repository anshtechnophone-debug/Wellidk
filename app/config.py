"""Environment-backed configuration with fail-fast validation."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///./data/xyron.db"


def _parse_ids(value: str) -> frozenset[int]:
    try:
        return frozenset(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise ValueError("PROTECTED_USER_IDS must be comma-separated numeric Telegram IDs") from exc


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    owner_id: int
    database_url: str = DEFAULT_DATABASE_URL
    owner_admin_required: bool = True
    protected_user_ids: frozenset[int] = frozenset()
    log_level: str = "INFO"
    timezone: str = "UTC"
    max_reminders_per_user: int = 20

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        token = os.getenv("BOT_TOKEN", "").strip()
        owner_value = os.getenv("OWNER_ID", "").strip()
        if not token or token == "replace_me":
            raise ValueError("BOT_TOKEN is required; set it in the environment or .env file")
        try:
            owner_id = int(owner_value)
        except ValueError as exc:
            raise ValueError("OWNER_ID must be the owner's numeric Telegram user ID") from exc
        if owner_id <= 0:
            raise ValueError("OWNER_ID must be a positive Telegram user ID")

        owner_admin_required = os.getenv("OWNER_ADMIN_REQUIRED", "true").strip().lower()
        if owner_admin_required not in {"true", "false"}:
            raise ValueError("OWNER_ADMIN_REQUIRED must be true or false")
        try:
            reminders_limit = int(os.getenv("MAX_REMINDERS_PER_USER", "20"))
        except ValueError as exc:
            raise ValueError("MAX_REMINDERS_PER_USER must be an integer") from exc
        if reminders_limit < 1 or reminders_limit > 500:
            raise ValueError("MAX_REMINDERS_PER_USER must be between 1 and 500")

        return cls(
            bot_token=token,
            owner_id=owner_id,
            database_url=os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL),
            owner_admin_required=owner_admin_required == "true",
            protected_user_ids=_parse_ids(os.getenv("PROTECTED_USER_IDS", "")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            timezone=os.getenv("TIMEZONE", "UTC"),
            max_reminders_per_user=reminders_limit,
        )
