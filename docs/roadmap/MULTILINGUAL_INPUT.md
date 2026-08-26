# Multilingual input: French, Pinyin, Kanji

Research notes, not a commitment. Written 2026-08-25 on
`research/multilingual-input`. Nothing here is implemented.

Scope is **input**: typing French, Chinese and Japanese *into other apps*.
Localising Alpha-OSK's own UI (menus, settings labels, tooltips) is a
separate project with a separate mechanism (Qt Linguist `.ts` files) and is
deliberately out of scope below. They are independent: a French speaker
typing French through an English-labelled keyboard is a working product,
and a translated UI that still predicts English words is not.

---

## The short version

**French is a fortnight of work that mostly consists of un-hardcoding
English. Pinyin is a new subsystem of maybe 3000 lines. Kanji is that
subsystem plus a second one, and it is the only one of the three whose
output you cannot check yourself.**

The recommendation is to do French first even if French is not the goal,
because every one of the ~15 English constants that French forces into a
per-language table is a constant that Pinyin and Kanji would otherwise
each have to fight separately. French is the cheap way to buy the
abstraction the other two need.

Do not attempt all three at once. They share the language-profile layer
and share nothing else: French is a wordlist problem, Pinyin is a
segmentation-plus-lattice problem, Kanji is Pinyin's problem plus
homophone disambiguation.

---

## What already works, and it is more than expected

Four pieces of existing architecture turn out to be the hard parts of an
IME, already built for unrelated reasons.

**Unicode output works end to end on all three platforms today.** There
is no synthesis work for any of these languages.
`WindowsKeySynthesizer.send_text` tries a scancode first and falls back
per character to `KEYEVENTF_UNICODE`, and `_resolve_char_scancode`
returns `None` for anything at or above `U+0080` (`windows.py:937`), so
`é`, `拼`, `漢` already take the Unicode path, and `_make_unicode_events`
already emits UTF-16 surrogate pairs for supplementary characters
(`windows.py:1115`, which covers the rarer CJK extension blocks). macOS
goes through `CGEventKeyboardSetUnicodeString` (`macos.py:448`), which is
the same contract. Linux `xdotool type` handles UTF-8 by remapping a
spare keycode. A French `é` key added to a layout JSON right now would
type correctly with zero Python changes.

**Precomposed characters mean French needs no dead keys.** A physical
AZERTY reaches `ê` through a dead `^` then `e`, two keystrokes and a
mode. Because we inject the codepoint directly, `ê` can be a key. On a
mouse-driven keyboard that halves the click cost of the single most
common French typing complaint. This is a genuine advantage over the
hardware we are emulating, and it is free.

**Layouts are data, auto-discovered.** `_load_layouts` globs
`data/layouts/*.json` (`keyboard_bridge.py:4963`) and
`getAvailableLayouts()` feeds the picker from whatever it found.
`data/layouts/azerty.json` is a new layout with no Python and no QML
change. `tests/test_layouts.py::TestEveryLayout` is parametrised over the
directory, so a new file inherits the structural tests automatically,
while `TestFullSizeLayoutsUnchanged` is pinned to the three existing
names and will not fire on it.

**The prediction bar is already a candidate window, and the token-pill
path is already an IME commit.** This is the important one. A CJK IME
needs to (a) show a ranked candidate list, (b) let the user pick one, and
(c) replace the run of characters they typed with something that is not a
prefix of it. `_insert_token_pill` (`keyboard_bridge.py:3089`) does
exactly (c) already: it computes `head = _raw_token[:len(_raw_token) -
len(typed)]`, decides between a suffix insert and a select-and-overwrite
based on whether the pill continues the typed run, and drives
`replace_text` when it does not. A pinyin candidate is that same
operation with `typed = "nihao"` and `word = "你好"`, where the
suffix branch is never taken. The insert-path invariants that file
documents at length (release sticky modifiers first, wrap in
`_without_held_modifiers`, never gate the insert itself on privacy mode,
compat-mode rewires to BackSpace and retype) are exactly the invariants an
IME commit needs, and they are already written down.

