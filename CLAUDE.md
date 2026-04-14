# CLAUDE.md — Alpha-OSK AI Onboarding

## About the Owner

Owen is a wheelchair user with muscular dystrophy. Typing is hard — be proactive, make decisions, don't ask for confirmation on small things. Offer A/B/C choices so he can type one letter instead of explaining. This is an accessibility tool he actually needs.

## What This Is

Alpha-OSK is an AI-powered on-screen keyboard for Windows and Linux. Users click keys in the UI to type into other applications. It uses a hybrid prediction engine (n-gram + PPM + fuzzy recognition) — no LLM/GPU required.

## How to Run

```bash
python run.py          # Creates venv, installs deps, launches keyboard
python -m pytest       # Run tests (269+ tests)
```

## Architecture Overview

```
User clicks key (QML)
  → KeyButton.qml sends signal
  → Main.qml calls keyboard.pressKey() / keyboard.pressSpecialKey()
  → keyboard_bridge.py (Python↔QML bridge)
    → platform/*.py synthesizes keystroke (xdotool on Linux, SendInput on Windows)
    → prediction engine updates suggestions
  → predictions emitted back to QML via Signal
```

## Key Directories

| Path | What |
|------|------|
| `src/keyboard_bridge.py` | Central bridge: key handling, modifiers, context tracking, predictions |
| `src/keyboard_app.py` | App launcher: QML engine, window flags, auto-save on exit |
| `src/platform/` | OS abstraction — `linux.py` (xdotool/ydotool), `windows.py` (SendInput), `password_detect.py` |
| `src/platform/__init__.py` | Platform detection, `get_config_dir()`, `get_model_dir()` |
| `src/prediction/` | Prediction engines (see below) |
| `qml/Main.qml` | Root UI — title bar, keyboard rows, prediction bar, resize handles |
| `qml/components/` | Reusable QML components (KeyButton, settings panels, etc.) |
| `data/` | Static data: dictionaries, training corpus, keyboard layouts, vocab packs |
| `build/` | Windows build: PyInstaller spec, UIAccess manifest, signing |
| `tests/` | pytest suite |

## Prediction Engine

All in `src/prediction/`. Orchestrated by `hybrid_predictor.py`:

| File | Role |
|------|------|
| `ngram_predictor.py` | Word-frequency model: unigrams, bigrams, trigrams. Learns from typing. |
| `ppm_predictor.py` | Character-level PPM (Dasher algorithm). Predicts next characters. |
| `fuzzy_recognizer.py` | Spatial error correction. Considers nearby keys as candidates. Has 6 accessibility profiles. |
| `hybrid_predictor.py` | Merges all predictors. Manages model save/load. Emits Qt signals. |
| `vocabulary_pack.py` | Domain vocab packs (medical, programming, etc.) + custom pack import |
| `transformer_predictor.py` | Optional LLM re-ranking (disabled by default) |

## Auto-Capitalization & Proper Nouns

Capitalization uses a **three-tier context-aware system** (same model as Android/Gboard):

### Tier 1 — Always capitalize
Words that are always capitalized regardless of position. Hardcoded in `ngram_predictor._always_capitalize`:
- `"I"`, `"I'm"`, `"I'll"`, `"I'd"`, `"I've"`

### Tier 2 — Sentence-start only (ambiguous names)
Words that are both common English AND proper names (e.g., "will", "jack", "may", "mark"). Listed in `ngram_predictor._ambiguous_names`. These are **only capitalized** after sentence-ending punctuation (`.!?`) or at the start of input. Mid-sentence, they stay lowercase — "the jack was loose" stays lowercase, but "Jack went home." capitalizes.

### Tier 3 — Unambiguous proper nouns
Everything else in `data/proper_nouns.txt` (~8,000 entries) and user-taught capitalizations. These are always capitalized: "Monday", "Paris", "iPhone", "Owen".

