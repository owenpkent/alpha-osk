# Security Audit

*Original audit: April 2026. Superseded by a repo-wide follow-up audit in August 2026, recorded first below. The April findings are preserved unmodified beneath it for the historical record, with two "Corrected" notes marking where the August pass proved them wrong.*

---

## August 2026 Audit

*Audited: August 2026*

A repo-wide security audit in August 2026, covering the full codebase rather than a single feature area, found **5 High, 3 Medium, and 10 Low severity issues**. All 18 were fixed as part of the same remediation pass; none were left open. This supersedes the "Overall rating: Strong" verdict in the April 2026 audit below, and corrects two of that audit's Pass findings (Logging, Network Exposure) that this pass showed were wrong.

### Findings, by severity

| # | Severity | Area | Finding | Fix |
|---|----------|------|---------|-----|
| 1 | High | Logging | `alpha-osk.log` recorded up to 200 characters of typed context on every prediction tap, including while privacy mode was active | Nine call sites in `keyboard_bridge.py` now log lengths and booleans only. New invariant: no record at INFO or above may carry typed content |
| 2 | High | Privacy gating | `pressPrediction` and `editPrediction` had no privacy-mode gate at all: a prediction accepted or edited while privacy mode was on still reached `record_prediction_selected`, `learn_from_selection`, `learn_capitalization`, and `set_capitalization` | Both now call `_check_password_field_sync()` and gate all four learning/tracking calls. Insertion itself stays ungated, so the tapped word still types |
| 3 | High | Data import | A crafted `snippets.json` in an imported Data Backup archive could plant a newline inside a snippet value; `xdotool type` turns `\n` into a real Return keystroke, so tapping the snippet once could run an arbitrary command in whatever app had focus | Imported snippet values have `\r`/`\n` flattened to spaces on import. Locally authored multi-line snippets (a mailing address, for example) are unaffected, since only the import path flattens them |
| 4 | High | Install integrity | The NSIS installer read `InstallDirRegKey HKCU`, a value nothing in the build ever wrote. Unprivileged local malware could create that value first, redirecting the next silent auto-update's install into an attacker-writable directory, then replace the EV-signed exe/DLLs there | `InstallDirRegKey HKCU` removed. The installer no longer trusts a registry value it never authored |
| 5 | High | Update trust chain | The auto-updater verified an installer's Authenticode signature and semver tag but not its embedded version, so a validly-signed **older** installer renamed to a newer version string would pass verification: a downgrade path back to a fixed vulnerability. The updater also derived the install directory implicitly rather than pinning it | `_verify_signature` now also checks the exe's embedded `FileVersion` against the claimed version. `_install_target_dir()` computes the install path explicitly, and the updater passes it via `/S /D=<dir>` (NSIS requires `/D=` to be the last parameter and unquoted) |
| 6 | Medium | QML rendering | 23 `Text` elements had no `textFormat` set, so an imported pack's `pack.json` name containing an `<img>` tag fired an outbound network request the moment Settings rendered the pack list | All 23 now set `textFormat: Text.PlainText`. One residual gap: an attached `ToolTip` cannot set `textFormat` |
| 7 | Medium | Data export | `_bounded_copy` did not enforce its per-entry / cross-entry byte caps against actual bytes read, and an unexpected `zipfile.BadZipFile` was uncaught, aborting import after some files had already been replaced | Caps now apply to real bytes copied; `BadZipFile` is caught and converted to `DataExportError`. (The declared, forgeable `file_size` was never itself a cap-bypass, since CPython's `ZipExtFile` truncates reads to it; the actual defect was the uncaught exception, not the cap logic) |
| 8 | Medium | Telemetry backend | The Cloudflare Worker had no real rate limiting on `/v1/submit` | Two `anon_id`-keyed layers: a Cloudflare rate-limit binding and a `SUBMIT_COOLDOWN_SECONDS` (3600s) window enforced in the D1 upsert. Neither reads a request header. All reject paths return the same 204 as success, so neither is an existence oracle. `app_version` / `os` are now validated against semver and a platform enum |
| 9 | Low | Privacy | A failed password-detector init (Linux AT-SPI unavailable, or any detector failure) fell back to a null detector silently; auto-pause on password fields would just stop working with no signal | New `detection_available()` (surfaced as bridge property `passwordDetectionAvailable`), a WARNING log on the fallback, and a low-key UI note so the silent case is now visible |
| 10 | Low | Data import | `_clean_value` (snippets / data export) did not strip C0 control characters other than tab and newline | Now strips all C0 controls except `\t` / `\n` |
| 11 | Low | Pack import | A pack folder named after a Windows reserved device name (`con`, `nul`, `com1`, and similar) passed the existing character-class validation but crashed the import loop when the OS refused to create it | Reserved device names are now rejected explicitly; a bad entry no longer aborts the whole per-pack write loop |
| 12 | Low | Resource limits | Vocabulary pack import had no size or entry ceiling | 64 KB meta cap, 20 MB per file, 50 MB per import, 200,000 entries each for words/bigrams/trigrams |
| 13 | Low | Resource limits | `analytics.json` had no file-size cap, and only `word_freq` (not `key_freq`) was capped at 5,000 entries | 5 MB file cap added; `key_freq` is now capped at 5,000 entries like `word_freq`, on both load and save |
| 14 | Low | Privilege | `ensure_admin_windows()` ran before dependency installation, so `pip install` executed elevated | Moved to after dependency installation. (The repo tree itself is still user-writable, so this reduces blast radius rather than eliminating it) |
| 15 | Low | Supply chain | `requirements.txt` used `>=` floors with no upper bound | Pinned to exact `==` versions. macOS-only `pyobjc-*` packages stay on floors; `--require-hashes` is an outstanding follow-up |
| 16 | Low | Key synthesis | The four Linux xdotool/ydotool call sites that type arbitrary text had no end-of-options guard, so text starting with a string the tool would parse as a flag could be misread as an option instead of typed. `_run()` also had no timeout and could hang the Qt UI thread indefinitely | A literal `--` now precedes user text at all four sites; `_run()` has a 2.0s timeout |
| 17 | Low | Build supply chain | `build/linux/build.py` fetched `appimagetool` from the mutable `continuous` GitHub tag with no integrity check | Pinned to tag `1.9.1` and SHA256-verified before execution. This is trust-on-first-use, since upstream ships no signed checksum manifest, but it catches a later swap, a re-pointed tag, or a corrupted or intercepted download |
| 18 | Low | CI supply chain | `.pre-commit-config.yaml`'s `ruff` hook tracked a mutable version tag, and `.env` was not gitignored | `ruff` pinned to a commit SHA; `.gitignore` gained `.env` |

### Corrected: Network Exposure

The April 2026 audit below states in its §2 that the application "makes **zero network calls** in normal operation." That was already wrong when it was written: the auto-update check (`src/updater.py`) shipped in v1.0.3, months before this audit, and calls the GitHub Releases API on startup by default. Opt-in telemetry (`src/telemetry.py`, off by default, client shipped ahead of its endpoint) was added afterward. Neither transmits typed content, see `docs/build/AUTO_UPDATE.md` and `docs/PRIVACY.md` for exactly what each sends, but "zero network calls" is not an accurate description of the shipped application at any point covered by either audit.

---

## April 2026 Audit (original)

This document summarizes the security posture of Alpha-OSK based on a comprehensive audit of the codebase, dependencies, and architecture.

---

## Summary

> **Superseded by the August 2026 audit above.** This assessment predates a repo-wide follow-up that found, and fixed, 5 High, 3 Medium, and 10 Low severity issues, including in two areas this audit rated Pass (see the "Corrected" notes under Logging and Network Exposure below).

**Overall rating: Strong.** No critical or high-severity vulnerabilities were found. The application follows security best practices across input handling, subprocess execution, file I/O, serialization, and privilege management.

---

## Areas Audited

### 1. Secrets and Credentials

**Status: Pass**

- No hardcoded API keys, tokens, passwords, or credentials in source code
- No `.env` files exist or are needed — the application has no remote services
- `.gitignore` properly excludes virtual environments, IDE configs, and build artifacts

### 2. Network Exposure

**Status: Pass**

> **Corrected by the August 2026 audit above.** The "zero network calls" claim below was already wrong at the time of this audit; see "Corrected: Network Exposure" above.

- The application makes **zero network calls** in normal operation
- All prediction runs entirely on-device (privacy-by-design)
- The optional dashboard (`run.py`) binds to `localhost:8080` only — not exposed to the network
- Optional `transformers` dependency may download models from Hugging Face on first use, but this is a standard ML library behavior

### 3. Subprocess and Shell Injection

**Status: Pass**

- All subprocess calls (Linux: `xdotool`, `ydotool`) use **list-form arguments**, never `shell=True`
- Inputs come from hardcoded key name mappings or pre-validated modifier lists
- No string interpolation in command construction
- `stdout` and `stderr` redirected to `DEVNULL` (no output leakage)

**Example (safe pattern):**
```python
subprocess.Popen(
    ["xdotool", "key", "--clearmodifiers", key_name],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
```

### 4. File Path Operations

**Status: Pass**

- Uses `pathlib.Path` throughout — no raw string path concatenation
- Existence checks (`path.exists()`, `path.is_dir()`) before all reads
- Config/model directories use platform-appropriate locations:
  - Windows: `%APPDATA%/alpha-osk/`
  - Linux: `~/.config/alpha-osk/`
- File import feature uses `Path.glob()` with an extension whitelist (`.txt`, `.md`, `.py`, `.js`, `.html`, `.css`, `.json`)

### 5. Deserialization

**Status: Pass**

- **JSON only** — no `pickle`, `yaml.load()`, `eval()`, or `exec()` anywhere in the codebase
- All model persistence uses `json.load()` / `json.dump()` with safe defaults
- JSON parsing wrapped in `try/except` with graceful fallback

### 6. Dependency Surface

**Status: Pass**

Only 3 runtime dependencies:

| Package | Purpose |
|---------|---------|
| `PySide6 >=6.6.0` | Qt6 UI framework |
| `transformers >=4.36.0` | Optional LLM re-ranking |
| `torch >=2.1.0` | Optional ML backend |

- All actively maintained, major projects
- No deprecated or known-vulnerable packages
- Dev dependencies (`pytest`, `ruff`, `mypy`) are standard tooling

### 7. Logging

**Status: Pass**

> **Corrected by the August 2026 audit above (finding #1).** This section was wrong: nine sites in `keyboard_bridge.py` logged up to 200 characters of typed context on every prediction tap, including while privacy mode was active. Fixed; see the August 2026 findings table above.

- Logs contain only operational metadata (platform info, model load paths, prediction stats)
- No user-typed text, keystrokes, or personal data written to logs
- Debug-level logging on `HybridPredictor` is verbose but non-sensitive

### 8. Privilege and Permission Handling

**Status: Pass**

- **Windows:** Admin elevation via `ShellExecuteW` with `"runas"` — triggers UAC dialog requiring user consent. Justified because `SendInput` needs appropriate privilege to inject keystrokes into elevated windows.
- **Linux:** No privilege escalation. `xdotool`/`ydotool` run as the current user.
- Model/config files stored in user-owned directories with standard permissions.

### 9. Input Validation

**Status: Pass**

- QML-to-Python bridge accepts key names from a fixed set of mappings
- Modifier state tracked internally, not derived from untrusted input
- Text import reads file content as raw text — no code execution paths

### 10. Code Quality and CI

**Status: Pass**

- `ruff` linter enforced in CI
- `mypy` type checking enforced in CI
- 266+ tests with 60% coverage minimum
- Pre-commit hooks configured

---

## Recommendations

These are low-severity hardening suggestions, not required fixes.

### 1. Set production log level for HybridPredictor

`keyboard_app.py` sets `HybridPredictor` to `DEBUG`. Consider `INFO` for production builds to reduce log noise.

**File:** `src/keyboard_app.py`

### 2. Add model file validation

JSON model files are loaded without schema validation. A corrupted or adversarially crafted model file could degrade predictions (though not execute code). Consider adding basic size limits or schema checks on load.

**Files:** `src/prediction/ngram_predictor.py`, `src/prediction/ppm_predictor.py`

### 3. Pin dependency versions

`requirements.txt` uses `>=` minimum bounds without upper limits. A lockfile or pinned versions would prevent unexpected upgrades from introducing vulnerabilities.

**File:** `requirements.txt`

**Done, August 2026 audit finding #15.** Pinned to exact `==` versions; `--require-hashes` remains an outstanding follow-up.

### 4. Restrict dashboard file serving scope

The `SimpleHTTPRequestHandler` in `run.py` serves files from the working directory. If the working directory is changed (e.g., launched from a different path), it could inadvertently expose unintended files. Consider restricting the serve directory explicitly.

**File:** `run.py`

### 5. File import boundary (multi-user environments)

`importTextFile()` and `importFolder()` accept any user-selected path. On shared systems, consider restricting imports to user-owned directories. This is by-design for single-user use but worth noting for future deployment scenarios.

**Files:** `src/keyboard_bridge.py`

---

## Architecture Strengths

- **Privacy-first:** All data stays on-device, no telemetry, no cloud calls
- **Minimal attack surface:** 3 runtime dependencies, stdlib for critical paths
- **Safe defaults:** JSON serialization, list-form subprocess, `pathlib` paths
- **Platform isolation:** Clean separation between Linux and Windows implementations
- **No code execution paths:** No `eval`, `exec`, `pickle`, or dynamic imports from user data

---

## Scope and Limitations

This audit covers the application source code, configuration, and dependencies as of April 2026. It does not cover:

- Runtime environment security (OS hardening, filesystem permissions)
- Code signing and distribution integrity (EV signing is documented but not yet implemented)
- Third-party dependency CVEs (recommend periodic `pip audit` scans)
- Future features (voice dictation, federated learning) which will require separate review