**Edit-mode routing is already a preedit mechanism.** `setEditMode(true)`
plus `editKeyTyped` / `editSpecialPressed` (`keyboard_bridge.py:1194`)
exists because our window cannot hold OS focus, so in-app text entry has
to be routed rather than focused. An IME composition buffer is the third
consumer of that pattern after the prediction-edit popup and the snippets
editor: keystrokes go to a buffer we own instead of to the app, until the
user commits. The plumbing, including the Ctrl chord table and the
`_release_edit_chord_modifiers` fifth copy of the auto-release block, is
already there.

**The vocabulary-pack system is already an import path for data we do not
want to ship.** Packs are import-only by design, hardened against
zip-slip and reserved device names, capped on load. Language data with
awkward licensing (see the table below: `SKK-JISYO.L` is GPL, CC-CEDICT
is CC BY-SA) can ride that path instead of the installer, which keeps the
MIT distribution clean and keeps 20 MB of kana out of the download.

---

## Everything hardcoded to English, ASCII, or the USA

The inventory. This is the actual work for French and the unavoidable
prerequisite for the other two.

| Where | What it assumes | Breaks on |
|---|---|---|
| `ngram_predictor.py:681` `_tokenize` | `re.findall(r"[a-zA-Z']+", ...)` | Every accented character and every CJK character is discarded before a word reaches the vocabulary. `hôpital` learns as `h` + `pital`. This is the single biggest blocker. |
| `ngram_predictor.py:692` `_SHORT_WORD_WHITELIST` | English 1- and 2-letter words | French `à`, `y`, `où`, `ce`, `ne`, `du`, `en` are treated as typing fragments and gated out of next-word predictions. |
| `ngram_predictor.py:740` `_is_plausible_word` | `c in "aeiou"`, `_VOWELS = "aeiouy"` | `é`, `è`, `à`, `ù`, `î` are not vowels, so `été` and `à` fail the shape filter and never enter `user_vocab`. Meaningless for CJK, where the whole filter must be bypassed. |
| `ngram_predictor.py:91` `_always_capitalize` | The English "I" family | French has no equivalent (`je` is lowercase mid-sentence). Needs to be per-language and empty for French, Chinese and Japanese. |
| `ngram_predictor.py:365`, `:1316` | Hardcoded paths to the two English wordlists | Needs to resolve per active language. |
| `fuzzy_recognizer.py:29`, `:105` | `QWERTY_POSITIONS`, and `positions or QWERTY_POSITIONS` with nothing ever passing `positions` | **The spatial model is QWERTY even when the user is on Dvorak or Colemak today.** This is an existing latent bug, not a multilingual one, and AZERTY makes it visible. Fixing it is a prerequisite. |
| `fuzzy_recognizer.py:528`, `ppm_predictor.py:613`, `transformer_predictor.py:138` | `c.isalpha() or c == "'"` filters | Unicode-safe for French by accident (`é`.isalpha() is True), but the apostrophe rule is English contraction logic, not French elision logic. |
| `swipe_recognizer.py:99` | `k.isalpha()` over single characters | Fine for French. Structurally meaningless for a kana or pinyin layout. |
| `symspell.py` | `.lower()` and a character-agnostic deletion index | Actually works for French unmodified, but accent-insensitive lookup needs the index built on the folded form. Meaningless for CJK. |
| `autocorrect.py` + `data/common_misspellings.txt` | English misspellings | Needs a French table or to be disabled. |
| `text_patterns.py:109` `_PHONE_GROUPINGS` | `(10,)`, `(3,3,4)`, `(3,4)`, `(1,3,3,4)`, all NANP | French mobile numbers are `06 12 34 56 78`, grouping `(2,2,2,2,2)`. Never detected today. |
| `text_patterns.py:116` `_STREET_SUFFIXES` + `_ADDRESS_RE` | US "123 Main Street" with a capitalised street-name anchor | French is `12 rue de la Paix`, lowercase `rue`, and the capitalised-word anchor lands on `Paix` at the wrong end. |
| `text_patterns.py:64` `_NUMERIC_RUN_RE`, intelligent spacing | `3.14` decimal point, `1,000` thousands | French is `3,14` and `1 000` with a non-breaking space. Intelligent spacing suppresses the wrong marks. |
| `text_patterns.py:354` `_AUTO_SPACED_PUNCTUATION` | Space *after* `.,:;` | French puts a narrow no-break space *before* `;`, `:`, `!`, `?`, `»` and after `«`. Chinese and Japanese use full-width punctuation with no space at all. |
| `keyboard_bridge.py:1509`, `:1533`, `:1305` | `char.isalnum()` / `char.isalpha()` word-character gates | Already Unicode-correct. Accented Latin and CJK characters pass both. Nothing to do; noted so nobody "fixes" it. |
| `KeyButton.qml:390` | `"Segoe UI, Inter, Ubuntu, Noto Sans, sans-serif"` | None of those carry CJK glyphs. Windows and macOS font fallback will find Yu Gothic / Microsoft YaHei / PingFang. A Linux AppImage on a minimal host will render tofu. |
| Prediction bar (`Main.qml` `computeFit`) | Latin advance widths, drops low-ranked pills rather than eliding | Measures CJK correctly (`FontMetrics.advanceWidth` handles wide glyphs), but an IME needs **paging** through 20+ candidates, and this bar has no paging concept at all: it silently drops what does not fit. |