### How it works
- **Built-in**: `data/proper_nouns.txt` loaded into `ngram_predictor.capitalization` on startup.
- **Learned**: When a user types a word with non-trivial capitalization (e.g., "iPhone", "Owen") and completes it with space, the preferred form is saved via `learn_capitalization()`.
- **User edits**: Right-click a prediction → Edit to correct capitalization. This calls `editPrediction()` which inserts the corrected word and saves the capitalization permanently.
- **Applied at output**: `hybrid_predictor._merge_predictions()` calls `ngram.get_capitalized(word, sentence_start)` on each result before returning to QML. The `sentence_start` flag is determined by checking if the context ends with `.!?` or is empty.
- **Persisted**: The `capitalization` dict is saved in `ngram_model.json`. User overrides merge with built-in proper nouns on load (user wins).

### Adding to always-capitalize or ambiguous lists
- Always-capitalize: edit `_always_capitalize` dict in `ngram_predictor.py`
- Ambiguous names: edit `_ambiguous_names` set in `ngram_predictor.py`

## Where User Data Lives

- **Settings** (layout, theme, toggles): Managed by Qt `Settings` in QML. Auto-saved on change. Stored in OS registry/config automatically by Qt.
- **Prediction model** (learned words/phrases): Saved to disk explicitly or via auto-save on exit.
  - Windows: `%APPDATA%/alpha-osk/models/`
  - Linux: `~/.config/alpha-osk/models/`
  - Files: `ngram_model.json`, `ppm_model.json`
- **Custom vocabulary packs**: Imported by user, stored separately from built-in packs.
  - Built-in: `data/packs/` (in repo — medical, programming, academic, gaming, business)
  - User-imported: `%APPDATA%/alpha-osk/packs/` (Windows) or `~/.config/alpha-osk/packs/` (Linux)
  - Pack format: folder with `dictionary.txt` (required), optional `bigrams.txt`, `trigrams.txt`, `pack.json`

## QML ↔ Python Bridge Pattern

QML calls Python via `@Slot` methods on `KeyboardBridge`. Python emits `Signal`s back to QML. Example flow:

1. QML: `keyboard.pressKey("a")` → calls `KeyboardBridge.pressKey()`
2. Python: synthesizes keystroke, updates context, runs prediction
3. Python: `self.predictionsChanged.emit(predictions)` → Signal
4. QML: binds to `keyboard.predictions` property, updates UI

## Sticky Modifiers (Ctrl, Alt, Win)

Modifier keys are **sticky** — tap once to activate, tap again to deactivate. While active, the modifier is held at the OS level via `hold_modifier()` / `release_modifier()` on the platform synthesizer. This means:

- **Modifier+click works**: e.g., Ctrl+click to open hyperlinks in terminals/browsers.
- **Modifier+key combos work**: e.g., tap Ctrl, then tap C → sends Ctrl+C.
- **Auto-release**: After any key press (character or special), active modifiers are released at the OS level and deactivated.

### Implementation
- `keyboard_bridge.py`: `toggleCtrl()` / `toggleAlt()` / `toggleWin()` call `_synth.hold_modifier()` on activate and `_synth.release_modifier()` on deactivate. All auto-release paths in `pressKey()` and `pressSpecialKey()` also call `release_modifier()`.
- `platform/base.py`: `hold_modifier()` and `release_modifier()` — default no-op.
- `platform/windows.py`: Sends `VK_CONTROL` / `VK_MENU` / `VK_LWIN` key-down or key-up via `SendInput`.
- `platform/linux.py`: Uses `xdotool keydown/keyup` or `ydotool key --key-down/--key-up`.

## Adding a New Setting

1. Add `property bool savedFoo: defaultValue` to `Settings {}` in `Main.qml`
2. Add `property bool foo: appSettings.savedFoo` to root in `Main.qml`
3. Add `property bool foo: defaultValue` to `UnifiedSettingsPanel.qml`
4. Add `SettingsToggle` in the appropriate section of `UnifiedSettingsPanel.qml`
5. Pass property through: `foo: root.foo` in the `Comp.UnifiedSettingsPanel {}` block
6. Handle in `onSettingChanged`: update root, save to appSettings, call bridge if needed
7. If Python needs it: add `@Slot(bool) def setFoo()` to `keyboard_bridge.py`
8. Load on startup in `Component.onCompleted` if it needs to be sent to the bridge

## Adding a New QML Component

