"""Persistent deterministic Hangman, Scramble, and Number Guess games."""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import CasualGameScore, CasualGameSession

_WORDS = {
    "apple",
    "beach",
    "bread",
    "chair",
    "cloud",
    "dance",
    "dream",
    "earth",
    "flame",
    "fruit",
    "ghost",
    "grape",
    "house",
    "lemon",
    "light",
    "magic",
    "music",
    "ocean",
    "paper",
    "plant",
    "queen",
    "river",
    "smile",
    "stone",
    "table",
    "tiger",
    "train",
    "water",
    "world",
    "yellow",
}
GAME_TIMEOUT = timedelta(minutes=15)
_QUESTION_BANKS: dict[str, tuple[dict[str, object], ...]] = {
    "trivia": (
        {
            "prompt": "Which planet is known as the Red Planet? A) Venus B) Mars C) Jupiter",
            "answer": "b",
        },
        {"prompt": "How many sides does a hexagon have? A) Five B) Six C) Eight", "answer": "b"},
        {"prompt": "What is the chemical symbol for water? A) H2O B) CO2 C) O2", "answer": "a"},
        {"prompt": "Which ocean is the largest? A) Atlantic B) Indian C) Pacific", "answer": "c"},
    ),
    "truefalse": (
        {"prompt": "An octopus has three hearts. True or False?", "answer": "true"},
        {
            "prompt": "The Pacific Ocean is smaller than the Atlantic. True or False?",
            "answer": "false",
        },
        {"prompt": "A group of crows is called a murder. True or False?", "answer": "true"},
    ),
    "riddle": (
        {"prompt": "What has keys but cannot open locks?", "answer": "piano"},
        {"prompt": "What gets wetter as it dries?", "answer": "towel"},
        {"prompt": "What has a face and two hands but no arms or legs?", "answer": "clock"},
    ),
}


class CasualGameError(ValueError):
    """The game action is invalid or no active game exists."""


@dataclass(frozen=True, slots=True)
class CasualTurn:
    status: str
    state: dict[str, object]
    points: int = 0


Transition = Callable[[dict[str, object]], CasualTurn]


def scramble_word(word: str, rng: random.Random | random.SystemRandom | None = None) -> str:
    source = rng or random.SystemRandom()
    letters = list(word)
    for _ in range(5):
        source.shuffle(letters)
        scrambled = "".join(letters)
        if scrambled != word:
            return scrambled
    return word[1:] + word[:1]


class CasualGameRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def start(
        self,
        chat_id: int,
        user_id: int,
        game_type: str,
        now: datetime | None = None,
        answer: str | None = None,
        rng: random.Random | random.SystemRandom | None = None,
    ) -> dict[str, object]:
        if game_type not in {
            "hangman",
            "scramble",
            "anagram",
            "number",
            "trivia",
            "truefalse",
            "riddle",
            "quickmath",
            "rps",
            "tictactoe",
            "wordchain",
            "teamword",
        }:
            raise CasualGameError("Unknown game type.")
        current = now or datetime.now(UTC)
        active_key = (
            f"{chat_id}:teamword" if game_type == "teamword" else f"{chat_id}:{user_id}:{game_type}"
        )
        try:
            async with self.sessions.begin() as session:
                old = await session.scalar(
                    select(CasualGameSession)
                    .where(CasualGameSession.active_key == active_key)
                    .with_for_update()
                )
                if old and old.expires_at > current:
                    raise CasualGameError(f"You already have an active {game_type} game here.")
                if old:
                    old.status = "expired"
                    old.active_key = None
                if game_type in {"hangman", "scramble", "anagram"}:
                    selected = (answer or random.SystemRandom().choice(sorted(_WORDS))).casefold()
                    if selected not in _WORDS:
                        raise CasualGameError("The selected answer is not in the local word list.")
                    if game_type == "hangman":
                        state = {"answer": selected, "guessed": [], "misses": []}
                    else:
                        state = {
                            "answer": selected,
                            "scrambled": scramble_word(selected, rng),
                            "attempts": 0,
                        }
                elif game_type == "number":
                    value = (
                        int(answer) if answer is not None else random.SystemRandom().randint(1, 100)
                    )
                    if not 1 <= value <= 100:
                        raise CasualGameError("Number answer must be from 1 to 100.")
                    state = {"answer": value, "attempts": 0}
                elif game_type == "quickmath":
                    source = rng or random.SystemRandom()
                    left, right = source.randint(2, 20), source.randint(2, 20)
                    operator = source.choice(["+", "-"])
                    if operator == "-" and right > left:
                        left, right = right, left
                    value = left + right if operator == "+" else left - right
                    state = {
                        "prompt": f"What is {left} {operator} {right}?",
                        "answer": value,
                        "attempts": 0,
                        "max_attempts": 2,
                    }
                elif game_type == "rps":
                    state = {
                        "answer": (rng or random.SystemRandom()).choice(
                            ["rock", "paper", "scissors"]
                        )
                    }
                elif game_type == "tictactoe":
                    state = {"board": [" "] * 9, "player": "X", "bot": "O"}
                elif game_type == "wordchain":
                    from app.modules.games.wordseek import _VALID_WORDS

                    source = rng or random.SystemRandom()
                    start_word = source.choice(sorted(_VALID_WORDS[4]))
                    state = {"last_word": start_word, "used": [start_word], "links": 0}
                elif game_type == "teamword":
                    from app.modules.games.wordseek import _VALID_WORDS

                    source = rng or random.SystemRandom()
                    answer_word = (answer or source.choice(sorted(_VALID_WORDS[5]))).casefold()
                    if answer_word not in _VALID_WORDS[5]:
                        raise CasualGameError("Team Word answer must be a valid five-letter word.")
                    state = {"answer": answer_word, "guesses": [], "contributors": []}
                else:
                    state = {
                        **(rng or random.SystemRandom()).choice(_QUESTION_BANKS[game_type]),
                        "attempts": 0,
                        "max_attempts": 2,
                    }
                game = CasualGameSession(
                    chat_id=chat_id,
                    user_id=user_id,
                    game_type=game_type,
                    active_key=active_key,
                    state=state,
                    status="active",
                    created_at=current,
                    expires_at=current + GAME_TIMEOUT,
                )
                session.add(game)
            return state
        except IntegrityError as exc:
            raise CasualGameError(
                "A game of this type is already active for you in this chat."
            ) from exc

    async def submit(
        self,
        chat_id: int,
        user_id: int,
        game_type: str,
        transition: Transition,
        now: datetime | None = None,
    ) -> CasualTurn:
        current = now or datetime.now(UTC)
        active_key = (
            f"{chat_id}:teamword" if game_type == "teamword" else f"{chat_id}:{user_id}:{game_type}"
        )
        expired = False
        turn: CasualTurn | None = None
        async with self.sessions.begin() as session:
            game = await session.scalar(
                select(CasualGameSession)
                .where(CasualGameSession.active_key == active_key)
                .with_for_update()
            )
            if game is None:
                raise CasualGameError(f"No active {game_type} game in this chat.")
            if game.expires_at <= current:
                game.status = "expired"
                game.active_key = None
                expired = True
            else:
                turn = transition(dict(game.state))
                game.state = turn.state
                game.status = turn.status
                if turn.status != "active":
                    game.active_key = None
                if turn.points:
                    session.add(
                        CasualGameScore(
                            session_id=game.id,
                            chat_id=chat_id,
                            user_id=user_id,
                            game_type=game_type,
                            points=turn.points,
                            created_at=current,
                        )
                    )
        if expired:
            raise CasualGameError(f"That {game_type} game expired; start another one.")
        assert turn is not None
        return turn

    async def cancel(self, chat_id: int, user_id: int, game_type: str) -> bool:
        active_key = (
            f"{chat_id}:teamword" if game_type == "teamword" else f"{chat_id}:{user_id}:{game_type}"
        )
        async with self.sessions.begin() as session:
            game = await session.scalar(
                select(CasualGameSession)
                .where(CasualGameSession.active_key == active_key)
                .with_for_update()
            )
            if not game:
                return False
            game.status = "cancelled"
            game.active_key = None
            return True

    async def leaderboard(self, chat_id: int, game_type: str, limit: int = 10):
        async with self.sessions() as session:
            result = await session.execute(
                select(CasualGameScore.user_id, func.sum(CasualGameScore.points))
                .where(
                    CasualGameScore.chat_id == chat_id,
                    CasualGameScore.game_type == game_type,
                )
                .group_by(CasualGameScore.user_id)
                .order_by(func.sum(CasualGameScore.points).desc(), CasualGameScore.user_id)
                .limit(limit)
            )
            return [(user_id, int(points)) for user_id, points in result]


