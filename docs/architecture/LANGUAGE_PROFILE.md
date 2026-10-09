# Language profile and layout-derived key positions

This document holds the full write-up behind the CLAUDE.md section *Multilingual Input (French, Pinyin, Kanji)*.

## Multilingual Input (French, Pinyin, Kanji)

### The language profile

`src/prediction/language.py` holds `LanguageProfile`, and **English is a profile like any other** (`language.ENGLISH`). Every field on it was a module-level literal in the prediction engine, written when English was the only language: the tokenizer's `[a-zA-Z']+`, the fragment filter's vowel sets, the short-word allow-list, the "I" family, and the paths to the two English wordlists. None of them were wrong; they are answers to questions with a different answer in French.

`NgramPredictor(model_path, profile)` and `HybridPredictor(..., profile=...)` take one and default to `ENGLISH`, so no caller had to change. The profile is **passed down, never read from a module-level "current language"**: a global would be one more copy to keep in sync with the layout, the model file and the settings layer, and this codebase already documents what parallel copies of one fact cost. It is a frozen dataclass because it is a description rather than state, and the engine reads it on every keystroke.

**The acceptance criterion for the refactor was that the rest of the suite passes unchanged**, which it does. That is also why `tests/test_language_profile.py` drives deliberately un-English profiles: a test that only exercised `ENGLISH` would pass just as happily against the literals, so it would not catch the engine having kept a private copy of a constant it was supposed to give up.

Two things are deliberately **not** on the profile:

- **Key positions.** Dvorak-English is a real combination, so the fuzzy spatial model belongs to the *layout*. See *The fuzzy spatial model is layout-derived* below.
- **The `text_patterns` locale data** (the NANP phone groupings, the US street-suffix matcher, which punctuation auto-spaces, the `3.14` / `1,000` number shapes). All of it is English-and-American and all of it belongs in a profile eventually, but those are free functions with about twenty call sites across the bridge and the token predictor, and **a field nothing reads is worse than a field that does not exist yet**. That slice lands with French, which is what gives it a second value to hold.


Research notes at `docs/roadmap/MULTILINGUAL_INPUT.md`. Not implemented. One finding worth knowing even if none of it ships: Unicode output already works end to end on all three platforms (`send_text` falls through to `KEYEVENTF_UNICODE` above `U+0080`, macOS uses `CGEventKeyboardSetUnicodeString`), so no key-synthesis work is needed for any language, and French needs no dead keys because we can put the precomposed `é` on a key.

### The fuzzy spatial model is layout-derived

`FuzzyRecognizer` used to fall back to its hardcoded `QWERTY_POSITIONS` unconditionally, and nothing anywhere passed it anything else, so **Dvorak and Colemak users were autocorrected against a keyboard they were not typing on**: `hwllo` resolved to `hello` on the strength of `w` neighbouring `e`, which on their layout it does not. That was a plain bug rather than a multilingual one; AZERTY is what made it worth finding.

`fuzzy_recognizer.positions_from_layout(rows)` derives the grid from the layout JSON, and `KeyboardBridge._apply_layout_key_positions` pushes it on construction and on every `setLayout`. Three things about the derivation are load-bearing:

- **Slots are assigned by a key's index within its row, not by accumulating `width` fields.** Dvorak, Colemak and AZERTY are letter remappings of the same physical board, so the physical slot is what matters, and the index rule is the only formulation that reproduces `QWERTY_POSITIONS` byte-for-byte, which is what makes moving the QWERTY user onto this path a provable no-op (`test_qwerty_json_reproduces_the_hardcoded_table`). The rendered widths would not: Tab is 1.3u here, not the ANSI 1.5u, so a geometric walk puts the home row at +0.3 rather than +0.25.
- **Punctuation is dropped from the result but still consumes a slot.** Dvorak's top row is `' , . p y f g c r l`, so `p` is the fourth key and belongs in the slot QWERTY gives `r`. Counting only the letters would put it at column 0, which is wrong in a new way rather than fixed.
- **The digit row is the one exception and is aligned by digit index**, preserving the deliberate "no horizontal stagger, `5` sits above `t`" rule `QWERTY_POSITIONS` already encoded. The leading backtick would otherwise push every digit one column right of the letter it sits above.

Rows carrying a `layer` other than the one being read are skipped, so compact's `sym` digits (which *replace* the letters rather than sitting above them) correctly contribute nothing. Known gap: the Number Row panel puts digits above the compact grid in the rendered UI, and that panel is a QML component rather than layout JSON, so those digits have no spatial position.

`FuzzyRecognizer.set_key_positions` rebuilds the whole `SpatialKeyModel` and **must re-point `word_generator.spatial_model` in the same breath** (the generator holds its own reference, so re-pointing only the recogniser leaves candidate generation on the old grid while every accessor reports the new one). An empty mapping is ignored rather than applied: blanking the grid would silently disable autocorrect instead of leaving the previous layout in place.
