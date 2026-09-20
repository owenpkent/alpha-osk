# Prediction & Autocorrect — Architecture Notes

Commercial keyboards (Gboard/LatinIME, Presage) treat prediction and spell-check as **one unified system**, not two. During a single dictionary trie traversal, they generate both completions and corrections scored together. The literal typed word competes against alternatives — autocorrect only fires if a correction scores 1.5–2x higher.

Deep-dive design docs for each algorithm: `FUZZY_RECOGNITION.md` (spatial model + tunable constants), `PPM.md` (variable-order character model + PPMD escape), `HYBRID_MERGING.md` (merge weights + validation + capitalization). Where the base context tables come from, and the ARPA format they are derived from: `NGRAM_SEEDS.md`.

## What Alpha-OSK does now
- **Hybrid prediction**: n-gram + fuzzy in the merge (same layered approach as Presage); PPM is trained and persisted but its word candidates left the merge on 2026-09-03, see the bullet below
- **SymSpell index for whole-word correction**: `src/prediction/symspell.py` precomputes deletion variants at index time, so a lookup is a hash hit rather than a scan (Garbe, 2012). `SymSpell.add_word` updates a built index in place, which is what lets a word learned this session become fuzzy-matchable without the ~0.5 s rebuild.
- **Spatial error correction**: `fuzzy_recognizer.py` considers nearby keys (same concept as LatinIME's key-distance weighting)
- **Three-tier capitalization**: always-capitalize ("I"), sentence-start-only (ambiguous names), always (proper nouns). NOTE: the live pill-facing behaviour is now "I"-family only — see the *Auto-Capitalization & Proper Nouns* section in `CLAUDE.md` for why Tiers 2/3 are inert.
- **Linear-interpolation n-gram scoring**: `NgramPredictor.predict()` ranks candidates with `score(w) = λ₃·P(w|w₋₂,w₋₁) + λ₂·P(w|w₋₁) + λ₁·P_uni(w)` (λ = 0.5 / 0.3 / 0.2). Trigram / bigram / unigram all live in probability space, so bigram evidence can actually beat the global unigram favourite after a trained context (e.g. "I want " → "to", not "the"). When there's no preceding word, the formula collapses to `P_uni` at full weight so partial-prefix completion isn't flattened. (Pre-fix bug: bigram added `freq·2`, unigram added `p·100_000` — unigram dominated by 1000×.)
- **Fragment filter on learning AND on dictionary load**: `_is_plausible_word` (length ≤ 2 must be in a short whitelist; length ≥ 3 needs both a vowel and a non-`aeiou` letter — `y` counts as both so "eye" and "cry" pass but "aaaa" and "xqz" don't) is applied in three places: (1) `NgramPredictor.learn()` rejects obvious keyboard-slip fragments before they enter the candidate pool, (2) `_load_frequency_wordlist` filters the Google 10K + 20K supplement dumps on first load — those wordlists are scraped from web search corpora and contain every letter of the alphabet plus ~370 two-letter abbreviations / state codes / fragments at high frequency, which would otherwise flood the pills when typing a one-letter prefix, and (3) `load()` strips fragments out of saved `unigrams` and `user_vocab` so existing users' models get cleaned up on the first launch after the filter shipped. Surviving unknown words from `learn()` go through a repetition gate: counted in `_candidate_counts` until 3 sightings, then promoted into `user_vocab`. Known base-dict words and `learn_word()` bypass the gate. **Pill clicks on unknown words are gated too**: `learn_from_pill_click` routes them through the same `_candidate_counts` pool (one click = one sighting), promoting with cumulative `_PILL_CLICK_WEIGHT` (×5) only at the 3-sighting threshold. Without this gate, a single click on a fuzzy- or PPM-generated pill for a never-typed word would inject it into `user_vocab` permanently with weight 5. Bigram / trigram reinforcement (`reinforce_context`) on pill click still fires immediately — the context edge was validated by that click, and a bigram pointing into a not-yet-promoted unigram surfaces the word only in that specific context, which is exactly the loop we want for "click more times to promote." `mark_good` (right-click → Show more) and vocab pack import deliberately bypass the gate — those are explicit user-boost actions, not implicit signals. Candidates also carry a `_candidate_last_seen` timestamp; `_sweep_stale_candidates` (called from `_apply_decay`) drops entries older than `_candidate_max_age_seconds` (30 days) so an accidental click doesn't sit in the pool indefinitely waiting for the multiplicative decay to drain it. Candidate counts and timestamps persist across save/load; an older save file without the timestamp field has its entries backfilled on the next sweep rather than instantly expired.
- **Space-time autocorrect is OFF by default.** `KeyboardBridge._autocorrect_enabled = False`. The on-space path that overwrites the typed word with a correction (`check_autocorrect` → `replace_text`) was clobbering deliberate input — "vs" → "is", and a hyphenated word followed by another word reportedly wiped both. The user wants corrections to surface as suggestion **pills only**, never silent overwrites. The fuzzy recogniser still contributes to the prediction merge so corrections appear as clickable pills (`HybridPredictor` includes fuzzy in its merge sources). `setAutocorrectEnabled(True)` re-enables the space path; tests that exercise it call this slot first.
- **Two-tier autocorrect threshold + short-typing guard** (still applies when autocorrect is on): `FuzzyRecognizer.should_autocorrect` first skips any typing under 3 chars — single-char and 2-char fragments carry too little signal ("v" → "is", "vs" → "is", "th" → "to" all fired before the guard, none of which the user asked for). Then it runs an *absolute* confidence gate (`confidence_threshold`, 0.65) and a *relative* margin gate (`autocorrect_margin`, 1.5×). The relative gate compares the correction's score against `_typed_baseline(typed_word) * 1.5`, where `_typed_baseline` returns `log1p(1) ≈ 0.69` for plausibly-shaped typings (vowel + consonant) and 0 for implausible slop. Plausible deliberate typings ("thru", "lol") are protected; implausible inputs ("xqz", "thx") fall back to the absolute threshold alone. This is the LatinIME / Gboard pattern — the literal typed word effectively competes with corrections — without the full unified-scoring rewrite. Genuine 2-char misspellings that need autocorrect ("im" → "I'm") go through the upstream `check_autocorrect` fast-path table and bypass the length guard.
- **Curated bigram / trigram seed corpus**: `data/common_bigrams.txt` (~700 pairs) and `data/common_trigrams.txt` (~700 sequences) are loaded with high weight (50 per bigram, 50 per trigram + 10 reinforcement on each internal bigram) so cold-start prediction has signal before the user's personal typing builds up. Edit those files to expand coverage; the n-gram loaders skip comment / blank lines and tokenise on whitespace.
- **Targeted reinforcement on prediction click**: `HybridPredictor.learn_from_selection(context, selected_word)` routes the unigram through `NgramPredictor.learn_from_pill_click` (immediate +5 for words already in the base dict or `user_vocab`; gated through `_candidate_counts` for unknown words — see *Fragment filter* above) and adds **only** the trailing `(prev_word, selected_word)` bigram and `(prev2, prev1, selected_word)` trigram via `NgramPredictor.reinforce_context`. Earlier the unigram path was the immediate `learn_word(+5)` for *every* word, so a single click on a fuzzy- or PPM-generated pill for a never-typed word permanently inflated the model. Earlier still, the bigram path passed `context + selected_word` to `learn()`, which re-incremented every bigram in the running buffer on each click — that was fixed separately and stays fixed: bigram reinforcement is still the clean +1 to the trailing edge that was actually validated by the click.
- **Structured tokens are a separate store, deliberately outside the merge**: `NgramPredictor._tokenize` is `[a-zA-Z']+`, so digits and symbols never reach the vocabulary and the engine cannot represent a phone number, a zip, a house number or an email address at all. `prediction/token_predictor.py` holds those as whole strings, prefix-matched, count-weighted, with no context model and no fuzzy matching. Both omissions are deliberate: the "this is the number I always type" signal is already carried by the count, and fuzzy-matching a *digit* silently changes a number rather than fixing a slip, which is worse than offering nothing. It is not merged into the pill ranking either, because the two are mutually exclusive rather than ranked: part-way through `owen@gm` or `555-123-`, no English word is a plausible suggestion, so `KeyboardBridge._in_token_context()` picks a bar rather than blending them. What may be stored is gated by `text_patterns.is_learnable_token`, which rejects runs of more than eight digits wholesale rather than enumerating which identifiers are sensitive; the full rationale (and the SSN / card near-misses that shaped it) is in the *Structured Tokens* section of `CLAUDE.md`. Persisted under a `tokens` key inside `ngram_model.json`.
- **Backspace as negative signal**: `NgramPredictor.unlearn_word(word)` retracts one sighting from `_candidate_counts` (most common — typo never made it into `user_vocab` yet) or, if already promoted, decrements `user_vocab` / `unigrams` / `_user_total` / `total_words` together. Bigrams/trigrams are intentionally untouched (one backspace shouldn't crater multi-word context history). Wired into `KeyboardBridge._rehydrate_current_word_from_context`: when backspace pops a trailing space and rehydrates a word back into `_current_word`, the rehydrated word is unlearned (gated on `not _privacy_mode`). Net effect: typing `teh ` then immediately backspacing past the space removes the candidate sighting; if the user re-completes the word with the same spelling, `learn()` counts it again. A word that has been typed many times and is already deep in `user_vocab` can't be unlearned in one keystroke — the decrement is per-sighting, not per-word.
- **Context tables are split into base and user halves** (2026-09-02): `bigrams` / `trigrams` are merged views over a base share rebuilt from the data files on every launch and a persisted user share (`_user_bigrams` / `_user_trigrams`). Scoring blends the two per prefix with a user weight of `U / (U + 5 + 0.02 B)`, so a fresh model is unchanged and a personal phrase after a common word surfaces after two typings instead of 55. Recency decay acts on the user share of both orders and no longer erodes the seeds; the seeds no longer inflate by 50 per launch. Full rationale in the *Context tables* section of `CLAUDE.md`; tests in `tests/test_ngram_context_split.py`; benchmarks under `scripts/bench/`.
- **Mid-word fuzzy completion is a prefix beam** (2026-09-02, `src/prediction/prefix_beam.py`): `get_fuzzy_predictions` completes a possibly mistyped prefix over live dictionary prefixes with substitution / omission / extra / transposition transitions and an unnormalised spatial emission, ranked with `0.55 * log1p(freq)`; the whole-word correction paths are unchanged. Mid-word recovery after a slip went from 0.0% to 94.5% (clean prefixes 1.4% to 92%), and one uncorrected mis-click per word now costs keystroke savings 53.6 -> 47.9% instead of 52.5 -> 34.6%. Rationale and constants in the *Prefix beam* section of `CLAUDE.md`.
- **The fuzzy dictionary follows the vocabulary, and PPM is out of the merge** (2026-09-03): every learning path pushes its words through `HybridPredictor._refresh_fuzzy_frequencies` (SymSpell and the prefix index update in place), a pack enable merges the whole table, and Clear Learned Data / import rebuild from scratch. PPM's word path was a dictionary-less character beam; removing it from the merge measured +1.4 pt keystroke savings and 21 -> 3 ms per keystroke with the prefix beam in place, so `_ppm_in_merge` defaults to False while the model keeps training for a later beam fusion. Details in the *Fuzzy dictionary refresh, and PPM out of the merge* section of `CLAUDE.md`.
- **Click position and a learned pointer bias** (2026-09-03): `KeyButton.qml` reports where inside the key a press landed, the bridge keeps that parallel to the current word, and the prefix beam scores the continuous position with a learned per-slot bias (`src/prediction/pointer_model.py`, persisted under `pointer` in `ngram_model.json`) taken out. Measured end to end it is worth a few tenths of a point of keystroke savings (more for a systematic pointer than a scattered one), because the beam already recovers most single-key errors from the reported key; the *Click position* section of `CLAUDE.md` has the sweep and the reasoning.

## Known gaps (future work, priority order)
1. **Unified scoring** — Make the literal typed word compete against corrections in the same ranked list with an explicit score, so the system knows when NOT to correct. The two-tier autocorrect threshold above is a partial proxy; full unified scoring is the proper fix. Reference implementation worth studying: `willwade/noisy-channel-correction` (Python, MIT, AAC-focused). It ranks candidates as `log P(intended) + log P(noisy | intended)` using a PPM language model plus a learned character-level confusion matrix. The interesting bit is the confusion matrix (built by simulating realistic errors against the target input modality), not the library itself; integration as a runtime dep would pull in NumPy + Levenshtein and the surface is CLI-script-shaped, so the realistic move is to port the scoring formula and confusion-matrix concept into `fuzzy_recognizer.py` rather than vendor the project.
2. **Spatial edit costs in ranking** — Key-distance weights from fuzzy_recognizer should feed into final prediction ranking, not just candidate generation.
3. **Katz / Stupid Backoff for sparse contexts** — The linear-interpolation formula above gives λ₃·P_tri even when the trigram table has never seen this 2-word prefix (P_tri = 0). Katz backoff discounts seen events and redistributes the mass to the bigram/unigram fallback. Better behaviour on rare contexts. Larger lift (~100 lines).
4. ~~**A real public n-gram source for the seed tables**~~ **Done (2026-09-05).**: `data/common_bigrams.txt` and `common_trigrams.txt` are 754 and 740 hand-written entries against a ~20k vocabulary, so the base context model is thousands of times smaller than the vocabulary it has to condition. The licence constrains the source more than the quality does: shipping alongside MIT means public domain, CC0 or CC BY only, never BY-SA (share-alike conflicts with MIT redistribution), BY-NC (MIT grants commercial use) or BY-ND (counts are a derivative). That rules out COCA, which is not redistributable at all, and Wikipedia-derived lists, which are BY-SA. What is clean: Keith Vertanen's text-entry language models (CC BY 4.0, ARPA format, a 64k-word 3-gram at 4.0 MB / 39.9 MB / 400 MB trained on 504M words of forum, blog and social text, plus an AAC-specific corpus and dev/test sets at aactext.org), the Open American National Corpus (~15M words, unrestricted, includes spoken transcripts), and Google Books Ngrams v3 (CC BY 3.0, huge but the wrong register). The work is not the download: ARPA carries log-probs and backoff weights while the base tables carry counts that `_context_probs` normalises, and a table this size cannot be rebuilt into Python dicts at every launch. The saving grace is that the bar shows at most ~5 pills, so pruning to the top ~10 continuations per prefix at build time collapses it. Landed: `data/seed_bigrams.txt` and `seed_trigrams.txt` are generated from Vertanen and Kristensson's forum model (CC BY 4.0) by `scripts/gen_seed_ngrams.py`, and are worth +1.8 and +1.7 points of keystroke savings and +5.8 and +5.2 points of next-word hit rate on the two AAC splits, for 2.56 MB and 121 ms at launch. The ARPA format, the licence rule, the probability-to-count conversion and the full measurement are in `NGRAM_SEEDS.md`.

## Benchmark baselines

The 2026-09-10 learning change separates the shipped corpus from personal
unigram history and treats its counts as a weak prior. Prediction edits
that change a word's spelling now teach it and its context immediately. Fresh-model learning
examples and lifecycle guarantees are in `HYBRID_MERGING.md` under
*Shipped examples no longer dilute personal learning*.

Base word frequencies still come largely from word-list rank. A screening
experiment on the first 150 `aac-dev` sentences compared the current curve
with joint rank priors proportional to `rank^-0.7` and `rank^-1.0`, preserving
vocabulary and overall base mass. The best clean KSR change was only +0.22
points, with a small next-word-hit regression, so no new curve was adopted.
A future dictionary-data upgrade should use measured conversational
frequencies and be evaluated on the full development and held-out sets.

`scripts/bench/ksr.py --corpus <name>`, cold-start engine, no personal
learning, 5 pills. Every number below is a fraction of an idealised user who
clicks the instant the intended word appears, so treat them as an upper bound
and as *relative* measures between conditions.

| corpus | sentences | words | KSR | next-word hit | never predicted |
|---|---|---|---|---|---|
| `builtin` | 30 | 313 | 54.9% | 32.6% | 8.6% |
| `aac-dev` | 557 | 2,956 | 49.1% | 28.7% | 13.6% |
| `aac-test` | 566 | 2,730 | 50.4% | 30.4% | 13.0% |

The AAC rows are with `data/seed_bigrams.txt` and `seed_trigrams.txt` in
place (2026-09-05). Before those landed the same engine read 47.3% and 48.7%
KSR at 22.9% and 25.2% next-word hit; `NGRAM_SEEDS.md` has the full
before-and-after and what each layer bought.

Three things to take from this.

**Every historical figure in this repo is on the `builtin` set**, including the
52.5 / 53.6 / 55.0% keystroke-savings numbers quoted here and in `CLAUDE.md`
for the context split, the prefix beam and the PPM removal. They are still
correct and still comparable to each other; they are not comparable to
anything measured on the AAC sets.

**The `builtin` set flatters the engine by six to seven points.** It is 30
sentences written by hand in this repo, so its register sits close to the
curated seeds and the training corpus. Real crowdsourced AAC communications
are more varied and more telegraphic ("wheres dad", "end it"), and the share
of words the engine never predicts at any prefix length nearly doubles, from
8.6% to about 14.7%.

**The gap between `aac-dev` and `aac-test` is 1.4 points**, and those are two
same-sized splits of one corpus separated only by which workers wrote them.
That is a usable noise floor: a one-point difference is not evidence even on
these sets, and it is certainly not evidence on 30 sentences. Tune against
`aac-dev` and report `aac-test`.

## Reference implementations
- **LatinIME (AOSP)**: trie-based dictionary with weighted edit distance, n-gram LM scoring. Open source.
- **Presage**: pluggable predictors (smoothed n-gram + Katz backoff, recency, trie completion). Linear interpolation merge. Similar to our hybrid approach.
- **Dasher**: PPM-C character-level prediction. Our PPM predictor is based on this.
- **SymSpell**: precompute all deletion variants within edit distance N at index time. Query = generate deletions of input + hash lookup. (github.com/wolfgarbe/SymSpell)
- **Hunspell**: affix-based dictionary + phonetic matching. Slower but handles morphology.
- **noisy-channel-correction (willwade)**: Python/MIT AAC correction library implementing the unified-scoring formula in Known gap #2. See that gap for integration notes.
- **WorldAlphabets (AACTools)**: MIT JSON data covering alphabets, letter frequencies, and keyboard layouts for 310+ languages. Not used today (Alpha-OSK is English-only), but the canonical dataset to reach for if multilingual support is ever scoped.

---

# Implementation notes (from CLAUDE.md)

Moved verbatim from `CLAUDE.md` on 2026-09-20, which keeps a summary of the load-bearing rules. This is the full reasoning.

## Context tables: base and user halves

`NgramPredictor.bigrams` / `.trigrams` are **merged views**. The user's share lives in `_user_bigrams` / `_user_trigrams` (floats), and that share is the **only context that is persisted** (`user_bigrams` / `user_trigrams` keys in `ngram_model.json`). The base share (curated seeds at +50 per pair from `data/common_bigrams.txt` / `common_trigrams.txt`, the training corpus at +1, vocabulary packs) is rebuilt from the data files on every launch and never written. Invariant: `bigrams[p][w] >= round(_user_bigrams[p][w])`; base is whatever is left over.

Why it is split, all measured on 2026-09-02 (the write-up with the numbers is linked from the memory index):
- The single merged table was persisted **and** re-seeded on top of itself at every launch, so seeds inflated: a live model had `i -> want` at 1,037 against 63 on a fresh install, and the trigram `i want -> to` at 3,848 against 53, growing by 50 per launch with no ceiling.
- `_apply_decay` scaled **every** bigram to a floor of 1 (its comment claimed user-only) and never touched trigrams. On a fresh model the curated ordering was flat within ~2,500 learns; on a matured one 78% of edges sat permanently at 1, which is where the user's own pairs ended up.
- A personal phrase after a common word could not surface: `the bus` typed once was P = 0.0004 against 2,600 seed mass, needed 55 typings back to back, and at once a day never reached the pills; the same phrase after a word with no seeds was scored as a certainty (P = 1.0).

How it works now:
- **Scoring** (`_context_probs`) trusts the user's distribution for a prefix with weight `U / (U + _CONTEXT_PRIOR_FLOOR + _CONTEXT_BASE_TRUST * B)` (5 and 0.02; `U` user evidence, `B` base count for that prefix), blending it with the base distribution. With no user evidence it is the old normalised row **exactly**, so a fresh model scores byte-for-byte as before (keystroke savings 52.5% before and after), and the whole existing suite is the regression guard for that. With it, `the bus` reaches the pills after 2 typings and one typing after a new word gets 1/6, not 1.0. The sweep behind the two constants: the floor barely matters between 2 and 10; trust 0.01 surfaces a phrase after one typing, 0.05 needs five.
- **Writes**: every user context write goes through `_bump_user_context` (from `learn()` and `reinforce_context`), which keeps the merged view in step. Base writes go straight to the merged tables: `learn(text, corpus=True)` (what `load_corpus` calls), `learn_corpus_context`, `_learn_base`, the two seed loaders, and `PackManager.apply_to_predictor`. A direct write to `.bigrams` in a test is therefore a base write and still works.
- **Decay** (`_decay_user_context`) acts on the user share of **both** orders, subtracting exactly what it removes from the merged view, and drops a count below `_USER_CONTEXT_MIN` (0.1, about a week for a single typing). Seeds are untouched however long the session runs (`tests/test_ngram_context_split.py::TestSeedsSurviveTheSession`). The merged count rounds to zero at tick 14 while the user share lasts to tick 45, so for a prefix with no base evidence the merged row is gone for two thirds of the pair's life; `_context_probs` scores from the user row alone in that window rather than returning nothing (`TestScoringTrustsTheUserInProportion::test_a_lone_user_pair_keeps_scoring_until_it_is_forgotten`).
- **Legacy files** carrying `bigrams` / `trigrams` are adopted wholesale as user history (`_adopt_user_context`): the halves cannot be separated after the fact, adopting keeps every ranking exactly as it was, and decay retires the inherited seed mass over the following weeks while the base is re-seeded cleanly underneath. The next save writes the new keys; an older build reading a new file loses only user context, since it re-seeds base itself.
- **`HybridPredictor.reload_from_disk` must call `_reseed_context()` after `load()`.** It used to work by accident, because the persisted table already carried the (inflated) seeds. `clear_user_data` wipes both halves and the hybrid re-seeds, as before. The reseed re-links the corpus through `learn_corpus_context`, which applies a known-or-third-sighting gate of its own over the corpus's sightings alone (it neither reads nor writes the user's candidate pool; since the corpus prior split, the launch path makes the same call, so the two agree by construction), so a reload rebuilds the base share a launch would; its first version linked every plausible word, and a reload grew edges for rare corpus words that a fresh start withholds (`TestTheMergedViewStaysHonest::test_reseeding_the_corpus_gates_rare_words_exactly_as_a_launch_does`).

Known follow-ups, deliberately not bundled: the seed weight of 50 now only matters relative to the corpus's +1 and could come down; the cross-order interpolation weights are fixed at 0.5/0.3/0.2 (renormalising when a table is silent would not reorder anything, since the bigram-to-unigram ratio is unchanged; evidence-weighted interpolation across orders is the change that would). Benchmarks: `scripts/bench/ksr.py` (keystroke savings, ablations, `--learn-half`) and `scripts/bench/fuzzy.py`; both run against a temporary model directory, never the live one. **Every keystroke-savings figure quoted in this file (52.5, 53.6, 55.0%) is on `ksr.py`'s `builtin` corpus**, 30 sentences written by hand in this repo. They stay correct and comparable to each other, and are not comparable to anything measured since: `--corpus aac-dev` / `aac-test` (real held-out AAC communications, added 2026-09-05) read 47.3% and 48.7% for the same engine, because the hand-written set sits close to the curated seeds and the training corpus. The generated context seeds landed the same day and took those to **49.1% and 50.4%**, at 28.7% and 30.4% next-word hit; see *Measured result* in `docs/architecture/NGRAM_SEEDS.md`. Quote the corpus with the number from now on, and treat anything under 1.4 points as noise, which is what the two AAC splits disagree by with nothing else changed. See *Benchmark baselines* in `docs/architecture/PREDICTION_NOTES.md`.

## Shipped corpus prior and faster personal learning

Since 2026-09-10, the shipped training corpus's unigrams live in
`NgramPredictor._corpus_unigrams` / `_corpus_total`, rebuilt in memory by
`load_corpus_prior`, instead of being added to `user_vocab` at each launch.
The old path injected 2,604 accepted tokens of pseudo-personal history even
on a fresh model. The n-gram and fuzzy scorers now share
`_effective_typing_count` / `_effective_typing_total`: real counts plus
`_CORPUS_PRIOR_WEIGHT` (0.1) times the corpus counts. With no real learning,
the weight cancels and the conversational distribution is preserved; new
learning competes with one tenth the former bootstrap mass. Existing saved
user counts are preserved because real and historical corpus counts cannot
be separated safely. The user-total invariant remains exact.

The prior uses its own local three-sighting gate and never changes user
candidate state or the decay clock. Its accepted words are known to both
ordinary typing and pill learning. **They live in `_corpus_unigrams` and
nowhere else.** The merged `unigrams` table is persisted by `save()`, and a
first version installed the prior there so membership tests would find it:
a corpus-only word then survived the release that dropped it from the
shipped file, kept passing the hybrid's validity check and was rebuilt into
the fuzzy dictionary on every launch. `NgramPredictor.in_vocabulary` and
`vocabulary()` are the membership test and the enumeration that see both
halves; `_is_valid_word` and `_fuzzy_frequencies` go through them, and
`_top_unigrams_with_scores` adds the prior at `_CORPUS_PRIOR_WEIGHT` rather
than at full count. Reload rebuilds the prior against the replacement user
history, and Clear Learned Data rebuilds it outside the PPM-enabled branch.
The generic `load_corpus` API is unchanged. Guarded by
`tests/test_corpus_prior.py::test_a_word_dropped_from_the_corpus_leaves_with_it`,
paired with the learned word that must stay.

Saving a prediction edit **whose spelling changed** calls
`learn_from_selection(..., explicit=True)`: each token gets an immediate +5,
its context is reinforced, and its fuzzy entry is refreshed. A Save that
kept the spelling (including a casing-only edit) is one ordinary pill tap,
gated exactly as a tap is, because opening the editor on a fuzzy-generated
pill and tapping Save must not be the one tap that injects a never-typed
word; the casing is still recorded through `set_capitalization`. The
explicit path applies the shape filter and the blacklist at edit time, the
same rule `load()` applies on the way back in, so a token is refused where
the user can see it rather than learned and silently stripped at the next
launch (a taught acronym passes as it does everywhere). `learn_word` also
retires the word's candidate-pool entry, which every later `learn()` would
otherwise leave persisted. Ordinary pill clicks keep the unknown-word
repetition gate. Privacy and learning freeze suppress the new learning path.
Measured fresh-model examples, lifecycle details, and limits are in
`docs/architecture/HYBRID_MERGING.md`; regressions are in
`tests/test_corpus_prior.py` and the hybrid/bridge edit tests.

## Prefix beam (mid-word fuzzy completion)

`src/prediction/prefix_beam.py`. `FuzzyRecognizer.get_fuzzy_predictions`, the fuzzy source the hybrid merges **mid-word**, completes the typed prefix through an error instead of correcting it as a finished word. The whole-word paths (`generate_candidates`, `get_correction`, `should_autocorrect`, SymSpell), which run on space, are unchanged; they measured well (75 to 99% top-1 by error type) and it was only their use mid-word that was wrong.

Why (measured 2026-09-02, write-up linked from the memory index): mid-word, the old fuzzy source put the intended word in its top five 0.0% of the time after a neighbour slip and 1.4% with no error at all, because its spatial beam only emitted sequences as long as the typed text and SymSpell reached two edits further, so 87% of its mid-word candidates were shorter than the prefix. The n-gram completer needs an exact prefix. Between them one mis-click cost +2.05 clicks per word (+69%).

How: a beam over the dictionary's **live prefixes** (`PrefixIndex`, about 24,000 for the shipped list, 0.01 s, rebuilt lazily after `load_dictionary` / `set_frequencies`, updated in place by `update_word` as the vocabulary changes (see *Fuzzy dictionary refresh*), and rebuilt whenever the spatial model object changes, which `set_key_positions` does on a layout switch) with an **unnormalised** Gaussian emission (`SpatialEmissions`: a hit costs 0, so a perfect 13-letter typing no longer prunes out and an edge key no longer outscores a central one for equal accuracy) and four transitions: substitution, omitted click, extra click, transposition. Completions are ranked by path score plus `0.55 * log1p(freq)`. Scores come back relative, in (0, 1], so `_normalise_source` sees positives. By default, below three typed characters it returns nothing (the `should_autocorrect` guard; the n-gram's exact match is the better source there), **unless the run is not a live prefix**, where both halves of that reasoning fail at once: spelling no word's opening is itself the evidence of an error, and the exact source it defers to has nothing to return. That was reported as "two letters shows nothing until you type a third", and it was 298 of the 676 two-letter runs against the shipped dictionary, all of which now fill. `MIN_TYPED_DEAD_PREFIX` (2) is the rescue floor and `PrefixBeam._worth_completing` is the rule. A *live* two-letter prefix is refused by default. Since 2026-09-10, `HybridPredictor.predict` opts into `allow_short_prefix` when fewer than `HybridPredictor.SHORT_PREFIX_RESCUE_FLOOR` (5, the benchmark's pill count) valid n-gram candidates can reach the bar, **independent of the max-suggestions setting**: its first version decided against the requested count, so whether the rescue fired, and with it which word held slot 1 for a short prefix, changed when the user raised the count, on a keyboard where pill position is muscle memory. This lets `ww` offer `we` alongside `wwe` and `wwii`, whose presence previously disabled correction altogether. Suppressed candidates do not count towards the floor. Prefixes with five or more exact suggestions keep their existing ranking, every bar is a prefix of the widest one (`TestTwoLettersAlwaysFillTheBar::test_the_rescue_does_not_depend_on_how_many_pills_are_shown`), and the standalone fuzzy API keeps its original default (`tests/test_fuzzy_prefix_beam.py::TestATwoLetterMisClickDoesNotEmptyTheBar` and `tests/test_hybrid_predictor.py::TestTwoLettersAlwaysFillTheBar`). One character stays too little to act on. Measured on `scripts/bench/ksr.py --mis-click`, whose slip lands on the second character of every word: clean typing is **unchanged to the decimal** (49.1% aac-dev, 50.4% aac-test), and with the slip 44.7 -> 45.3% and 46.3 -> 46.7%, at unchanged latency. That gain is small because KSR counts clicks saved rather than whether anything was offered at all, and the report was about the blank bar.

