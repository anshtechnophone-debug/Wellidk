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
async def test_wordstate_returns_global_personal_points_wins_and_rank(tmp_path):
    engine, sessions = create_database(f"sqlite+aiosqlite:///{tmp_path / 'wordseek-state.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    now = datetime.now(UTC)
    async with sessions.begin() as session:
        for user_id, chat_id, points in ((101, -100123, 50), (101, -100456, 20), (202, -100123, 90), (303, -100123, 30)):
            game = WordGameSession(
                chat_id=chat_id,
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
                    chat_id=chat_id,
                    user_id=user_id,
                    points=points,
                    created_at=now,
                )
            )

    state = await WordSeekRepository(sessions).player_state(None, 101)
    assert state == {"points": 70, "wins": 2, "rank": 2, "players": 3}

    empty = await WordSeekRepository(sessions).player_state(None, 404)
    assert empty == {"points": 0, "wins": 0, "rank": None, "players": 3}
    await engine.dispose()
