# C++ / Qt6 Windows rewrite (`cpp-rewrite` branch)

A native C++ / Qt6 port of the Alpha-OSK backend, living on the `cpp-rewrite`
branch. **The QML UI (`qml/`) and the data files (`data/`) are reused
unchanged**; only the Python backend (`src/`) is rewritten in C++ under `cpp/`.

This document is the canonical build/run reference for the rewrite. The in-tree
quick reference is [`cpp/README.md`](../../cpp/README.md).

## Why a C++ rewrite

- **No bundled Python runtime.** The PyInstaller build is ~85 MB and attracts
  SmartScreen / AV false positives. A Qt6 C++ build is ~1/3 the size with no
  interpreter shipped.
- **Native speed** for the prediction engine.
- **Mechanical UI reuse.** The QML <-> bridge contract maps 1:1 onto a C++
  `QObject`: every `@Slot` becomes `Q_INVOKABLE`, every `Signal` a Qt signal,
  and the QML barely changes. Widgets / Win32 alternatives were rejected because
  they would throw away 8,500+ lines of working, themed QML for no functional
  gain (and Win32 would also abandon Linux/macOS).

## Status

Ordered by the commits that landed them on `cpp-rewrite`:

| Area | State | Notes |
|---|---|---|
| App bootstrap | done | `QApplication` + `QQmlApplicationEngine`, `keyboard` context property, no-focus window flags (`WS_EX_NOACTIVATE` + always-on-top), `AppUserModelID`, high-DPI passthrough, save-then-shutdown on quit. |
| Key synthesis | done | Behaviour-identical Win32 `SendInput` port: scancode-first ASCII + chords, Unicode fallback, dead-key / AltGr / Caps-Lock bail ladder, `EXTENDEDKEY`, sticky `hold/release_modifier`, `replaceText` (Shift+Left selection, BackSpace+retype in terminals). |
| Typing state machine | done | Press flow, word-boundary handling, backspace buffer-trim + mid-word rehydration, suffix-only pill insertion (+ replace / compat fallbacks), sticky auto-release with the nav-key exception, caps/shift pill mirroring. |
| Prediction: n-gram | done | Unigram/bigram/trigram linear interpolation, completion + next-word, learn-on-type, candidate promotion, recency decay, suppression/boost, "I"-family caps. Reads the existing `ngram_model.json`. |
| Prediction: PPM | done | Character-level PPMD escape model (trie, variable-order blend, beam-search word completion). Loads the existing `ppm_model.json`, else trains from `training_corpus.txt`. |
| Prediction: fuzzy | done | Gaussian spatial key model + SymSpell deletion index (Damerau-Levenshtein), edit-distance classification, `should_autocorrect` confidence gates, `common_misspellings` table. Typo correction verified (`recieve`->`receive`, `wrk`->`work`). |
| Hybrid merge | done | Default `rank` fusion of ngram (3.0/1.0) + ppm (0.3/0.8) + fuzzy (0.6 + bigram-context bonus); `checkAutocorrect` wired (misspellings fast path then fuzzy gates, emits `autocorrectSuggested`). |
| Swipe typing | done | SHARK² shape decoder; `setSwipeEnabled`/`setSwipeLayout`/`processSwipe` wired against the unigram vocabulary. |
| Key-click audio | done | Win32 `PlaySound` (winmm) instead of pulling in the QtMultimedia module. |
| Settings | done | Persisted by the QML `Settings` element (org/app names set); the bridge setter slots (layout / merge-strategy / auto-space / auto-cap / prediction-count / compat / privacy) are functional. |
| Snippets | done | `SnippetStore` reads/writes `snippets.json`; insert sends the value verbatim. Reads the user's existing file. |
| Vocab packs | done | Import-only `PackManager` (discover / enable / disable / import via the hardened path); enabled packs inject vocab into the n-gram tables. |
| Password detection + privacy | done | UIA (+ Win32 fallback) auto-pauses learning on password fields; manual privacy toggle layers on top. |
| Analytics tracking | done | Session + lifetime counters in `analytics.json`; the dashboard tiles (keystrokes/time/effort saved, acceptance) compute from real data. |
| Compat auto-detect | done | 250 ms foreground poll; pill insertion switches to BackSpace+retype in remote-desktop / IDE windows (class + exe whitelist). |
| Telemetry, auto-update, data backup | stub | No-op / minimal stubs so the reused QML never calls a missing method. |

Everything stubbed is present as a method on the bridge so the reused QML never
hits a missing-member error at runtime.

## Toolchain (one-time)

CMake, Ninja, Qt 6.5 (msvc2019_64), and the MSVC compiler (Visual Studio 2019
Build Tools). The pip route needs no Qt account:

