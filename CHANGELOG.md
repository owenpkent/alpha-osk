# Changelog

All notable changes to Alpha-OSK are documented in this file.

## [Unreleased]

## [1.0.14] — 2026-04-28

Right-click on a key types its shifted variant, like the Windows on-screen keyboard. Plus the design doc for the long-press alternates feature (deferred — see "Why this is paused" section in the doc).

### Added
- **Right-click → shifted variant.** Right-click on any character key types the shifted glyph (`!` from `1`, `<` from `,`, `A` from `a`) without flipping the sticky shift state. Modifier and special keys are deliberate no-ops. Toggle in *Settings → Input → "Right-Click for Shifted Character"*; default ON since left-click behaviour is unaffected. `KeyButton.qml` gains a `keyRightPressed` signal; `MouseArea` accepts both buttons but the right-button branch returns before the auto-repeat path so right-click is always a one-shot. Settings flow mirrors the swipe toggle (`savedRightClickShift` in the QML `Settings {}`).
- **`KeyboardBridge.pressKeyLiteral`.** New `@Slot(str)` that types a character verbatim, bypassing the shift / caps-lock case normalization that `pressKey` applies. Right-click goes through this slot — `pressKey` was lowercasing the `'A'` we'd already chosen back to `'a'`. Internally both slots now delegate to `_press_char(key, literal)`; existing `pressKey` callers and behaviour are unchanged.
- **`docs/LONG_PRESS_ALTERNATES.md` — design doc for the deferred Gboard-style press-and-hold-for-accents feature.** Includes the data file format (`data/key_alternates.json`), the popup architecture, the press-on-release timing change required to implement it, and the reason it's paused: making press timing depend on a hold threshold is hostile to slow-motor users, which is exactly the audience this OSK serves. Picked back up when adding non-English layouts or when a user explicitly asks for diacritics.

### Internal
- **`KeyButton.keyRightPressed`** is the new contract for right-click handling. Press visuals + ripple still fire so the user gets the same tactile feedback as a left-click; only the auto-repeat timer is skipped (right-click is a one-shot).

## [1.0.13] — 2026-04-26

Lifetime analytics, a stuck-key visual fix, the height-binding bug behind several recent layout complaints, a Windows code-review sweep, and a local pre-push CI parity script.

### Added
- **Lifetime analytics — every metric, not just totals.** `src/analytics.py` already persisted four lifetime counters (words, keystrokes saved, sessions, minutes), but everything else — WPM, prediction hit rate, savings %, backspace rate, top words, key frequencies, prediction quality score — was session-only. Now every session counter has an `_alltime_*` mirror that's loaded, merged on save, and surfaced in `get_session_stats()` as `alltime<Metric>`. `_compute_quality_score` takes kwargs so the same code computes either session or lifetime score. `AnalyticsDashboard.qml` got a Lifetime / Session toggle that drives every tile (Speed, Saved, Predictions Used, Corrections, Top Words). The "Prediction Quality" bar always reads lifetime — session quality is noisy until you've typed enough words. `word_freq` is capped at 5000 unique entries on save (top-N by count) so `analytics.json` stays bounded.
- **Single-instance lock.** Running `alpha-osk.exe` twice no longer spawns two windows. `keyboard_app.py::_acquire_singleton_or_surface` takes a `QSharedMemory("alpha-osk-singleton-v1")` lock right after `QApplication` is constructed; on Windows duplicates, the new process enumerates top-level windows for one titled "Alpha-OSK", calls `ShowWindow(SW_RESTORE)` and `SetForegroundWindow`, and exits 0 — so a second launch surfaces the existing keyboard instead of stacking another. POSIX recovery (attach + detach + retry create) handles the case where a crash leaves a sysv segment behind.
- **UAC / Secure Desktop guidance in the help panel and docs.** Owen asked whether Alpha-OSK could appear during admin-password prompts the way Microsoft's `osk.exe` does. Short answer: no third-party app can run on the Secure Desktop, but the user can move UAC consent prompts back to the regular desktop via `secpol.msc` → "Switch to the secure desktop when prompting for elevation" → Disabled (or `PromptOnSecureDesktop=0` in the registry). The help panel walks through the steps with the security trade-off called out; `docs/WINDOWS.md` has the full technical writeup. Also corrected the existing UIAccess docs / manifest comment that incorrectly claimed UIAccess covered the Secure Desktop — it doesn't.

