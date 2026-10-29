"""Environment-backed configuration with fail-fast validation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

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
    secondary_owner_ids: frozenset[int] = frozenset({8797066795})
    database_url: str = DEFAULT_DATABASE_URL
    owner_admin_required: bool = True
    protected_user_ids: frozenset[int] = frozenset()
    log_level: str = "INFO"
    timezone: str = "UTC"
    max_reminders_per_user: int = 20
    ai_enabled: bool = False
    ai_provider: str = "openai_compatible"
    ai_model: str = ""
    ai_fallback_model: str = "inclusionai/ling-3.1-flash"
    ai_api_key: str = ""
    ai_base_url: str = "https://api.openai.com/v1"
    ai_context_messages: int = 10
    ai_context_retention_seconds: int = 3600
    ai_response_cooldown_seconds: int = 60
    ai_group_requests_per_hour: int = 12
    ai_user_requests_per_day: int = 10
    ai_daily_token_budget: int = 50000
    ai_global_emergency_stop: bool = True
    ai_activity_threshold: int = 5
    ai_activity_window_seconds: int = 120
    report_destination_id: int | None = None

    @property
    def owner_ids(self) -> frozenset[int]:
        return frozenset({self.owner_id, *self.secondary_owner_ids})

    def is_owner(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.owner_ids

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
        report_destination_value = os.getenv("REPORT_DESTINATION_ID", "").strip()
        if report_destination_value:
            try:
                report_destination_id = int(report_destination_value)
            except ValueError as exc:
                raise ValueError("REPORT_DESTINATION_ID must be a numeric chat ID") from exc
        else:
            report_destination_id = None

        ai_enabled = _parse_bool("AI_ENABLED", False)
        ai_emergency_stop = _parse_bool("AI_GLOBAL_EMERGENCY_STOP", True)
        ai_provider = os.getenv("AI_PROVIDER", "openai_compatible").strip().lower()
        ai_model = os.getenv("AI_MODEL", "").strip()
        ai_fallback_model = os.getenv(
            "AI_FALLBACK_MODEL", "inclusionai/ling-3.1-flash"
        ).strip()
        ai_api_key = os.getenv("AI_API_KEY", "").strip()
        ai_base_url = os.getenv("AI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
        if ai_enabled and ai_emergency_stop:
            raise ValueError("AI_ENABLED cannot be true while AI_GLOBAL_EMERGENCY_STOP is true")
        if ai_enabled and (not ai_model or not ai_api_key):
            raise ValueError("AI_MODEL and AI_API_KEY are required when AI_ENABLED=true")
        if ai_provider != "openai_compatible":
            raise ValueError("AI_PROVIDER currently supports only openai_compatible")
        parsed_ai_url = urlparse(ai_base_url)
        local_hosts = {"localhost", "127.0.0.1", "::1"}
        if not parsed_ai_url.hostname or not (
            parsed_ai_url.scheme == "https"
            or (parsed_ai_url.scheme == "http" and parsed_ai_url.hostname in local_hosts)
        ):
            raise ValueError("AI_BASE_URL must use HTTPS except for localhost providers")
        ai_values = {
            "AI_CONTEXT_MESSAGES": (os.getenv("AI_CONTEXT_MESSAGES", "10"), 1, 30),
            "AI_CONTEXT_RETENTION_SECONDS": (
                os.getenv("AI_CONTEXT_RETENTION_SECONDS", "3600"),
                60,
                86400,
            ),
            "AI_RESPONSE_COOLDOWN_SECONDS": (
                os.getenv("AI_RESPONSE_COOLDOWN_SECONDS", "60"),
                0,
                86400,
            ),
            "AI_GROUP_REQUESTS_PER_HOUR": (
                os.getenv("AI_GROUP_REQUESTS_PER_HOUR", "12"),
                1,
                1000,
            ),
            "AI_USER_REQUESTS_PER_DAY": (
                os.getenv("AI_USER_REQUESTS_PER_DAY", "10"),
                1,
                1000,
            ),
            "AI_DAILY_TOKEN_BUDGET": (
                os.getenv("AI_DAILY_TOKEN_BUDGET", "50000"),
                100,
                100_000_000,
            ),
            "AI_ACTIVITY_THRESHOLD": (os.getenv("AI_ACTIVITY_THRESHOLD", "5"), 2, 100),
            "AI_ACTIVITY_WINDOW_SECONDS": (
                os.getenv("AI_ACTIVITY_WINDOW_SECONDS", "120"),
                10,
                600,
            ),
        }
        ai_limits: dict[str, int] = {}
        for key, (raw_value, minimum, maximum) in ai_values.items():
            try:
                value = int(raw_value)
            except ValueError as exc:
                raise ValueError(f"{key} must be an integer") from exc
            if not minimum <= value <= maximum:
                raise ValueError(f"{key} must be between {minimum} and {maximum}")
            ai_limits[key] = value

        return cls(
            bot_token=token,
            owner_id=owner_id,
            secondary_owner_ids=_parse_ids(os.getenv("SECONDARY_OWNER_IDS", "8797066795")),
            database_url=os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL),
            owner_admin_required=owner_admin_required == "true",
            protected_user_ids=_parse_ids(os.getenv("PROTECTED_USER_IDS", "")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            timezone=os.getenv("TIMEZONE", "UTC"),
            max_reminders_per_user=reminders_limit,
            ai_enabled=ai_enabled,
            ai_provider=ai_provider,
            ai_model=ai_model,
            ai_fallback_model=ai_fallback_model,
            ai_api_key=ai_api_key,
            ai_base_url=ai_base_url,
            ai_context_messages=ai_limits["AI_CONTEXT_MESSAGES"],
            ai_context_retention_seconds=ai_limits["AI_CONTEXT_RETENTION_SECONDS"],
            ai_response_cooldown_seconds=ai_limits["AI_RESPONSE_COOLDOWN_SECONDS"],
            ai_group_requests_per_hour=ai_limits["AI_GROUP_REQUESTS_PER_HOUR"],
            ai_user_requests_per_day=ai_limits["AI_USER_REQUESTS_PER_DAY"],
            ai_daily_token_budget=ai_limits["AI_DAILY_TOKEN_BUDGET"],
            ai_global_emergency_stop=ai_emergency_stop,
            ai_activity_threshold=ai_limits["AI_ACTIVITY_THRESHOLD"],
            ai_activity_window_seconds=ai_limits["AI_ACTIVITY_WINDOW_SECONDS"],
            report_destination_id=report_destination_id,
        )


def _parse_bool(name: str, default: bool) -> bool:
    value = os.getenv(name, str(default).lower()).strip().lower()
    if value not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false")
    return value == "true"
