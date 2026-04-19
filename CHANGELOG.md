# Changelog

All notable changes to Alpha-OSK are documented in this file.

## [Unreleased]

### Fixed
- **OSK keystrokes now actually land in the edit-prediction popup.** Follow-up to the dismissal fix: even after the popup stopped vanishing, pressing OSK keys had no effect because (a) `modal: true` installed an overlay that swallowed the MouseArea events and (b) even without the overlay, OSK keys synthesize via xdotool/SendInput to the *OS-focused app* behind Alpha-OSK, not the popup's QML TextField. Added `KeyboardBridge.setEditMode(bool)` plus `editKeyTyped(str)` / `editSpecialPressed(str)` signals; when the popup opens it flips edit mode on, `pressKey` / `pressSpecialKey` short-circuit the synthesizer and emit instead, and the popup's `Connections {}` mutates the TextField directly (insert at cursor, backspace, arrow-key cursor motion, home/end, return-to-accept, escape-to-cancel). Shift/caps still affect letter case; ctrl/alt/win are ignored in edit mode so stray chords can't leak to the app behind us.
- **Prediction pills now match Caps Lock display case.** With Caps Lock on, the user's `_current_word` was accumulating uppercase (e.g. "HELL") while the pills still showed lowercase ("hello"). Even worse, clicking a pill inserted the lowercase text next to the uppercase prefix. Added a `_display_cased()` transform applied at every prediction emit site (instant, refined, next-word-after-selection, edited, swipe) that uppercases the engine's output while Caps Lock is active. Toggling Caps Lock while pills are visible now re-queries the engine so the visible pills flip case immediately. Shift is deliberately not mirrored — it's one-shot and sentence-start capitalisation is already handled upstream by `get_capitalized`.
- **Edit-prediction popup no longer dismisses on the first OSK keystroke.** The popup's `closePolicy` included `Popup.CloseOnPressOutside`, so every click on an OSK character or arrow-row key registered as a "press outside the popup" and closed it before the keystroke could land. Dropped `CloseOnPressOutside` from the policy and added an explicit ✕ cancel button next to the ✓ confirm button; Escape still dismisses too.

