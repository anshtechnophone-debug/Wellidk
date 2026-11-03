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

_EXTRA_WORDS = {
    4: """acid aged aide aims airy alas ally amid apex arch aria aunt auto axis bald balm band bang bark barn bead beam bean bear beat beef beer bell bend bent bias bile bind bite bleak? blob blue blur boil bolt bomb bond bone book boom boot bore boss both bowl bulk bull bump burn burst? bush bust busy byte cafe cage cake calf call calm came camp cane cape card care cargo? case cash cast cave cell chat chef chew chic chin chip chop cite city clad clam clan clap clay clip clog club clue coal coast? coat code coil coin cold come cone cook cool cope copy cord core cork corn cost cozy crab crew crib crop crow cube cuff cult curb cure curl cute dale dark dart dash data date dawn days daze dead deaf deal dear debt deck deed deem deep deer deny desk dial dice diet dime dine ding dirt disc dish disk dive dock does doll dome done doom door dose down drag draw drew drip drop drum dual duck dull dump dune dusk dust duty each earn ease east easy echo edge edit else envy epic even ever exam exit face fact fade fail fair fall fame fang fare farm fast fate fear feed feel feet fell felt fend fern file fill film find fine fire firm fish fist five flag flap flat flaw flee flew flip flow foam fold folk food fool foot fork form fort four free frog from fuel full fund fuse gain gale game gang gap? gate gave gaze gear geek gene gift girl give glad glow glue goal goat goes gold golf gone good gray grew grid grim grin grip grow gulf guru hack hail hair half hall halt hand hang hard harm harp hate haul have head heal heap hear heat heel held hell help herd here hero hide high hill hint hire hold hole holy home hood hook hope horn host hour huge hung hunt hurt idea idol inch info into iron item jade jail jazz join joke jolt jump just keen keep kept kick kids kind king kiss kite knee knew knit knob knot know lace lack lady laid lake lamb lame lamp land lane last late lazy lead leaf leak lean leap left lend lens less liar lice lick life lift like limb lime limp line link lion lips list live load loaf loan lock logo lone long look loop lord lose loss lost love luck lump lung lure lush made mail main make male mall many map? mare mark mask mass mast mate math maze meal mean meat meet melt memo menu mere mesh mess mild mile milk mill mime mind mine mint miss mist mode mold mole monk mood moon moor more most moth move much mule must myth name navy near neat neck need nest news next nice nine node none noon nose note noun numb obey odds okay once only open oral oven over pace pack page paid pain pair pale palm park part pass past path peak pear peel peer pelt pick pile pill pine pink pipe plan play plea plot plug plum plus poem poet pole poll pond pool poor port pose post pull pulp pump pure push quit quiz race rail rain rake rank rare rate read real rear reef rent rest rice rich ride ring rinse riot rise risk road roam roar rock role roll roof room root rope rose rule rush rust safe sage said sail sake sale salt same sand sane sang save scan scar seat seed seek seem seen self sell send sent sets shed sheep? ship shoe shop shot show shut sick side sift sign silk sill sine sing sink site size skin skip slab slam slap sled slim slip slow slug snap snow soak soap soar sock soda sofa soft soil sold sole some song soon soot sort soul soup sour span spar spec sped spin spit spot star stay stem step stir stop such suit sure swan swim sync taco tail take tale talk tall tame tank tape task team tear tell tend tent term test text than that them then they thin this thus tick tide tidy tile till tilt time tiny tire told toll tone took tool top? torn toss tour town trap tree trek trim trip true tube tuck tuna tune turn twin type ugly undo unit upon urge used user vain vale vary vast veil vein very vial vice view visa void vote wage wait wake walk wall wand want ward warm warn warp wash wave ways weak wear weed week weep well went were west what when whip wide wife wild will wilt wind wine wing wink wipe wire wise wish with wolf wood wool word wore work worm wrap wreck wren yard yarn year yell yoga zero zone""".split(),
    5: """abide abode abort abuse actor acute adapt adept admit adopt adore adult after again agent agile aging agony agree ahead alarm album alert algae alike alive allow alloy alone along alter amend ample angel anger angle angry angst anime ankle annoy apart apple apply arena argue arise armed armor aroma array arrow aside asset atlas audio audit avoid awake award aware awful badly baker basic basil batch beach beard beast began begin being below bench berry birth black blade blame blank blast blaze bleak blend bless blind blink bliss block blond blood bloom blown board boast bonus boost booth bound brain brake brand brave bread break brick bride brief bring broad broke brook brown brush build built bunch buyer cable cabin camel candy canon cargo carry carve catch cater cause cease chain chair chalk charm chart chase cheap cheat check cheek cheer chess chest chief child chill china choir choke chord chose civic civil claim clamp class clean clear clerk click cliff climb cling clinic clock clone close cloth cloud clown coach coast coral couch could count court cover crack craft crash cream creek crime crisp cross crowd crown crude crush crust curve cycle daily dairy daisy dance dated deals dealt death debut decay delay depth devil diary digit dirty doubt dozen draft drama dream dress dried drift drill drink drive drove drunk eager early earth easel eaten eight elbow elder elect elite empty enact enemy enjoy enter entry equal error essay event every exact exist extra faith false fancy fault favor feast fence fetch fever fiber field fifth fifty fight final first fixed flame flash fleet flesh flick flies float floor flour fluid flush focus force forge forth forty forum found frame frank fraud freak fresh front frost fruit fully funny fuzzy giant given glass glaze globe glory going grace grade grain grand grant grape graph grasp grass great green greet grief grill grind gross group grown guard guess guest guide habit happy harsh haste hasty haunt heart heavy hello hence hobby honey honor horse hotel house human humor ideal image imply inbox index inner input issue ivory jolly joint judge juice juicy jumbo karma kayak kneel knife knock known label labor large laser later laugh layer learn lease least leave legal leery lemon level lever light limit linen liver local logic loose lower loyal lucky lunch lyric magic major maker march marry match maybe mayor medal media mercy merge merit merry metal meter might minor minus mixed model money month moral motor mount mouse mouth movie music naive naked nerve never newly night noble noise north novel nurse occur ocean offer often onion opera orbit order other ought outer owner oxide paint panel panic paper party peace pearl phase phone photo piano piece pilot pinch pitch place plain plane plant plate plead plenty point polar pound power press price pride prime print prior prize proof proud prove queen query quest quick quiet quite quote radar radio raise range rapid ratio reach react ready realm rebel refer relax relay reply reset retro rhyme rider right rigid rival river robot rocky roman rough round route royal ruler rural sad? saint salad salon sandy scale scene scent scope score scout scrap screw serve seven shade shadow shaft shake shall shame shape share sharp sheep sheer sheet shelf shell shift shine shiny shirt shock shoes shoot shore short shout shown sight sigma since sixth sixty sized skill skirt skull slave sleep slice slide slight slime sling slip? slope small smart smell smile smoke snack snake sneak sober solar solid solve sorry sound south space spare speak speed spell spend spent spice spicy spike spill spin split spoke spoon sport spray squad staff stage stake stamp stand stare stark start state steam steel steep steer stick stiff still stock stone stood stool store storm story strip stuck study stuff style sugar suite sunny super sweet table taken taste teach tears teeth terms thank their theme there these thick thief thing think third those three threw throw thumb tight timer times tired title today topic total touch tough tower toxic trace track trade train treat trend trial tribe trick tried trips truly trust truth twice uncle under union unity until upper upset urban usage usual valid value vapor video vigil villa vinyl viral virus visit vital vivid vocal voice voter waist waste watch water weary weave wedge weird whale wheat wheel where which while white whole whose widow width wield winds wince winer? wings winner wiser witch woman women world worry worse worst worth would wound woven wreck wrist write wrong yacht yearn yeast yield young youth""".split(),
    6: """abroad accept access accuse across acting action active actual adapt? admire advice advise affect afford afraid agency agenda almost always amount anchor animal annual answer anyone anyhow appeal appear arcade argued arrive artist aspect assess assign assist assume assure attack attend august author autumn avenue backup barely basket battle beacon become before behalf behave belief belong beloved better beyond bicycle bishop bitter blanket blossom blouse board? border bottle bottom branch brandy breath breeze bridge bright broken broker budget buffer burden button camera camp? cancel cancer candle cannot canvas canyon career carpet carrot castle casual caught center chance change charge cheese cherry chest? choice choose chrome church circle circus claims clarify client climate closed closet coffee collect colony color? combine comedy comfort comic common comply compose concert conduct confirm connect consist consume contact contain content contest control convert cookie copper corner costly cotton county couple course cousin cover? create credit crisis critic custom cycle danger debate decade decide decline decode defend degree delay? demand depend depict design desire desktop detail device differ dinner direct doctor dollar domain donate double dozen? driven during easily eating editor effect effort either eleven emerge empire employ enable ending energy engage engine enough enrich ensure entire entity entry? escape estate ethics ethnic except excess excite excuse expand expect expert export extend extent fabric factor failed fairly family famous farmer father fellow female figure filing finger finish fiscal fitness flower follow foolish forest forget formal format former fossil foster friend frozen future gadget galaxy garden gather gender gentle gifted global golden gossip govern grab? ground growth guitar guilty handle happen harbor hardly health height helmet hidden highly honest horror hostel impact impose income indeed infant inform inhale inherit inside insist intend invest invite island itself jacket junior keeper kernel kitchen kitten latest launch lawyer leader league length lesson letter likely listen little locate lonely lovely lumber luxury machine mainly manner manual margin marine market master matter mature medium member memory mental mentor merely method middle minute mirror mobile modern modest modify moment monday monkey mother motion museum mutual nation native nature nearby nearly needle nephew nickel nobody normal notice number? object obtain occupy occur? office online option orange orbit? origin output oxygen parent people period permit person phrase picnic planet player please pocket poetry police policy polish polite postal powder pretty prince prison profit proper public pursue puzzle random rarely rather reader reason recent record reduce reform regard region relate relief remain remark remedy remind remote remove repair repeat replace reply report rescue resist result retail return reveal review rhythm safety sample satisfy school screen search season second secret secure select senior series server settle severe shadow? shield signal silver simple simply single sister slowly smooth social source speech sphere spider spirit spoken spring square stable stadium staff? status steady stolen street stress strict string strive strong submit sudden suffer summer summit supply surely survey switch symbol system talent target taught tenant tender theory thirty though thread threat ticket timely tissue tomato tomorrow? toward travel treaty twelve unable unique united unless unlike unlock update useful valley vanish velvet vendor verify version victim viewer village violent vision visual volume wallet wander wealth weapon? weekly weight window winner winter wisdom wonder wooden worker writer yellow""".split(),
}

