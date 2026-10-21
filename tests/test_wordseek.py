from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.database.models import Base, WordGameScore, WordGameSession
from app.database.session import create_database
from app.modules.games.wordseek import (
    WordGameError,
    WordSeekRepository,
    is_valid_word,
    score_guess,
)


@pytest.fixture
async def games(tmp_path):
    engine, sessions = create_database(f"sqlite+aiosqlite:///{tmp_path / 'games.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield WordSeekRepository(sessions), sessions
    await engine.dispose()


def test_word_check_and_duplicate_letter_feedback():
    from app.modules.games.wordseek import _VALID_WORDS

    assert all(len(word) == length for length, words in _VALID_WORDS.items() for word in words)
    assert all(_VALID_WORDS[length] for length in (4, 5, 6))
    assert is_valid_word("game", 4)
    assert is_valid_word("about", 5)
    assert is_valid_word("across", 6)
    assert not is_valid_word("zzzz", 4)
    assert score_guess("game", "name") == "⬛🟩🟩🟩"
    assert score_guess("else", "eels") == "🟩🟨🟨🟨"


@pytest.mark.parametrize("length", [4, 5, 6])
def test_each_advertised_word_length_has_only_matching_valid_words(length):
    from app.modules.games.wordseek import _VALID_WORDS

    assert _VALID_WORDS[length]
    assert all(len(word) == length for word in _VALID_WORDS[length])


@pytest.mark.asyncio
async def test_game_win_scores_once_and_persists_across_sessions(games):
    repository, sessions = games
    await repository.start(-1001, 17, 4, answer="game")
    first = await repository.guess(-1001, 17, "name")
    assert first.state == "active"
    assert first.guesses_left == 5
    with pytest.raises(WordGameError, match="already tried"):
        await repository.guess(-1001, 17, "name")
    won = await repository.guess(-1001, 17, "game")
    assert (won.state, won.points, won.answer) == ("won", 60, "game")
    with pytest.raises(WordGameError, match="No active"):
        await repository.guess(-1001, 17, "game")
    async with sessions() as session:
        scores = list(await session.scalars(select(WordGameScore)))
    assert len(scores) == 1
    assert scores[0].points == 60
    await repository.start(-1001, 17, 4, answer="game")


@pytest.mark.asyncio
async def test_games_are_isolated_by_chat_and_player(games):
    repository, sessions = games
    await repository.start(-1001, 17, 4, answer="game")
    await repository.start(-1002, 17, 4, answer="game")
    await repository.start(-1001, 18, 4, answer="game")
    with pytest.raises(WordGameError, match="already have"):
        await repository.start(-1001, 17, 4, answer="game")
    result = await repository.guess(-1002, 17, "game")
    assert result.state == "won"
    async with sessions() as session:
        active = list(
            await session.scalars(select(WordGameSession).where(WordGameSession.state == "active"))
        )
    assert {(game.chat_id, game.user_id) for game in active} == {(-1001, 17), (-1001, 18)}


@pytest.mark.asyncio
async def test_game_timeout_and_cancellation(games):
    repository, sessions = games
    now = datetime.now(UTC)
    await repository.start(-1001, 17, 4, answer="game", now=now)
    with pytest.raises(WordGameError, match="expired"):
        await repository.guess(-1001, 17, "game", now=now + timedelta(minutes=16))
    assert await repository.cancel(-1001, 17) is False
    async with sessions() as session:
        game = await session.scalar(select(WordGameSession))
    assert game.state == "expired"


@pytest.mark.asyncio
async def test_expired_game_can_be_replaced_without_a_guess(games):
    repository, sessions = games
    now = datetime.now(UTC)
    await repository.start(-1001, 17, 4, answer="game", now=now)
    replacement = await repository.start(
        -1001, 17, 4, answer="able", now=now + timedelta(minutes=16)
    )
    assert replacement.answer == "able"
    async with sessions() as session:
        games_in_db = list(await session.scalars(select(WordGameSession)))
    assert [game.state for game in games_in_db] == ["expired", "active"]