---

## The proposal: a language profile

Every row above is the same shape: a constant that is really a property of
a language, stored as a module-level literal. The proposal is one
dataclass, `src/prediction/language.py`, resolving from the active layout:

```python
@dataclass(frozen=True)
class LanguageProfile:
    code: str                       # "en-US", "fr-FR", "zh-Hans", "ja-JP"
    word_re: re.Pattern             # replaces _tokenize's literal
    fold: Callable[[str], str]      # accent folding for index keys; identity for en
    vowels: frozenset[str]
    short_words: frozenset[str]
    always_capitalize: Mapping[str, str]
    dictionary: Path
    frequency: Path | None
    key_positions: Mapping[str, tuple[float, float]]   # fuzzy spatial model
    spacing: SpacingRules           # which marks auto-space, which side
    phone_groupings: frozenset[tuple[int, ...]]
    address_re: re.Pattern | None
    ime: IMEEngine | None           # None for alphabetic languages
```

Two properties make this worth doing rather than threading a language
string through 40 call sites. The profile is **resolved once and passed
down**, so there is no global to get out of sync, matching how
`FuzzyRecognizer` already takes an optional `positions`. And `ime is
None` is the entire branch between an alphabetic language and a CJK one:
everything above `ime` is shared, everything below it is the new
subsystem.

The English profile must be constructed from the existing constants and
must produce byte-identical behaviour. That is the migration's only
acceptance criterion, and it is testable: the whole existing suite passes
unchanged, because English is a profile like any other.

---

## French

The cheapest and the one with a real accessibility argument beyond
"supports another language".

**Layout.** `data/layouts/azerty.json` plus `azerty-compact.json`. One
decision worth making deliberately: a hardware AZERTY puts the digits on
Shift and `& é " ' ( - è _ ç à` unshifted on the number row. We are not
bound by that. On a keyboard where every modifier is a separate click,
making digits cost two clicks to preserve fidelity to a hardware quirk is
the wrong trade. Recommendation: digits unshifted, the AZERTY punctuation
on the shifted position, and the accented letters as their own keys.

**No dead keys.** `ê ë ï î ô û ù à â ç é è` all become direct keys or
right-click alternates on their base letter (the right-click-shifted
mechanism already resolves `kd.shifted` from the layout JSON and routes
through `pressKeyLiteral`, which is exactly the right path since
`pressKey` would lowercase it). Note that `_resolve_char_scancode`
deliberately bails on dead-key virtual keys (`windows.py:958`), so even a
user whose *OS* layout is US-International gets the composed character
rather than a dead-key state, because we fall through to Unicode.

**Accent-insensitive prediction is the feature.** Type `eleve`, get
`élève`. Implementation is one line of concept and a day of care: index
the folded form (NFD, drop combining marks) and keep the accented surface
form as the value, in both the n-gram vocabulary and the SymSpell
deletion index. The payoff on a mouse-driven keyboard is large, because
it means a French user can type entirely on unaccented keys and let the
pills restore the diacritics. That is the same trade the English build
already makes with capitals: type flat, let the engine do the shape.