Constants were set by sweep against the shipped list **with the n-gram's counts**, which is how the hybrid runs it: the bare wordlist carries no frequencies, so without `set_frequencies` the frequency term is a constant and rankings fall to insertion order (the first prototype's numbers were spatial-only for that reason; any test or bench of this path must inject the counts, see the fixture in `tests/test_fuzzy_prefix_beam.py`). With them, and drawing words from the 2,000 most frequent the way typing is distributed (a uniform draw over all 10,000 weights a word used once a year the same as "because" and reads 30% / 62% on the first two figures; the first bench did that): a clean 4-letter prefix completes to the intended word 92% of the time, a one-slip prefix 94.5%, a dropped click 71%, a doubled click 58%, a transposition 96%, and the top pick never overrides a typed prefix (100%). Omission and extra pull against each other (a cheaper omission explains a doubled click away as something else: at -2.0 it is 83% / 52%, at -3.0 58% / 66%); -2.5 leans toward omission, the dominant error for this kind of input. `scripts/bench/fuzzy.py --n 300` reproduces the shape (`--legacy` for the before column): clean prefixes 0.2% -> 89%, a slipped prefix 0 / 0 / 0.3 / 0.3% -> 56 / 92 / 98 / 99.7% by prefix length 3 to 6. End to end (`scripts/bench/ksr.py --conditions legacy-fuzzy,full --mis-click`): keystroke savings 52.5 -> 53.6% on clean typing (the source is no longer net-negative) and **34.6 -> 47.9%** with one uncorrected mis-click per word, at about 0.3 ms per keystroke.

