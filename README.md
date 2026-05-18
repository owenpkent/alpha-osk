<div align="center">

<img src="assets/logo-1024.png" alt="Alpha-OSK logo" width="160" />

# Alpha-OSK

**AI-powered on-screen keyboard for Windows and Linux.**

Accessibility-first, with hybrid predictive text (n-gram + PPM + fuzzy spatial correction). No LLM. No GPU. Runs on a stock Python install.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/okstudio1/alpha-osk/actions/workflows/ci.yml/badge.svg)](https://github.com/okstudio1/alpha-osk/actions/workflows/ci.yml)
[![Tests](https://img.shields.io/badge/tests-608-brightgreen.svg)](tests/)
[![Releases](https://img.shields.io/badge/releases-alpha--osk--releases-orange.svg)](https://github.com/okstudio1/alpha-osk-releases/releases)

</div>

---

## What it is

Alpha-OSK is an on-screen keyboard built for users who can't comfortably use a physical keyboard. Click keys in the UI to type into whatever application is focused: editor, browser, terminal, game, remote desktop. The keyboard never steals focus, stays on top, and remembers its size and position.

The predictive engine is a hybrid of three classical models (n-gram, PPM, fuzzy spatial recognition) merged with one of four user-selectable strategies. It learns from your typing on-device, with no data leaving the machine unless you opt into anonymous telemetry. Accuracy gets better the longer you use it.

The project exists because the user is a wheelchair user with muscular dystrophy and the keyboards shipped with Windows and Linux are inadequate for daily use. Everything in here is built around that constraint.

## Install

### End users

Download the latest installer from the releases repo:

**[github.com/okstudio1/alpha-osk-releases](https://github.com/okstudio1/alpha-osk-releases/releases)**

- **Windows**: `Alpha-OSK-Setup-X.Y.Z.exe` (signed with an EV cert, includes auto-update)
- **Linux**: `Alpha-OSK-X.Y.Z-x86_64.AppImage` (unsigned by design; `chmod +x` and run)
- **macOS**: in progress (see [`docs/MACOS.md`](docs/MACOS.md))

### From source

```bash
git clone https://github.com/okstudio1/alpha-osk.git
cd alpha-osk
python run.py
```

`run.py` creates a venv, installs PySide6 and dependencies, and launches the keyboard. Linux needs `xdotool` (X11) or `ydotool` (Wayland) installed at the OS level for key synthesis; Windows uses the Win32 SendInput API and has no system dependencies.

## Status

| Area | State |
|------|-------|
| Core keyboard (Windows + Linux) | Shipping |
| Hybrid prediction engine | Shipping |
| Custom vocabulary import | Shipping |
| Swipe / glide typing | Shipping |
| Auto-update (Windows) | Shipping |
| Anonymous telemetry (opt-in) | Shipping |
| Analytics dashboard | Shipping |
| Test suite | 608 tests passing |
| macOS port | In progress |
| Federated learning | Designed, not implemented |
| Voice dictation | Lives in sibling project (MacroVox) |

## Features

### Typing

- QWERTY, Dvorak, and Colemak layouts
- Sticky modifiers (Shift, Ctrl, Alt, Win) with auto-release after the next key
- Multi-modifier chords (Win+Shift+S, Ctrl+Alt+Del, etc.)
- Right-click a character key for its shifted variant without flipping sticky shift
- Optional swipe / glide typing (Gboard-style shape matching)
- Numpad panel with NumLock toggle between digits and navigation keys

### Prediction

- Hybrid n-gram + PPM + fuzzy spatial recognition merged with one of four user-selectable strategies (rank, RRF, linear, log-linear)
- Learns your vocabulary on-device, with backspace as a negative signal
- Right-click any prediction pill to show more, show less, or remove permanently
- Auto-rehabilitation of suppressed words after 3 manual retypes
- Custom vocabulary pack import (domain dictionaries: medical, programming, etc.)
- Per-keystroke autocorrect surfaced as suggestion pills, never silent overwrites

### Accessibility

- Tunable repeat delay and repeat interval per user
- Compatibility mode for IDEs (VS Code, JetBrains) and remote desktop (TeamViewer, RDP, VNC) where suffix-only insertion is unsafe
- Privacy mode: auto-detects password fields and pauses learning
- Window never steals focus from the app you're typing into
- Nine themes including high-contrast options
- Adjustable opacity, key spacing, key sizing
- Drag-to-resize from either edge (width only; height auto-fits content)

### Reliability

- Single-instance lock prevents accidental duplicates
- 608 tests covering prediction, platform abstraction, bridge, vocab packs, telemetry
- CI runs ruff + mypy + pytest + OSV CVE scan on every push and PR
- All-time analytics persist across sessions (keystrokes saved, time saved, acceptance rate)

## Architecture

```
qml/                   Qt Quick UI (Main.qml, keyboard rows, prediction bar, settings)
src/
  keyboard_app.py      QML engine, window flags, OS focus handling
  keyboard_bridge.py   Python<->QML bridge: keys, modifiers, predictions, context
  platform/            OS abstraction (Linux xdotool/ydotool, Windows SendInput, macOS Quartz)
  prediction/          Hybrid engine: n-gram, PPM, fuzzy, swipe, hybrid orchestrator
  analytics.py         Session + lifetime stats
  telemetry.py         Opt-in anonymous metrics client
  updater.py           Auto-update via GitHub Releases
build/                 Per-platform packaging: windows/, linux/, macos/
backend/cf-worker/     Cloudflare Worker for telemetry aggregation (optional)
data/                  Dictionaries, n-gram seed corpora, keyboard layouts
docs/                  Design docs (HYBRID_MERGING, FUZZY_RECOGNITION, PPM, SWIPE_TYPING, WINDOWS, LINUX, MACOS, ...)
tests/                 pytest suite
```

For a guided tour, read [`CLAUDE.md`](CLAUDE.md). It's primarily an AI-onboarding doc but is the clearest map of the codebase: prediction engine internals, QML/Python bridge patterns, platform gotchas, build pipeline, settings architecture.

## Development

```bash
python run.py                               # Launch the keyboard
python -m pytest                            # Run the test suite (608 tests)
python check.py                             # Pre-push gate: ruff + mypy + pytest (~85s)
python check.py --full                      # Adds coverage gate (~3min, matches CI)
python build/windows/build.py               # Build signed Windows installer
python build/linux/build.py --appimage      # Build Linux AppImage
```

CI gates pushes on lint, types, tests, and OSV CVE scanning of both Python and worker lockfiles. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full development flow.

## Contributing

Contributions are welcome, especially from users of adaptive technology. Start with [`CONTRIBUTING.md`](CONTRIBUTING.md) for development setup and conventions. All participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md).

For security issues, follow [`SECURITY.md`](SECURITY.md). Do not file public issues for vulnerabilities.

## Privacy

- Learning is on-device only. Your typing never leaves your computer unless you opt into telemetry.
- Password fields are auto-detected (Windows UI Automation, Linux AT-SPI) and pause learning automatically. There's also a manual Learning / Paused toggle in the title bar.
- Telemetry is opt-in, off by default, and sends nine integer counters per week with no content. See [`docs/PRIVACY.md`](docs/PRIVACY.md) and [`docs/TELEMETRY.md`](docs/TELEMETRY.md).
- Auto-update fetches release metadata from GitHub. Installers are verified against an EV-signed certificate before launching.

## License

[MIT License](LICENSE). Free for personal and commercial use.

### Why MIT

Alpha-OSK is open-source primarily as a code-quality showcase. MIT is the lowest-friction license that still gives users (and forkers, and resume readers) clear permission to do whatever they want, including ship commercial products on top of it. Apache 2.0 was the realistic alternative; the trade was a longer license body, a `NOTICE` file requirement, and a "modified files must be marked" obligation in exchange for an explicit patent grant. There are no patents in play here (the prediction engine is published academic work: n-gram, PPM, fuzzy spatial correction, all decades old), so the patent grant solves a problem the project doesn't have. MIT is what most well-known TypeScript / Python projects pick for the same reason. If Alpha-OSK ever grows a contributor base or a commercial wrapper, switching to Apache 2.0 is a one-PR change.

## Related projects

Alpha-OSK is part of a four-tool adaptive-input platform built by the same author:

- **Alpha-OSK** (this repo) — keystrokes
- **MacroVox** — voice dictation (Deepgram STT to clipboard)
- **Octavium** — MIDI control (virtual piano and pads)
- **Nimbus** — joystick (vJoy / ViGEm)

See [`docs/ECOSYSTEM.md`](docs/ECOSYSTEM.md) for the integration plan.

---

This repo is tracked by [Constellation](https://github.com/owenpkent/constellation) for cross-project visibility.
