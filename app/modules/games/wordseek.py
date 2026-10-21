"""Local, persistent Word Seek game logic. No AI provider or external word API is used."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import WordGameScore, WordGameSession

_WORDS = {
    4: (
        "able also area bake ball band bank bark base bath bear beat been bell belt "
        "best bird blow blue boat body bone book born both bowl burn busy cake call "
        "calm came camp card care case cash cast cell chat city club coal coat code "
        "cold come cook cool cost dark data date dawn days deal dear deep desk diet "
        "dirt dish door down draw drop drug drum each earn ease east easy edge else "
        "even ever exam face fact fair fall farm fast fate fear feed feel feet fell "
        "felt file fill film find fine fire firm fish five flat flew flow food foot "
        "form four free from fuel full fund game gate gave gear gift girl give glad "
        "goal goes gold golf gone good grew grow hair half hall hand hang hard harm "
        "hate have head hear heat held help here hero high hill hint hold hole holy "
        "home hope host hour huge hung hunt hurt idea inch into iron item jury just "
        "keep kept kick kids kind king knew know lack lady laid lake land lane last "
        "late lawn lazy lead leaf leak lean leap left lend less life lift like lime "
        "line link lion list live load loan lock long look lord lose lost love luck "
        "made mail main make male many mark mask mass mate meal mean meat meet melt "
        "menu mere mess mild mile milk mind mine miss mode mood moon more most move "
        "much must name navy near neat neck need news next nice nine node none nose "
        "note okay once only open oral oven over pace pack page paid pain pair pale "
        "palm park part pass past path peak pick pink pipe plan play poem pole poll "
        "pool poor port pose post pull pure push race rail rain rare rate read real "
        "rear rent rest rice rich ride ring rise risk road rock role roll room root "
        "rose rule safe said sail sake sale salt same sand sane sang save scan seat "
        "seed seek seem seen self sell send sent sets shed ship shoe shop shot show "
        "shut sick side sign silk sine site size skin skip slow snow soft soil sold "
        "sole some song soon sort soul spot star stay step stop such suit sure take "
        "tale tall tank tape task team tell tend term test text than that them then "
        "they thin this thus tide tile time tiny told tone took tool topy torn town "
        "trap true tune turn type unit upon urge used user vain vast very visa wait "
        "wake walk wall want warm wash wave ways weak wear weed week well went were "
        "west what when whom wide wife wild will wind wine wing wise wish with wood "
        "word work worm wrap yard year yell zero zone "
    ).split(),
    5: """
        about above actor adapt admit adopt adult after again agent agree ahead alarm album alert
        alien alike alive allow alone along alter amber among anger angle angry apart apple apply
        arena argue arise armed array aside asset audio audit avoid awake award aware badly baker
        basic beach began begin being below bench berry birth black blame blank blind block blood
        board brain brand brave bread break breed brick brief bring broad broke brown brush build
        built buyer cable carry catch cause chain chair chart chase cheap check cheer chess chest
        chief child china choir chose civil claim class clean clear clerk click clock close coach
        coast could count court cover craft crash cream crime cross crowd crown curve cycle daily
        dance dated deals death debut delay depth doing doubt dozen draft drama dream dress drink
        drive earth eight elite empty enemy enjoy enter entry equal error event every exact exist
        extra faith false fancy fault fiber field fifth fifty fight final first fixed flame flash
        fleet floor fluid focus force forth forty forum found frame frank fraud fresh front fruit
        fully funny giant given glass globe going grace grade grand grant grass great green gross
        group guard guess guest guide habit happy heart heavy hence horse hotel house human ideal
        image index inner input issue joint judge known label labor large later layer learn least
        leave legal lemon level light limit local logic loose lower lucky lunch major maker march
        match maybe mayor meant media metal might minor minus mixed model money month moral motor
        mount mouse mouth movie music needs never newly night noise north novel nurse occur ocean
        offer often order other ought paint panel paper party peace phase phone photo piano piece
        pilot pitch place plain plane plant plate point pound power press price pride prime print
        prior prize proof proud prove queen quick quiet quite radio raise range rapid ratio reach
        react ready realm reply right rival river robot rocky roman rough round route royal rural
        scale scene scope score sense serve seven shall shape share sharp sheet shelf shell shift
        shine shirt shock shoot short shown sight since sixth sixty sized skill sleep slice slide
        small smart smile smoke solid solve sorry sound south space spare speak speed spend spent
        split spoke sport staff stage stake stand start state steam steel stick still stock stone
        stood store storm story strip stuck study stuff style sugar suite super sweet table taken
        taste teach teeth terms thick thing think third those three threw throw tight times tired
        title today topic total touch tough tower track trade train treat trend trial tribe trick
        truly trust truth twice uncle under union unity until upper upset urban usage usual value
        video virus visit vital voice waste watch water wheel where which while white whole whose
        woman women world worry worse worst worth would write wrong yield young youth
    """.split(),
    6: """
        across action active actual admire advice affect afford afraid agency agenda almost
        always amount animal annual answer anyone appeal appear arrive artist aspect assist assume
        attack attend author autumn avenue backup barely basket battle beauty become before behalf
        behave belief belong better beyond bishop border bottle bottom branch breath bridge
        bright broken budget button camera cancel cancer candle cannot canvas career carpet castle
        casual caught center chance change charge choice choose church circle client closed
        coffee common credit custom danger debate decide degree demand
        depend
        design desire detail differ dinner direct doctor dollar domain double driven during easily
        editor effect effort either eleven emerge empire employ enable ending energy engage engine
        enough ensure entire entity escape estate except excite expand expect expert export extend
        fabric factor family famous father fellow female figure finger finish fiscal flower follow
        forest forget formal former fossil friend future garden gather gender gentle global golden
        ground growth handle happen health height honest impact impose income indeed
        inform inside intend island itself jacket junior keeper kernel latest launch lawyer leader
        length lesson letter likely listen little locate lovely making manage manner margin market
        master matter medium member memory mental method middle minute mirror mobile modern modest
        moment mother motion museum mutual nation native nature nearby nobody normal notice obtain
        office online option orange origin output parent people period person phrase picnic planet
        player please pocket police policy postal powder pretty prince prison proper
        public pursue puzzle random rather reader reason recent record reduce reform regard region
        relate relief remain remote remind remove repair repeat report rescue result retail return
        review rhythm safety school screen search season second secret select senior series server
        settle severe signal silver simple single sister slowly smooth social source speech
        spring square stable status steady street stress strict string strong submit sudden suffer
        summer supply surely survey system talent target taught tenant theory thirty though thread
        ticket toward travel treaty twelve unable unique united unless unlike useful valley verify
        victim vision visual volume wealth weekly weight window winner winter wonder
        worker writer yellow
    """.split(),
}

_VALID_WORDS = {length: set(words) for length, words in _WORDS.items()}
MAX_GUESSES = 6
SESSION_TIMEOUT = timedelta(minutes=15)


class WordGameError(ValueError):
    """A game action could not be applied."""


@dataclass(frozen=True, slots=True)
class GuessResult:
    feedback: str
    state: str
    guesses_left: int
    points: int = 0
    answer: str | None = None


def is_valid_word(word: str, length: int) -> bool:
    return word.casefold() in _VALID_WORDS.get(length, set())


def score_guess(answer: str, guess: str) -> str:
    answer = answer.casefold()
    guess = guess.casefold()
    feedback = ["⬛"] * len(answer)
    remaining: dict[str, int] = {}
    for index, (target_letter, guessed_letter) in enumerate(zip(answer, guess, strict=True)):
        if target_letter == guessed_letter:
            feedback[index] = "🟩"
        else:
            remaining[target_letter] = remaining.get(target_letter, 0) + 1
    for index, guessed_letter in enumerate(guess):
        if feedback[index] == "🟩":
            continue
        if remaining.get(guessed_letter, 0) > 0:
            feedback[index] = "🟨"
            remaining[guessed_letter] -= 1
    return "".join(feedback)


class WordSeekRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def start(
        self,
        chat_id: int,
        user_id: int,
        length: int,
        answer: str | None = None,
        now: datetime | None = None,
    ) -> WordGameSession:
        if length not in _VALID_WORDS:
            raise WordGameError("Choose a supported word length: 4, 5, or 6.")
        selected = (answer or random.SystemRandom().choice(sorted(_VALID_WORDS[length]))).casefold()
        if not is_valid_word(selected, length):
            raise WordGameError("The selected answer is outside the valid word list.")
        current = now or datetime.now(UTC)
        active_key = f"{chat_id}:{user_id}:wordseek"
        async with self.sessions.begin() as session:
            active = await session.scalar(
                select(WordGameSession)
                .where(
                    WordGameSession.active_key == active_key,
                )
                .with_for_update()
            )
            if active and active.expires_at > current:
                raise WordGameError("You already have an active Word Seek game in this chat.")
            if active:
                active.state = "expired"
                active.active_key = None
            game = WordGameSession(
                chat_id=chat_id,
                user_id=user_id,
                active_key=active_key,
                word_length=length,
                answer=selected,
                guesses=[],
                max_guesses=MAX_GUESSES,
                state="active",
                points_awarded=False,
                created_at=current,
                expires_at=current + SESSION_TIMEOUT,
            )
            session.add(game)
            await session.flush()
            return game

    async def guess(
        self, chat_id: int, user_id: int, guess: str, now: datetime | None = None
    ) -> GuessResult:
        value = guess.strip().casefold()
        current = now or datetime.now(UTC)
        active_key = f"{chat_id}:{user_id}:wordseek"
        expired = False
        async with self.sessions.begin() as session:
            game = await session.scalar(
                select(WordGameSession)
                .where(WordGameSession.active_key == active_key)
                .with_for_update()
            )
            if game is None:
                raise WordGameError("No active Word Seek game. Start one with /wordseek [4|5|6].")
            if game.expires_at <= current:
                game.state = "expired"
                game.active_key = None
                expired = True
            elif len(value) != game.word_length or not value.isalpha():
                raise WordGameError(f"Enter a {game.word_length}-letter word.")
            elif not is_valid_word(value, game.word_length):
                raise WordGameError("That word is not in XYRON's built-in word list.")
            elif value in game.guesses:
                raise WordGameError("You already tried that word.")
            else:
                game.guesses = [*game.guesses, value]
                feedback = score_guess(game.answer, value)
                points = 0
                if value == game.answer:
                    game.state = "won"
                    points = max(10, 70 - 10 * (len(game.guesses) - 1))
                    if not game.points_awarded:
                        session.add(
                            WordGameScore(
                                session_id=game.id,
                                chat_id=chat_id,
                                user_id=user_id,
                                points=points,
                                created_at=current,
                            )
                        )
                        game.points_awarded = True
                elif len(game.guesses) >= game.max_guesses:
                    game.state = "lost"
                if game.state != "active":
                    game.active_key = None
                result = GuessResult(
                    feedback=feedback,
                    state=game.state,
                    guesses_left=game.max_guesses - len(game.guesses),
                    points=points,
                    answer=game.answer if game.state in {"won", "lost"} else None,
                )
        if expired:
            raise WordGameError("That Word Seek game expired. Start a new one with /wordseek.")
        return result

    async def cancel(self, chat_id: int, user_id: int) -> bool:
        async with self.sessions.begin() as session:
            game = await session.scalar(
                select(WordGameSession)
                .where(WordGameSession.active_key == f"{chat_id}:{user_id}:wordseek")
                .with_for_update()
            )
            if not game:
                return False
            game.state = "cancelled"
            game.active_key = None
            return True

    async def leaderboard(self, chat_id: int, limit: int = 10) -> list[tuple[int, int]]:
        from sqlalchemy import func

        async with self.sessions() as session:
            result = await session.execute(
                select(WordGameScore.user_id, func.sum(WordGameScore.points).label("points"))
                .where(WordGameScore.chat_id == chat_id)
                .group_by(WordGameScore.user_id)
                .order_by(func.sum(WordGameScore.points).desc(), WordGameScore.user_id)
                .limit(limit)
            )
            return [(user_id, int(points)) for user_id, points in result]
