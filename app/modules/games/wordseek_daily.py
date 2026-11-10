"""Daily WordSeek challenge, implemented on XYRON's own SQLAlchemy stack."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import WordDailyAttempt
from app.modules.games.wordseek import _VALID_WORDS, is_valid_word, score_guess


DAILY_ATTEMPTS = 6


class DailyWordError(ValueError):
    """A daily challenge action could not be applied."""


@dataclass(frozen=True, slots=True)
class DailyWordResult:
    puzzle_date: date
    state: str
    guesses: tuple[str, ...]
    feedbacks: tuple[str, ...]
    guesses_left: int
    answer: str | None = None


def daily_answer(puzzle_date: date) -> str:
    """Return the same deterministic five-letter answer for every player that day."""
    words = sorted(_VALID_WORDS[5])
    digest = hashlib.sha256(f"xyron-wordseek:{puzzle_date.isoformat()}".encode()).digest()
    return words[int.from_bytes(digest[:8], "big") % len(words)]


def _result(row: WordDailyAttempt, reveal: bool = False) -> DailyWordResult:
    guesses = tuple(row.guesses or ())
    feedbacks = tuple(row.feedbacks or ())
    return DailyWordResult(
        puzzle_date=row.puzzle_date,
        state=row.state,
        guesses=guesses,
        feedbacks=feedbacks,
        guesses_left=max(0, DAILY_ATTEMPTS - len(guesses)),
        answer=row.answer if reveal else None,
    )


class WordSeekDailyRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def start(self, user_id: int, today: date | None = None) -> DailyWordResult:
        puzzle_date = today or datetime.now(UTC).date()
        now = datetime.now(UTC)
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(WordDailyAttempt)
                .where(
                    WordDailyAttempt.user_id == user_id,
                    WordDailyAttempt.puzzle_date == puzzle_date,
                )
                .with_for_update()
            )
            if row is None:
                row = WordDailyAttempt(
                    user_id=user_id,
                    puzzle_date=puzzle_date,
                    answer=daily_answer(puzzle_date),
                    guesses=[],
                    feedbacks=[],
                    state="active",
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                await session.flush()
            return _result(row, reveal=row.state != "active")

    async def guess(
        self, user_id: int, guess: str, today: date | None = None
    ) -> DailyWordResult:
        puzzle_date = today or datetime.now(UTC).date()
        value = guess.strip().casefold()
        if len(value) != 5 or not value.isalpha():
            raise DailyWordError("Today's puzzle needs a 5-letter word.")
        if not is_valid_word(value, 5):
            raise DailyWordError("That word is not in XYRON's word list.")

        now = datetime.now(UTC)
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(WordDailyAttempt)
                .where(
                    WordDailyAttempt.user_id == user_id,
                    WordDailyAttempt.puzzle_date == puzzle_date,
                )
                .with_for_update()
            )
            if row is None:
                raise DailyWordError("Start today's puzzle first with /dailyword.")
            if row.state != "active":
                raise DailyWordError("You have already completed today's WordSeek challenge.")
            if value in (row.guesses or []):
                raise DailyWordError("You already tried that word.")

            guesses = [*(row.guesses or []), value]
            feedbacks = [*(row.feedbacks or []), score_guess(row.answer, value)]
            if value == row.answer:
                state = "won"
            elif len(guesses) >= DAILY_ATTEMPTS:
                state = "lost"
            else:
                state = "active"

            row.guesses = guesses
            row.feedbacks = feedbacks
            row.state = state
            row.updated_at = now
            return DailyWordResult(
                puzzle_date=puzzle_date,
                state=state,
                guesses=tuple(guesses),
                feedbacks=tuple(feedbacks),
                guesses_left=max(0, DAILY_ATTEMPTS - len(guesses)),
                answer=row.answer if state in {"won", "lost"} else None,
            )

    async def stats(self, user_id: int, today: date | None = None) -> dict[str, object]:
        current_day = today or datetime.now(UTC).date()
        async with self.sessions() as session:
            rows = list(
                await session.scalars(
                    select(WordDailyAttempt)
                    .where(WordDailyAttempt.user_id == user_id)
                    .order_by(WordDailyAttempt.puzzle_date)
                )
            )

        wins = [row for row in rows if row.state == "won"]
        win_dates = {row.puzzle_date for row in wins}
        played = sum(bool(row.guesses) for row in rows)
        anchor = current_day if current_day in {row.puzzle_date for row in rows} else current_day - timedelta(days=1)
        current_streak = 0
        cursor = anchor
        while cursor in win_dates:
            current_streak += 1
            cursor -= timedelta(days=1)

        best_streak = streak = 0
        previous: date | None = None
        for day in sorted(win_dates):
            streak = streak + 1 if previous and day == previous + timedelta(days=1) else 1
            best_streak = max(best_streak, streak)
            previous = day

        distribution = {str(attempt): 0 for attempt in range(1, DAILY_ATTEMPTS + 1)}
        for row in wins:
            distribution[str(len(row.guesses))] += 1

        return {
            "played": played,
            "wins": len(wins),
            "win_rate": round((len(wins) / played) * 100) if played else 0,
            "current_streak": current_streak,
            "best_streak": best_streak,
            "distribution": distribution,
        }
