from datetime import UTC, datetime, timedelta

import pytest

from app.database.models import Base, WordGameScore, WordGameSession
from app.database.session import create_database
from app.modules.games.wordseek import WordSeekRepository, is_valid_word


@pytest.mark.asyncio
async def test_expanded_dictionary_accepts_more_common_words():
    assert is_valid_word("abbey", 5)
    assert is_valid_word("across", 6)
    assert is_valid_word("able", 4)


@pytest.mark.asyncio
async def test_wordstate_returns_personal_points_wins_and_chat_rank(tmp_path):
    engine, sessions = create_database(f"sqlite+aiosqlite:///{tmp_path / 'wordseek-state.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    now = datetime.now(UTC)
    async with sessions.begin() as session:
        for user_id, points in ((101, 50), (202, 90), (303, 30)):
            game = WordGameSession(
                chat_id=-100123,
                user_id=user_id,
                active_key=None,
                word_length=5,
                answer="abbey",
                guesses=["abbey"],
                max_guesses=30,
                state="won",
                points_awarded=True,
                created_at=now,
                expires_at=now + timedelta(minutes=15),
            )
            session.add(game)
            await session.flush()
            session.add(
                WordGameScore(
                    session_id=game.id,
                    chat_id=-100123,
                    user_id=user_id,
                    points=points,
                    created_at=now,
                )
            )

    state = await WordSeekRepository(sessions).player_state(-100123, 101)
    assert state == {"points": 50, "wins": 1, "rank": 2, "players": 3}

    empty = await WordSeekRepository(sessions).player_state(-100123, 404)
    assert empty == {"points": 0, "wins": 0, "rank": None, "players": 3}
    await engine.dispose()
