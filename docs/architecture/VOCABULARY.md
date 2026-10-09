# Vocabulary, explicit-content filtering and vocabulary packs

This document holds the full write-up behind the CLAUDE.md sections *Vocabulary*, *Explicit content is filtered from suggestions, not from the vocabulary* and *Vocabulary Packs*.

## Vocabulary

- **Base**: Google 10K wordlist (`data/google-10000-english-usa-no-swears.txt`) + 10K supplement (`data/google-20000-supplement.txt`, filtered for explicit content) + `data/english-expanded.txt`, 64,400 words from SCOWL size 60 by way of the ESDB bundle (permissively licensed, see `data/licenses/ESDB.txt`, pinned by sha256 in `data/english-expanded.manifest`). ~83K total. The SCOWL half enters at **one base count each**, so it supplies coverage without competing with conversational frequencies or reading as personal history. It is a speller's list, which is why it carries explicit content that the curated lists do not: see *Explicit content is filtered from suggestions, not from the vocabulary* below. Slurs are removed from it at generation (`data/slurs.txt`), which is why the count is 43 below the 64,443 of 1.5.0.
- **Packs**: No built-ins ship. The system is import-only - see *Vocabulary Packs* below. Imported packs appear as toggles in Settings -> Your Language Model -> Vocabulary Packs.
- **Numpad**: Toggles between numbers and navigation keys (Home/End/PgUp/PgDn/arrows/Ins/Del) via NumLock. Key 5 is blank in nav mode. Layout mirrors a physical numpad: rows `7 8 9 /`, `4 5 6 *`, `1 2 3 -`, `0(span 2) . +`, `Enter(span 3) NumLock`. NumLock sits at the bottom-right (active highlight uses the theme accent), Enter is the wide bottom-row key. Earlier builds put NumLock on the top row and stretched `+` / Enter as 2-row spans on the right column. The flat 5-row layout was the user's request to match a physical 10-key.

## Explicit content is filtered from suggestions, not from the vocabulary

*Settings -> Smart Typing -> Suggestions -> Filter Explicit Words*, **default
ON**. The whole design is in the distinction the title makes: the words stay
in the dictionary, stay typable character by character, and stay learnable.
The setting decides only what the prediction bar **volunteers**.

That is deliberate and was the owner's call (2026-09-16): a keyboard that
cannot swear is a dignity problem for an AAC user, so the answer is not to
remove the words but to let the user decide whether the bar offers them.
**Slurs are the exception, also the owner's call (2026-09-23)**: they are
removed outright, see *Slurs are removed, not filtered* below. Profanity
stays in the wordlist and the filter is the control over it. Do not
re-litigate either decision; do keep the filter honest.

- **`data/explicit_words.txt` is generated, not hand-edited**
  (`scripts/gen_explicit_words.py`). It is a list of **exact words**, so the
  runtime is a set lookup with no suffix logic to get wrong.
- **Matching is stem-plus-closed-suffix-set, never substring.** That rule and
  its suffix list come from `data/explicit_stems.txt`, which already
  documented it. Substring matching is the Scunthorpe problem and it flags
  `class`, `assess`, `cocktail`, `peacock`, `dictionary`, `analysis` and
  `shiitake`. A filter that visibly swallows ordinary words is one the user
  switches off and leaves off, which is the same outcome as not having it.
  `"spook"` is deliberately **not** a stem for exactly this reason: it would
  take `spooky` and `spooked` with it.
- **Nothing is filtered at generation time any more, and that is the fix for
  a bug worth remembering.** `explicit_stems.txt` (named
  `explicit_exclusions.txt` until 2026-09-16) used to be applied by
  `gen_vocabulary.py`, so the words it named were absent from the shipped
  list and no setting could bring them back. It had been written to cover
  swearing and it missed slurs, so the shipped vocabulary ended up carrying
  **slurs but no common profanity**, which is the exact inverse of what
  anyone wanted: you could not predict `fuck` at all, while the slurs were
  one keystroke from a pill. The stems now only *seed* the suggestion
  filter, profanity ships in the wordlist, and the file was renamed because
  a file called "exclusions" that excludes nothing is how the two jobs got
  confused in the first place. (Slurs are now excluded at generation, but
  from their own exact-word list, never from these stems.)
- **The filter is applied in exactly one place**, `_finalize_scores`, beside
  the short-word gate, because every suggestion from every strategy passes
  through there. A second copy at another emit site is the parallel-blocks
  failure CLAUDE.md warns about for sticky-modifier release.
- **Personal vocabulary outranks the filter.** A flagged word in
  `user_vocab` is offered normally, because at that point the keyboard has
  direct evidence of the user's own register. **One typing is enough**, not
  three: the three-sighting candidate gate applies to words the model does
  not already know, and these are all in the shipped dictionary, so `learn`
  takes the known-word branch. Worth knowing because the neighbouring gate
  makes three the number a reader expects.
- **It fails open.** A missing or unreadable list leaves the filter inert
  rather than stopping construction, the same trade `_load_extra_vocabulary`
  makes, and `explicit_filter_available` is what lets the UI say so rather
  than showing a toggle that governs nothing.

Guarded by `tests/test_explicit_filter.py`, where every case is paired with
its inverse: the suppression test is paired with turning the filter off (a
filter that suggested nothing at all would satisfy the first alone), and the
flag list is checked against the shipped **no-swears** frequency list as
ground truth, so a false positive is caught by construction rather than by
anyone's judgement.

Slurs: see `PREDICTION_NOTES.md`, *Slurs are removed, not filtered*, and CLAUDE.md.

## Vocabulary Packs