_EXTRA_WORDS[4] = tuple([*_EXTRA_WORDS[4], *"""acorn acre aged amid anew ante arch area arms army atom aunt aura avid axis babe back bald balm band bang barn bass bead beak beam bean beef beer bend bent bias bike bill bind bite blow blur bold bolt bomb bond boom boot bore bout brag bran brat brew brow bulk bump bunk burr bush bust buzz cafe cage calf cane cape cart cave cent chew chic chin chip chop clad clam clap claw clip clog clue coil coin cola colt comb cone cope copy cord core cork corn cove cram crew crib crop cube cuff cult curb curl cute dame damp dare dart dash daze dean debt deem deer deny dial dice dime dine ding disc disk dive dock does doll dome done doom dose dove drag drew drip dual duck dull dump dune dusk dust echo edit envy epic exit fade fail fame fang fare fear feat fend fern flag flap flaw flee flip foam fold ford fort frog fuse gape gang gasp geek gear gaze glow goal goat grab gray grid grim grin guru hail halt heal heap heel hell herd hide hire hood hook horn jade jazz join jolt jump keen kiss kite knee knit knob knot lace lamb lame lamp lens liar lice lick limb limp lips loaf logo loop loss lump lung lure mast maze memo mesh mill mime mint mist mold mole monk moor moth mule myth nest noon noun numb odds pave pear peel peer pelt pick pile pill pine plea plot plug plum plus poet poll pond pulp pump quit quiz rake rank reef roam roar roof rope rush rust sage scan scar shed sift silk sill sing sink slab slam sled slim slip slug snap soak soap soar sock soda sofa soot soup sour span spar sped spin spit stem stir swan swim sync taco tail tame tear tent tick tilt toll toss trek trim trip tube tuck tuna twin vale vary veil vein vial vice view void vote wage wand ward warn warp weep whip wilt wink wire wolf wool wore wreck wren yarn yoga""".split()])
_EXTRA_WORDS[5] = tuple([*_EXTRA_WORDS[5], *"""aback abase abbey abbot abide abort abyss acorn acted adorn aisle algae alias allay alley allot aloud alpha altar amiss amuse ankle antic apron arose attic bacon badge bagel balmy banal banjo basin bathe beady berth biker binge blade bland bleed blink bliss boast brace brake brisk brute cacao cache cacti cadre cameo canny canoe caper cedar champ chaos chime chunk clang clash cleat cling clink cloak clout cobra cocoa colon comet comma conch condo cough coupe covet crack crate crave crawl crazy credo creep crest cried crook crude cruel crumb cubic cumin curly curry curse daisy decor decoy defer deity delta demon denim dense depot derby deter diner disco ditch ditto dizzy dodge donor dryer eagle easel eater ebony edict eerie elude email ember emcee endow ennui equip erase erupt ethic evade exalt excel exert exile expel fable facet faint fairy fatal favor ferry fewer fiend filet finch flare flask flesh flick fling flock flute foggy forge freak frisk fungi gamut gamma gamer gauge gaunt gauze gazer gecko geeky genie genre ghost glaze gleam glide gloom glove gnash gnome hardy haven havoc hazel heath hedge heron hinge hobby hound humid idiom incur inept inert infer intro ionic irony jazzy jelly jewel jiffy joist joker joust kebab khaki koala laden lager lance lapel larva latch latte leach leafy leaky leash lemur lilac liner lipid liter llama lodge login lorry lotus lunar macro madam magma mambo manga mango manor maple marsh mason matey metro micro midst milky mimic miner mixer modem mogul moist molar moody mower mulch mummy mural nanny nasal nasty natal naval needy niche ninja ninth noisy nosey notch octal olive omega onset opine optic ounce ozone pansy parka pasta paste patio peach pecan pedal penal penny peril pinch pixel plaid pluck plumb plume plump plush poise poker polka porch posit proxy pudgy puree purge rabbi rabid racer rally ranch raven recap regal rehab reign renew repay rerun resin retry ridge rifle rigor ripen risen rivet rodeo roomy roost rotor rover rubric rugby rumor salsa satin sauce scalp scarf scary scold scoop scorn scrub seedy setup sewer shady shard shark shave shawl shear sheen shook shred shrub shrug siege silly sinus skate slain slang slant slate slick slight slink sloth slump smash smirk snare snarl sniff snoop snore snowy sonic spear spiny spoil spoof spout spree sprig squat staid stain stair stale stalk stall stamp stark stave stead steak steep stern sting stink stint stoic stoke stool stoop stout stove strap straw stray strut stump sulky surge sushi swami swamp swarm swash swath swear sweep swept swift swipe swirl swish sword sworn taboo tacit tally talon tango tangy tapir tasty tease tempo tempt tenet tenor tense tenth tepid terra terse testy theta thigh thong thorn thump tiara tidal tiger timid tipsy titan toast token tonal tonic tooth towel tract trail trait tramp trash tripe trite troll troop trout trove trump trunk tuber tumor tummy tuple tutor twine twist ultra umber undue unfit unify unite utter vague valet valor vault vegan venue verge verse vigil villa vinyl viola vowel waive weary whisk widen windy winch witty yacht yearn""".split()])
_EXTRA_WORDS[6] = tuple([*_EXTRA_WORDS[6], *"""abduct absorb absurd accent accord accrue achieve acumen advance advent aerial alight almond alpine anchor ardent argent armada around arrest ascend astute atomic attach awaken babble badger bedlam behold benefit berate bidder bikini blight blonde breach broker bronze browse bypass cabana campus canary carbon carton casino cavern chapel charter checkup cherish chisel clever coffin collar column combat coming concur confer confess conifer console context crochet cruise crumble crunch cutter damage daring deduct deploy desert detect disclose discuss display divide drawer driver evolve granite grapple graphic gravity humble hunter hybrid jagged jargon jersey jigsaw jingle kettle kidney kindly magnet nimble object occupy number oxygen permit polish profit remedy reveal secure shadow shield simply smooth sphere spider spirit spoken stable stadium stolen summit survey symbol tenant tender threat timber timely tissue tomato unlock vanish velvet vendor verify version viewer village wallet wander wisdom""".split()])