**The emission takes a position.** `get_fuzzy_predictions(..., positions=[...])` carries one optional `(row, col)` in key units per character of the current word, defaulting to the key's centre. `mouse.x / mouse.y` from `KeyButton.qml` now reaches it as `offsets=`; see *Click position and the learned pointer bias* for what that turned out to be worth.

`FuzzyRecognizer.prefix_completion = False` restores the pre-beam path. It exists for the benchmark's before/after (`ksr.py`'s `legacy-fuzzy` condition, `fuzzy.py --legacy`) and nothing else: there is deliberately no user setting, since a user could not act on it and the merge weights are untouched. Tests: `tests/test_fuzzy_prefix_beam.py`.

## Fuzzy dictionary refresh, and PPM out of the merge

**Packed correction index (2026-09-15).** `SymSpell.prepare()` builds an
immutable deletion index with UTF-8 key storage, packed 32-bit word IDs and
offsets, and open-addressed hash slots (`src/prediction/packed_deletes.py`).
New words added afterward go into a mutable deletion overlay, so personal
learning still takes effect without a rebuild. Known-word frequency updates
only touch the shared frequency map. Each query merges base postings before
overlay postings to preserve candidate tie ordering. Reset/reload still
replaces the whole SymSpell instance. Distance thresholds, serialized model
formats, and dependencies are unchanged.