**Elision.** `l'hôpital`, `d'abord`, `qu'il`. The current tokenizer keeps
apostrophes inside a token because English contractions are single words
(`don't`). French elision is the opposite: `l'` is a separate clitic and
`hôpital` is the word to predict. This is a per-profile tokenizer rule,
not a global change, and it is the reason `word_re` belongs in the
profile rather than being widened in place.

**Typographic spacing.** French puts a narrow no-break space (U+202F)
before `; : ! ?` and inside `« »`. Getting this right is a small, highly
visible quality signal. It is also a risk: some apps and web forms mangle
U+202F. Recommendation is a setting defaulting to on, with the plain
space as the fallback, and it belongs next to Intelligent Spacing.

**Data.** Grammalecte / Dicollecte `hunspell-fr` is MPL 2.0, which is
file-level copyleft with no effect on our MIT code, and is the cleanest
source available. Frequencies from Leipzig Corpora (CC BY) or the
OpenSubtitles-derived lists (CC BY-SA 4.0). Bigrams and trigrams need a
corpus pass; the same shape as `data/common_bigrams.txt` today.

**Cost.** The profile refactor plus the French profile plus a layout plus
data preparation. Two weeks of focused work, most of it the refactor,
which is spent once for all three languages. No new subsystems.

---

## Chinese, pinyin

A real IME, but a tractable one.

**The pipeline.** Latin keystrokes accumulate in a composition buffer.
The buffer is segmented into syllables, the segmentation is matched
against a reading-to-phrase dictionary, candidates are ranked, the user
taps one, and the hanzi replaces the whole typed run.

**Segmentation is the interesting part.** There are about 410 valid
toneless syllables and the segmentation is genuinely ambiguous: `xian` is
either `xian` or `xi'an`, `fangan` is `fan'gan` or `fang'an`. The
standard solution is a DP over the syllable table producing every valid
segmentation, then a lattice over dictionary matches scored by phrase
frequency, resolved by Viterbi. libpinyin does exactly this and is the
reference to read. Apostrophe is the user's manual disambiguator and must
be accepted in the buffer.

**Two features that are not optional in a Chinese IME**, both cheap once
the lattice exists: *abbreviation input*, where `bjdx` matches
`北京大学` by initials only, which is how experienced users actually
type; and *fuzzy pinyin*, the `z/zh`, `c/ch`, `s/sh`, `n/l`, `en/eng`,
`in/ing` equivalences that speakers of southern topolects need. The
second maps onto Alpha-OSK's existing fuzzy layer conceptually but not
mechanically: the existing recogniser is spatial (nearby keys), and fuzzy
pinyin is phonological (nearby sounds). Same slot in the merge, different
model.

**Sentence mode versus phrase mode.** A full IME converts a whole
sentence of pinyin at once. That needs a hanzi-word bigram language
model, which is more data and considerably more code. Phrase mode, where
the user commits a word or two at a time, needs only the phrase
dictionary and its frequencies, and it is a much better fit for this
keyboard: the existing flow is already "type a prefix, tap a pill", the
bar is already a candidate list, and every commit is one click. **Start
with phrase mode.** Sentence mode is a later upgrade that reuses the
segmenter.

**What has to be turned off for Chinese**, all of it profile-driven:
auto-space (Chinese has no inter-word spaces), auto-capitalisation, the
short-word filter, the vowel shape filter, `_display_cased` in its
entirety, swipe typing, and the spatial fuzzy recogniser. Punctuation
should map to full-width forms (`，。！？；：（）`), which is a
per-profile substitution table on the punctuation keys rather than new
logic.

**Simplified versus traditional** is a conversion table, not a second
dictionary. OpenCC (Apache 2.0) is the standard one and is
redistributable.

**Data and size.** A usable phrase dictionary is 100k to 400k entries.
That is 10 to 100 times the current `data/` volume, and bundling it would
be felt in both the installer and in bridge startup, which is already
about a second and which `check.py`'s runtime depends on. It must load
lazily and it should not live in the installer. See *Packaging* below.

**Cost.** The segmenter, the dictionary structure, the candidate ranker,
the composition buffer and its bridge wiring, the paging UI, and the
profile suppressions. Call it 3000 lines and a month, assuming the
profile layer already exists.

---

## Japanese, kanji