1. Create `qml/components/MyComponent.qml`
2. It's auto-discovered — the `components/` directory is imported as `"components" as Comp` in Main.qml
3. Use as `Comp.MyComponent {}` in Main.qml

## Accessibility Profiles

Defined in `src/prediction/fuzzy_recognizer.py`. Six profiles adjust:
- **spatial_uncertainty**: How far off-center a keypress can be (in key-widths)
- **confidence_threshold**: Minimum score to autocorrect
- **prediction_weight**: How much fuzzy candidates influence ranking
- **key_hold_delay**: Milliseconds to ignore jitter/tremor double-taps
- **autocorrect_enabled**: Whether to auto-correct at all

## Testing

```bash
python -m pytest                    # All tests
python -m pytest tests/test_keyboard_bridge.py  # Bridge tests
python -m pytest -k "fuzzy"         # Fuzzy recognizer tests
```

Linting: `ruff check src/`, type checking: `mypy src/`

## Word Suppression

Users can right-click prediction pills to:
- **Remove from vocabulary** — adds to `ngram_predictor.blacklist` (word never appears again)
- **Bad suggestion** — increments `ngram_predictor.dispreference` (word is downweighted by `1 / (1 + count * 0.5)`)

Both are persisted in `ngram_model.json` and applied in `hybrid_predictor._merge_predictions()`.

### Restoring Suppressed Words
In the Model Visualization dashboard (Settings → Tools → Language Model Visualization → Dashboard tab → Suppressed Words), blacklisted and dispreferred words display as clickable tags. Click a tag to restore it.

Bridge slots: `keyboard.unblacklistWord(word)`, `keyboard.undisprefer(word)`.

### Auto-Rehabilitation
If a user manually types a blacklisted word 3 times (completing it with space), the word is automatically restored to predictions. Tracked via `ngram_predictor._blacklist_type_count`, persisted in `ngram_model.json`.

## Model Visualization

Accessed via Settings → Tools → Language Model Visualization. Three tabs:
- **Word Cloud** — circle-packed bubble chart of top words, sized by frequency
- **Word Flow** — network graph of bigram word→word connections
- **Dashboard** — stats cards, top words bar chart, interactive suppressed words, top word pairs

Data provided by `keyboard_bridge.getVisualizationData()` → `ModelVisualization.qml`.

## Privacy Mode & Password Detection

Protects sensitive input (passwords, PINs) from leaking into the prediction model.

### How it works
- **Auto-detection** (Windows): A `QTimer` polls every 500ms, calling `is_password_field()` from `src/platform/password_detect.py`. Uses Windows UI Automation COM (`IUIAutomation::GetFocusedElement` → `UIA_IsPasswordPropertyId`) to detect password fields in native apps and browsers. Falls back to Win32 `EM_GETPASSWORDCHAR` if UIA fails.
- **Manual toggle**: Play/pause icon in the title bar (Canvas-drawn). Overrides auto-detection.
- **When active**: Keystrokes still reach the OS, but `_current_word`, predictions, and learning are all suppressed. The prediction bar shows "Learning paused".

### Key files
- `src/platform/password_detect.py` — platform-specific detection (UIA COM via ctypes)
- `src/keyboard_bridge.py` — `_privacy_mode` flag, `_check_password_field()` timer, `setPrivacyMode()` slot

### Linux
Auto-detection not yet implemented. Users should use the manual toggle.

## Themes

Defined in `themeData` in `Main.qml`. Each theme has: `name`, `background`, `keyColor`, `keyPressed`, `textColor`, `accent`, `border`, `animation`.

**9 themes**: Dark, Light, Ocean, Forest, Amethyst, Vaporwave, Blackboard, Typewriter, Spaceship.

Theme colors flow to all components: main keyboard keys, prediction pills, nav panel, numpad, title bar icons, and active key states (NumLock, Shift, etc.). `KeyButton.qml` auto-computes text contrast on active/pressed states using luminance.

**Animations** (optional per theme): Canvas overlay at 15% opacity. Vaporwave has gradient shift, Spaceship has twinkling stars.

Theme picker in settings shows labeled color swatches with mini key previews.

## Vocabulary

