from datetime import UTC, datetime, timedelta

import pytest

from app.core.durations import DurationError, expiration_from_now, parse_duration


@pytest.mark.parametrize(
    ("value", "expected"),
    [("30s", 30), ("4m", 240), ("3h", 10800), ("6d", 518400), ("5w", 3024000)],
)
def test_parse_duration(value: str, expected: int) -> None:
    assert parse_duration(value) == timedelta(seconds=expected)


@pytest.mark.parametrize("value", ["0s", "-2h", "1.5h", "abc", "367d", "999999w"])
def test_reject_invalid_duration(value: str) -> None:
    with pytest.raises(DurationError):
        parse_duration(value)


def test_expiration_requires_timezone_aware_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        expiration_from_now(timedelta(hours=1), now=datetime(2026, 1, 1))
    assert expiration_from_now(timedelta(hours=1), now=datetime(2026, 1, 1, tzinfo=UTC))