Everything Chinese needs, plus the two hardest problems in Japanese
input.

**Romaji to kana is the easy half** and is genuinely easy: a
deterministic trie of about 150 rules, no licensing, no data. The
wrinkles are well documented and finite: sokuon (`tta` gives `った`),
the `n`/`nn` ambiguity before a vowel, and small kana.

**A kana layout may be better than romaji.** A 5x10 gojūon grid is 50
keys of one tap each, versus two to three Latin taps per kana. On a
mouse-driven keyboard that is a real reduction in clicks, and it is the
kind of thing that is obvious in this project's context and not obvious
in a phone IME's. Worth prototyping both.

**Kana to kanji is the hard half, and there are two schools.**

*Mozc-style* converts a whole sentence with a Viterbi pass over a
connection-cost matrix. It is what Google Japanese Input and every
modern IME does, it produces the best results, and it is a large amount
of code and data.

*SKK-style* makes the user mark where conversion starts, then does a
dictionary lookup and shows candidates. No language model, no sentence
segmentation, roughly a tenth of the code. It is considered old-fashioned
precisely because it puts work on the user, in the form of one extra
keystroke per conversion.

**Recommendation is SKK-style**, and the reasoning is specific to this
product rather than general. The objection to SKK is the extra keystroke;
this keyboard's users are already tapping a pill for every word and the
bar is already a candidate window, so the marginal cost is far lower here
than on a hardware keyboard. And the failure modes differ in the right
direction: a bad Viterbi segmentation silently produces wrong kanji in
the middle of a committed sentence, while a dictionary lookup that misses
simply shows no candidate. On a tool where a wrong commit costs several
clicks to repair, predictable beats clever.

**Homophones are the thing that makes Japanese different from Chinese
here.** `きかん` is `期間`, `機関`, `器官`, `気管`, `帰還` and more, all
plausible. Every real IME annotates the candidate list with a short gloss
to disambiguate. Our pills are single-line text with a width fitter that
drops low-ranked entries rather than eliding them. **A Japanese candidate
list needs a two-line pill or a secondary annotation row, and that is a
genuine change to the bar, not a configuration of it.** It is the single
largest UI change any of these three languages demands.

**Also needed:** hiragana and katakana toggle, half-width and full-width
toggle, okurigana handling in the dictionary lookup, and full-width
punctuation as in Chinese.

**Data.** Mozc's `dictionary_oss` is the right source: it derives from
IPAdic under NAIST's permissive BSD-style terms plus a public-domain
Okinawan dictionary, which makes it redistributable alongside MIT code
with a notice. `SKK-JISYO.L` is the more natural fit for an SKK-style
engine and is **GPL**, so it must not go in the installer; if it is used
at all it goes through the pack-import path as something the user
fetches. Note the irony worth remembering: the permissively-licensed data
suits the architecture we are not recommending, and the copyleft data
suits the one we are.

**Cost.** Chinese's cost plus the romaji trie plus the annotation UI
plus the mode toggles. Six weeks, and it is the one where the estimate is
least trustworthy.

---

## Data sources and licensing

| Data | Language | Licence | Bundleable with MIT? |
|---|---|---|---|
| Grammalecte / Dicollecte `hunspell-fr` | French | MPL 2.0 | Yes, file-level copyleft only |
| Leipzig Corpora frequency lists | French | CC BY | Yes, with attribution |
| OpenSubtitles frequency lists | French | CC BY-SA 4.0 | Yes, but a *derived* n-gram file is an adaptation and inherits share-alike |
| Lexique / Openlexicon | French | GNU-style per their site, verify before use | Check before relying on it |
| `phrase-pinyin-data` (mozillazg) | Chinese | MIT overall | Yes, except its `cc_cedict.txt` which inherits CC BY-SA 3.0 |
| CC-CEDICT | Chinese | CC BY-SA 3.0 | With attribution; derived counts inherit share-alike |
| OpenCC (simplified/traditional) | Chinese | Apache 2.0 | Yes |
| Mozc `dictionary_oss` | Japanese | IPAdic / NAIST permissive + public domain | Yes, with the NAIST notice reproduced |
| `SKK-JISYO.L` | Japanese | **GPL** | **No.** Import-path only |
| JMdict / KANJIDIC2 | Japanese | CC BY-SA 4.0 | With attribution; share-alike on derivatives |

