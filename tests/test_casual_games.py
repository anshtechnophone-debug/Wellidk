import random
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.database.models import Base, CasualGameScore, CasualGameSession
from app.database.session import create_database
from app.modules.games.casual import (
    CasualGameError,
    CasualGameRepository,
    anagram_transition,
    hangman_transition,
    number_transition,
    question_transition,
    rps_transition,
    scramble_transition,
    teamword_transition,
    tictactoe_transition,
    wordchain_transition,
)


@pytest.fixture
async def casual_games(tmp_path):
    engine, sessions = create_database(f"sqlite+aiosqlite:///{tmp_path / 'casual.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield CasualGameRepository(sessions), sessions
    await engine.dispose()


@pytest.mark.asyncio
async def test_hangman_persists_and_scores_a_win_once(casual_games):
    repository, sessions = casual_games
    await repository.start(-1001, 12, "hangman", answer="apple")
    for letter in "aple":
        turn = await repository.submit(-1001, 12, "hangman", hangman_transition(letter))
    assert turn.status == "won"
    assert turn.points > 0
    with pytest.raises(CasualGameError, match="No active"):
        await repository.submit(-1001, 12, "hangman", hangman_transition("a"))
    async with sessions() as session:
        scores = list(await session.scalars(select(CasualGameScore)))
    assert len(scores) == 1
    assert scores[0].game_type == "hangman"


@pytest.mark.asyncio
async def test_scramble_ends_after_three_wrong_answers(casual_games):
    repository, _ = casual_games
    await repository.start(-1001, 12, "scramble", answer="apple")
    for _ in range(2):
        turn = await repository.submit(-1001, 12, "scramble", scramble_transition("wrong"))
        assert turn.status == "active"
    turn = await repository.submit(-1001, 12, "scramble", scramble_transition("wrong"))
    assert turn.status == "lost"
    assert turn.points == 0


@pytest.mark.asyncio
async def test_number_guess_isolated_and_gives_hints(casual_games):
    repository, _ = casual_games
    await repository.start(-1001, 12, "number", answer="42")
    await repository.start(-1002, 12, "number", answer="42")
    high = await repository.submit(-1001, 12, "number", number_transition(20))
    assert high.state["last_hint"] == "higher"
    won = await repository.submit(-1001, 12, "number", number_transition(42))
    assert won.status == "won" and won.points == 70
    await repository.start(-1001, 12, "number", answer="7")
    board = await repository.leaderboard(-1001, "number")
    assert board == [(12, 70)]


@pytest.mark.asyncio
async def test_session_timeout_persists_terminal_state(casual_games):
    repository, sessions = casual_games
    now = datetime.now(UTC)
    await repository.start(-1001, 12, "number", now=now, answer="42")
    with pytest.raises(CasualGameError, match="expired"):
        await repository.submit(
            -1001,
            12,
            "number",
            number_transition(42),
            now=now + timedelta(minutes=16),
        )
    async with sessions() as session:
        game = await session.scalar(select(CasualGameSession))
    assert game.status == "expired"
    assert game.active_key is None


@pytest.mark.asyncio
async def test_trivia_and_quickmath_are_locally_scored(casual_games):
    repository, sessions = casual_games
    trivia = await repository.start(-1001, 12, "trivia", rng=random.Random(2))
    answer = str(trivia["answer"])
    result = await repository.submit(-1001, 12, "trivia", question_transition(answer))
    assert result.status == "won" and result.points == 30
    math = await repository.start(-1001, 12, "quickmath", rng=random.Random(5))
    result = await repository.submit(
        -1001,
        12,
        "quickmath",
        question_transition(str(math["answer"])),
    )
    assert result.status == "won"
    board = await repository.leaderboard(-1001, "trivia")
    assert board == [(12, 30)]
    async with sessions() as session:
        scores = list(await session.scalars(select(CasualGameScore)))
    assert len(scores) == 2


@pytest.mark.asyncio
async def test_rps_and_tictactoe_use_valid_terminal_scoring(casual_games):
    repository, sessions = casual_games
    rps = await repository.start(-1001, 12, "rps", rng=random.Random(1))
    winner = {"rock": "paper", "paper": "scissors", "scissors": "rock"}[rps["answer"]]
    turn = await repository.submit(-1001, 12, "rps", rps_transition(winner))
    assert turn.status == "won" and turn.points == 1

    await repository.start(-1001, 13, "tictactoe")
    turn = await repository.submit(-1001, 13, "tictactoe", tictactoe_transition(1))
    assert turn.status == "active"
    assert turn.state["board"][0] == "X"
    assert turn.state["board"][4] == "O"
    with pytest.raises(CasualGameError, match="occupied"):
        await repository.submit(-1001, 13, "tictactoe", tictactoe_transition(5))
    async with sessions() as session:
        scores = list(await session.scalars(select(CasualGameScore)))
    assert len(scores) == 1 and scores[0].game_type == "rps"


@pytest.mark.asyncio
async def test_word_chain_checks_continuation_and_duplicate_words(casual_games):
    from app.modules.games.wordseek import _VALID_WORDS

    repository, _ = casual_games
    state = await repository.start(-1001, 12, "wordchain", rng=random.Random(1))
    previous = str(state["last_word"])
    next_word = next(
        word
        for length in (4, 5, 6)
        for word in sorted(_VALID_WORDS[length])
        if word.startswith(previous[-1]) and word != previous
    )
    turn = await repository.submit(-1001, 12, "wordchain", wordchain_transition(next_word))
    assert turn.state["links"] == 1
    with pytest.raises(CasualGameError, match="already been used"):
        await repository.submit(-1001, 12, "wordchain", wordchain_transition(previous))
    wrong_word = next(
        word
        for length in (4, 5, 6)
        for word in sorted(_VALID_WORDS[length])
        if not word.startswith(str(turn.state["last_word"])[-1]) and word not in turn.state["used"]
    )
    with pytest.raises(CasualGameError, match="start with"):
        await repository.submit(-1001, 12, "wordchain", wordchain_transition(wrong_word))


@pytest.mark.asyncio
async def test_anagram_is_single_attempt_and_scores_correct_answer(casual_games):
    repository, _ = casual_games
    await repository.start(-1001, 12, "anagram", answer="apple")
    turn = await repository.submit(-1001, 12, "anagram", anagram_transition("apple"))
    assert turn.status == "won" and turn.points == 25
    with pytest.raises(CasualGameError, match="No active"):
        await repository.submit(-1001, 12, "anagram", anagram_transition("apple"))

    @pytest.mark.asyncio
    async def test_team_word_is_shared_by_chat_and_scores_the_solver(casual_games):
        repository, sessions = casual_games
        await repository.start(-1001, 12, "teamword", answer="about")
        with pytest.raises(CasualGameError, match="already have"):
            await repository.start(-1001, 13, "teamword", answer="other")
        first = await repository.submit(-1001, 12, "teamword", teamword_transition("other", 12))
        assert first.status == "active"
        won = await repository.submit(-1001, 13, "teamword", teamword_transition("about", 13))
        assert won.status == "won" and won.points == 50
        assert set(won.state["contributors"]) == {12, 13}
        assert await repository.leaderboard(-1001, "teamword") == [(13, 50)]
        async with sessions() as session:
            scores = list(await session.scalars(select(CasualGameScore)))
        assert len(scores) == 1 and scores[0].user_id == 13
