# Word suppression and boosting

The full write-up behind the CLAUDE.md section *Word Suppression and Boosting*.

## Word Suppression and Boosting

Users can right-click prediction pills to:
- **Show more** - clears any prior dispreference and bumps `ngram_predictor.unigrams` / `user_vocab` by +5 (same magnitude as the prediction-click reinforcement), then records the boost in `ngram_predictor.preferred` so the dashboard can surface it and the user can roll it back.
- **Show less** - increments `ngram_predictor.dispreference` (word is downweighted by `1 / (1 + count * 0.5)`)
- **Remove** - adds to `ngram_predictor.blacklist` (word never appears again)

All three are persisted in `ngram_model.json`. Suppression is applied in `hybrid_predictor._merge_predictions()`; boosting is implicit in the bumped unigram counts (no separate multiplier - the engine treats a boosted word the same as a heavily-typed word).

### Boost rollback math
`unprefer(word)` decrements `unigrams` / `user_vocab` / `_user_total` / `total_words` by the cumulative boost amount, capped at the current `user_vocab` count so a word that was also organically learned keeps its organic count after the boost is removed. The `preferred` entry is then dropped. Boosts are never applied to bigrams or trigrams.

### Restoring Suppressed and Boosted Words
In the Model Visualization dashboard (Settings -> Your Language Model -> Open Dashboard -> Dashboard tab), three sections surface user-adjusted words as clickable tags:
- **Boosted Words** - green tags labelled `word (+N)` where N is the cumulative boost. Click to call `keyboard.unprefer(word)` which rolls back the boost (see math above).
- **Suppressed Words -> Blocked** - red tags for blacklisted words. Click to call `keyboard.unblacklistWord(word)`.
- **Suppressed Words -> Downweighted** - yellow tags for dispreferred words. Click to call `keyboard.undisprefer(word)`.

Each section is hidden when the corresponding list is empty (`preferredCount > 0`, `blacklistCount > 0`, `dispreferenceCount > 0`).

Bridge slots: `keyboard.markGoodSuggestion(word)`, `keyboard.markBadSuggestion(word)`, `keyboard.blacklistWord(word)`, `keyboard.unprefer(word)`, `keyboard.unblacklistWord(word)`, `keyboard.undisprefer(word)`.

### Auto-Rehabilitation
If a user manually types a blacklisted word 3 times (completing it with space), the word is automatically restored to predictions. Tracked via `ngram_predictor._blacklist_type_count`, persisted in `ngram_model.json`.


