from datetime import date

import pytest
from sqlalchemy import select

from app.database.models import Base, WordDailyAttempt
from app.database.session import create_database
from app.modules.games.wordseek_daily import (
    DAILY_ATTEMPTS,
    DailyWordError,
    WordSeekDailyRepository,
    daily_answer,
)


@pytest.fixture
async def daily_games(tmp_path):
    engine, sessions = create_database(f"sqlite+aiosqlite:///{tmp_path / 'daily-wordseek.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield WordSeekDailyRepository(sessions), sessions
    await engine.dispose()


@pytest.mark.asyncio
async def test_daily_puzzle_is_shared_and_persisted_per_user(daily_games):
    repository, sessions = daily_games
    puzzle_day = date(2026, 11, 10)

    first = await repository.start(101, today=puzzle_day)
    again = await repository.start(101, today=puzzle_day)
    other_player = await repository.start(202, today=puzzle_day)

    assert first.state == "active"
    assert first.guesses_left == DAILY_ATTEMPTS
    assert again == first
    assert daily_answer(puzzle_day) == daily_answer(puzzle_day)
    assert first.puzzle_date == other_player.puzzle_date

    async with sessions() as session:
        rows = list(await session.scalars(select(WordDailyAttempt)))
    assert len(rows) == 2
    assert rows[0].answer == rows[1].answer


@pytest.mark.asyncio
async def test_daily_win_and_stats_are_saved_once(daily_games):
    repository, sessions = daily_games
    puzzle_day = date(2026, 11, 10)
    answer = daily_answer(puzzle_day)

    await repository.start(101, today=puzzle_day)
    won = await repository.guess(101, answer, today=puzzle_day)

    assert won.state == "won"
    assert won.answer == answer
    assert won.guesses == (answer,)
    assert won.guesses_left == 5

    with pytest.raises(DailyWordError, match="already completed"):
        await repository.guess(101, answer, today=puzzle_day)

    stats = await repository.stats(101, today=puzzle_day)
    assert stats["played"] == 1
    assert stats["wins"] == 1
    assert stats["win_rate"] == 100
    assert stats["current_streak"] == 1
    assert stats["best_streak"] == 1
    assert stats["distribution"]["1"] == 1


@pytest.mark.asyncio
async def test_daily_puzzle_requires_start_and_limits_to_six_guesses(daily_games):
    repository, sessions = daily_games
    puzzle_day = date(2026, 11, 10)
    answer = daily_answer(puzzle_day)

    with pytest.raises(DailyWordError, match="Start today's puzzle"):
        await repository.guess(101, answer, today=puzzle_day)

    await repository.start(101, today=puzzle_day)
    from app.modules.games.wordseek import _VALID_WORDS

    wrong_words = [word for word in sorted(_VALID_WORDS[5]) if word != answer][:DAILY_ATTEMPTS]
    result = None
    for word in wrong_words:
        result = await repository.guess(101, word, today=puzzle_day)

    assert result is not None
    assert result.state == "lost"
    assert result.guesses_left == 0
    assert result.answer == answer
