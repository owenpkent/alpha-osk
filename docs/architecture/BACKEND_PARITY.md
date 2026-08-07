# Backend × Platform Parity Matrix

Alpha-OSK has **two backends** implementing the same behaviour, and **three
platforms**. These are perpendicular axes, and the repo is organised around that:

- **Backend** (language/runtime): the Python backend (`src/`) and the in-progress
  C++/Qt6 rewrite (`cpp/`, on the `cpp-rewrite` branch). See
  [`docs/build/CPP_WINDOWS.md`](../build/CPP_WINDOWS.md) for the rewrite's rationale.
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
| Right-click modifier lock | 🚧 ¹ | 🚧 ¹ | 🚧 ¹ | ✅ | ❌ | ❌ |
| Key-click audio | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| Key preview bubble (pure QML) | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| Compact view + layers (pure QML + data) ⁷ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| **Prediction engine** | | | | | | |
| n-gram (uni/bi/trigram) | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| PPM (char model) | ✅ | ✅ | ✅ | 🚧 ² | ❌ | ❌ |
| Fuzzy / autocorrect | ✅ | ✅ | ✅ | 🚧 ² | ❌ | ❌ |
| Hybrid merge (rank default) | ✅ | ✅ | ✅ | 🚧 ² | ❌ | ❌ |
| Swipe typing | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
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
| Game key-hold compat | 🚧 ¹ | 🚧 ¹ | 🚧 ¹ | ✅ | ❌ | ❌ |
| Context reset on focus change | 🚧 ¹ | 🚧 ¹ | 🚧 ¹ | ✅ | ❌ | ❌ |

## Footnotes

1. **Stranded on `cpp-rewrite`.** Right-click modifier lock, game key-hold compat,
   and focus-change context reset were built on the `cpp-rewrite` branch (both the
   shared QML and the Python backend halves) but have **not** landed on `main`.
   They should be extracted to `main` so the Python backend gains them
   independently of the rewrite. See the branch-sync plan (below / `git log`
   commits `9fbf065`, `8942af7`, `31a7b96`, `91bfce5`).
2. **Doc says done, header says stubbed.** `docs/build/CPP_WINDOWS.md` marks the
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
   unbuilt, so it reads as N/A rather than missing.
7. **Free on every backend.** The compact view is a layout JSON plus QML row
   filtering — keyboard *layers* are a QML-side view concept the backends never
   see, and `_load_layouts` already globs `data/layouts/*.json`. So there was no
   port: both backends gained it from the shared `qml/` + `data/` contract, and
   the C++ columns track the C++ backend's general state rather than this
   feature. See [`COMPACT_VIEW.md`](COMPACT_VIEW.md). A row like this is the
   argument for keeping `qml/` and `data/` shared.

## Keeping this current

This table is the single source of truth for "what's ported where." Update the
relevant cell **in the same change** that lands a feature or ports a pillar. When
a C++ pillar reaches parity, flip its row to ✅ **and** move it into
`CPP_PORTED_PILLARS` in `tests/conformance/test_conformance.py` so the harness
starts enforcing it.