`PrefixIndex` also uses packed keys, child rows, and top-completion word IDs
(`packed_prefixes.py`), with mutable personal overlays and at most 4,096
cached successful lookups. The base vocabulary grows from 18,989 to 83,386:
64,443 ESDB size-60 words at one base count each, plus 27 curated
care/accessibility/software words at 25 counts. `LanguageProfile.extra_vocabulary`
owns the extra list. Exact and fuzzy prediction both consult the current base
after model load without repopulating rejected saved entries or overwriting
personal counts; clear/reload rebuild fuzzy coverage. The generator,
checksum, source manifest, and full bundled license are documented in
`docs/research/VOCABULARY_MEMORY.md` alongside measured memory and startup costs.
Hybrid startup and full dictionary rebuilds call `prepare_prefix_index()`
after frequency injection, keeping construction off the first typed prefix.
Standalone fuzzy callers still prepare lazily; layout changes reuse the index.

The existing `allow_short_prefix` rescue remains tied to the fixed five-candidate
floor, independent of the requested pill count. New rare words can make a
two-letter typo a live prefix, but suppressed entries do not count toward that
floor. The standalone fuzzy API keeps its former default, and one character
never triggers this fallback.

The n-gram scorer retains all user/corpus/context candidates but only the best requested
number of matching base words. Nonnegative mixture terms make this cutoff exact;
unusual weights/counts fall back to the full scan. A versioned base map invalidates
the sorted word-reference list and bounded next-word top rows on mutation. Equal
scores now sort lexically, so the larger frequency-1 tail is deterministic across
processes. See `tests/test_ngram_candidate_index.py` for brute-force parity and
`scripts/bench/ngram_candidates.py` for the same-snapshot speed comparison.

