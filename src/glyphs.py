"""Static catalogue of characters for the "Symbols & Emoji" picker window.

This is the long tail behind the keyboard's own symbol layer. That layer
(``?123`` / ``sym2`` on the compact layouts, and the punctuation row on the
full-size ones) holds roughly 34 glyphs, each worth a single click. Everything
past that lives here instead: typographic punctuation, math and currency
signs, accented Latin letters an English keyboard cannot reach, and the
common emoji ranges. `keyboard_bridge.py` hands `categories()` to QML for a
tabbed picker; nothing here is loaded lazily or cached, because the whole
module is a few hundred short strings.

No I/O, no persistence, no logging, no Qt imports. Every other data file in
this project (the n-gram model, snippets, packs, analytics) is user-writable
and therefore carries load-time size caps and validation; this module is
none of that. It is baked into the source tree, never touched at runtime,
so it needs no caps and no defensive parsing.

Two rendering rules shape every glyph below:

- **No skin-tone modifiers (U+1F3FB..U+1F3FF) and no ZWJ sequences
  (U+200D).** A ZWJ sequence the host font does not compose (a family, a
  couple, a flag built from regional indicators) renders as two or three
  separate glyphs crammed into a single 48px picker cell, which is worse
  than not offering it. A skin tone the user did not choose is worse than
  none. Every emoji here is a single base code point, optionally followed
  by variation selector-16.
- **Variation selector-16 (U+FE0F) is added explicitly to any pictograph
  whose default presentation is text rather than emoji** (the classic
  cases: heart U+2764, the heavy check mark U+2714, warning U+26A0). Left
  off, those render as a flat monochrome glyph sitting beside a row of
  colourful ones. The five typographic categories (`text`, `arrows`,
  `math`, `money`, `accents`) are deliberately exempted: those glyphs are
  meant to read as plain text symbols, matching the punctuation and
  currency signs beside them, not as colour emoji.

Every VS16 addition is written explicitly in the source as the base
character literally followed by the ``"\\ufe0f"`` escape, so a reader can
see the selector is there rather than hunting for an invisible code point.
"""

from __future__ import annotations

from typing import NamedTuple


class GlyphCategory(NamedTuple):
    """One tab in the picker: a stable id, a short label, and its glyphs."""

    id: str  # stable slug, [a-z_]+
    label: str  # short tab label shown in the UI, 1 word where possible
    glyphs: tuple[str, ...]


# Typographic punctuation not on the base keyboard: dashes, quotation marks,
# reference marks, and a handful of legacy symbols (section, pilcrow, the
# copyright/registered/trademark trio, per mille, the inverted Spanish
# punctuation, and the numero sign).
_TEXT: tuple[str, ...] = (
    "–",  # en dash
    "—",  # em dash
    "…",  # horizontal ellipsis
    "‘",  # left single quotation mark
    "’",  # right single quotation mark
    "“",  # left double quotation mark
    "”",  # right double quotation mark
    "‹",  # single left-pointing angle quotation mark
    "›",  # single right-pointing angle quotation mark
    "«",  # left-pointing double angle quotation mark
    "»",  # right-pointing double angle quotation mark
    "†",  # dagger
    "‡",  # double dagger
    "•",  # bullet
    "·",  # middle dot
    "§",  # section sign
    "¶",  # pilcrow
    "©",  # copyright
    "®",  # registered
    "™",  # trademark
    "‰",  # per mille
    "¿",  # inverted question mark
    "¡",  # inverted exclamation mark
    "№",  # numero sign
)

# Directional and block arrows. Kept as plain text glyphs (no VS16) so they
# read as symbols, matching the rest of the typographic categories.
_ARROWS: tuple[str, ...] = (
    "←",  # leftwards arrow
    "→",  # rightwards arrow
    "↑",  # upwards arrow
    "↓",  # downwards arrow
    "↖",  # north west arrow
    "↗",  # north east arrow
    "↘",  # south east arrow
    "↙",  # south west arrow
    "↔",  # left right arrow
    "↕",  # up down arrow
    "⇒",  # rightwards double arrow
    "⇐",  # leftwards double arrow
    "↩",  # leftwards arrow with hook (curved return)
    "⬅",  # black leftwards arrow
    "⬆",  # black upwards arrow
    "⬇",  # black downwards arrow
    "➡",  # black rightwards arrow
)

