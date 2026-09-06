# Training Data

The files that bootstrap Alpha-OSK's prediction engine.

**Everything here is re-read at every launch and none of it is persisted.**
The n-gram model's context tables are a merged view over a *base* share
rebuilt from these files on each start and a *user* share written to
`ngram_model.json` (see the *Context tables* section of `CLAUDE.md`). So
editing a file here changes what a fresh install predicts, and an existing
install picks the change up on its next launch without losing anything it has
learned.

## Files

| File | Purpose | Format |
|------|---------|--------|
| `google-10000-english-usa-no-swears.txt` | Frequency-ranked base vocabulary (~9.9k words) | One word per line, ranked by frequency |
| `google-20000-supplement.txt` | Second ~10k, filtered for explicit content | Same |
| `base_dictionary.txt` | Additional vocabulary for unigram boosting | One word per line. **Never** put multiple words on one line: that creates fake bigrams. |
| `common_bigrams.txt` | Seed word pairs for next-word prediction | `word1 word2` per line, whitespace-separated |
| `common_trigrams.txt` | Seed word triples for next-word prediction | `word1 word2 word3` per line |
| `training_corpus.txt` | Natural sentences for n-gram and PPM training | One sentence per line |
| `common_misspellings.txt` | Autocorrect fast-path table | `wrong right` per line, lowercase, one-to-one only |
| `proper_nouns.txt` | Capitalisation forms (**currently inert**, see below) | One word per line, preferred capitalisation |
| `layouts/` | Keyboard layout definitions | One JSON file per layout |
| `sounds/` | Key click audio | `click.wav` |

Lines starting with `#` are comments and are skipped in every text file here.

## How they're loaded

All of it happens in `HybridPredictor.__init__` (`src/prediction/hybrid_predictor.py`):

1. `NgramPredictor.__init__` loads the profile's wordlists for base unigram
   frequencies (`google-10000…` plus the supplement, named by
   `language.ENGLISH`).
2. `load_base_dictionary()` boosts unigrams from `base_dictionary.txt`.
3. `load_common_bigrams()` / `load_common_trigrams()` seed the base context
   tables at **+50 per entry**, with each trigram also reinforcing its two
   internal bigrams at +10.
4. `CommonMisspellings.load()` reads `common_misspellings.txt`.
5. `_load_training_corpus()` trains both the n-gram model and PPM on
   `training_corpus.txt`, contributing **+1** per observed pair.

`proper_nouns.txt` is read by `NgramPredictor._load_proper_nouns()` into the
`capitalization` table, but **nothing reads that table back for pills today**:
auto-capitalisation is only the "I" family
(`language.ENGLISH.always_capitalize`), and everything else follows the casing
the user typed. The file and the table are kept so a future opt-in
"capitalize proper nouns" switch would not have to re-teach from scratch. See
the *Auto-Capitalization & Proper Nouns* section of `CLAUDE.md` before
changing that.

## Adding training data

- **New words**: `base_dictionary.txt`, one per line
- **New word pairs**: `common_bigrams.txt` as `word1 word2`
- **New word triples**: `common_trigrams.txt` as `word1 word2 word3`
- **New sentences**: `training_corpus.txt`, one sentence per line
- **New typo fixes**: `common_misspellings.txt` as `wrong right`

Two things to keep in mind. The seeds are weighted 50x the corpus, so a
handful of curated pairs outweighs a lot of sentences; and a seeded pair
competes against the user's own typing under the blend in
`NgramPredictor._context_probs`, so adding a marginal pair costs more than
leaving it out. Measure a change with `scripts/bench/ksr.py` rather than
guessing.

## Provenance and licensing

The base vocabulary is derived from the Google 10 000 / 20 000
English word-frequency lists, filtered for explicit content. The bigram,
trigram, misspelling and corpus files are hand-curated for this project.

Anything added here has to be redistributable under terms compatible with the
project's MIT licence. In practice that means public domain, CC0, or CC BY
(with an entry in `THIRD_PARTY_NOTICES.md`). **CC BY-SA and CC BY-NC are not
usable**: the share-alike condition conflicts with MIT redistribution, and the
non-commercial condition conflicts with MIT outright. Record the source of
any new file in `THIRD_PARTY_NOTICES.md` at the same time you add it.

## Vocabulary packs

Domain-specific vocabulary packs are **import-only. No packs ship with the
application.** Six used to (medical, programming, academic, gaming, business,
nsfw), but each was 200-400 words, which is too thin to compete with personal
learning: after the user accepts a word from the prediction bar three times
their own model already outranks the seed list. The *Vocabulary Packs* section
of `CLAUDE.md` has the full reasoning, including what re-introducing a
built-in pack would take.

Imported packs live in the user's config directory, not here:

- Windows: `%APPDATA%/alpha-osk/packs/`
- Linux: `~/.config/alpha-osk/packs/`
- macOS: `~/Library/Application Support/alpha-osk/packs/`

### Pack format

```
<pack-id>/
├── pack.json          # {"name": "...", "description": "...", "version": 1}
├── dictionary.txt     # One word per line (unigrams only!)
├── bigrams.txt        # word1 word2 per line (optional)
└── trigrams.txt       # word1 word2 word3 per line (optional)
```

The pack id is the directory name and must match `[a-z0-9_-]{1,64}`.

### Creating a custom pack

1. Build a folder in the shape above.
2. Import it from *Settings → Your Language Model → Vocabulary Packs →
   Import Custom Pack*, which validates and copies it into the user packs
   directory.
3. Enable it from the same panel. `KeyboardBridge.enableVocabularyPack(pack_id)`
   is the slot behind that toggle.

Import is security-hardened (id sanitising, Windows reserved-name rejection,
symlink skipping, per-file and total size caps). Read `PackManager.import_pack`
and `tests/test_vocabulary_pack.py::TestImportPackSecurity` before changing
any of it.
