# Backend × Platform Parity Matrix

Alpha-OSK has **two backends** implementing the same behaviour, and **three
platforms**. These are perpendicular axes, and the repo is organised around that:

- **Backend** (language/runtime): the Python backend (`src/`) and the in-progress
  C++/Qt6 rewrite (`cpp/`, on the `cpp-rewrite` branch). See
  [`docs/build/CPP_WINDOWS.md`](https://github.com/owenpkent/alpha-osk/blob/cpp-rewrite/docs/build/CPP_WINDOWS.md)
  for the rewrite's rationale. That file lives on the `cpp-rewrite` branch only,
  so the link is absolute: a relative one resolves to nothing on `main`.
- **Platform** (thin sub-layer inside each backend): Windows / Linux / macOS,
  isolated to one synthesizer + one password-detector file per OS.
- **Shared by every cell below**: `qml/`, `data/`, the `ngram_model.json` /
  `ppm_model.json` file formats, and this doc. The rewrite reuses the QML and data
  **unchanged** — that shared contract is what keeps the two backends cheap to
  keep in sync (verified mechanically by [`tests/conformance/`](../../tests/conformance/README.md)).

Platform is a thin sub-layer, **never** a repo boundary: a per-platform split would
slice through both backends and fork the 8,500-line QML three ways.

## Legend

| Symbol | Meaning |
|:--:|---|
| ✅ | Done / at parity |
| 🚧 | Partial or in progress |
| ❌ | Not started |
| — | Not applicable on this platform |
| ⓝ | Footnote below |

Python status columns reflect `main`; C++ columns reflect the `cpp-rewrite` branch.

## Matrix

| Feature | Py·Win | Py·Linux | Py·Mac | C++·Win | C++·Linux | C++·Mac |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| **Core runtime** | | | | | | |
| No-focus window flags | ✅ | ✅ | 🚧 | ✅ | ❌ | ❌ |
| App icon + system tray | ✅ | ✅ | 🚧 | ✅ | ❌ | ❌ |
| Key synthesis | ✅ | ✅ | 🚧 ⁴ | ✅ | ❌ | ❌ |
| Typing state machine (press / backspace / suffix insert) | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| Sticky modifiers + auto-release | ✅ | ✅ ⁵ | ✅ | ✅ | ❌ | ❌ |
| Right-click modifier lock | ✅ ¹ | ✅ ¹ | ✅ ¹ | ✅ | ❌ | ❌ |
| Key-click audio | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| Key preview bubble (pure QML) | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| Compact view + layers (pure QML + data) ⁷ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| **Prediction engine** | | | | | | |
| n-gram (uni/bi/trigram) | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| PPM (char model) | ✅ | ✅ | ✅ | 🚧 ² | ❌ | ❌ |
| Fuzzy / autocorrect | ✅ | ✅ | ✅ | 🚧 ² | ❌ | ❌ |
| Hybrid merge (rank default) | ✅ | ✅ | ✅ | 🚧 ² | ❌ | ❌ |
| Structured tokens (numbers / phones / email domains) ⁹ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| **Features** | | | | | | |
| Settings panel | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| Snippets | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| Vocab packs (import-only) | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| Data backup (export / import) | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| Analytics (session + lifetime) | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| Telemetry (opt-in, off by default) | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| Auto-update | ✅ | 🚧 ³ | ❌ | 🚧 ² | ❌ | ❌ |
| **Platform integration** | | | | | | |
| Password detection + privacy auto-pause | ✅ | ✅ | ❌ ⁴ | ✅ | ❌ | ❌ |
| Compat auto-detect (IDE / RDP) | ✅ | — ⁶ | — ⁶ | ✅ | ❌ | ❌ |
| Game key-hold compat | ✅ ¹ | — ⁶ | — ⁶ | ✅ | ❌ | ❌ |
| Context reset on focus change | ✅ ¹ | 🚧 ⁸ | 🚧 ⁸ | ✅ | ❌ | ❌ |

## Footnotes

1. **Was stranded on `cpp-rewrite`, now landed.** Right-click modifier lock, game
   key-hold compat and focus-change context reset were all built on the
   `cpp-rewrite` branch first and have since been extracted to the Python backend
   on `main` (`lockModifier` / `_*_locked`, `_window_is_game` +
   `_GAME_KEY_HOLD_SECONDS`, `_check_foreground_window` + `_reset_typing_context`).
   The platform caveats that remain are per-OS, not per-branch, and are footnoted
   on their own rows.
2. **Doc says done, header says stubbed.** `docs/build/CPP_WINDOWS.md` (on the
   `cpp-rewrite` branch) marks the
   C++ PPM / fuzzy / hybrid pillars "done", but the `cpp/prediction/HybridPredictor.h`
   header comment states only the n-gram pillar is live. The conformance harness
   ([`tests/conformance/`](../../tests/conformance/README.md)) is what settles this
   empirically — update this row to ✅ once the harness shows parity. Auto-update
   install is deferred until a signed C++ installer pipeline exists (version check works).
3. Linux auto-update: the AppImage is unsigned by design; the update *path* differs
   from Windows (no in-place signed installer). Version check applies; install is manual.
4. macOS is Phase-1 WIP: `MacOSKeySynthesizer` (Quartz CGEvent) exists but needs an
   Accessibility TCC grant to reach other apps; AXUIElement password detection and
   notarized auto-update are explicit follow-up phases. See `docs/build/MACOS.md`.
5. Linux never *holds* Super/`win` (a held Super triggers a WM pointer grab);
   Super+key combos still fire as atomic chords. See the Sticky Modifiers note in
   `CLAUDE.md`.
6. Compat auto-detect matches on Windows `.exe` basenames (VS Code / JetBrains /
   RDP). The mechanism is Windows-specific today; the Linux/macOS equivalent is
   unbuilt, so it reads as N/A rather than missing. Game key-hold compat shares
   both halves of that reasoning: it keys off the same exe list plus a Win32
   borderless-fullscreen probe, and the reason it exists (a key-down+up injected
   in one batch lands between two of the game's input polls) is a `SendInput`
   property, so there is nothing to port to `xdotool` as-is.
7. **Free on every backend.** The compact view is a layout JSON plus QML row
   filtering — keyboard *layers* are a QML-side view concept the backends never
   see, and `_load_layouts` already globs `data/layouts/*.json`. So there was no
   port: both backends gained it from the shared `qml/` + `data/` contract, and
   the C++ columns track the C++ backend's general state rather than this
   feature. See [`COMPACT_VIEW.md`](COMPACT_VIEW.md). A row like this is the
   argument for keeping `qml/` and `data/` shared.
8. **Context reset is four signals on Windows and one everywhere else.** All
   platforms notice an app switch (`GetForegroundWindow` / `xdotool
   getactivewindow`; Wayland exposes nothing, so it is a no-op there). The three
   that catch a move *inside* one window are Windows-only: the UIA focused-element
   RuntimeId, the published caret rectangle, and an outside-click probe
   (`src/platform/pointer.py`). Linux could gain the first through AT-SPI, which
   already backs password detection there; macOS could through AX. Both are
   unbuilt, so those platforms still carry stale context when the user clicks from
   one field to another in a single window. See the *Clearing stale context* section
   in `CLAUDE.md`.
9. **Python-only, and it is a real gap rather than a platform one.** The token
   store (`src/prediction/token_predictor.py`), its admission rule
   (`text_patterns.is_learnable_token`) and the bridge's two-bar switch
   (`_in_token_context` / `_token_pill_words` / `_insert_token_pill`) have no
   C++ counterpart. Nothing about it is platform-specific: it is plain string
   handling plus a `tokens` key in `ngram_model.json`, so it ports wholesale
   whenever the C++ bridge is next worked on. Port the admission rule *first* and
   test it against the same SSN / card near-misses. That half is what keeps the
   feature from remembering things it should not, and it is the half that looks
   most droppable when transcribing. See the *Structured Tokens* section in
   `CLAUDE.md`.

## Keeping this current

This table is the single source of truth for "what's ported where." Update the
relevant cell **in the same change** that lands a feature or ports a pillar. When
a C++ pillar reaches parity, flip its row to ✅ **and** move it into
`CPP_PORTED_PILLARS` in `tests/conformance/test_conformance.py` so the harness
starts enforcing it.
