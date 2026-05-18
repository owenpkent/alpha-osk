# Alpha-OSK

**AI-Powered On-Screen Keyboard for Linux & Windows**

An accessible on-screen keyboard designed for users with motor disabilities, featuring AI-enabled predictive text, voice dictation, and federated learning for personalized adaptation. Runs natively on both **Linux** (X11/Wayland) and **Windows** (with optional UIAccess for elevated-window support via EV code signing).

---

## Status

**Phase:** � Active Development

| Area | Status |
|------|--------|
| Core Keyboard | ✅ Complete |
| AI Prediction | ✅ Smart learning, trigrams, custom vocabulary import |
| Test Suite | ✅ 266 tests passing |
| Voice Dictation | ⏳ Planned |
| Federated Learning | ⏳ Planned |
| Collaboration Features | ⏳ Planned |
| Dashboard | ✅ Complete |

---

## Vision

Current on-screen keyboards on Linux (GNOME On-Board) and Windows (built-in OSK) lack modern AI capabilities. Alpha-OSK builds on the accessibility-first approach of On-Board with:

- **Intelligent word prediction** that learns your vocabulary
- **Voice dictation** with low-latency transcription
- **Federated learning** for privacy-preserving personalization
- **Collaborative dictionaries** shared across disability communities
- **Adaptive layouts** optimized for limited mobility

---

## Key Features (Planned)

### 🧠 AI-Powered Prediction
- Context-aware word and phrase completion
- Personal vocabulary learning (on-device)
- Specialized dictionaries (medical terms, assistive tech jargon)

### 🎤 Voice Dictation
- Real-time speech-to-text (Whisper-based)
- Voice commands for navigation and editing
- Hybrid input: switch between voice and touch seamlessly

### 🔒 Federated Learning
- Model improves without sending raw data to servers
- Privacy-first personalization
- Opt-in aggregated learning across users

### 🤝 Collaboration
- Shared word lists and abbreviation expansions
- Community-contributed accessibility profiles
- Sync settings across devices

### 🎨 Adaptive Layout
- Customizable key sizes and spacing
- Dwell-click and scanning support
- High-contrast and low-vision themes

---

## Current Features