```powershell
pip install cmake ninja aqtinstall
python -m aqt install-qt windows desktop 6.5.3 win64_msvc2019_64 --outputdir C:\Qt
```

Qt 6.5 LTS is chosen because its prebuilt binaries are `win64_msvc2019_64`,
exactly matching the VS2019 Build Tools (zero ABI risk, no compiler upgrade).
`cmake` and `ninja` install as pip wheels into the Python `Scripts` directory;
add that directory to `PATH` for the build commands if it isn't already.

## Build

The Visual Studio generator locates the MSVC toolchain itself, so no `vcvars`
sourcing is needed:

```powershell
cmake -S . -B build-cpp -G "Visual Studio 16 2019" -A x64 -DCMAKE_PREFIX_PATH=C:/Qt/6.5.3/msvc2019_64
cmake --build build-cpp --config Release
```

Use a dedicated `build-cpp/` directory (gitignored). Do **not** build into
`build/` — that holds the committed Python packaging pipeline
(`build/windows/`, `build/linux/`, `build/launcher.py`).

## Deploy + run

```powershell
C:\Qt\6.5.3\msvc2019_64\bin\windeployqt.exe --qmldir qml --no-translations build-cpp\Release\alpha-osk.exe
build-cpp\Release\alpha-osk.exe              # launch the keyboard
build-cpp\Release\alpha-osk.exe --selftest   # headless: print sample predictions + a swipe decode, then exit
```

The exe finds `qml/` and `data/` by walking up from its own directory to the
project root (or the source path baked in at build time via `APP_PROJECT_ROOT`),
and reads/writes the learned model at
`%APPDATA%/alpha-osk/models/{ngram,ppm}_model.json` — the same files the Python
app uses, so a user's learned vocabulary carries over.

`--selftest` is the headless verification path used during development. It loads
the real model + base dictionaries and prints predictions for a few prefixes
(including typos, to exercise fuzzy correction) plus a synthetic swipe decode.

## Source map (C++ <- Python)

| C++ | Ported from |
|-----|-------------|
| `cpp/main.cpp`, `cpp/WinUtil.*` | `src/keyboard_app.py` |
| `cpp/KeyboardBridge.*` | `src/keyboard_bridge.py` |
| `cpp/platform/KeySynthesizer.h` | `src/platform/base.py` |
| `cpp/platform/WindowsKeySynthesizer.*` | `src/platform/windows.py` |
| `cpp/prediction/NgramPredictor.*` | `src/prediction/ngram_predictor.py` |
| `cpp/prediction/PPMPredictor.*` | `src/prediction/ppm_predictor.py` |
| `cpp/prediction/FuzzyRecognizer.*` | `src/prediction/{fuzzy_recognizer,symspell,autocorrect}.py` |
| `cpp/prediction/SwipeRecognizer.*` | `src/prediction/swipe_recognizer.py` |
| `cpp/prediction/HybridPredictor.*` | `src/prediction/hybrid_predictor.py` |
| `cpp/Paths.*` | `src/platform/__init__.py` (`get_config_dir` / `get_model_dir`) |

The algorithm deep-dives that governed the port are unchanged and still apply:
[`../architecture/HYBRID_MERGING.md`](../architecture/HYBRID_MERGING.md),
[`../architecture/PPM.md`](../architecture/PPM.md),
[`../architecture/FUZZY_RECOGNITION.md`](../architecture/FUZZY_RECOGNITION.md),
[`../architecture/SWIPE_TYPING.md`](../architecture/SWIPE_TYPING.md).

## Troubleshooting

- **`cmake` / `ninja` not found** — they are pip wheels under the Python
  `Scripts` dir, which may not be on `PATH`. Prepend it, or call `cmake.exe` by
  full path.
- **`Qt6 not found` at configure** — pass `-DCMAKE_PREFIX_PATH=C:/Qt/6.5.3/msvc2019_64`.
- **"Cannot find Visual Studio installation directory" from windeployqt** —
  benign; it is looking for the MSVC redist, not a blocker for the bundle.
- **App launches but keystrokes don't reach other apps** — confirm the target
  isn't elevated (UIAccess/admin is required to inject into higher-integrity
  windows; the Python build's EV-signed + manifested release handles this).
- **`ModelVisualization` binding warnings** — fixed; `getVisualizationData`
  returns a populated `stats` object.

## Open work

Live keystroke synthesis into other apps and the interactive UI (swipe gesture,
pill clicks, modifier holds) still need a human-in-the-loop pass — they cannot
be verified headlessly. Next feature candidates: packaging (WIN32 subsystem to
drop the console window, a clean `windeployqt` dist folder), then the remaining
stubbed features (telemetry, auto-update, data backup).