# Arithmetic, comparison, calculus and set notation, plus the superscript
# digits and vulgar fractions a word processor's autocorrect would normally
# supply.
_MATH: tuple[str, ...] = (
    "×",  # multiplication sign
    "÷",  # division sign
    "±",  # plus-minus sign
    "−",  # minus sign
    "≠",  # not equal to
    "≈",  # almost equal to
    "≡",  # identical to
    "≤",  # less-than or equal to
    "≥",  # greater-than or equal to
    "∞",  # infinity
    "√",  # square root
    "π",  # greek small letter pi
    "µ",  # micro sign
    "°",  # degree sign
    "′",  # prime
    "″",  # double prime
    "∑",  # n-ary summation
    "∏",  # n-ary product
    "∫",  # integral
    "∂",  # partial differential
    "Δ",  # greek capital delta
    "∈",  # element of
    "²",  # superscript two
    "³",  # superscript three
    "½",  # vulgar fraction one half
    "¼",  # vulgar fraction one quarter
    "¾",  # vulgar fraction three quarters
    "⅓",  # vulgar fraction one third
    "⅔",  # vulgar fraction two thirds
)

# World currency symbols. The generic currency sign closes out the set for
# the currency nobody named.
_MONEY: tuple[str, ...] = (
    "€",  # euro sign
    "£",  # pound sign
    "¥",  # yen sign
    "¢",  # cent sign
    "₹",  # indian rupee sign
    "₩",  # won sign
    "₽",  # ruble sign
    "₺",  # lira sign
    "₦",  # naira sign
    "₱",  # peso sign
    "₴",  # hryvnia sign
    "₪",  # shekel sign
    "₫",  # dong sign
    "฿",  # baht sign
    "₸",  # tenge sign
    "₿",  # bitcoin sign
    "¤",  # generic currency sign
)

# Accented Latin letters an English QWERTY layout cannot reach directly.
# Lowercase only: uppercase would double the count for little gain, and the
# picker's own Recent tab covers the rare uppercase need.
_ACCENTS: tuple[str, ...] = (
    "à",  # a grave
    "á",  # a acute
    "â",  # a circumflex
    "ã",  # a tilde
    "ä",  # a diaeresis
    "å",  # a ring above
    "æ",  # ae ligature
    "ç",  # c cedilla
    "è",  # e grave
    "é",  # e acute
    "ê",  # e circumflex
    "ë",  # e diaeresis
    "ì",  # i grave
    "í",  # i acute
    "î",  # i circumflex
    "ï",  # i diaeresis
    "ò",  # o grave
    "ó",  # o acute
    "ô",  # o circumflex
    "ö",  # o diaeresis
    "ù",  # u grave
    "ú",  # u acute
    "û",  # u circumflex
    "ü",  # u diaeresis
    "ñ",  # n tilde
    "ø",  # o slash
    "œ",  # oe ligature
    "ÿ",  # y diaeresis
    "ß",  # eszett
    "þ",  # thorn
    "ð",  # eth
)

# Common face emoji, from plain grins through to the "unwell" family. All of
# these have default emoji presentation already, so none need VS16.
_FACES: tuple[str, ...] = (
    "😀",  # grinning face
    "😃",  # grinning face with big eyes
    "😄",  # grinning face with smiling eyes
    "😁",  # beaming face with smiling eyes
    "😆",  # grinning squinting face
    "😅",  # grinning face with sweat
    "🤣",  # rolling on the floor laughing
    "😂",  # face with tears of joy
    "🙂",  # slightly smiling face
    "🙃",  # upside-down face
    "😉",  # winking face
    "😊",  # smiling face with smiling eyes
    "😇",  # smiling face with halo
    "🥰",  # smiling face with hearts
    "😍",  # heart eyes
    "🤩",  # star-struck
    "😘",  # face blowing a kiss
    "😗",  # kissing face
    "😚",  # kissing face with closed eyes
    "😙",  # kissing face with smiling eyes
    "😋",  # face savoring food
    "😛",  # face with tongue
    "😜",  # winking face with tongue
    "🤪",  # zany face
    "😝",  # squinting face with tongue
    "🤑",  # money-mouth face
    "🤗",  # hugging face
    "🤭",  # face with hand over mouth
    "🤫",  # shushing face
    "🤔",  # thinking face
    "🤐",  # zipper-mouth face
    "🤨",  # face with raised eyebrow
    "😐",  # neutral face
    "😑",  # expressionless face
    "😶",  # face without mouth
    "😏",  # smirking face
    "😒",  # unamused face
    "🙄",  # face with rolling eyes
    "😬",  # grimacing face
    "🤥",  # lying face
    "😌",  # relieved face
    "😴",  # sleeping face
    "🤒",  # face with thermometer
    "🥳",  # partying face
    "😎",  # smiling face with sunglasses
    "🤓",  # nerd face
    "🧐",  # face with monocle
    "😢",  # crying face
)

