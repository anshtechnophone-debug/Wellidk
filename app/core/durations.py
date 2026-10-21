"""Strict parsing for bounded Telegram moderation durations."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

_DURATION_PATTERN = re.compile(r"^(?P<amount>[0-9]{1,6})(?P<unit>[smhdw])$", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
MAX_DURATION = timedelta(days=366)


class DurationError(ValueError):
    """Raised when a duration is malformed or outside the supported range."""


def parse_duration(value: str) -> timedelta:
    match = _DURATION_PATTERN.fullmatch(value.strip())
    if match is None:
        raise DurationError("Use a duration such as 30s, 4m, 3h, 6d, or 5w.")

    amount = int(match.group("amount"))
    seconds = amount * _UNIT_SECONDS[match.group("unit").lower()]
    if amount <= 0:
        raise DurationError("Duration must be greater than zero.")
    if seconds > MAX_DURATION.total_seconds():
        raise DurationError("Duration cannot exceed 366 days.")
    return timedelta(seconds=seconds)


def expiration_from_now(duration: timedelta, *, now: datetime | None = None) -> datetime:
    current = now or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if duration <= timedelta(0) or duration > MAX_DURATION:
        raise DurationError("Duration must be positive and no longer than 366 days.")
    return current + duration
