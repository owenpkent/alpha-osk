# TODO

## Phase 1: Foundation ✅

- [x] **Set up project structure** — Create src directories
- [x] **Basic keyboard window** — PySide6 + QML6 floating window
- [x] **Key input simulation** — Send keystrokes to focused app via xdotool
- [x] **Simple QWERTY layout** — Standard keyboard arrangement

## Known bugs

- [x] **The update relauncher waited out an installer that was never coming.** The original diagnosis here was wrong and worth recording: it claimed there was "no exit path for the parent already being gone", so the helper "waits indefinitely". Measured, `_wait_for_parent_exit` returns in 0.00 s for a dead pid and the phases have always summed to a bounded 245 s. The real waste was narrower. A dev-mode spawn passes `--target-exe sys.executable`, whose mtime can never advance past the parent's death, so the new-exe wait ran its full 180 s every time and *then* reported failure, leaving a detached, console-less process alive for ~185 s per attempt. That is what showed up in process lists. Dev targets now return promptly without launching (`python.exe` with no arguments is not the keyboard, and launching it would swap one stranded process for another), and a `_MAX_TOTAL_RUNTIME_S` ceiling clamps every phase to what is left of it, so no future phase can extend the total silently.

- [ ] **Two QML suites fight over persisted window/panel settings under `-n auto`, and the loser fails intermittently.** Seen as `test_qml_compact_view.py::TestTheMainWindowRestoreClampsToTheWholeDesktop::test_a_position_already_on_screen_survives_the_restore_unchanged` reading back `x = -176` where it seeded `savedWindowWidth = 500` and `savedWindowX = 50`, alongside `test_qml_ui_state_fuzz.py::TestRestartPersistence::test_quitting_in_compact_comes_back_in_compact_with_panels_off`. The `-176` is the tell: it is the shared clamp pulling a window *wider than 500* back onto the 800 px offscreen screen, so the seeded width did not survive, which means `minimumWidth` was raised by panel settings a different test left behind. `TEST_ORG` is already suffixed per xdist worker, so this is not cross-worker leakage; it is two modules sharing one worker's store, and which tests share a worker changes run to run.

  **Reproduced on `main` at 6bbe679, before dictation existed**, so it is not caused by any recent feature. Observed rate: once in four full sharded runs on that base checkout, and twice in eight runs of the QML modules alone. Note it is a *failed assertion*, not a setup error; a cascade of `ERROR at setup` across a whole QML module is a different problem (see the `QGuiApplication`-per-process note in CLAUDE.md's testing section) and should not be filed here. It is worth fixing rather than tolerating for two reasons: it makes `python check.py` fail on a clean tree, which trains people to re-run the gate instead of reading it, and adding `tests/test_qml_dictation.py` as a fourth QML module changes the worker mix and so changes how often it bites. The likely fix is for both modules' fixtures to reset the *panel* settings (`savedShowNavigation` / `savedShowNumpad` / `savedShowFunctionRow` / `savedCompactView`), not just clear-and-reseed the three keys each test names, since the clamp depends on `minimumWidth` and `minimumWidth` depends on which panels are on.

## Possible improvements

- [x] **Tell a caret-moving outside click from a caret-neutral one.** Done, with the race that deferred it solved rather than ignored. The obvious version reads `caret_position_token()` when the press is seen and skips the reset if it has not moved, and that silently undoes the fix the click signal exists for: the poll is up to 50 ms behind the press and the target app may not have handled it yet, so the read reports the old caret for a click that is about to move it. The decision is settled over `_CLICK_SETTLE_MS` (200 ms) instead, against a baseline from the *previous* poll, which is the only reading that reliably predates a press detected as a transition. `_last_caret_token` cannot serve as that baseline: the 4 Hz foreground poll lands between a press and its detection often enough to matter, and when it does it already holds the post-click value. Where no caret is published (browsers, Electron, the case the signal was invented for) the behaviour is byte-identical to resetting on every press.

- [ ] **A scrollbar click still clears the typing context.** The click signal now tells a caret-moving press from a caret-neutral one, which rescues toolbar buttons, title bars and empty chrome, but not this: `caret_position_token()` is built from `GetGUIThreadInfo`'s `rcCaret`, a *client-relative rectangle*, so scrolling the view drags it while the caret has not moved a character in the text. Scrolling and clicking into another paragraph are therefore the same event as far as the token is concerned. `_check_caret_moved` handles this by only acting between words; that guard was tried on the click path and reversed for a stronger reason (a partial word is exactly when the bar is full of completions for the field the caret has just left), so don't re-unify them. Closing it properly needs a caret identity that survives a scroll, which Windows does not publish. Cost of the gap is one lost set of next-word suggestions per scroll.

## Phase 2: Accessibility Core

- [ ] **Dwell-click support** — Trigger keys by hovering
- [ ] **Scanning mode** — Row/column scanning for switch users
- [x] **Adjustable key sizes** — Compact mode toggle in settings
- [ ] **High-contrast themes** — WCAG-compliant color schemes
- [x] **Sticky/latch keys** — Shift, Caps, Ctrl, Alt, Win/Super (all auto-release after keypress)
- [x] **Modular layout** — Toggleable Function Row, Nav Panel, Numpad
- [x] **Key hover effect** — Keys lighten on mouse hover
- [x] **Multi-modifier shortcuts** — Win+Shift+S, Ctrl+Shift+T, etc. work correctly
- [x] **Escape key always visible** — Placed in number row (not behind Function Keys toggle)
- [x] **System keys in nav panel** — PrtSc, ScrLk, Pause grouped with navigation keys
- [x] **Persistent preferences** — Layout toggles, theme, suggestions saved via Qt Settings
- [x] **Suggestions toggle** — Settings → Smart Typing → Suggestions → Show Suggestions
- [x] **Predictions clear on deactivation** — Suggestions clear when user clicks away
- [x] **No predictions for numbers** — Typing digits/symbols clears suggestion bar
- [x] **Configurable suggestion count** — 3–10 suggestions (default 8), adjustable in settings
- [x] **Comprehensive settings panel** — Four-category drill-down: Appearance, Smart Typing, Your Language Model, Data & Privacy

## Phase 3: AI Prediction ✅

- [x] **Word prediction engine** — Hybrid n-gram + DistilGPT-2 LLM
- [x] **Prediction integration** — Connected to QML UI with real-time updates
- [x] **Personal vocabulary** — Learns from typed words and selections
- [ ] **Abbreviation expansion** — Custom shortcuts (e.g., "omw" → "on my way")

## Phase 4: Voice Dictation

- [ ] **Whisper integration** — Local speech-to-text
- [ ] **Real-time transcription** — Streaming audio input
- [ ] **Voice commands** — "Delete word", "New line", etc.
- [ ] **Hybrid mode** — Switch between voice and keyboard

## Phase 5: Federated Learning

- [ ] **Local model training** — On-device personalization
- [ ] **Flower client setup** — Federated learning framework
- [ ] **Privacy controls** — User consent and data visibility
- [ ] **Model aggregation** — Contribute to shared improvements

## Phase 6: Collaboration

- [ ] **Shared word lists** — Import/export vocabularies (the import side ships today; export is open)
- [ ] **Cloud sync** — Settings across devices (optional)

## Backlog

- [ ] Multi-language support
- [ ] Emoji and symbol panels
- [ ] Macro recording
- [ ] Integration with AAC software
- [ ] Eye-tracking support
- [ ] Game controller input

---

## Phase 7: Windows Port ✅

- [x] **Platform abstraction layer** — `src/platform/` with base class, Linux, and Windows backends
- [x] **Windows key synthesis** — Win32 SendInput API via ctypes (zero external deps)
- [x] **Cross-platform keyboard_bridge.py** — Refactored to use platform layer
- [x] **Cross-platform keyboard_app.py** — Platform-aware env setup + Win32 WS_EX_NOACTIVATE
- [x] **Cross-platform run.py** — Venv paths (bin vs Scripts), system dep checks
- [x] **UIAccess manifest** — `build/windows/alpha-osk.exe.manifest` for EV-signed builds
- [x] **PyInstaller spec** — `build/windows/alpha-osk.spec` for standalone .exe builds
- [x] **Cross-platform model storage** — AppData on Windows, .config on Linux
- [x] **Documentation** — `docs/build/WINDOWS.md`, `docs/architecture/PLATFORM_ARCHITECTURE.md`
- [x] **Updated all docs** — README, LLM_ONBOARDING, DESIGN for cross-platform

## Phase 8: Windows Polish ✅

- [x] **Build pipeline** — `build/windows/build.py` (PyInstaller → Sign → NSIS → Verify)
- [x] **Code signing** — `build/windows/sign.py` with retry logic (matches gitconnect's `sign.js` pattern)
- [x] **NSIS installer** — `build/windows/installer.nsh` (kill running app, old-version cleanup, shortcuts, AppData prompt)
- [x] **App icon** — `build/windows/alpha-osk.ico` wired into PyInstaller spec
- [x] **Shortcut helpers** — `create_start_menu_shortcut()`, `create_desktop_shortcut()`, `add_to_startup()`, `remove_from_startup()` in `src/platform/windows.py`
- [x] **Documentation updated** — `docs/build/WINDOWS.md` with real eToken signing steps, NSIS details, troubleshooting

### Remaining (manual steps)

- [ ] **Plug in eToken and run** `python build/windows/build.py` for a signed release
- [ ] **Test UIAccess** — Install to Program Files, type into elevated Command Prompt
- [ ] **Replace placeholder icon** — Swap `build/windows/alpha-osk.ico` with professional design
- [ ] **Full integration test** on Windows 10 and Windows 11

## Completed

- [x] Project planning
- [x] Initial documentation
- [x] Dashboard setup
- [x] PySide6 + QML6 architecture
- [x] Python↔QML bridge (keyboard_bridge.py)
- [x] Full QWERTY layout with all symbols (`, [], {}, \|, etc.)
- [x] Sticky modifiers (Shift, Caps, Ctrl, Alt, Win/Super)
- [x] Key synthesis via xdotool/ydotool (Linux) and SendInput (Windows)
- [x] Dark theme with press animations
- [x] Draggable window (stays on top, no focus steal)
- [x] Hybrid prediction engine (n-gram + LLM)
- [x] Function row (F1-F12)
- [x] Escape key always visible in number row
- [x] Navigation panel (PrtSc, ScrLk, Pause, Ins, Del, Home, End, PgUp, PgDn, Arrows)
- [x] Number pad with NumLock
- [x] Settings panel — four-category drill-down (Appearance / Smart Typing / Your Language Model / Data & Privacy)
- [x] Compact mode option
- [x] LLM_ONBOARDING.md updated for AI assistants
- [x] Key hold/repeat functionality
- [x] Key hover effect (lighten on mouse hover)
- [x] Next-word prediction after word selection
- [x] Suggestions toggle (Settings → Smart Typing → Suggestions)
- [x] Persistent preferences via Qt Settings
- [x] Multi-modifier shortcuts (Win+Shift+S, etc.)
- [x] Windows port — Platform abstraction, SendInput, UIAccess manifest