Two more of the 2026-09-02 findings, fixed together on 2026-09-03.

**The fuzzy dictionary follows the vocabulary.** It used to be loaded once at startup (`load_dictionary` plus one `set_frequencies(ngram.unigrams)`) and never touched again: the constructor comment named a `_refresh_fuzzy_frequencies` that did not exist, and `enable_vocabulary_pack` wrote pack words into the n-gram only, so a word learned this session was not fuzzy-matchable (nor reachable by the prefix beam) until a restart, and a pack's words never were. Now every learning path pushes the changed words through `HybridPredictor._refresh_fuzzy_frequencies(words)` (`learn`, `learn_word`, `learn_from_selection`, `mark_good_suggestion`), which calls `FuzzyRecognizer.update_word(word, count)`: the dictionary entry is raised, **SymSpell indexes a new word in place** (`SymSpell.add_word` on a built index no longer invalidates it, since the rebuild is about 0.5 s and this runs on the keystroke path) and `PrefixIndex.update_word` adjusts the word's own prefixes. A pack enable merges the whole table (`_refresh_fuzzy_frequencies()` with no words). Both only add or raise, the `max` rule `set_frequencies` always had, so the three events that *shrink* the vocabulary, Clear Learned Data, a Data Backup import and a boost rollback from the dashboard (`clear_user_data`, `reload_from_disk`, `unprefer`), go through `_rebuild_fuzzy_dictionary`: `reset_dictionary`, the profile's wordlist, then the counts, about half a second at a moment the user asked for. `unprefer` was missed at first, and since the boost itself had reached the fuzzy dictionary through the refresh, a rolled-back word kept winning mid-word completions until the next restart. The one lowering deliberately *not* followed is `unlearn_word`, the backspace negative signal: it moves a count by one on the keystroke path, where the rebuild has no place, so the fuzzy count can sit a sighting or two above the n-gram's until the next rebuild. Tests: `tests/test_fuzzy_refresh.py`, one vocabulary event each, asking the fuzzy source on the same instance.