### Added
- **Prediction pill reveals full word on hover when truncated.** Long predictions are elided with `Text.ElideRight` when the pill is narrower than the word (common at high prediction counts or narrow window widths). Hovering over a truncated pill now surfaces a tooltip with the full word after a 400 ms delay. Gated on `predText.truncated` so short words that already fit don't trigger a redundant tooltip.
- **Linux: atomic prediction replacement via `replace_text()`.** Picking a prediction whose casing or prefix differs from what was typed (e.g. "iph" → "iPhone") used to fall through to sequential `xdotool key BackSpace` calls, which raced with xdotool's subprocess latency and produced visible stuttering / apparent duplicated characters in fast typers' sessions. `LinuxKeySynthesizer.replace_text()` now chains N `shift+Left` chords into a single `xdotool key` invocation then a separate `xdotool type`, mirroring the Windows single-`SendInput` path. Wayland / ydotool gets the equivalent `--key-down shift` → `Left`×N → `--key-up shift` → `type` sequence.
- **Linux: app-switch context reset.** Predictions, current word, and sentence buffers now clear when the user switches applications on X11, same as Windows. `KeyboardBridge._get_foreground_window_id()` dispatches: Windows → `GetForegroundWindow` via ctypes (unchanged), X11 → `xdotool getactivewindow` (~5 ms at 4 Hz), Wayland → no-op (compositors don't expose focused window to unprivileged clients).
- **Linux: password-field auto-detection via AT-SPI 2.** Privacy mode now flips on automatically when focus lands on a password field (GTK `GtkEntry` with `visibility=false`, Qt `QLineEdit` in password echo mode, browsers that expose accessibility metadata). Implementation in `src/platform/password_detect.py` spawns a daemon thread that owns a GLib event loop and listens for `object:state-changed:focused`; the focused accessible's state set is checked for `STATE_PASSWORD_TEXT`. Requires `python3-gi` + `gir1.2-atspi-2.0` on the host; falls back silently to the null detector (manual toggle still works) if either is missing.

### Chores
- Added `tests/test_password_detect.py` (12 tests) and `TestForegroundWindow` / `TestLinuxReplaceText` classes covering the new Linux paths. Tests mock `subprocess.run` and the `gi` import so they run on any OS, including the Windows CI lane where `xdotool` / AT-SPI don't exist.

## [1.0.7] — 2026-04-16

### Fixed
- **Auto-updater downloads no longer fail on the post-redirect host check.** GitHub's release-asset CDN moved from `objects.githubusercontent.com` to `release-assets.githubusercontent.com`; the MITM-defence host whitelist only knew about the historical name, so every legitimate v1.0.6 download was rejected with "Update download or signature verification failed". The new hostname is now in `_ALLOWED_DOWNLOAD_HOSTS`. The two pinned hostnames (plus `github.com`) are spelled out in `src/updater.py` rather than allowing `*.githubusercontent.com` so an attacker who finds a way to publish content under the wider umbrella can't redirect us there.
- **Update banner now allows retry after failure.** `Install` was permanently disabled the moment `updateError !== ""`, so a transient network blip required dismissing the banner and waiting for the next auto-check to recover. The button now stays enabled and reads "Retry" after a failure; clicking it clears the error and re-enters the install path.
- **Failed updates now name the failed step in the banner.** `download_and_install` returns a `(ok, error)` tuple; the bridge forwards the short step-specific message ("Download failed", "Signature check failed") instead of the generic "Update download or signature verification failed".

### Required manual install
v1.0.5 / v1.0.6 users have to install v1.0.7 by hand once because their auto-updater can't pull this build (their host whitelist is the broken one). Auto-update works for every release after that.

## [1.0.6] — 2026-04-16

### Fixed
- **`=` / `+` key restored to the number row** — all three built-in layouts (QWERTY, Colemak, Dvorak) jumped straight from `-` / `_` to Backspace, missing the `=` / `+` key that lives between them on a real keyboard. Added back in `data/layouts/{qwerty,colemak,dvorak}.json`.

## [1.0.5] — 2026-04-16

### Fixed
- **Auto-updater now actually works** — v1.0.3 and v1.0.4 hard-coded the GitHub API endpoint to the source repo (`okstudio1/alpha-osk`), which is private. Private repos return 404 on `/releases/latest` to unauthenticated callers, so the updater always silently saw "no update available". The endpoint is now `okstudio1/alpha-osk-releases` — a separate public repo whose only purpose is to host release binaries. The source repo stays private. **One-time manual install required:** v1.0.3 / v1.0.4 users need to install v1.0.5 by hand once; auto-update works for every release after that. Diagnosis + threat-model update in `docs/AUTO_UPDATE.md`.

### Added
- **Installed version surfaced in Settings → Updates** — `KeyboardBridge.appVersion` reads from `src/__version__.py` and is shown above the "Check for updates on startup" toggle. Easiest way to confirm an upgrade actually landed.

## [1.0.4] — 2026-04-16

### Changed
- **Installer ~48 % smaller** — `Qt6WebEngineCore.dll` (193 MB by itself, half the bundle) and the rest of the PySide6 WebEngine / WebView / WebChannel families are now stripped in `build/alpha-osk.spec`. Alpha-OSK never embeds a browser; PyInstaller was pulling them in transitively. Module-level `excludes` alone weren't enough — the PySide6 hook still copied the matching Qt DLLs verbatim. The spec now also walks `a.binaries` after Analysis and removes 7 entries by filename pattern. Installer drops from 164 MB to **85 MB**; uncompressed install folder from 418 MB to **224 MB**.

### Fixed
- **`ReferenceError: parent is not defined` no longer spams the log on every privacy-mode toggle** — the privacy-mode play/pause icon's repaint trigger lived inside a `Connections {}` block that referenced `parent.children[0]`, but `parent` doesn't resolve inside Connections. The icon glyph silently failed to repaint. Now references the Canvas by `id` directly.
- **`ruff check` no longer fails on `tests/test_updater.py`** — removed an unused `pathlib.Path` import left over from an earlier draft.

## [1.0.3] — 2026-04-16

### Added
- **Auto-update with MITM hardening** — Alpha-OSK now checks GitHub Releases on startup (3 s after launch) and shows an in-app banner when a newer signed installer is available. Click *Install* and the app downloads, **verifies the installer's Authenticode signature against our pinned EV-cert thumbprint**, and runs it silently. The NSIS installer kills the running app, runs the previous uninstaller, and installs the new build.
  - Layered defences against attacks on the update channel: HTTPS-only with cert validation, host whitelist (`github.com` / `objects.githubusercontent.com`), strict semver compare (refuses pre-release/garbage tags so `v1.0.3-evil` can't pass), filename pattern lock (`Alpha-OSK-Setup-{version}.exe`), byte-cap downloads (500 MB), post-redirect host re-validation, and the load-bearing **Authenticode pin** against thumbprint `fc22b522…` + `Status == Valid` + signer CN `OK Studio Inc.` before any exec. Release notes are sanitised before reaching QML.
  - QML never sees the download URL — the bridge holds the `UpdateInfo` from the check, so a compromised QML can't substitute an attacker URL into the install path.
  - Threat model + per-defence rationale in `docs/AUTO_UPDATE.md` and `CLAUDE.md`.
  - User-facing toggle: *Settings → Updates → "Check for updates on startup"* (default on). Manual *Check Now* button next to it.
- **Single source of truth for the version** — `src/__version__.py`. `build/build_windows.py` reads from it; the updater compares against it. Bumping the version is now a one-line change.

## [1.0.2] — 2026-04-16

### Security
- **Path traversal in `PackManager.import_pack` (CRITICAL)** — a source folder whose pathlib `.name` resolves to `..` would have made `dest_dir` the parent of the user packs directory, and the existing `shutil.rmtree(dest_dir)` call would then have wiped the app config root (learned models, settings). Now the pack id is sanitised with a strict `[a-z0-9_-]{1,64}` pattern and the resolved destination is verified to sit under `user_packs_dir`; symlinks in the source tree are skipped rather than dereferenced.
- **Background QTimers no longer fire during shutdown (HIGH)** — `_password_timer` and `_foreground_timer` are now stopped from `KeyboardBridge.shutdown()` via `QApplication.aboutToQuit`, so a final `timeout` cannot run against half-collected bridge state.
- **JSON model load capped (HIGH)** — `NgramPredictor.load` / `PPMPredictor.load` refuse to open files larger than 50 MB, and the n-gram loader also rejects files with more than 500k unigrams, 500k bigram prefixes, or 100k capitalisation entries. A hostile or corrupted model file in `%APPDATA%/alpha-osk/models/` can no longer OOM the app at startup.
- **Password-field detection race closed (HIGH)** — `pressKey` / `pressSpecialKey` now call `is_password_field()` synchronously (rate-limited to ~50 ms) before touching prediction state, instead of relying on the 200 ms background timer.
- **`editPrediction` input sanitised (MEDIUM)** — `_sanitize_edit` strips control characters, caps length to 64, and rejects empty-after-strip so malformed QML input can't persist junk into the capitalisation table.

### Added
- **Swipe / glide typing** — drag the mouse across letters to type a whole word in one gesture (Gboard-style). Uses simplified SHARK² shape matching against the dictionary, with a frequency prior. Off by default; toggle in *Settings → Suggestions → Swipe Typing*. Design doc: `docs/SWIPE_TYPING.md`.
- **Deep-dive algorithm docs** — `docs/FUZZY_RECOGNITION.md` (spatial model + accessibility profiles), `docs/PPM.md` (variable-order character model + PPMD escape), `docs/HYBRID_MERGING.md` (merge weights + validation + capitalization).

### Changed
- **Next-word scoring now uses linear interpolation** — `NgramPredictor.predict()` ranks candidates by `λ₃·P(w | w₋₂, w₋₁) + λ₂·P(w | w₋₁) + λ₁·P_uni(w)` with λ = 0.5 / 0.3 / 0.2, all in probability space. Previously, raw trigram/bigram frequencies (× 2–3) competed against a 100 000-scaled unigram probability, so the global unigram favourite drowned real context — "I want " predicted "the" instead of "to". When there is no preceding word, the formula collapses to `P_uni` at full weight so partial-prefix completion isn't flattened.
- **Keyboard-slip fragments no longer enter the learned vocabulary** — `NgramPredictor.learn()` gates unknown words through a shape check (length ≤ 2 must match a small whitelist; length ≥ 3 needs both a vowel and a non-`aeiou` letter — `y` counts as both, so "eye" and "cry" pass but "aaaa" and "xqz" don't). Surviving unknown words go through a repetition gate: counted in a candidate pool until 3 sightings, then promoted. Known base-dict words and `learn_word()` bypass the gate. Candidate counts decay with the rest of user vocab and persist across save/load.
- **Personal vocabulary now outranks dictionary words in predictions** — the n-gram unigram scoring now blends a separate base-dictionary table with the user's personal typing counts in probability space (`P = α·P_user + (1−α)·P_base`, α = 0.7 by default). Previously, a word typed 10 times scored ~10 while a common dictionary word scored ~5,000; now a few uses is enough for a personal word to rise to the top for its prefix. Tunable via `NgramPredictor.personal_weight`. See `docs/HYBRID_MERGING.md` → "Personal vs. Base Vocabulary".
- **Tray icon double-click now minimizes** — single click still toggles show/hide, but a double-click on the system tray icon sends the keyboard to the minimized state (same as clicking the `−` button in the title bar). A short timer on the single-click action waits for the system's double-click interval so the two gestures don't fight.
- **Encapsulation** — `keyboard_bridge.processSwipe` now calls `HybridPredictor.get_unigram_freqs()` / `get_capitalized()` instead of reaching through `_predictor._ngram`.
- **`NgramPredictor._user_total` is tracked incrementally** — `learn` / `learn_word` / `_apply_decay` / `clear_user_data` / `load` all keep the running total in sync, removing an O(N) `sum()` from every keystroke's `predict()` call.

### Fixed
- **Double-typed keystrokes eliminated** — two separate causes were firing a single click as two characters:
  1. No software debounce on the MouseArea press, so hardware button bounce (common on cheap / worn / adaptive mice) would emit two press events.
  2. Character keys had auto-repeat enabled with a 400 ms delay; a slightly-slow click held past that threshold would fire the key twice.
  
  `KeyButton` now debounces press events within 150 ms of the last accepted one, and auto-repeat is off by default. Only backspace (main keyboard) and the arrow / Delete / PageUp / PageDown keys in the navigation panel opt in to repeat — where it's actually useful for held navigation.
- **Predictions now honour capitalization** — picking a prediction like "iPhone" after typing "iph" no longer outputs "iphone". Suffix-only typing now requires a case-sensitive prefix match; mismatched casing falls back to select-and-replace.
- **Caps Lock no longer also turns Shift on** — caps and shift are independent toggles. Letter keys still display uppercase under either, but the Shift key is no longer forcibly highlighted while caps is active.
- **Held key auto-releases on drag-off** — moving the cursor off a key while held now stops the key-repeat timer immediately, instead of continuing to fire until the mouse button is released.
- **Base dictionary no longer pollutes personal vocabulary** — `load_base_dictionary` previously routed through `learn()`, which added every dictionary word to `user_vocab`, making recency decay eat real personal typing alongside base words. Now routes through `_learn_base()` which updates only the base table.

## [1.0.1] — 2026-04-14

### Added
- **System tray icon** — Alpha-OSK now appears in the notification area with the app logo. Right-click for Show/Hide and Quit. Double-click to toggle keyboard visibility.
- **Branded installer** — NSIS installer now shows the Alpha-OSK logo on welcome/finish sidebar, header image on all pages, and custom welcome text with feature highlights.
- **New app icon** — custom "A" logo (Midjourney-generated) embedded in the exe, shortcuts, installer, and system tray. Multi-resolution ICO (16–256px).
- **Auto-space after comma, semicolon, colon** — mid-sentence punctuation now inserts a trailing space (same as sentence-ending punctuation), without triggering auto-capitalize.
- **Build & release documentation** — comprehensive build/sign/release checklist added to CLAUDE.md with prerequisites, troubleshooting, and installer upgrade behavior.
- **Branding guide** — Midjourney prompts, asset specs, color palette, and icon generation workflow in docs/BRANDING.md.

### Fixed
- **Modifier+click now works** — Ctrl, Alt, and Win keys are held at the OS level via SendInput (Windows) / xdotool (Linux), so Ctrl+click to open hyperlinks and similar modifier+mouse combos work correctly.
- **Prediction selection no longer outputs fragments** — backspace + replacement text is now sent as a single atomic SendInput call, preventing race conditions that produced output like "ose" instead of "choose".
- **Typed fragments no longer pollute the model** — selecting a prediction no longer learns the partial word that was being replaced.
- **Key repeat disabled on character keys** — only navigational keys (backspace, arrows, etc.) repeat on hold, preventing accidental repeated characters.
- **Clear User Data actually clears everything** — now flushes unigrams, bigrams, trigrams, PPM state, and the capitalization dict. Saves to disk immediately so stale model files don't resurrect weird learned words on restart.
- **Installer removes previous versions on upgrade** — same-directory upgrades now silently run the old uninstaller before extracting new files, removing orphaned files from prior versions. User's learned vocabulary is preserved during upgrades.
- **Model visualization shows only user-typed words**, not pretrained dictionary data.
- Removed quotes from no-space-before punctuation set.

### Chores
- Added PyInstaller output and `.coverage` to `.gitignore`.
- Source logos stored in `assets/`.

## [1.0.0] — 2026-04-12

Initial public release.
