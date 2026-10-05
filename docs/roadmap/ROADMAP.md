# Alpha-OSK Improvement Roadmap

A phased plan to harden Alpha-OSK's codebase, starting with the highest-impact, lowest-risk improvements and building toward more ambitious changes.

---

## Phase 1 — Automated Testing (Complete)

**Goal:** Establish a test suite that covers the core logic so future changes don't silently break things.

| Area | What | Priority |
|------|------|----------|
| Infrastructure | pytest config, conftest with shared fixtures, `requirements-dev.txt` | Setup |
| N-gram predictor | Predict with empty/partial/full context, learn/save/load round-trip, bigram/trigram weighting | High |
| PPM predictor | Train/predict cycle, normalization, probability distribution sums to ~1, save/load round-trip | High |
| Fuzzy recognizer | Spatial model key probabilities, profile switching, candidate generation, autocorrect threshold | High |
| Hybrid predictor | Merge logic weighting, next-word vs completion mode, learn-from-selection | High |
| Platform layer | Factory returns correct backend, base interface contract, mocked send_key/send_text | Medium |
| Keyboard bridge | Modifier state machine (shift, caps, ctrl auto-release), context buffer management, prediction wiring | Medium |

**Definition of done:** `pytest` passes with >80% line coverage on `src/prediction/` and meaningful coverage on `src/platform/` and `src/keyboard_bridge.py`.

---

## Phase 2 — CI/CD Pipeline

**Goal:** Catch regressions automatically on every push.

- [x] GitHub Actions workflow: lint (ruff), type-check (mypy), test (pytest) — `.github/workflows/ci.yml`
- [x] Coverage reporting with threshold gate (60% minimum)
- [x] Pre-commit hooks (ruff) — `.pre-commit-config.yaml`
- [x] Badge in README for build status
- [x] Ruff lint fixes across all source files (444 issues auto-fixed)

**Status:** Complete.

---

## Phase 3 — Smart Learning (Complete)

**Goal:** Make the prediction engine actually learn from the user's typing patterns, not just individual words.

- [x] **Sentence-level learning** — `learn()` called with full sentence context on space, building real bigrams/trigrams from typing (was only calling `learn_word()` which only boosted unigram frequency)
- [x] **Sentence boundary detection** — Period, exclamation, question mark, and Return all trigger full-sentence learning and reset the sentence buffer
- [x] **Recency decay** — Every 50 `learn()` calls, all user-learned frequencies are scaled by 0.95× so recent words gradually outweigh old ones. Words that decay below 1 are pruned.
- [x] **Expanded context buffer** — 50 → 200 chars, so trigram context isn't lost after a few words
- [x] **Preserved context across lines** — Return no longer wipes the context buffer; it adds a sentence boundary and keeps predicting

**Status:** Complete. 183 tests passing.

---

## Phase 3b — Training Data Quality (Complete)

**Goal:** Fix training data formats and expand coverage so predictions are accurate out of the box.

- [x] **Fixed `base_dictionary.txt`** — Restructured from multi-word lines (which poisoned bigrams with "the→be", "be→to" etc.) to one-word-per-line format (pure unigram boosting)
- [x] **Expanded `training_corpus.txt`** — From ~328 formulaic lines to 500+ diverse, natural sentences covering: greetings, casual chat, texting style (lol, brb, omg), work, tech, accessibility, emotions, food, shopping, directions, and sentence-starter patterns for trigram building
- [x] **Created `common_trigrams.txt`** — 200+ three-word sequences (e.g. "i want to", "how are you", "looking forward to") loaded with high weight. Also reinforces contained bigrams.
- [x] **Wired trigram loading** — `NgramPredictor.load_common_trigrams()` added and called from `HybridPredictor` init
- [x] **Added `data/README.md`** — Documents all data file formats and how they're loaded

**Status:** Complete. 205 tests passing.

---

## Phase 3c — Vocabulary Packs (Complete)

**Goal:** Let users enable domain-specific vocabulary without bloating the base dictionary. Packs are orthogonal to accessibility profiles (motor settings ≠ vocabulary).