- ✅ **QWERTY layout** with shift/caps lock toggle
- ✅ **Number and symbol layers** (123/#+= toggle)
- ✅ **Sticky modifiers** — Shift, Ctrl, Alt, Win (all auto-release after next key)
- ✅ **Multi-modifier shortcuts** — Win+Shift+S, Ctrl+Shift+T, etc. work correctly
- ✅ **Key synthesis** via xdotool/ydotool (Linux) or SendInput (Windows)
- ✅ **Dark theme** with press animations and key hover effect
- ✅ **Draggable window** — stays on top, doesn't steal focus
- ✅ **Prediction bar** — hybrid n-gram/PPM/fuzzy engine with smart learning (up to 8 suggestions)
- ✅ **Title bar controls** — Learning/Paused privacy toggle, ⟲ clear-suggestion-context, ⚙ settings, minimize, close, plus a download indicator when an update is pending. Every button has a hover tooltip.
- ✅ **Persistent preferences** — layout panels, theme, suggestions, input timing, and compatibility mode all saved across sessions
- ✅ **Navigation panel** — arrow keys, Ins/Del/Home/End/PgUp/PgDn, plus PrtSc/ScrLk/Pause (open by default)
- ✅ **Escape key** always visible in the number row
- ✅ **Custom vocabulary import** — drop a folder with `dictionary.txt` (and optional bigrams/trigrams) for domain-specific words. No built-in packs ship; the engine learns your typing instead.
- ✅ **Settings panel** — drill-down into four categories: Appearance (panels, layout, theme, opacity), Smart Typing (suggestions, prediction engine, input behaviour), Your Language Model (custom vocab import, learned data, dashboard), Data & Privacy (telemetry, updates, help)

## Inspiration

- **GNOME On-Board (Linux)** — Great customization, but limited AI
- **Project-Nimbus** — PySide6+QML architecture pattern
- **iOS/Android keyboards** — Excellent prediction, not accessible enough

Alpha-OSK combines the best of accessibility-first design with modern AI.

---

## Tech Stack

- **Language:** Python 3.9+
- **UI Framework:** PySide6 + QML6 (Qt Quick)
- **Key Synthesis:**
  - **Linux:** xdotool (X11) / ydotool (Wayland) via subprocess
  - **Windows:** Win32 SendInput API via ctypes (zero external deps)
- **AI/ML (Planned):** 
  - Transformers (Hugging Face) for prediction
  - Whisper (OpenAI) for voice
  - Flower for federated learning
- **Dashboard:** HTML/CSS (served via Python)
- **Windows Build:** PyInstaller + EV code signing for UIAccess
- **Linux Build:** PyInstaller bundle + optional AppImage

---

## Development

### Running Tests

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run the full test suite
python -m pytest

# Run with verbose output
python -m pytest -v

# Run a specific test file
python -m pytest tests/test_ngram_predictor.py

# Run with coverage
python -m pytest --cov=src --cov-report=term-missing
```

See [ROADMAP.md](ROADMAP.md) for the phased improvement plan and [docs/](docs/) for design, philosophy, and AI-assistant onboarding.

---

## Quick Start

### Linux

```bash
# Install system dependency
sudo apt install xdotool  # For X11 key synthesis

# Run the keyboard (auto-creates venv, installs PySide6)
python run.py
```

### Windows

```powershell
# No system dependencies needed — SendInput is built-in
python run.py
```

The launcher automatically:
- Detects your platform (Linux or Windows)
- Creates a virtual environment (`venv/`)
- Installs PySide6 and dependencies
- Launches the on-screen keyboard

### Run the Dashboard
```bash
python run.py --dashboard
```

Dashboard opens at `http://localhost:8080`

---

## Project Structure

```
alpha-osk/
├── README.md              # This file
├── CHANGELOG.md           # Release notes
├── CLAUDE.md              # Claude Code project instructions
├── ROADMAP.md             # Phased improvement plan
├── TODO.md                # Task tracking
├── IDEAS.md               # Idea scratch pad
├── docs/                  # Design specs, philosophy, AI-assistant onboarding,
│                          # per-feature deep dives (FUZZY_RECOGNITION, PPM, etc.)
├── run.py                 # Cross-platform launcher (venv + deps)
├── check.py               # Pre-push gate (ruff + mypy + pytest)
├── requirements.txt       # Python dependencies
├── requirements-dev.txt   # Dev/test dependencies
├── pyproject.toml         # pytest, ruff, mypy config
├── src/
│   ├── keyboard_app.py    # QML engine setup and window config
│   ├── keyboard_bridge.py # Python↔QML bridge (platform-agnostic)
│   ├── platform/          # ★ Platform abstraction layer
│   │   ├── __init__.py    #   Factory, detection, config paths
│   │   ├── base.py        #   Abstract key synthesizer interface
│   │   ├── linux.py       #   Linux: xdotool / ydotool
│   │   └── windows.py     #   Windows: SendInput via ctypes
│   ├── prediction/        # AI prediction engine
│   │   ├── hybrid_predictor.py
│   │   ├── ngram_predictor.py
│   │   ├── ppm_predictor.py
│   │   └── fuzzy_recognizer.py
│   └── __init__.py
├── tests/                 # ★ Automated test suite (pytest)
│   ├── conftest.py        #   Shared fixtures
│   ├── test_ngram_predictor.py
│   ├── test_ppm_predictor.py
│   ├── test_fuzzy_recognizer.py
│   ├── test_hybrid_predictor.py
│   ├── test_platform.py
│   └── test_keyboard_bridge.py
├── qml/
│   ├── Main.qml           # Main keyboard window
│   └── components/        # Reusable QML components
├── build/                 # ★ Packaging pipelines (per-platform)
│   ├── launcher.py              # Shared PyInstaller entry point
│   ├── linux/                   # PyInstaller spec + AppImage assets
│   │   ├── alpha-osk.spec
│   │   ├── build.py             #   python build/linux/build.py [--appimage]
│   │   ├── AppRun
│   │   └── alpha-osk.desktop
│   └── windows/                 # PyInstaller spec + NSIS + EV signing
│       ├── alpha-osk.spec
│       ├── alpha-osk.exe.manifest
│       ├── alpha-osk.ico
│       ├── build.py             #   python build/windows/build.py
│       ├── installer.nsh
│       └── sign.py
├── data/                  # ★ Training data (see data/README.md)
│   ├── base_dictionary.txt        # Unigram vocabulary (one word/line)
│   ├── common_bigrams.txt         # Word pairs for prediction
│   ├── common_trigrams.txt        # Word triples for prediction
│   ├── training_corpus.txt        # Natural sentences for training
│   └── google-10000-english-usa-no-swears.txt
├── templates/             # Project dashboard (HTML)
└── docs/                  # Extended documentation
    ├── WINDOWS.md         # ★ Windows setup & signing guide
    ├── LINUX.md           # ★ Linux build (PyInstaller + AppImage)
    ├── PLATFORM_ARCHITECTURE.md  # ★ Cross-platform design
    └── ...
```

---

## Contributing

Contributions are welcome, especially from users of adaptive technology.
Start with [CONTRIBUTING.md](CONTRIBUTING.md) for development setup,
coding conventions, and the PR flow. All participation is governed by
the [Code of Conduct](CODE_OF_CONDUCT.md).

For security issues, follow [SECURITY.md](SECURITY.md) instead of opening
a public issue.

---

## License

[MIT License](LICENSE). Free for personal and commercial use.

### Why MIT

Alpha-OSK is open-source primarily as a code-quality showcase. MIT is the lowest-friction license that still gives users (and forkers, and resume readers) clear permission to do whatever they want, including ship commercial products on top of it. Apache 2.0 was the realistic alternative; the trade was a longer license body, a `NOTICE` file requirement, and a "modified files must be marked" obligation in exchange for an explicit patent grant. There are no patents in play here (the prediction engine is published academic work: n-gram, PPM, fuzzy spatial correction, all decades old), so the patent grant solves a problem the project doesn't have. MIT is what most well-known TypeScript / Python projects pick for the same reason. If Alpha-OSK ever grows a contributor base or a commercial wrapper, switching to Apache 2.0 is a one-PR change.

---

## Constellation

This repo is tracked by [Constellation](https://github.com/owenpkent/constellation) for cross-project visibility.