# Hands, gestures and simple people emoji. No skin-tone modifiers, so these
# render in the default cartoon-yellow tone. Four of them (victory hand,
# index pointing up, hand with fingers splayed, writing hand) default to a
# text presentation and need VS16 to render as colour emoji.
_GESTURES: tuple[str, ...] = (
    "👍",  # thumbs up
    "👎",  # thumbs down
    "👏",  # clapping hands
    "👋",  # waving hand
    "👌",  # ok hand
    "🙏",  # folded hands
    "💪",  # flexed biceps
    "👆",  # backhand index pointing up
    "👇",  # backhand index pointing down
    "👈",  # backhand index pointing left
    "👉",  # backhand index pointing right
    "✋",  # raised hand
    "✌\ufe0f",  # victory hand
    "☝\ufe0f",  # index pointing up
    "👊",  # oncoming fist
    "✊",  # raised fist
    "🤞",  # crossed fingers
    "🤟",  # love-you gesture
    "🤘",  # sign of the horns
    "🖖",  # vulcan salute
    "🤙",  # call me hand
    "👐",  # open hands
    "🤲",  # palms up together
    "🤝",  # handshake
    "🙌",  # raising hands
    "🖐\ufe0f",  # hand with fingers splayed
    "✍\ufe0f",  # writing hand
    "🫶",  # heart hands
)

# Animals, plants, weather and sky. Sun and snowflake default to a text
# presentation and need VS16.
_NATURE: tuple[str, ...] = (
    "🐶",  # dog face
    "🐱",  # cat face
    "🐭",  # mouse face
    "🐹",  # hamster
    "🐰",  # rabbit face
    "🦊",  # fox
    "🐻",  # bear
    "🐼",  # panda
    "🐨",  # koala
    "🐯",  # tiger face
    "🦁",  # lion
    "🐮",  # cow face
    "🐷",  # pig face
    "🐸",  # frog
    "🐵",  # monkey face
    "🐔",  # chicken
    "🐧",  # penguin
    "🦆",  # duck
    "🦉",  # owl
    "🐺",  # wolf
    "🐴",  # horse face
    "🦄",  # unicorn
    "🐝",  # honeybee
    "🦋",  # butterfly
    "🐢",  # turtle
    "🐍",  # snake
    "🐙",  # octopus
    "🐬",  # dolphin
    "🐳",  # spouting whale
    "🐘",  # elephant
    "🌵",  # cactus
    "🌲",  # evergreen tree
    "🌴",  # palm tree
    "🌸",  # cherry blossom
    "🌈",  # rainbow
    "☀\ufe0f",  # sun
    "🌙",  # crescent moon
    "❄\ufe0f",  # snowflake
)

# Food and drink. Hot beverage already defaults to emoji presentation.
_FOOD: tuple[str, ...] = (
    "🍏",  # green apple
    "🍎",  # red apple
    "🍊",  # tangerine
    "🍋",  # lemon
    "🍌",  # banana
    "🍉",  # watermelon
    "🍇",  # grapes
    "🍓",  # strawberry
    "🍒",  # cherries
    "🍑",  # peach
    "🥭",  # mango
    "🍍",  # pineapple
    "🥥",  # coconut
    "🍅",  # tomato
    "🥑",  # avocado
    "🥦",  # broccoli
    "🌽",  # ear of corn
    "🥕",  # carrot
    "🥐",  # croissant
    "🍞",  # bread
    "🧀",  # cheese wedge
    "🥚",  # egg
    "🍳",  # cooking (fried egg)
    "🥓",  # bacon
    "🍗",  # poultry leg
    "🍔",  # hamburger
    "🍟",  # french fries
    "🍕",  # pizza
    "🌮",  # taco
    "🌯",  # burrito
    "🥗",  # green salad
    "🍿",  # popcorn
    "🍣",  # sushi
    "🍜",  # steaming bowl
    "🍰",  # shortcake
    "🎂",  # birthday cake
    "🍩",  # doughnut
    "☕",  # hot beverage
)

# Vehicles, places, buildings and transport. Motorcycle, airplane, sailboat,
# passenger ship, anchor and snow-capped mountain default to a text
# presentation and need VS16.
_TRAVEL: tuple[str, ...] = (
    "🚗",  # automobile
    "🚕",  # taxi
    "🚙",  # sport utility vehicle
    "🚌",  # bus
    "🚓",  # police car
    "🚑",  # ambulance
    "🚒",  # fire engine
    "🚚",  # delivery truck
    "🚲",  # bicycle
    "🏍\ufe0f",  # motorcycle
    "🛴",  # kick scooter
    "🚂",  # locomotive
    "🚆",  # train
    "🚇",  # metro
    "🚈",  # light rail
    "✈\ufe0f",  # airplane
    "🛫",  # airplane departure
    "🚀",  # rocket
    "🛸",  # flying saucer
    "🚁",  # helicopter
    "⛵\ufe0f",  # sailboat
    "🚤",  # speedboat
    "🛳\ufe0f",  # passenger ship
    "🚢",  # ship
    "⚓\ufe0f",  # anchor
    "🗼",  # tokyo tower
    "🗽",  # statue of liberty
    "🏰",  # castle
    "🎡",  # ferris wheel
    "🎢",  # roller coaster
    "🌋",  # volcano
    "🏔\ufe0f",  # snow-capped mountain
    "🏠",  # house
    "🏢",  # office building
)