### Fixed
- **Window height stops following content after persistence kicks in.** This was the root cause of three separate-looking complaints: the bottom row clipping when the keyboard was widened, big empty bands above and below the keys, and weird centering at certain sizes. `Main.qml` declares `height: outerLayout.implicitHeight + 60` as a binding so the OSK auto-sizes to its content. v1.0.11's window-size persistence imperatively assigned `root.height = appSettings.savedWindowHeight` in `Component.onCompleted`, which **destroys** the binding — once dead, height stays wherever the saved value put it while content keeps changing with width. Stopped persisting height entirely (the resize handles are `SizeHorCursor` only, so width is the only user-controlled dimension); width is still saved as before. Old `savedWindowHeight` values in existing installs become harmless dead settings.
- **Keys could appear visually stuck-down after dragging off them.** With `WS_EX_NOACTIVATE`, Qt occasionally drops the release event when the cursor leaves the OSK window onto another app's window — `mouseArea.pressed` stayed true, the key stayed visibly pressed, no automatic recovery. `KeyButton.qml` now drives press visuals off a `_visualPressed` property that's set on press and cleared on release, cancel, drag-off, OR a 5 s safety timeout — four independent paths back to neutral. The keyPressed signal still fires once on press; only the visual feedback got the defensive treatment.
- **Prediction pills didn't resize with the window.** `predPillHeight` / `predFontSize` / horizontal padding / minimum width were all hardcoded — at default geometry pills were correctly sized, but widening the window left them looking like tiny tags next to grown keys. They now scale with `keyW` / `keyH` (clamped to the historical defaults at the default geometry, so this is invisible at the original size).
- **PrtSc / ScrLk / Pause shorter than the rest of the nav grid.** The system-keys row used `cellH * 0.72` while every other row used full `cellH`. Now they match. Font bumped 9 → 10 to match.
- **`KEYBDINPUT.dwExtraInfo` typed as `POINTER(c_ulong)` allocated dangling Python pointers.** MSDN documents the field as `ULONG_PTR` (a pointer-sized integer). The struct definition in `src/platform/windows.py` allocated `ctypes.pointer(ctypes.c_ulong(0))` per event in three places — the underlying `c_ulong` could be reaped before SendInput consumed the INPUT struct. Hadn't bitten us because the kernel doesn't dereference the field, but it was UB. Aliased `ULONG_PTR = ctypes.c_size_t`, retyped the field, set `dwExtraInfo = 0` at all three call sites.
- **`GetWindowLongW` / `SetWindowLongW` failures were silently swallowed.** `_apply_windows_extended_styles` now pins `argtypes` / `restype` so 64-bit Windows doesn't truncate handles, and uses the `SetLastError(0)` + `GetLastError` pattern around both calls (and around `SetWindowPos`) so a real failure logs at WARN instead of letting the OSK run without `WS_EX_NOACTIVATE` and steal focus on every click.
- **Foreground-window detection swallowed every exception.** `keyboard_bridge._get_foreground_window_id` had `except Exception: return 0`, masking real Win32 errors (ACCESS_DENIED, missing `xdotool`, etc.). Errors are now logged once per unique exception type so the issue surfaces in logs without flooding at the 4 Hz poll cadence.
- **`CoInitializeEx` in the Windows password detector wasn't paired with `CoUninitialize`.** Negligible at process exit (the OS reaps it) but the COM apartment wasn't released on graceful shutdown, and `CoCreateInstance` failure left it leaked. `_WindowsUIADetector` now tracks `_owns_com` and `close()` releases the IUIAutomation interface and uninits if we own it. Wired into `KeyboardBridge.shutdown`.

### Changed
- **Updater API URL pinning is now exact-equal, not `startswith()`.** `src/updater.py` checks `api_url == GITHUB_API_URL` so a future careless override can't aim the updater at any other endpoint within the same repo. `startswith()` was already host+repo-pinned so the practical exposure was nil, but `==` is tighter.
- **Installer welcome-page copy is now marketing, not a feature list.** Was: "Alpha-OSK is an AI-powered on-screen keyboard for Windows. Features: smart word prediction… UIAccess… 9 themes…". Now: "The smartest keyboard you'll never touch. Click less. Type faster. Alpha-OSK predicts what you want to say before you finish typing it." Takes effect on the next `python build/windows/build.py` run since it's baked into the NSIS script.