def hangman_transition(letter: str) -> Transition:
    value = letter.casefold()
    if len(value) != 1 or not value.isalpha():
        raise CasualGameError("Enter exactly one letter from A to Z.")

    def apply(state: dict[str, object]) -> CasualTurn:
        answer = str(state["answer"])
        guessed = set(state["guessed"])
        misses = set(state["misses"])
        if value in guessed or value in misses:
            raise CasualGameError("You already guessed that letter.")
        if value in answer:
            guessed.add(value)
            status = "won" if set(answer) <= guessed else "active"
            points = max(10, 60 - 8 * len(misses)) if status == "won" else 0
        else:
            misses.add(value)
            status = "lost" if len(misses) >= 6 else "active"
            points = 0
        updated = {**state, "guessed": sorted(guessed), "misses": sorted(misses)}
        return CasualTurn(status, updated, points)

    return apply


def scramble_transition(guess: str) -> Transition:
    value = guess.strip().casefold()
    if not value.isalpha() or not 3 <= len(value) <= 12:
        raise CasualGameError("Enter a word using 3-12 letters.")

    def apply(state: dict[str, object]) -> CasualTurn:
        if value == state["answer"]:
            attempts = int(state["attempts"]) + 1
            return CasualTurn(
                "won", {**state, "attempts": attempts}, max(10, 40 - 10 * (attempts - 1))
            )
        attempts = int(state["attempts"]) + 1
        status = "lost" if attempts >= 3 else "active"
        return CasualTurn(status, {**state, "attempts": attempts})

    return apply


def anagram_transition(guess: str) -> Transition:
    value = guess.strip().casefold()
    if not value.isalpha() or not 3 <= len(value) <= 12:
        raise CasualGameError("Enter a word using 3-12 letters.")

    def apply(state: dict[str, object]) -> CasualTurn:
        correct = value == state["answer"]
        status = "won" if correct else "lost"
        return CasualTurn(status, {**state, "attempts": 1}, 25 if correct else 0)

    return apply


def number_transition(guess: int) -> Transition:
    if not 1 <= guess <= 100:
        raise CasualGameError("Guess a number from 1 to 100.")

    def apply(state: dict[str, object]) -> CasualTurn:
        attempts = int(state["attempts"]) + 1
        answer = int(state["answer"])
        status = "won" if guess == answer else "lost" if attempts >= 7 else "active"
        updated = {
            **state,
            "attempts": attempts,
            "last_hint": "higher" if guess < answer else "lower",
        }
        points = max(10, 80 - 10 * (attempts - 1)) if status == "won" else 0
        return CasualTurn(status, updated, points)

    return apply


def question_transition(answer: str) -> Transition:
    value = answer.strip().casefold()
    if not value or len(value) > 32:
        raise CasualGameError("Answer must be 1-32 characters.")

    def apply(state: dict[str, object]) -> CasualTurn:
        attempts = int(state["attempts"]) + 1
        correct = value == str(state["answer"]).casefold()
        status = (
            "won" if correct else "lost" if attempts >= int(state["max_attempts"]) else "active"
        )
        points = max(10, 30 - 10 * (attempts - 1)) if correct else 0
        return CasualTurn(status, {**state, "attempts": attempts}, points)

    return apply