**Fuzzy frequencies are on the n-gram's scale, not the raw unigram count.** Plumbing the refresh through was not enough on its own: the fuzzy dictionary took the merged unigram count, which puts a base word at its rank-derived count (up to 9,885) and a personal word typed three times at 3, and on that scale the beam's frequency term buys four slips' worth of spatial cost, so a typed `zorb` ranked `spent` (z to s, o to p, r to e, b to n) above the `zorblat` the user had just taught it. The n-gram never had this problem, because `P(w) = 0.7 * P_user + 0.3 * P_base` makes a personal word far likelier than a base word. `HybridPredictor._fuzzy_frequency(word)` maps that same belief onto the base count's scale (a word the user has never typed keeps exactly its base count, so a fresh model ranks as before; a typed one is lifted by the n-gram's personal weight) and every injection into the fuzzy dictionary, at startup, on refresh and on rebuild, goes through it. It is the unigram cousin of the context-table split above: the same base-versus-user scale mismatch, one layer over. The beam carries one rule on top (`PrefixBeam._protect_exact_completions`): when the typed prefix is itself live, a candidate reached only by paths costing more than one cheap edit (`FREQUENCY_MAY_BUY`, -1.5: an adjacent slip is -0.69, a swap -1.0) is moved just below the exact prefix's own completions rather than bought past them by frequency, which is what lets a pack word at weight 3 surface against `question` two slips away, while `teh` still offers `the` first because one swap competes on frequency as before. A hard exact-first tier was tried and reversed: `teh` is a live prefix of a rare word in the shipped list, and the tier buried `the`.

**PPM no longer contributes word candidates.** `PPMWordPredictor` was constructed without a dictionary, so its dictionary-completion path was dead code and it ran as a bare character beam that emitted fragments (`ing` held a pill at every sentence start). Measured with the prefix beam in place (`scripts/bench/ksr.py --conditions ppm-merge,full --mis-click`): taking it out of the merge raised keystroke savings 53.6 -> 55.0% on clean typing and 47.9 -> 49.9% with a mis-click, next-word hits 30.7 -> 32.6%, and cut per-keystroke latency from 21 ms to 3 ms. It did not help in-domain learning (learn-half 53.8% with it against 54.4% without), and the one thing a character model could add, surfacing a word before its third sighting, never reached the bar, because `_is_valid_word` gates on the unigram table either way. `HybridPredictor._ppm_in_merge` (default `False`) is the switch. The model still trains on every `learn`, still loads and saves `ppm_model.json`, and `enable_ppm` still governs that, so a later fusion inside the prefix beam (the VelociTap shape) has a trained model to use. No user setting, for the reason the prefix beam has none. Any text elsewhere that describes the merge as "n-gram + PPM + fuzzy" describes it before this date; `docs/architecture/PPM.md` and `HYBRID_MERGING.md` carry a note.

## Click position and the learned pointer bias

The last of the 2026-09-02 recommendations, landed 2026-09-03, and the one whose measured gain is smallest; read the numbers before extending it.

**What travels.** `KeyButton.qml` always had `mouse.x / mouse.y` at the press and used it for the ripple. It now publishes `pressDx` / `pressDy`, the press as a fraction of the key's width and height from its centre (-0.5 to 0.5), and `Main.qml` hands them to the bridge with the character on all three char paths (`pressKey`, `pressKeyLiteral`, the right-click variant); auto-repeat re-reads the same values. The bridge slots are overloaded (`@Slot(str)` and `@Slot(str, float, float)` on one method with Python defaults), so every existing caller, tests included, still means "key centre". `_press_char` keeps the offsets in `_word_offsets`, parallel to `_current_word`, **each entry carrying the character it was recorded under**, and **does not try to maintain that list at every site that resets the word**: it re-syncs at the append (padding with centres when the recorded characters are not the word so far) and `_update_predictions` only hands the list on when the recorded characters spell the word (`_offsets_spell` / `_offsets_for_word`), so any path that rewrites `_current_word` without knowing about it degrades to today's key-centre behaviour rather than mis-scoring. The first version compared lengths, and a rewrite of the same length slipped through it: `hwllo` typed, the `hello` pill tapped, a backspace into it, and five offsets sat under four wrong letters (autocorrect has the same shape). Matching on the characters closes that without touching any of the sites that rewrite the word, the same argument `_token_pill_words` makes; backspacing into a word exactly as it was typed still carries its offsets, since those are the user's own presses. `HybridPredictor.predict` / `predict_with_refinement` take `offsets=` and pass them to `FuzzyRecognizer.get_fuzzy_predictions`, whose `positions_for` resolves each offset against the reported key's centre in the current spatial model (so it follows the layout) with the learned bias for that slot taken out, and the prefix beam scores the continuous position. Characters the model does not place (punctuation, the space bar) resolve to `None` and fall back to the key centre.

**What is learned.** `src/prediction/pointer_model.py` keeps, per **physical slot** (`slot_id` of the key's row and column, so the bias belongs to the pointer rather than the letter and survives a Dvorak or Colemak remap with no layout reset), the count and sum of offsets, and estimates each slot's bias as its own mean shrunk toward the global mean with `PRIOR = 10` pseudo-observations, Gboard's clustering idea in its simplest form. The bridge observes a press inside the not-privacy branch of `_press_char`, beside every other learning; unmapped characters are ignored; offsets are clamped to one key; nothing is logged. The table is owned by `NgramPredictor.pointer` for the reasons the token store is (one file for everything the user taught the engine, with the load caps, the backup archive and Clear Learned Data already in place; `pointer` key in `ngram_model.json`, malformed rows skipped one at a time) and the hybrid binds `FuzzyRecognizer.pointer` to that same object, which is therefore mutated in place and never rebound.

**Two emission widths.** `SpatialEmissions.KEY_SIGMA` (0.85) is the uncertainty when only the key is known: somewhere inside it, plus scatter. `POSITION_SIGMA` (0.55) is the uncertainty when the position inside the key is known. It was set by sweep and the sweep is the part to remember: sharper than 0.55 *hurt* both simulated pointers (0.3 lost 1.6 points of keystroke savings, 0.22 lost 6), because scatter then puts the intended key on the expensive side of the click more often than the extra precision helps.

**The gain is modest, and that is the finding.** `scripts/bench/ksr.py --pointer BIAS_X,BIAS_Y,NOISE` simulates a pointer end to end (reported key, offset, and the presses the bias is learned from) and reports each condition three ways. A pointer whose misses are mostly random (bias 0.2, 0.15; noise 0.3; about a quarter of clicks on the wrong key), the robustness check: keys only 50.5%, plus offsets 50.8%, plus learned bias 50.9%. One whose misses are mostly systematic (0.35, 0.25; 0.15; 22% wrong), the case this feature is for: 51.1%, 51.5%, **51.8%**. The earlier per-key simulation (75% to 86.5% intended-key recovery with a learned bias) was real but did not translate, because the prefix beam plus the dictionary already recover most single-key errors from the reported key alone; what a click position adds on top is a few tenths of a point, more for a systematic pointer than a scattered one. It ships because it never regresses at 0.55, costs nothing at runtime, is privacy-gated like every other learning, and because the persisted table is the first measurement of this user's actual pointer bias, which no simulation can supply. Tests: `tests/test_pointer_model.py`, `tests/test_click_position.py` (recognizer, bridge and persistence hops, each paired with the near-miss it must leave alone) and `tests/test_qml_click_position.py`, which loads `KeyButton.qml` on its own in a `QQuickView` (one item, not a Repeater delegate, so the scene point is reliable under the offscreen plugin) and presses at a known point, and which also calls the overloaded `pressKey` / `pressKeyLiteral` slots with three arguments and with one from a QML function against a real bridge: the Python tests call the slots directly and never touch Qt's overload resolution, and a three-argument call that failed to bind from QML would degrade every press to the key centre with no warning anyone would see.

## The apostrophe is optional in a typed prefix

Typing `ill` offers `I'll`, `hes` offers `he's`, `im` offers `I'm`. The rule is
one clause in `NgramPredictor._matches_partial`, the single choke point every
prefix match goes through: a word containing an apostrophe also matches a typed
prefix that its apostrophe-stripped form starts with, **and only when the user
has typed no apostrophe themselves** (once they have, `don'` already matches
`don't` exactly, and stripping on top would make the prefix mean less than what
was typed rather than more).

**It belongs in the n-gram's exact match, not in the fuzzy source, and that is
the whole finding.** A user who types `ill` for `I'll` has not mis-clicked: the
apostrophe costs an extra click here and a layer hop on the compact layouts, so
it is the character they skip deliberately. This is the same thing
`_APOSTROPHE_INSERTION_PROB` (0.50 against a generic 0.15) already encodes for
the whole-word path, one layer over. The fuzzy source structurally cannot
rescue it mid-word: the prefix beam reaches `i'll` only through an omitted
click at `LOG_OMIT` (-2.5), which is past `FREQUENCY_MAY_BUY` (-1.5), so
`_protect_exact_completions` clamps it below every completion of the live
prefix `ill`, and `ill` has eight. `he's` was not reached at all. Cheapening
the beam's apostrophe omission was tried and is the wrong lever: it prices a
deliberate skip as a motor error, and it still has to buy past eight exact
completions on frequency alone.

**Typing the apostrophe must not blank the bar.** `_press_char`'s gate on
whether to re-query was `char.isalpha()`, so the apostrophe threw the bar away
at the moment it was right: `don` put `don't` at the top, and the `'` cleared
it one click short of the word; `i'` discarded `I'm` / `I'll` / `I'd` / `I've`,
which are the entire reason to type an apostrophe there. That is the same
oversight the digit had, and which the comment at that call site already
describes. `_continues_a_word` is the gate now: letters always, plus `'` when
there are letters in front of it (a leading one carries no prefix, so asking
costs a round trip and returns nothing). It is deliberately a separate question
from the word-character rule in `_press_char` that decides what `_current_word`
keeps: that one says what a word is made of, this one says whether the run so
far is worth asking about. **The underscore is deliberately not in the gate**,
although `_press_char` keeps it in the word so `snake_case` stays one token: the
tokenizer keeps letters and apostrophes only, so after `snake_` the model
predicts from `snake` while the typed run is `snake_`, every pill is an exact
completion of a prefix that discards a typed character, and tapping `snake`
called `replace_text(6, "snake ")`, removing the underscore just typed. Until
the tokenizer and the gate agree on it, an underscore clears the bar as it
always did (`TestTypingTheApostropheKeepsTheBar::test_an_underscore_still_clears_the_bar`).

Measured on the held-out AAC corpora, counting every contraction occurrence and
typing it the way a user of this keyboard does (no apostrophe), the word is
offered somewhere while typing it **65.1% -> 94.5%** of the time; `i'm`, the
most common contraction in the set at 24 occurrences, was previously
unreachable at every prefix length. Keystroke savings are unchanged to the
decimal (49.1% aac-dev, 50.4% aac-test): those corpora type contractions *with*
their apostrophes, so the benchmark cannot see this at all and is a regression
check here, not a measurement of it. The remaining misses are possessives
(`doctor's`, `today's`) that are not in the vocabulary as words, which no
prefix rule can reach. Across a sweep of 4,056 two- and three-letter prefixes
only 10 change, 7 by gaining a contraction and **none by losing one**; the
words displaced are all rank 5-6 tail items.

Guarded by `tests/test_ngram_predictor.py::TestTheApostropheIsOptionalInATypedPrefix`
(the predicate), `tests/test_hybrid_predictor.py::TestASkippedApostropheStillFindsTheWord`
(the ranking, on the shipped word lists rather than a stub, because the claim
is about real frequencies) and
`tests/test_keyboard_bridge.py::TestTypingTheApostropheKeepsTheBar`. Every
positive is paired with the near-miss it must still reject, and the pairs that
bite are the ones a rule that matched too much would satisfy: `ill` must keep
offering `ill` and `illinois`, a prefix with no contraction behind it must grow
none, and a bare `'` must still ask nothing.

## Taught acronyms (why "PR" would never learn)

The engine could not hold an acronym, however many times it was typed.
`NgramPredictor._is_plausible_word` rejects a 1- or 2-letter word that is
not on the profile's `short_words` list, and a longer word with no vowel;
`pr` fails the first rule and `prs` the second, for exactly the reason
`th` and `xqz` do. `learn()` therefore dropped both before the 3-sighting
candidate gate, `_link_context` formed no `a -> pr` edge across the gap,
and the strip on load re-deleted them from any model that somehow held
them. The shape filter cannot tell a vowel-less acronym from a vowel-less
slip, and no rule over the letters ever will: they are the same shape.

**The evidence is what the user paid to type it.**
`NgramPredictor.is_taught_acronym(word)` is true when
`capitalization[word]` carries **two or more capitals**, which on this
keyboard means shifting or right-clicking each letter individually, since
`learn_capitalization` refuses an all-caps form unless Caps Lock was off
for the whole word (`_word_typed_under_caps_lock`). No new store, no new
setting: the capitalisation table already recorded exactly this, and
already has the load caps, the backup archive and Clear Learned Data
behind it.

Three things about it are load-bearing:

- **Two capitals, not one.** A leading capital is what every word at a
  sentence start carries, so `Th` from an interrupted word reaches
  `learn_capitalization` the same way an acronym does, and a one-capital
  rule would hand the fragment class the filter exists for a free pass.
- **`load()` merges `capitalization` *before* the fragment strip.** It
  used to merge after, and the strip asks `_is_plausible_word`, so every
  learned acronym would have been deleted on the way back in and the
  model could never hold one across a restart.
- **The next-word gate consults it too** (`HybridPredictor._next_word_allowed`,
  which both merge sites now call). Letting `pr` into the vocabulary is
  not enough on its own: `_short_word_allowed` would still drop it for
  being two characters, in the position the pill is worth the most.

`get_capitalized` returns the taught form under two further guards, and
this is the only thing besides the "I" family that it will capitalise.
It is **not** the removed Tier 3, which was wrong because it fired on
ordinary words and on forms the user had typed lowercase: the word must
not be in `_base_unigrams` (so `us`, `ok`, `it` keep their own casing
however they were once typed) and the taught form must be
**acronym-shaped**, all caps but for a plural `s` (so `ZigZaqCorp` stays
lowercase). The shape guard is what keeps this narrow, and the reason it
is needed at all is that `_display_cased` already renders a mixed-case
brand correctly from the typed prefix, while an acronym is the one case
that mirror cannot reach: every capital after the first falls outside any
prefix short enough to still want a pill, so `PR` came back `Pr` and got
retyped by hand.

Known limitation: the acronym has to be taught with per-letter shift or
right-click at least once. Typed under Caps Lock it teaches nothing,
because that is one click for the whole word and therefore no evidence
about it. Guarded by `tests/test_ngram_predictor.py::TestTaughtAcronymsAreLearnable`
and `tests/test_hybrid_predictor.py::TestTaughtAcronymsReachTheBar`, where
every positive case is paired with the near-miss it must still reject.
