# Alpha-OSK -- C++ / Qt6 Windows rewrite (`cpp-rewrite` branch)

> Quick reference. The canonical build/run doc with the full status table and
> troubleshooting is [`docs/build/CPP_WINDOWS.md`](../docs/build/CPP_WINDOWS.md).

This is a native C++ / Qt6 port of the backend. The **QML UI under `qml/` and
the data files under `data/` are reused unchanged** from the Python app; only
the Python backend (`src/`) is rewritten in C++ here under `cpp/`.

Why C++: drop the bundled Python runtime (the PyInstaller build was ~85 MB and
attracts AV false positives), ship a single native exe, native-speed
prediction. The QML <-> bridge contract maps 1:1 onto a C++ `QObject` (every
`@Slot` becomes `Q_INVOKABLE`, every `Signal` a Qt signal), so the UI is kept
as-is.

## Status: MVP

Implemented and verified:

- **Window bootstrap** -- `QApplication` + `QQmlApplicationEngine` loading
  `qml/Main.qml`, the `keyboard` context property, no-focus window flags
  (`WS_EX_NOACTIVATE` + always-on-top), `AppUserModelID`, high-DPI passthrough,
  save-then-shutdown on quit. (`main.cpp`, `WinUtil.*`)
- **Key synthesis** -- behavior-identical port of the Win32 `SendInput` backend:
  scancode-first ASCII + chords (so input reaches Blender/games and relays over
  RDP), Unicode fallback, the full dead-key / AltGr / Caps-Lock bail ladder,
  `EXTENDEDKEY` handling, sticky `hold/release_modifier`, and `replaceText`
  (Shift+Left selection, BackSpace+retype in terminals).
  (`platform/WindowsKeySynthesizer.*`)
- **Prediction** -- n-gram engine (unigram/bigram/trigram linear interpolation,
  prefix completion + next-word, learn-on-type, candidate promotion, recency
  decay, suppression/boost, "I"-family capitalization) reading the user's
  existing `ngram_model.json`; `HybridPredictor` runs the default `rank` merge
  + finalize. (`prediction/NgramPredictor.*`, `prediction/HybridPredictor.*`)
- **Bridge state machine** -- the full typing path: `pressKey`/`pressKeyLiteral`/
  `pressSpecialKey`, word-boundary handling, backspace buffer-trim + mid-word
  rehydration, suffix-only pill insertion (+ replace / compat fallbacks),
  sticky-modifier auto-release with the nav-key exception, caps/shift pill
  mirroring. (`KeyboardBridge.*`)

Deferred (present as no-op / minimal stubs so the reused QML never calls a
missing method): PPM + fuzzy + swipe predictors, snippets, vocabulary packs,
telemetry, auto-update, data backup/export, analytics dashboard data,
password detection / privacy auto-detect, compat-mode foreground auto-detect,
key-click audio.

## Build

Toolchain (one-time): CMake, Ninja, Qt 6.5 (msvc2019_64), and the MSVC
compiler (Visual Studio 2019 Build Tools). The pip route, no Qt account:

```powershell
pip install cmake ninja aqtinstall
python -m aqt install-qt windows desktop 6.5.3 win64_msvc2019_64 --outputdir C:\Qt
```

Configure + build (Visual Studio generator finds the MSVC toolchain itself):

```powershell
cmake -S . -B build-cpp -G "Visual Studio 16 2019" -A x64 -DCMAKE_PREFIX_PATH=C:/Qt/6.5.3/msvc2019_64
cmake --build build-cpp --config Release
```

Deploy the Qt runtime next to the exe so it runs standalone:

```powershell
C:\Qt\6.5.3\msvc2019_64\bin\windeployqt.exe --qmldir qml --no-translations build-cpp\Release\alpha-osk.exe
```

## Run

```powershell
build-cpp\Release\alpha-osk.exe              # the keyboard
build-cpp\Release\alpha-osk.exe --selftest   # headless: print sample predictions and exit
```

The exe finds `qml/` and `data/` by walking up from its own directory to the
project root (or the source path baked in at build time), and reads/writes the
learned model at `%APPDATA%/alpha-osk/models/ngram_model.json` -- the same file
the Python app uses, so a user's learned vocabulary carries over.

## Source map (C++ <- Python)

| C++ | Ported from |
|-----|-------------|
| `main.cpp`, `WinUtil.*` | `src/keyboard_app.py` |
| `KeyboardBridge.*` | `src/keyboard_bridge.py` |
| `platform/KeySynthesizer.h` | `src/platform/base.py` |
| `platform/WindowsKeySynthesizer.*` | `src/platform/windows.py` |
| `prediction/NgramPredictor.*` | `src/prediction/ngram_predictor.py` |
| `prediction/HybridPredictor.*` | `src/prediction/hybrid_predictor.py` |
| `Paths.*` | `src/platform/__init__.py` (`get_config_dir`/`get_model_dir`) |