### Internal
- **`check.py` — local pre-push CI parity.** Runs the same three gates GitHub Actions runs (`ruff`, `mypy`, `pytest`) so lint failures get caught locally instead of after a red CI run. Default mode skips coverage tracking (~85 s); `--full` adds the `--cov-fail-under=60` gate to match CI exactly (~3 min). Ships with a `[tool.mypy.overrides]` block in `pyproject.toml` that sets `follow_imports = "skip"` on `huggingface_hub.*` / `transformers.*` / `torch.*` — those are optional transitive deps via the commented-out transformers extra in `requirements.txt`; CI doesn't install them, but if they're present locally mypy used to choke on `huggingface_hub`'s py3.10 `match` syntax (we target py3.9). `CLAUDE.md` has a new "Pre-push check" section under Testing.
- **`KeyButton._visualPressed`** is the new contract for press visuals. New visual bindings should use `keyRoot._visualPressed`, not `mouseArea.pressed`.
- **`_SINGLETON_LOCK`** in `keyboard_app.py` is module-level on purpose — `QSharedMemory`'s segment is freed when the holding object is destroyed, so a function-local would release the lock immediately. If you refactor `_acquire_singleton_or_surface`, keep the reference alive somewhere with longer lifetime than `QApplication`.
- **`predPillHeight` decoupling**: an interim version of the resize-fix work made `predPillHeight` track `keyW` instead of `keyH` to break a (genuine) binding loop. The height-binding fix above made that loop impossible — `predPillHeight` is back to tracking `keyH * 0.72` like the original.

### Fixed (lint hotfix)
- **`E501` in `src/analytics.py:135` and `I001` in `src/keyboard_app.py:37`** — the two ruff errors that turned the first push of this work red on CI. Line-broke the `prediction_rank_*` assignments and reordered the `PySide6.QtCore` import to put `QSharedMemory` before `Qt` (case-insensitive alphabetical).

## [1.0.12] — 2026-04-25

Hotfix: 1.0.11 wouldn't launch.