- [x] **Pack system architecture** — `VocabularyPack` class + `PackManager` in `src/prediction/vocabulary_pack.py`
- [x] **5 built-in packs:**
  - **Medical & Health** — conditions, medications, therapy, assistive equipment (~300 words, 100+ bigrams)
  - **Programming & Tech** — languages, frameworks, CLI, dev workflow (~350 words, 150+ bigrams)
  - **Academic & Scientific** — research terms, scientific vocabulary, writing phrases (~300 words, 100+ bigrams)
  - **Gaming** — game genres, multiplayer chat, streaming terms (~200 words, 60+ bigrams)
  - **Business & Finance** — corporate, finance, management vocabulary (~150 words, 60+ bigrams)
- [x] **Runtime enable/disable** — Packs load/unload without restart, exposed via QML bridge slots
- [x] **Additive injection** — Enabled packs inject vocabulary into n-gram model with lower weight than user-learned words
- [x] **Pack format** — `data/packs/<name>/` with `pack.json`, `dictionary.txt`, `bigrams.txt`, `trigrams.txt`

**Status:** Complete. 266 tests passing.

---

## Phase 4 — Error Handling & Resilience

**Goal:** Replace broad `except Exception` patterns with specific handling and add missing guards.

| Location | Issue | Fix |
|----------|-------|-----|
| `keyboard_bridge.py:importTextFile` | Generic exception, no encoding validation | Catch `OSError`/`UnicodeDecodeError` specifically |
| `keyboard_bridge.py:importFolder` | No file-size limit | Skip files >10 MB |
| `ngram_predictor.py:load` | Silent fallback on corrupted JSON | Validate schema, log corruption, back up old file |
| `ppm_predictor.py:load` | Same | Same |
| Platform synthesizers | Don't check tool availability before first use | Validate on construction, surface clear message |
| Debug log | Unbounded in-memory list | Already capped at 100 — add rotation or ring buffer |

---

## Phase 5 — Performance

**Goal:** Reduce unnecessary work and make the prediction pipeline snappier.

- [ ] **Debounce predictions** — 100–150 ms delay in `_update_predictions` so rapid keystrokes don't each trigger a full predict cycle
- [ ] **LRU cache eviction** — Replace PPMWordPredictor's dict cache with `functools.lru_cache` or explicit LRU
- [ ] **Cancel stale LLM loads** — Thread cancellation token for `_load_llm_async`
- [ ] **Profile neighbor cache** — Only rebuild `SpatialKeyModel._neighbors` when radius actually changes

---

## Phase 6 — Accessibility & UX Polish

**Goal:** Make the tool as usable as possible for the people it's built for.

- [ ] Add `Accessible.name` and `Accessible.description` to all interactive QML elements
- [ ] Tab/arrow-key navigation within the OSK window
- [x] **Voice dictation** (Deepgram, not the Whisper placeholder this line first imagined): `src/dictation/`, off by default and inert until the user supplies their own API key. See `docs/architecture/DICTATION.md`
- [ ] Cognitive accessibility profile (simplified layout, fewer keys visible)
- [ ] Auditory feedback option (key click sounds, spoken predictions)

---

## Phase 7 — Data Integrity

**Goal:** Protect user-learned vocabulary from loss or corruption.

- [ ] Model versioning field in saved JSON (`"version": 1`)
- [ ] Schema validation on load with migration path
- [ ] Automatic backup before overwrite (keep last 3)
- [ ] Export/import user vocabulary as portable format

---

## Phase 8 — Build & Distribution

**Goal:** Streamline the release pipeline.

- [ ] Lock dependencies (`pip-compile` or `uv lock`)
- [ ] Separate `requirements-dev.txt` and `requirements-build.txt`
- [ ] Automated Windows build in CI (PyInstaller + signing)
- [ ] Linux packaging (AppImage or Flatpak)

---

## Principles

1. **Don't break what works.** Every change must pass the test suite.
2. **Accessibility first.** If a change helps typical users but hurts accessibility, it doesn't ship.
3. **Keep it lean.** Alpha-OSK runs alongside other apps — memory and CPU budgets matter.
4. **Test the hard parts.** Prediction logic, platform synthesis, and modifier state are where bugs hide.
