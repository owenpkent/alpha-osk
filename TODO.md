# TODO

## Phase 1: Foundation ✅

- [x] **Set up project structure** — Create src directories
- [x] **Basic keyboard window** — PySide6 + QML6 floating window
- [x] **Key input simulation** — Send keystrokes to focused app via xdotool
- [x] **Simple QWERTY layout** — Standard keyboard arrangement

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
- [x] **Suggestions toggle** — "Aa" button in title bar, quick on/off
- [x] **Predictions clear on deactivation** — Suggestions clear when user clicks away
- [x] **No predictions for numbers** — Typing digits/symbols clears suggestion bar
- [x] **Configurable suggestion count** — 3–10 suggestions (default 8), adjustable in settings
- [x] **Comprehensive settings panel** — Layout, suggestions, accessibility profiles, vocabulary packs, theme, data management

## Phase 3: AI Prediction ✅

- [x] **Word prediction engine** — Hybrid n-gram + DistilGPT-2 LLM
- [x] **Prediction integration** — Connected to QML UI with real-time updates
- [x] **Personal vocabulary** — Learns from typed words and selections
- [ ] **Abbreviation expansion** — Custom shortcuts (e.g., "omw" → "on my way")
- [ ] **Medical/AT dictionary** — Specialized terms

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

- [ ] **Shared word lists** — Import/export vocabularies
- [ ] **Community profiles** — Pre-built accessibility configs
- [ ] **Cloud sync** — Settings across devices (optional)
- [ ] **Accessibility presets** — One-click configurations

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
- [x] **Documentation** — `docs/WINDOWS.md`, `docs/PLATFORM_ARCHITECTURE.md`
- [x] **Updated all docs** — README, LLM_ONBOARDING, DESIGN for cross-platform

## Phase 8: Windows Polish ✅

- [x] **Build pipeline** — `build/windows/build.py` (PyInstaller → Sign → NSIS → Verify)
- [x] **Code signing** — `build/windows/sign.py` with retry logic (matches gitconnect's `sign.js` pattern)
- [x] **NSIS installer** — `build/windows/installer.nsh` (kill running app, old-version cleanup, shortcuts, AppData prompt)
- [x] **App icon** — `build/windows/alpha-osk.ico` wired into PyInstaller spec
- [x] **Shortcut helpers** — `create_start_menu_shortcut()`, `create_desktop_shortcut()`, `add_to_startup()`, `remove_from_startup()` in `src/platform/windows.py`
- [x] **Documentation updated** — `docs/WINDOWS.md` with real eToken signing steps, NSIS details, troubleshooting

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
- [x] Settings panel — layout, suggestions, accessibility profiles, vocabulary packs, theme, data
- [x] Compact mode option
- [x] LLM_ONBOARDING.md updated for AI assistants
- [x] Key hold/repeat functionality
- [x] Key hover effect (lighten on mouse hover)
- [x] Next-word prediction after word selection
- [x] Suggestions toggle (Aa button in title bar)
- [x] Persistent preferences via Qt Settings
- [x] Multi-modifier shortcuts (Win+Shift+S, etc.)
- [x] Windows port — Platform abstraction, SendInput, UIAccess manifest