### Fixed
- **QML duplicate-handler crash on launch.** The window-size persistence work in 1.0.11 added top-level `onWidthChanged` / `onHeightChanged` handlers without noticing that handlers for those signals were already declared further down the file. Qt rejects duplicate signal handlers on the same object with "Property value set multiple times" and the QML file failed to load — the app started, logged the error, and exited. Merged the size-save call into the existing handlers instead. The dev build masked the issue (it elevated and exited fast enough that the failure wasn't obvious); the frozen 1.0.11 installer hit it on every launch. Anyone on 1.0.11 needs to install 1.0.12 by hand to recover.

## [1.0.11] — 2026-04-25

UX cleanup: standard taskbar minimize, persistent window size, plus the apostrophe-contractions work that landed on `main` after 1.0.10.

### Changed
- **Minimize behaves like Chrome now.** Clicking the title-bar `−` calls `Window.showMinimized()` and the OSK drops to the taskbar; click the taskbar entry to restore. Earlier builds set `Qt.Tool` and `WS_EX_TOOLWINDOW` to suppress the taskbar entry, which meant the minimize button had to `hide()` the window entirely — and the only path back was the easily-missed system-tray icon. Trade-off: the OSK now appears in Alt+Tab. `WS_EX_NOACTIVATE` still prevents focus theft on click.
- **Window size is remembered across launches.** `savedWindowWidth` / `savedWindowHeight` added to the persisted settings; restored at the top of `Component.onCompleted` (before any other init that could fight a binding). Resize writes are debounced 300 ms to avoid hammering the OS settings store on every pixel during a drag.

### Added
- **Apostrophe-less contractions autocomplete and autocorrect.** Typing `im` surfaces `I'm` in the predictions and replaces to `I'm` on space; same for `dont` → `don't`, `youre` → `you're`, `cant` → `can't`, etc. Three pieces:
  - `'` added to `FuzzyWordGenerator._ALPHABET` so the insertion edit-distance path can produce contraction candidates from bare-form input.
  - `FuzzyWordGenerator._APOSTROPHE_INSERTION_PROB = 0.50` — apostrophe insertion gets a much higher per-edit probability than the generic letter-insertion penalty (0.15) because missing apostrophes are the dominant insertion error in real OSK typing. Without this, `i'm` got buried at rank 9 below noisier candidates like `him`, `aim`, `um`.
  - 42 common contractions added to `data/base_dictionary.txt` with realistic frequencies (`i'm` at 8000, comparable to `hello` / `the`); 32 unambiguous bare-form → with-apostrophe entries added to `data/common_misspellings.txt` for the autocorrect-on-space path. Skipped bare forms that are valid words on their own (`its`, `lets`, `were`, `wed`, `id`, `ill`, `hes`).
- **`load_base_dictionary` accepts a `word count` syntax** for entries that need a frequency higher than the default `+1` boost, so contractions can compete with the Google 10K wordlist's 1000–10000 range.

## [1.0.10] — 2026-04-25

Auto-updater finally works end-to-end on Windows, plus a UI cleanup.

### Fixed
- **Auto-update install step now triggers UAC.** `subprocess.Popen([installer, "/S"])` was failing with `WinError 740: The requested operation requires elevation` — Windows refuses to launch a manifest-elevated process from a non-elevated parent without an explicit `runas`. Replaced with `ctypes.windll.shell32.ShellExecuteW(None, "runas", installer, "/S", None, SW_SHOWNORMAL)`, which surfaces the UAC consent prompt; if the user accepts, the installer launches elevated and `/S` runs it silently from there. If the user declines, we surface "Update cancelled at UAC prompt" instead of a generic failure. **v1.0.5 / v1.0.6 / v1.0.7 / v1.0.8 / v1.0.9 users have to install v1.0.10 by hand once** because their auto-updater can't elevate; auto-update works for every release after that. Pulled the launch into a `_launch_installer` helper so tests can mock the seam without faking `ctypes`.

### Changed
- **Update notification is a title-bar icon, not a full-width banner.** The banner ate a row of OSK real estate for a passive notification — replaced with a small ↓ icon next to the play/privacy toggle that opens a popup with version info and Install / Later buttons. Icon turns red and shows the failure reason inline if a previous install attempt failed (e.g. UAC declined). Popup uses `Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent` so OSK keystrokes don't dismiss it.

## [1.0.9] — 2026-04-25

Fuzzy / autocorrect overhaul plus a Windows-terminal fix for prediction-pill replacement.

### Fixed
- **Prediction-pill replace works in Windows terminals.** In `ConsoleWindowClass` (cmd / PowerShell / conhost), Windows Terminal, and mintty, `Shift+Left` moves the cursor without selecting. The default `replace_text` path therefore left the user's typed prefix intact and inserted the prediction at the new cursor position — typing `owen` and clicking the `Owen` pill produced `Owenowen`. `WindowsKeySynthesizer` now detects terminal window classes via `GetClassNameW` and falls back to BackSpace+type for them. BackSpace would still break Slack-style chat compose, but those aren't terminals — class-based dispatch lets us pick the right deletion strategy per app.

### Changed
- **Removed accessibility profiles.** The six profiles (Precise / Normal / Mild–Severe Tremor / Limited Mobility) pretended to give six dials when really five of those settings were shades of the same dial. Most users picked "Normal" and `key_hold_delay` was documented but never actually consumed. Replaced with one tuned default on `FuzzyRecognizer`: `spatial_uncertainty=1.4` (was 1.0 in Normal — covers diagonal neighbours), `confidence_threshold=0.65` (was 0.8 — more willing to autocorrect), `prediction_weight=0.6` (was 0.5), beam-search `min_prob=0.001` (was 0.01 — lets a single-substitution path survive across a 5+ char word). Profile picker UI gone; the `set_accessibility_profile` / `setAccessibilityProfile` paths through `FuzzyRecognizer`, `HybridPredictor`, and `KeyboardBridge` are removed along with `AccessibilityPanel.qml`.

### Added
- **Frequency-weighted fuzzy ranking.** Dictionary is now `Dict[str, float]` (word → frequency). `generate_candidates` multiplies spatial probability by `log(freq + 1)` so a high-frequency word with a slightly worse spatial match still beats a rare word with a perfect spatial match. Frequencies are sourced from the n-gram unigram counts (`HybridPredictor.__init__` injects `ngram.unigrams` into the fuzzy dictionary; re-injected after the training corpus expands those counts). Fixes "the" and "tha" tying on a 3-letter spatial match.
- **Edit-distance fuzzy candidates** — transposition, deletion, insertion. The spatial-substitution beam search only catches typos where the user hit a near key; "teh" (transposition), "thee" (extra letter), and "th" (missing letter) all slipped through. New `_edit_distance_candidates` path generates dictionary hits at edit distance 1 with per-edit penalties (transposition 0.30, deletion 0.20, insertion 0.15, divided by typed length so longer words aren't unfairly penalised). Insertion path skipped for inputs over 12 chars to bound per-keystroke cost — typical 5-letter input does ~170 hash lookups.
- **Bigram prior on fuzzy candidates in the hybrid merge.** Fuzzy candidates were context-blind; "the" after `of ` tied with "thy" and "tha". `HybridPredictor._merge_predictions` now multiplies each fuzzy candidate's positional score by `1 + log1p(bigram_count) / 2`. The /2 slope is intentional — fuzzy candidates have no other context signal, so a confident bigram should be able to override positional ranking.
- **Space-time autocorrect (hybrid).** Two-tier: a curated common-misspellings table at `data/common_misspellings.txt` (~150 entries from public-domain English misspelling lists, focused on errors fuzzy machinery would either miss or only weakly correct — silent letters, doubled vs single letters, vowel confusions over edit distance 1), then `fuzzy.should_autocorrect` as the slow path with the 0.65 confidence gate. Runs on space, replaces the typed letters and the trailing space atomically via `replace_text()` so terminals work correctly. Casing follows the typed word (all-upper / title / passthrough). Privacy mode and edit mode skip autocorrect entirely. Toggle via `setAutocorrectEnabled` slot (default on).

### Chores
- **CLAUDE.md trimmed by 33 %.** The auto-update threat-model table and the full Windows release runbook were duplicate copies of content that already lived in `docs/AUTO_UPDATE.md` and `docs/WINDOWS.md` respectively, but loaded into context every conversation. Replaced with concise pointers preserving the actually-actionable bits (public-repo gotcha, single source of truth for version, non-elevated-shell trap). `docs/WINDOWS.md` gained a "Release Checklist" section and an "Installer Upgrade Behavior" table so the post-build steps live alongside the existing build/sign documentation.

## [1.0.8] — 2026-04-25

v1.0.7 was committed but never built or released; its fixes ship here in 1.0.8 alongside the post-1.0.7 work.

### Fixed
- **Auto-updater downloads no longer fail on the post-redirect host check.** GitHub's release-asset CDN moved from `objects.githubusercontent.com` to `release-assets.githubusercontent.com`; the MITM-defence host whitelist only knew about the historical name, so every legitimate v1.0.6 download was rejected with "Update download or signature verification failed". The new hostname is now in `_ALLOWED_DOWNLOAD_HOSTS`. The two pinned hostnames (plus `github.com`) are spelled out in `src/updater.py` rather than allowing `*.githubusercontent.com` so an attacker who finds a way to publish content under the wider umbrella can't redirect us there.
- **Update banner now allows retry after failure.** `Install` was permanently disabled the moment `updateError !== ""`, so a transient network blip required dismissing the banner and waiting for the next auto-check to recover. The button now stays enabled and reads "Retry" after a failure; clicking it clears the error and re-enters the install path.
- **Failed updates now name the failed step in the banner.** `download_and_install` returns a `(ok, error)` tuple; the bridge forwards the short step-specific message ("Download failed", "Signature check failed") instead of the generic "Update download or signature verification failed".
- **OSK keystrokes now actually land in the edit-prediction popup.** Follow-up to the dismissal fix: even after the popup stopped vanishing, pressing OSK keys had no effect because (a) `modal: true` installed an overlay that swallowed the MouseArea events and (b) even without the overlay, OSK keys synthesize via xdotool/SendInput to the *OS-focused app* behind Alpha-OSK, not the popup's QML TextField. Added `KeyboardBridge.setEditMode(bool)` plus `editKeyTyped(str)` / `editSpecialPressed(str)` signals; when the popup opens it flips edit mode on, `pressKey` / `pressSpecialKey` short-circuit the synthesizer and emit instead, and the popup's `Connections {}` mutates the TextField directly (insert at cursor, backspace, arrow-key cursor motion, home/end, return-to-accept, escape-to-cancel). Shift/caps still affect letter case; ctrl/alt/win are ignored in edit mode so stray chords can't leak to the app behind us.
- **Prediction pills now match Caps Lock display case.** With Caps Lock on, the user's `_current_word` was accumulating uppercase (e.g. "HELL") while the pills still showed lowercase ("hello"). Even worse, clicking a pill inserted the lowercase text next to the uppercase prefix. Added a `_display_cased()` transform applied at every prediction emit site (instant, refined, next-word-after-selection, edited, swipe) that uppercases the engine's output while Caps Lock is active. Toggling Caps Lock while pills are visible now re-queries the engine so the visible pills flip case immediately. Shift is deliberately not mirrored — it's one-shot and sentence-start capitalisation is already handled upstream by `get_capitalized`.
- **Edit-prediction popup no longer dismisses on the first OSK keystroke.** The popup's `closePolicy` included `Popup.CloseOnPressOutside`, so every click on an OSK character or arrow-row key registered as a "press outside the popup" and closed it before the keystroke could land. Dropped `CloseOnPressOutside` from the policy and added an explicit ✕ cancel button next to the ✓ confirm button; Escape still dismisses too.

### Added
- **File logging at `%APPDATA%/alpha-osk/alpha-osk.log`** (Linux: `~/.config/alpha-osk/alpha-osk.log`). The frozen build runs without a console, so updater errors and crash tracebacks previously had nowhere to land — the banner's "see log" hint pointed at logs that didn't exist. `keyboard_app._configure_logging()` now wires a `RotatingFileHandler` (2 MB cap, 3 backups) alongside the existing stderr handler. Log file path is announced at startup.
- **Prediction pill reveals full word on hover when truncated.** Long predictions are elided with `Text.ElideRight` when the pill is narrower than the word (common at high prediction counts or narrow window widths). Hovering over a truncated pill now surfaces a tooltip with the full word after a 400 ms delay. Gated on `predText.truncated` so short words that already fit don't trigger a redundant tooltip.
- **Linux: atomic prediction replacement via `replace_text()`.** Picking a prediction whose casing or prefix differs from what was typed (e.g. "iph" → "iPhone") used to fall through to sequential `xdotool key BackSpace` calls, which raced with xdotool's subprocess latency and produced visible stuttering / apparent duplicated characters in fast typers' sessions. `LinuxKeySynthesizer.replace_text()` now chains N `shift+Left` chords into a single `xdotool key` invocation then a separate `xdotool type`, mirroring the Windows single-`SendInput` path. Wayland / ydotool gets the equivalent `--key-down shift` → `Left`×N → `--key-up shift` → `type` sequence.
- **Linux: app-switch context reset.** Predictions, current word, and sentence buffers now clear when the user switches applications on X11, same as Windows. `KeyboardBridge._get_foreground_window_id()` dispatches: Windows → `GetForegroundWindow` via ctypes (unchanged), X11 → `xdotool getactivewindow` (~5 ms at 4 Hz), Wayland → no-op (compositors don't expose focused window to unprivileged clients).
- **Linux: password-field auto-detection via AT-SPI 2.** Privacy mode now flips on automatically when focus lands on a password field (GTK `GtkEntry` with `visibility=false`, Qt `QLineEdit` in password echo mode, browsers that expose accessibility metadata). Implementation in `src/platform/password_detect.py` spawns a daemon thread that owns a GLib event loop and listens for `object:state-changed:focused`; the focused accessible's state set is checked for `STATE_PASSWORD_TEXT`. Requires `python3-gi` + `gir1.2-atspi-2.0` on the host; falls back silently to the null detector (manual toggle still works) if either is missing.

### Chores
- Added `tests/test_password_detect.py` (12 tests) and `TestForegroundWindow` / `TestLinuxReplaceText` classes covering the new Linux paths. Tests mock `subprocess.run` and the `gi` import so they run on any OS, including the Windows CI lane where `xdotool` / AT-SPI don't exist.

### Required manual install
v1.0.5 / v1.0.6 users have to install v1.0.8 by hand once because their auto-updater can't pull this build (their host whitelist is the broken one). Auto-update works for every release after that.

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