def rps_transition(guess: str) -> Transition:
    value = guess.strip().casefold()
    if value not in {"rock", "paper", "scissors"}:
        raise CasualGameError("Choose rock, paper, or scissors.")

    def apply(state: dict[str, object]) -> CasualTurn:
        answer = str(state["answer"])
        if value == answer:
            return CasualTurn("tied", {**state, "player_choice": value}, 0)
        wins = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
        won = wins[value] == answer
        return CasualTurn(
            "won" if won else "lost",
            {**state, "player_choice": value},
            1 if won else 0,
        )

    return apply


def tictactoe_transition(position: int) -> Transition:
    if not 1 <= position <= 9:
        raise CasualGameError("Choose a board position from 1 to 9.")

    def apply(state: dict[str, object]) -> CasualTurn:
        board = list(state["board"])
        index = position - 1
        if board[index] != " ":
            raise CasualGameError("That square is already occupied.")
        board[index] = "X"
        if _winner(board, "X"):
            return CasualTurn("won", {**state, "board": board}, 10)
        if " " not in board:
            return CasualTurn("tied", {**state, "board": board})
        move = _best_tictactoe_move(board)
        board[move] = "O"
        if _winner(board, "O"):
            status = "lost"
        elif " " not in board:
            status = "tied"
        else:
            status = "active"
        return CasualTurn(status, {**state, "board": board})

    return apply


def wordchain_transition(word: str) -> Transition:
    from app.modules.games.wordseek import is_valid_word

    value = word.strip().casefold()
    if not is_valid_word(value, len(value)):
        raise CasualGameError("Use a valid 4-, 5-, or 6-letter word from XYRON's word list.")

    def apply(state: dict[str, object]) -> CasualTurn:
        previous = str(state["last_word"])
        used = set(state["used"])
        if value in used:
            raise CasualGameError("That word has already been used in this chain.")
        if value[0] != previous[-1]:
            raise CasualGameError(f"Your word must start with '{previous[-1]}'.")
        used.add(value)
        links = int(state["links"]) + 1
        status = "won" if links >= 10 else "active"
        points = 50 if status == "won" else 0
        return CasualTurn(
            status,
            {"last_word": value, "used": sorted(used), "links": links},
            points,
        )

    return apply


def teamword_transition(guess: str, user_id: int) -> Transition:
    from app.modules.games.wordseek import is_valid_word, score_guess

    value = guess.strip().casefold()
    if len(value) != 5 or not value.isalpha() or not is_valid_word(value, 5):
        raise CasualGameError("Enter a valid five-letter word from XYRON's word list.")

    def apply(state: dict[str, object]) -> CasualTurn:
        guesses = list(state["guesses"])
        if value in guesses:
            raise CasualGameError("The team already tried that word.")
        guesses.append(value)
        contributors = set(state["contributors"])
        contributors.add(user_id)
        answer = str(state["answer"])
        status = "won" if value == answer else "lost" if len(guesses) >= 8 else "active"
        points = 50 if status == "won" else 0
        return CasualTurn(
            status,
            {
                **state,
                "guesses": guesses,
                "contributors": sorted(contributors),
                "feedback": score_guess(answer, value),
            },
            points,
        )

    return apply


def _winner(board: list[str], mark: str) -> bool:
    lines = (
        (0, 1, 2),
        (3, 4, 5),
        (6, 7, 8),
        (0, 3, 6),
        (1, 4, 7),
        (2, 5, 8),
        (0, 4, 8),
        (2, 4, 6),
    )
    return any(all(board[index] == mark for index in line) for line in lines)


def _best_tictactoe_move(board: list[str]) -> int:
    def evaluate(position: list[str], maximizing: bool, depth: int) -> int:
        if _winner(position, "O"):
            return 10 - depth
        if _winner(position, "X"):
            return depth - 10
        if " " not in position:
            return 0
        scores = []
        for index, mark in enumerate(position):
            if mark == " ":
                position[index] = "O" if maximizing else "X"
                scores.append(evaluate(position, not maximizing, depth + 1))
                position[index] = " "
        return max(scores) if maximizing else min(scores)

    best_move, best_score = -1, -11
    for index, mark in enumerate(board):
        if mark == " ":
            board[index] = "O"
            score = evaluate(board, False, 0)
            board[index] = " "
            if score > best_score:
                best_move, best_score = index, score
    return best_move