# Everyday objects, tools, tech and office items, and timekeeping. Keyboard,
# desktop computer, printer, computer mouse, telephone, mantelpiece clock,
# candle, gear and bed default to a text presentation and need VS16.
_OBJECTS: tuple[str, ...] = (
    "⌚",  # watch
    "📱",  # mobile phone
    "💻",  # laptop
    "⌨\ufe0f",  # keyboard
    "🖥\ufe0f",  # desktop computer
    "🖨\ufe0f",  # printer
    "🖱\ufe0f",  # computer mouse
    "💽",  # computer disk
    "💾",  # floppy disk
    "💿",  # optical disk
    "📷",  # camera
    "📹",  # video camera
    "🎥",  # movie camera
    "📞",  # telephone receiver
    "☎\ufe0f",  # telephone
    "📺",  # television
    "📻",  # radio
    "🧭",  # compass
    "⏰",  # alarm clock
    "🕰\ufe0f",  # mantelpiece clock
    "⌛",  # hourglass done
    "⏳",  # hourglass not done
    "🔋",  # battery
    "🔌",  # electric plug
    "💡",  # light bulb
    "🔦",  # flashlight
    "🕯\ufe0f",  # candle
    "💰",  # money bag
    "💳",  # credit card
    "💎",  # gem stone
    "🔧",  # wrench
    "🔨",  # hammer
    "⚙\ufe0f",  # gear
    "🔩",  # nut and bolt
    "🔫",  # water pistol
    "🔑",  # key
    "🚪",  # door
    "🛏\ufe0f",  # bed
)

# Hearts in several colours, stars, check/cross marks and other pictographic
# marks that do not fit the more specific categories above. Red heart, heavy
# check mark, warning, recycling symbol and heavy heart exclamation default
# to a text presentation and need VS16; star and cross mark are already
# emoji-default.
_SYMBOLS: tuple[str, ...] = (
    "❤\ufe0f",  # red heart
    "🧡",  # orange heart
    "💛",  # yellow heart
    "💚",  # green heart
    "💙",  # blue heart
    "💜",  # purple heart
    "🖤",  # black heart
    "🤍",  # white heart
    "🤎",  # brown heart
    "💔",  # broken heart
    "⭐",  # star (already emoji-default)
    "🌟",  # glowing star
    "✨",  # sparkles
    "✅",  # check mark button
    "✔\ufe0f",  # heavy check mark
    "❌",  # cross mark (already emoji-default)
    "❎",  # cross mark button
    "⚠\ufe0f",  # warning
    "🚫",  # prohibited
    "♻\ufe0f",  # recycling symbol
    "🎵",  # musical note
    "🎶",  # musical notes
    "🎉",  # party popper
    "🎊",  # confetti ball
    "🔥",  # fire
    "💯",  # hundred points
    "💤",  # zzz
    "🔔",  # bell
    "🆗",  # ok button
    "❣\ufe0f",  # heavy heart exclamation
)

GLYPH_CATEGORIES: tuple[GlyphCategory, ...] = (
    GlyphCategory("text", "Text", _TEXT),
    GlyphCategory("arrows", "Arrows", _ARROWS),
    GlyphCategory("math", "Math", _MATH),
    GlyphCategory("money", "Money", _MONEY),
    GlyphCategory("accents", "Accents", _ACCENTS),
    GlyphCategory("faces", "Faces", _FACES),
    GlyphCategory("gestures", "Gestures", _GESTURES),
    GlyphCategory("nature", "Nature", _NATURE),
    GlyphCategory("food", "Food", _FOOD),
    GlyphCategory("travel", "Travel", _TRAVEL),
    GlyphCategory("objects", "Objects", _OBJECTS),
    GlyphCategory("symbols", "Symbols", _SYMBOLS),
)

# Cap the UI applies to its recently-used list.
MAX_RECENT: int = 24


def categories() -> list[dict]:
    """Return the catalogue as plain dicts/lists for the QML picker.

    A `Q_INVOKABLE`/`@Slot` return value has to survive the Qt/QML marshalling
    boundary, and a `NamedTuple` does not: QML sees it as an opaque object
    rather than a JS array/object the way a plain `list`/`dict` is. Shaped as
    ``[{"id": str, "label": str, "glyphs": [str, ...]}, ...]``.
    """
    return [
        {"id": category.id, "label": category.label, "glyphs": list(category.glyphs)}
        for category in GLYPH_CATEGORIES
    ]