Two things to carry forward. `THIRD_PARTY_NOTICES.md` already exists and
already has the right shape for these entries. And share-alike is a trap
worth naming: a frequency table *computed from* a CC BY-SA corpus is an
adaptation of it, so the derived `.txt` in `data/` inherits the licence
even though our code does not. That is acceptable for a data file with a
notice, and it is the reason to prefer the CC BY and MPL sources where
there is a choice.

---

## Packaging

Language data should not go in the installer.

The current `data/` directory is about 250 KB. A French wordlist with
frequencies is a few megabytes. A Chinese phrase dictionary is 10 to 30.
`SKK-JISYO.L` alone is around 20. Bundling all of it multiplies the
download for every user to serve a minority of them, and it lands on
`_MAX_PACK_FILE_BYTES` and the model-loader caps that already exist for
good reasons.

The mechanism is already built twice over. Releases are published as
assets on `okstudio1/alpha-osk-releases` and the updater already
downloads, verifies and installs from there. Vocabulary packs already
have a hardened import path with size caps, allow-list extraction and
reserved-name rejection. A language pack is a vocabulary pack with a
profile attached, fetched from the releases repo on demand. That reuses
the hardening rather than re-deriving it, and it keeps GPL and CC BY-SA
data out of the signed installer, which is worth something on its own.

---

## Testing, and the part that needs saying

The mechanical layers test the way everything else here does. Tokenizer
and folding rules are pure functions. Segmentation has known-ambiguous
inputs (`xian`, `fangan`) that make good paired positive/negative cases
in the house style. The romaji trie is a table with edge cases that are
already enumerated in every IME's test suite. Layouts inherit
`TestEveryLayout` for free. The profile refactor's acceptance test is
that the entire existing suite passes with English expressed as a
profile.

What does not test that way is whether the output is *right*. Whether
`きかん` ranks `期間` above `器官` in ordinary use, whether the pinyin
candidate order matches what a Chinese typist expects, whether the French
elision split feels correct: none of that is checkable from the code, and
none of it is checkable by you. **Shipping Chinese or Japanese input
without a native speaker testing it is shipping something you cannot
evaluate**, in a product whose whole premise is that its author uses it
daily and notices when it is wrong. That is a real argument for doing
French first and stopping to reassess, and it is a real argument for
treating CJK as a contribution-shaped feature (a language pack a native
speaker can build and tune) rather than a first-party one.

---

## Recommended phasing

1. **Profile refactor.** English expressed as a `LanguageProfile`, no
   behaviour change, whole suite green. Includes fixing the
   `QWERTY_POSITIONS` bug that already affects Dvorak and Colemak users.
2. **French.** Layout, profile, accent folding, elision, data. Ship it.
   Reassess.
3. **Candidate paging in the bar.** Needed by both CJK languages and
   independently useful. Currently the fitter drops what does not fit,
   which is the correct behaviour for word predictions and the wrong one
   for an IME.
4. **Composition mode.** The buffer, the preedit strip, the commit path
   through the existing token-pill mechanism. Language-agnostic.
5. **Pinyin**, phrase mode, as a downloadable language pack.
6. **Japanese**, SKK-style, only with a native speaker on the loop, and
   only after the annotation problem in the bar has a design.

Steps 1 and 3 are worth doing on their own merits regardless of whether
any of these languages ships.

---

## Open questions

- Where does a language picker live in Settings? Layout lives under
  Appearance, but language drives the prediction engine, the spacing
  rules and the pattern matchers, so Appearance is wrong. The honest
  answer is probably a new top-level category, which by this project's
  own rule ("if you cannot decide which category, that is a UX smell")
  means the setting is really two settings: a layout under Appearance and
  a prediction language under Smart Typing.
- Does a language switch need to be *fast*? A bilingual user switching
  mid-sentence wants a one-click toggle, not a settings drill-down.
  That is a title-bar control, and it competes for space with the
  Learning switch.
- Does the learned model stay per-language? It has to, or French words
  pollute English predictions. That means `ngram_model.json` becomes
  per-profile, which touches the Data Backup archive schema and its
  `SCHEMA_VERSION`.
