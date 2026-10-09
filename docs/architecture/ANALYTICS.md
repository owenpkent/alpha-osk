# Analytics and the model visualization

The full write-up behind the CLAUDE.md sections *Model Visualization* and *Analytics*.

## Model Visualization

Accessed via Settings -> Your Language Model -> Open Dashboard. Three tabs:
- **Word Cloud** - circle-packed bubble chart of top words, sized by frequency
- **Word Flow** - network graph of bigram word->word connections
- **Dashboard** - embedded AnalyticsDashboard (lifetime/session typing stats) at top, then stats cards, top words bar chart, interactive boosted words, interactive suppressed words, top word pairs. The AnalyticsDashboard was previously a separate section at the top of the Settings panel; it was moved here because lifetime savings is user-typing data and belongs with the rest of the user's model.

Data provided by `keyboard_bridge.getVisualizationData()` -> `ModelVisualization.qml`.

### Click-to-drill-down

Clicking a circle in the Word Cloud or a node in the Word Flow opens a side panel with that word's top successors (bigram `word -> next`), top predecessors (`prev -> word`), and trigram windows (`X word Y` middle position + `X Y word` trailing position). Predecessor / successor entries are themselves clickable - click "asked" under "claude"'s predecessors to drill into "asked". Data comes from `keyboard.getWordContext(word)` (bridge slot) which reads `ngram.bigrams` / `ngram.trigrams` directly - no extra tracking. Hit-testing is canvas-side: a `MouseArea` over each canvas walks `circles[]` / `nodes[]` and matches against squared-distance-to-center, so the click target is the visible circle. Selected node is outlined white; the drill-down panel slides in from the right at z=5 over Cloud and Flow tabs (hidden on Dashboard since it has its own Suppressed Words drill-in).

### Live pulse on the active edge

While the visualization window is open, typing in the foreground app pulses the matching node and edge. Driven by `KeyboardBridge.activeContextChanged(prev_word, current_partial)`, emitted from `_update_predictions` on every keystroke (suppressed in privacy mode - must not leak password chars or password-field context). The viz holds `activePrevWord` / `activeCurrentWord` properties and the canvases compare `n.word === activePrevWord || n.word === activeCurrentWord` per node and `from.word === activePrevWord && to.word === activeCurrentWord` per edge. Active node gets a warm gold glow + `#ffd84d` border; active edge draws gold with thicker stroke. The signal is intentionally cheap - raw lowercased tokens, no formatting - and the viz drives a short pulse off the property rebinding, no Timer per canvas.



## Analytics

`src/analytics.py` tracks session and all-time stats. All-time stats persist to `<config_dir>/analytics.json`.

Every session counter has an `_alltime_*` mirror that's loaded on launch, merged with the session at exit, and surfaced in `get_session_stats()` as both `<metric>` (session) and `alltime<Metric>` (lifetime). The dashboard's Lifetime / Session toggle (`AnalyticsDashboard.qml`) drives every tile off these paired keys. Persisted fields include: keystrokes, words, predictions (hits), keystrokes_saved, sessions, minutes, **backspaces, prediction_offers, prediction_rank_sum/count, top_pick_count, word_freq, key_freq**. Word and key frequencies are each capped at 5000 unique entries (`_WORD_FREQ_CAP` / `_KEY_FREQ_CAP`), applied on both load and save (top-N by count), so `analytics.json` stays bounded over years of typing. `_load_alltime` also `stat()`s the file against `_MAX_STATS_FILE_BYTES` (5 MB) before reading it at all, and parses into local variables first, only assigning to `self` once every field has parsed successfully, so a bad type or a cap trip partway through a load can never leave the in-memory lifetime counters half-updated.

The dashboard is now a **single section**: scope toggle (Lifetime / This Session) + 2x2 tile grid + sparkline + top words. Earlier versions layered a separate hero card ("10.3k keystrokes saved" with green border), an all-time stats pill row (words / sessions / hours), and a horizontal divider above the tile grid; the user reported it read as 4 disconnected sections rather than one analytics view. Promoting Keystrokes Saved into the tile grid carries the headline number, and the words/sessions/hours pills were dropped (sessions and hours weren't load-bearing; words is implicit from the prediction-related tiles).

The four tiles are **Keystrokes Saved** (formatted count, subtext "keys you didn't have to press"), **Time Saved** (formatted hours/min from `keystrokes_saved x user's own seconds per keystroke`, falling back to 0.5s/key for new installs; subtext "avoided by predictions"), **Effort Saved** (`savingsPercent`, subtext "of total keystrokes"), and **Acceptance** (`acceptanceRate` = `prediction_hits / prediction_offers`, subtext "of offered suggestions accepted"). Keystrokes Saved + Time Saved + Effort Saved are three framings of the same underlying engine output: absolute count, wall-clock, and percentage respectively. They're shown together because each lands differently with different mindsets (a daily-saving thinker, a wall-clock thinker, a relative-effort thinker). Acceptance is **distinct from** the others: it asks "when the keyboard offered a suggestion, how often was it useful enough to take" (an engine quality signal), independent of how many keystrokes the user typed total. All four subtexts are deliberately verbose ("of total keystrokes" not "of typing effort") to make the denominator unambiguous; the user iterated on terser variants and found them ambiguous.

Earlier iterations also had **Typing Effort** (total keystrokes typed) and **Predictions Used** (hit rate %) and **Corrections** (backspace count) tiles. All three metrics are still tracked and exposed in `getAnalytics()` (`alltimeKeystrokes`, `predictionHitRate`, `alltimeBackspaces`, etc.) because the Model Visualization Dashboard and other callers may use them; only the AnalyticsDashboard surface dropped them. WPM lived on the first tile briefly but was unusable on cold sessions (a fresh "0.5 avg wpm" reading next to a lifetime "103 hrs saved" hero card visually contradicted itself).

The `StatBox` component grows its background Rectangle from `contentCol.implicitHeight + 14` rather than using a fixed `implicitHeight: 50`. The fixed height was ~10 px shorter than the three text elements need, so subtext rendered past the rounded gray background. If you add a fourth Text element to StatBox, this binding still works as long as the inner ColumnLayout is anchored only horizontally + verticalCenter (don't switch to `anchors.fill: parent`, which would break the implicit-height computation by yoking layout size to rectangle size).

The earlier composite Prediction Quality Score (0-100, weighted savings + hit rate + rank + low-correction) was removed because the number wasn't actionable: a user can act on "you've saved 4.2 hours" but a "73/100" composite hides which lever moved. Don't reintroduce the composite as a primary surface; if you need a single internal scoring number for ranking strategy comparisons, compute it ad-hoc in tests rather than baking it back into `get_session_stats`.

`top_pick_count` is still computed and persisted, incremented inside `record_prediction_selected` only when `rank == 1`, and surfaced as `alltimeTopPickRate` for the Model Visualization Dashboard. It was briefly the subtext on the Predictions Used tile but reads "0%" for any user upgrading from a prior build (the counter didn't exist then), which masked real usage. The bridge already passes a 1-based rank in `pressPrediction`, so a new prediction surface needs no caller-side change: it just has to call `record_prediction_selected` with the right rank.