for length, words in _EXTRA_WORDS.items():
    _WORDS[length] = tuple(dict.fromkeys([*_WORDS[length], *words]))
_VALID_WORDS = {length: {word for word in words if len(word) == length and word.isalpha()} for length, words in _WORDS.items()}
MAX_GUESSES = 30
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
    guesses: tuple[str, ...] = ()
    feedbacks: tuple[str, ...] = ()


def is_valid_word(word: str, length: int) -> bool:
    return word.casefold() in _VALID_WORDS.get(length, set())


def score_guess(answer: str, guess: str) -> str:
    answer = answer.casefold()
    guess = guess.casefold()
    feedback = ["🟥"] * len(answer)
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
        active_key = f"{chat_id}:wordseek"
        async with self.sessions.begin() as session:
            active = await session.scalar(
                select(WordGameSession)
                .where(
                    WordGameSession.active_key == active_key,
                )
                .with_for_update()
            )
            if active and active.expires_at > current:
                raise WordGameError("A Word Seek game is already active in this chat. Everyone can join by guessing!")
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
                .where(WordGameSession.active_key == f"{chat_id}:wordseek")
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
                    guesses=tuple(game.guesses),
                    feedbacks=tuple(score_guess(game.answer, item) for item in game.guesses),
                )
        if expired:
            raise WordGameError("That Word Seek game expired. Start a new one with /wordseek.")
        return result

    async def cancel(self, chat_id: int, user_id: int) -> bool:
        async with self.sessions.begin() as session:
            game = await session.scalar(
                select(WordGameSession)
                .where(WordGameSession.active_key == f"{chat_id}:wordseek")
                .with_for_update()
            )
            if not game or game.user_id != user_id:
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