- **Base**: Google 10K wordlist (`data/google-10000-english-usa-no-swears.txt`) + 10K supplement (`data/google-20000-supplement.txt`, filtered for explicit content). ~20K total regular words.
- **Packs**: Medical, Programming, Academic, Gaming, Business, NSFW. Toggled in Settings → Vocabulary Packs. NSFW is off by default.
- **Numpad**: Toggles between numbers and navigation keys (Home/End/PgUp/PgDn/arrows/Ins/Del) via NumLock. Key 5 is blank in nav mode.

## Analytics & Quality Scoring

`src/analytics.py` tracks session and all-time stats. All-time stats persist to `<config_dir>/analytics.json`.

**Prediction Quality Score** (0-100) is a weighted combination:
- Keystroke savings rate (40%) — how much effort predictions save
- Prediction hit rate (25%) — how often predictions are used
- Rank accuracy (20%) — how often users pick the #1 suggestion
- Low correction rate (15%) — inverse of backspace rate

## Prediction & Autocorrect — Architecture Notes

Commercial keyboards (Gboard/LatinIME, Presage) treat prediction and spell-check as **one unified system**, not two. During a single dictionary trie traversal, they generate both completions and corrections scored together. The literal typed word competes against alternatives — autocorrect only fires if a correction scores 1.5–2x higher.