Import-only. **No built-in packs ship.** Earlier releases shipped six (medical / programming / academic / gaming / business / nsfw) but each was 200-400 words - too thin to compete with personal learning, which bumps a word's score by +5 every time the user accepts it as a pill. After typing "physical therapy" three times, the user's own model already knows it, and the seed list saves nothing. Sourcing a real domain vocabulary (SNOMED-grade for medical, full API surface for programming) is its own project and runs into licensing rabbit holes; curated 300-word lists were strictly worse than no shipped packs at all. They were also drifting in maintenance (NSFW had a different `pack.json` schema and no n-grams) and there was an open correctness bug (see *Known limitations* below).

### What the system still does
- `src/prediction/vocabulary_pack.py` (`VocabularyPack`, `PackManager`) discovers packs from `data/packs/` (now absent) and from the user dir (`%APPDATA%/alpha-osk/packs/` Windows, `~/.config/alpha-osk/packs/` Linux). The user dir is created on first launch.
- Pack format: a folder containing `dictionary.txt` (required, one word per line, `#` comments allowed), optional `bigrams.txt` (whitespace-separated word pairs), `trigrams.txt` (word triples), and `pack.json` (`{name, description, version}` - generated automatically if missing on import).
- Settings -> Your Language Model -> Vocabulary Packs shows one toggle per imported pack (driven by `keyboard.getAvailablePacks()` returning the rich `{id, name, description, version, words, bigrams, trigrams}` list - the `id` field is the directory name and `VocabularyPack.get_info()` includes it explicitly so the QML side can call enable/disable). A pack's `name`/`description` are attacker-controlled strings read from an imported `pack.json`, so they pass through `_clean_meta_text` on load: collapsed to a single line, bounded to `_MAX_PACK_META_FIELD_LEN` (200), and replaced by the directory name / empty string if they are not strings at all. That's for the *log*, which this module writes the pack name into on every cap trip and which users attach to bug reports, so an embedded newline would let a pack name forge whole log lines. Separately, the `Text` elements that render them set `textFormat: Text.PlainText` so an `<img>` tag in a pack name can't make Qt fire an outbound request just from being displayed (see CLAUDE.md *Things to Watch Out For*).
- Empty state: just the "Import Custom Pack..." button + a one-line note about the format. The hardcoded `[{id: "medical", label: "Medical"}, ...]` Repeater that drove the old UI is gone - adding a new pack only requires importing it (or, in a future release, dropping a folder under `data/packs/`); no QML edit needed.
- Import hardening (security-critical, **don't loosen**): folder name sanitised to `[a-z0-9_-]{1,64}` and rejected outright if it collides with a Windows reserved device name (`con`, `prn`, `aux`, `nul`, `com1`-`9`, `lpt1`-`9`, checked case-insensitively on the base name before any extension). Those names pass the id regex but would fail `mkdir` on Windows. Resolved destination verified to sit strictly under `user_packs_dir` before any `rmtree`/`copytree`, symlinks inside the source tree are skipped rather than dereferenced. Built-in packs (if any) cannot be overwritten via import. The id rule itself (the pattern plus the reserved-name check) lives once in `src/prediction/pack_ids.py`; both this loader and `src/data_export.py` import it, so the two can't drift the way they once did. See `tests/test_vocabulary_pack.py::TestImportPackSecurity` for the regression coverage.
- Load and import size caps (packs previously had none; every sibling loader in the codebase already capped its input): `pack.json` metadata capped at `_MAX_PACK_META_BYTES` (64 KB), each of `dictionary.txt`/`bigrams.txt`/`trigrams.txt` capped at `_MAX_PACK_FILE_BYTES` (20 MB), and the whole source folder capped at `_MAX_PACK_IMPORT_TOTAL_BYTES` (50 MB, walked and checked before `import_pack`'s `copytree` starts). These are whole-file rejections. Separately, `load()` caps entries at `_MAX_PACK_WORDS` / `_MAX_PACK_BIGRAM_ENTRIES` / `_MAX_PACK_TRIGRAM_ENTRIES` (200 000 each); unlike the byte caps this is discovered mid-iteration, so a file under the byte cap but with millions of short lines is truncated and kept rather than rejected outright. See `tests/test_vocabulary_pack.py::TestPackInputCaps`.

### Known limitations
- **Disabling a pack does not undo its predictor injection.** `apply_to_predictor` writes pack words into `predictor.unigrams / .bigrams / .trigrams` with `max()`. `disable_pack` calls `pack.unload()` which clears the *pack's own* in-memory copy, but the entries it pushed into the predictor stay there until the next process restart. Mostly invisible now that no built-ins ship (only users who imported a pack and then disabled it without restarting hit this), but worth fixing if we ever ship built-ins again. The clean fix is to track per-pack `(word, prior_value)` tuples at apply time and revert on disable, with a guard that only reverts when the predictor's current value still equals the pack's contribution (so words that piled on organic learning after enable aren't clobbered).
- **`apply_to_predictor` uses `max()` for bigrams/trigrams, not addition.** Earlier comments in this file claimed bigrams/trigrams were "additive with weight 30" - that was the doc, not the code. Code is correct: additive would compound on every enable cycle. The doc is now consistent.

### Re-introducing a built-in pack
If a future release ships a built-in pack, mirror it back into `data/packs/<id>/` with the four files described above. PackManager's `_discover_packs` will pick it up automatically (it iterates both built-in and user dirs). Add a parametrised structural test back to `tests/test_vocabulary_pack.py` modelled on the deleted `TestRealPacks` class - the `sample_pack_dir` fixture in that file shows the expected shape.
