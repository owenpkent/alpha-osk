# Alpha-OSK Documentation

The canonical reference is the [white paper](WHITEPAPER.md). User-facing privacy lives in [`PRIVACY.md`](PRIVACY.md). Everything else is grouped into four buckets below.

For the codebase orientation that pairs with these docs, read [`CLAUDE.md`](../CLAUDE.md) at the repo root.

---

## Top-level

- [`WHITEPAPER.md`](WHITEPAPER.md): the canonical reference paper. Architecture, prediction stack, privacy and security model, and the open work. Read this if you want one document that explains the whole system.
- [`PRIVACY.md`](PRIVACY.md): user-facing data policy. What's stored locally, what (optionally) leaves your machine, and how to delete contributed data.

## `architecture/`: how the running system works

Active design docs for the engine, the platform abstraction, and the user-facing extensibility surfaces. Edit when the code in that area changes.

- [`architecture/HYBRID_MERGING.md`](architecture/HYBRID_MERGING.md): how the n-gram, PPM, and fuzzy predictors are combined, the four merge strategies, capitalisation pipeline, validation.
- [`architecture/PPM.md`](architecture/PPM.md): variable-order character model with PPMD escape, used for next-character prediction inside a partial word.
- [`architecture/FUZZY_RECOGNITION.md`](architecture/FUZZY_RECOGNITION.md): spatial error correction, tunable constants, the relationship to SymSpell.
- [`architecture/SWIPE_TYPING.md`](architecture/SWIPE_TYPING.md): shape-matching gesture decoder (simplified SHARK²).
- [`architecture/PLATFORM_ARCHITECTURE.md`](architecture/PLATFORM_ARCHITECTURE.md): cross-platform abstraction details (key synthesis, password-field detection, config paths).
- [`architecture/MODULAR_LAYOUTS.md`](architecture/MODULAR_LAYOUTS.md): custom keyboard layouts inspired by Octavium / Nimbus.
- [`architecture/LONG_PRESS_ALTERNATES.md`](architecture/LONG_PRESS_ALTERNATES.md): long-press accent picker design (deferred; rationale in the doc).
- [`architecture/EXTRA_BUTTONS.md`](architecture/EXTRA_BUTTONS.md): brainstorm of beyond-keyboard button options.
- [`architecture/TELEMETRY.md`](architecture/TELEMETRY.md): opt-in usage-stats pipeline (payload schema, anon_id lifecycle, backend, deployment workflow).

## `build/`: packaging, signing, releases, updates

Per-platform build pipelines and the auto-update path. Edit when the release process or platform handling changes.

- [`build/WINDOWS.md`](build/WINDOWS.md): Windows build, EV signing, NSIS installer, release checklist, troubleshooting.
- [`build/LINUX.md`](build/LINUX.md): Linux build pipeline, AppImage internals, troubleshooting.
- [`build/MACOS.md`](build/MACOS.md): macOS port plan, phase breakdown, TCC and SEI specifics.
- [`build/AUTO_UPDATE.md`](build/AUTO_UPDATE.md): auto-update flow, threat model, signature verification.
- [`build/BRANDING.md`](build/BRANDING.md): asset regeneration (icons, installer images).

## `roadmap/`: planned, not-yet-built

Forward-looking design docs and active launch planning. Convert entries into `architecture/` or `build/` once they ship.

- [`roadmap/LAUNCH_PLAN.md`](roadmap/LAUNCH_PLAN.md): release prep checklist.
- [`roadmap/launch_tasks.csv`](roadmap/launch_tasks.csv): structured task tracking.
- [`roadmap/FEDERATED_LEARNING.md`](roadmap/FEDERATED_LEARNING.md): federated-learning roadmap (separate from §5.6 telemetry; not yet implemented).
- [`roadmap/ECOSYSTEM.md`](roadmap/ECOSYSTEM.md): four-tool adaptive-input platform (Alpha-OSK + MacroVox + Octavium + Nimbus).
- [`roadmap/MACROVOX_INTEGRATION.md`](roadmap/MACROVOX_INTEGRATION.md): voice-dictation integration plan.
- [`roadmap/DOCUMENT_IMPORT.md`](roadmap/DOCUMENT_IMPORT.md): import-from-document feature spec.

## `research/`: background, philosophy, audits, brainstorms

Reference material that informed the design but isn't a living spec. Useful for context; not authoritative for current behaviour.

- [`research/PHILOSOPHY.md`](research/PHILOSOPHY.md): project philosophy and information-theoretic foundations.
- [`research/USER_PERSPECTIVE.md`](research/USER_PERSPECTIVE.md): user-lens framing of the project.
- [`research/DESIGN.md`](research/DESIGN.md): early overall design notes.
- [`research/INNOVATION_SOURCES.md`](research/INNOVATION_SOURCES.md): index of innovations drawn from Dasher, Gboard, LatinIME, Presage, etc.
- [`research/TECHNICAL_INNOVATIONS.md`](research/TECHNICAL_INNOVATIONS.md): deep dive into the Dasher Project innovations.
- [`research/MOBILE_KEYBOARD_INNOVATIONS.md`](research/MOBILE_KEYBOARD_INNOVATIONS.md): deep dive into the Gboard / SwiftKey approaches.
- [`research/LEARNING_IMPROVEMENTS.md`](research/LEARNING_IMPROVEMENTS.md): exploration of learning-loop changes.
- [`research/PREDICTION_OPTIONS.md`](research/PREDICTION_OPTIONS.md): exploration of prediction-stack options.
- [`research/TRAINING_DATA_STRATEGY.md`](research/TRAINING_DATA_STRATEGY.md): training-data sourcing strategy.
- [`research/STRATEGIC_OPPORTUNITIES.md`](research/STRATEGIC_OPPORTUNITIES.md): strategic positioning notes.
- [`research/SECURITY_AUDIT.md`](research/SECURITY_AUDIT.md): security-audit record from the hardening pass.
- [`research/LLM_ONBOARDING.md`](research/LLM_ONBOARDING.md): early AI-onboarding doc (largely superseded by [`../CLAUDE.md`](../CLAUDE.md)).
- [`research/LLM_ONBOARDING_TEMPLATE.md`](research/LLM_ONBOARDING_TEMPLATE.md): template for the above.
- [`research/CONSTELLATION_INTEGRATION_GUIDE.md`](research/CONSTELLATION_INTEGRATION_GUIDE.md): Constellation cross-project integration guide.
- [`research/SLIDES_WORKFLOW.md`](research/SLIDES_WORKFLOW.md): presentation/slides workflow.

---

## When to put a doc where

- **Top-level docs** (`WHITEPAPER.md`, `PRIVACY.md`): user-facing or canonical-reference. Anything an end user or external reviewer should see without drilling.
- **`architecture/`**: a live system that's currently shipping. The code in `src/` should match what the doc says.
- **`build/`**: anything specific to producing a release artefact or shipping it to users.
- **`roadmap/`**: designed but not yet implemented. Move out of `roadmap/` once it ships.
- **`research/`**: written to inform a decision (or capture an audit) but not the source of truth for current behaviour.