### What Alpha-OSK does now
- **Hybrid prediction**: n-gram + PPM + fuzzy (same layered approach as Presage)
- **Spatial error correction**: `fuzzy_recognizer.py` considers nearby keys (same concept as LatinIME's key-distance weighting)
- **Three-tier capitalization**: always-capitalize ("I"), sentence-start-only (ambiguous names), always (proper nouns)

### Known gaps (future work, priority order)
1. **SymSpell for fuzzy matching** — Replace Levenshtein edit-distance in `fuzzy_recognizer.py` with SymSpell's precomputed-deletion approach. ~1000x faster, O(1) lookup, ~30MB RAM. (Garbe, 2012)
2. **Autocorrect confidence threshold** — Only auto-replace if the correction scores 1.5–2x higher than the literal typed word. Prevents over-correction. (LatinIME uses ~1.8x)
3. **Unified scoring** — Make the literal typed word compete against corrections in the same ranked list with an explicit score, so the system knows when NOT to correct.
4. **Spatial edit costs in ranking** — Key-distance weights from fuzzy_recognizer should feed into final prediction ranking, not just candidate generation.

### Reference implementations
- **LatinIME (AOSP)**: trie-based dictionary with weighted edit distance, n-gram LM scoring. Open source.
- **Presage**: pluggable predictors (smoothed n-gram + Katz backoff, recency, trie completion). Linear interpolation merge. Similar to our hybrid approach.
- **Dasher**: PPM-C character-level prediction. Our PPM predictor is based on this.
- **SymSpell**: precompute all deletion variants within edit distance N at index time. Query = generate deletions of input + hash lookup. (github.com/wolfgarbe/SymSpell)
- **Hunspell**: affix-based dictionary + phonetic matching. Slower but handles morphology.

## MacroVox Integration

Design doc at `docs/MACROVOX_INTEGRATION.md`. MacroVox (`C:\Users\Owen\dev\MacroVox`) is a managed voice dictation app (Tauri 2 / Rust + React, Deepgram STT, Claude AI cleanup). Together with Alpha-OSK it forms a complete accessibility input suite: type when you can, dictate when you can't.

**Phases**: (1) Launch & trigger via mic button + Ctrl+Space hotkey. (2) Clipboard bridge — dictated text feeds prediction context. (3) Named pipe IPC — real-time transcript streaming + keyword boosting. (4) Unified installer and shared auth.

Phase 1 requires no MacroVox changes — just a mic icon in Alpha-OSK's title bar.

## Federated Learning

Design doc at `docs/FEDERATED_LEARNING.md`. Not yet implemented — Phase 1 (local delta computation) is the next step.

## Building & Signing a Release

This is the end-to-end process for shipping a new version. **Do not skip steps** — unsigned builds won't get UIAccess, and forgetting to bump the version means the installer overwrites without proper upgrade logic.

### Prerequisites (one-time setup)

| Requirement | Install | Verify |
|-------------|---------|--------|
| Python 3.9+ | `python.org` | `python --version` |
| PyInstaller | `pip install pyinstaller` | `pyinstaller --version` |
| NSIS 3.x | `winget install NSIS.NSIS` | `makensis /VERSION` |
| Windows SDK (signtool) | Visual Studio Installer → Windows SDK | `where signtool.exe` |
| SafeNet Authentication Client | Comes with the USB eToken hardware | Tray icon visible |
| EV Certificate (OK Studio Inc.) | Already provisioned on eToken | `certutil -store -user My` → look for thumbprint `fc22b522...` |

### Step-by-step release checklist

#### 1. Bump the version

The version string is in **one place**: `build/build_windows.py`, the `version` variable inside `build_nsis_installer()` (~line 317). Update it:

```python
version = "1.0.2"  # was "1.0.1"
```

This flows into:
- Installer filename: `Alpha-OSK-Setup-1.0.2.exe`
- NSIS `APP_VERSION` (shown in Add/Remove Programs)
- Registry `DisplayVersion`

#### 2. Update the CHANGELOG

Edit `CHANGELOG.md`. Add a new `## [x.y.z] — YYYY-MM-DD` section at the top. Categorize changes under `### Added`, `### Fixed`, `### Changed`, `### Chores` as appropriate.

#### 3. Commit the version bump + changelog

```bash
git add build/build_windows.py CHANGELOG.md
git commit -m "chore: bump version to x.y.z"
```

#### 4. Run the full build

**Must run from a normal (non-elevated) shell.** The eToken is not visible to admin processes.

```bash
python build/build_windows.py
```

This does, in order:
1. Checks prerequisites (Python, PyInstaller, NSIS, signtool, eToken certificate)
2. Runs PyInstaller with `build/alpha-osk.spec` → `dist/alpha-osk/`
3. Signs all `.exe` files in `dist/alpha-osk/` with the EV cert
4. Generates NSIS installer → `release/Alpha-OSK-Setup-x.y.z.exe`
5. Signs the installer
6. Verifies all signatures

**Common flags:**

| Flag | When to use |
|------|-------------|
| `--no-sign` | Dev/test builds without the eToken plugged in |
| `--skip-build` | Re-package/re-sign without re-running PyInstaller (faster) |
| `--verify-only` | Just check existing signatures |
| `--no-installer` | Produce portable exe only, skip NSIS |

#### 5. Test the installer

1. Run `release/Alpha-OSK-Setup-x.y.z.exe`
2. Verify it detects and removes the previous version (same-directory: silent uninstall; different-directory: prompts)
3. Verify it installs to `C:\Program Files\Alpha-OSK` by default
4. Verify shortcuts are created (Desktop + Start Menu)
5. Launch from the installer's "Launch Alpha-OSK" checkbox
6. **Test UIAccess**: open an elevated Command Prompt (Run as Admin) and verify keystrokes reach it
7. Verify the app icon appears correctly in the taskbar and Start Menu

#### 6. Test the portable exe

```bash
dist/alpha-osk/alpha-osk.exe
```

This won't have UIAccess (not in Program Files), but should work for normal apps.

#### 7. Tag the release

```bash
git tag v1.0.2
git push origin main --tags
```

#### 8. Create GitHub release (optional)

```bash
gh release create v1.0.2 release/Alpha-OSK-Setup-1.0.2.exe --title "v1.0.2" --notes "See CHANGELOG.md"
```

### Signing details

| Field | Value |
|-------|-------|
| Certificate | OK Studio Inc. (EV, Sectigo) |
| Thumbprint | `fc22b5221318f3f3f6b3eb2d969d7f99091557bf` |
| Timestamp server | `http://timestamp.digicert.com` |
| Sign script | `build/sign.py` (5 retries, exponential backoff for Defender locks) |
| What gets signed | All `.exe` in `dist/alpha-osk/` + the final installer `.exe` |

**Why non-elevated?** The SafeNet eToken driver exposes the cert to the current user session only. Elevated (admin) shells can't see it. Always build from a **normal shell**.

### Installer upgrade behavior

The NSIS installer handles upgrades as follows:

| Scenario | Behavior |
|----------|----------|
| Same directory (default `C:\Program Files\Alpha-OSK`) | Silently runs old `uninstall.exe /S` before extracting new files. Preserves `%APPDATA%\alpha-osk` (learned vocabulary). |
| Different directory | Prompts user: "Remove previous version?" If yes, runs old uninstaller. If no, both coexist. |
| Running instance detected | Prompts to close, then kills `alpha-osk.exe` via `taskkill`. |
| Interactive uninstall | Prompts whether to delete `%APPDATA%\alpha-osk` (learned vocabulary and settings). |

Key files:
- `build/build_windows.py` — orchestrates the full pipeline, generates the `.nsi` script
- `build/installer.nsh` — NSIS macros for init, install, and uninstall customization
- `build/sign.py` — signing with retry logic
- `build/alpha-osk.spec` — PyInstaller specification
- `build/alpha-osk.exe.manifest` — UIAccess manifest embedded in the exe
- `build/alpha-osk.ico` — app icon (multi-resolution: 16–256px)

### Troubleshooting builds

| Problem | Cause | Fix |
|---------|-------|-----|
| `Cannot find certificate` | Elevated shell, or eToken unplugged | Use normal shell; check eToken LED is on |
| `SignTool Error: file being used` | Defender scanning the exe | `sign.py` retries automatically (5x) |
| `signtool not found` | Windows SDK not installed | `winget install Microsoft.WindowsSDK` or install via VS Installer |
| `makensis not found` | NSIS not installed or not on PATH | `winget install NSIS.NSIS` |
| `ModuleNotFoundError` at runtime | Missing hidden import in spec | Add to `hiddenimports` in `build/alpha-osk.spec` |
| Installer doesn't remove old version | Same-directory upgrade path broken | Check `IfFileExists "$INSTDIR\\uninstall.exe"` block in generated NSI |
| UIAccess not working after install | Not in Program Files, or unsigned | Verify: signed + installed to `C:\Program Files\Alpha-OSK` |

### Assets & branding

- Source logos: `assets/logo-1024.png`, `assets/logo-2048.png`
- App icon: `build/alpha-osk.ico` (generated from logo via Pillow)
- Midjourney prompts and icon generation workflow: `docs/BRANDING.md`

To regenerate the `.ico` from a new PNG:
```python
from PIL import Image
img = Image.open("assets/logo-1024.png").convert("RGBA")
sizes = [(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)]
resized = [img.resize(s, Image.LANCZOS) for s in sizes]
resized[0].save("build/alpha-osk.ico", format="ICO", sizes=sizes, append_images=resized[1:])
```

## Git Conventions

Conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`

## Things to Watch Out For

- `Main.qml` is large (~1300 lines). The keyboard rows are data-driven from `keyboard.getLayoutRows()`.
- `keyboard_bridge.py` is the biggest Python file (~1000 lines). It handles everything: keys, modifiers, context, predictions, settings, privacy mode.
- Window flags are critical — the keyboard must never steal focus from the user's app. See `_apply_window_flags()` in `keyboard_app.py`.
- On Windows, `WS_EX_NOACTIVATE` is set via Win32 API (not just Qt flags).
- Key spacing and sizing are calculated dynamically from window width — see `keyW`, `keyH`, `keySpacing`, `layoutFixedPixels` properties in Main.qml.
- The title bar has play/pause (privacy), ⚙ (settings), minimize, and close. Help and visualization are in Settings → Tools.
- Predictions clear when the user switches apps — monitored via `GetForegroundWindow()` polling every 250ms in `keyboard_bridge.py`. The QML `onActiveChanged` doesn't fire reliably with `WS_EX_NOACTIVATE`, so Python handles it. Full context reset on app switch (`_current_word`, `_context_buffer`, `_sentence_buffer`).
- Prediction selection uses **suffix-only typing** — if the user typed "hel" and picks "hello", only "lo " is sent. No Backspace (empties Slack compose), no Shift+Left (doesn't work in terminals). Falls back to `replace_text()` only when the prediction doesn't match the typed prefix.
