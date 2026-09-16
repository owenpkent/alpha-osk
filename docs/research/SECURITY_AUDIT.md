# Security Audit

*Three passes, newest first: a September 2026 follow-up (which also records the fixes that landed between audits), the repo-wide August 2026 audit, and the original April 2026 audit, preserved unmodified beneath them with two "Corrected" notes marking where the August pass proved it wrong.*

---

## September 2026 Audit

*Audited: 15 September 2026, against `main` at `09c1c53` (v1.4.1 plus the fixes since).*

A third repo-wide pass, run as six parallel reviews by surface (import and persistence; subprocess, OS and privilege; network and the telemetry backend; privacy gating, secrets and logging; build, installer and CI; QML and the bridge boundary). Each review excluded what the two earlier audits and the interim fixes below had already closed, and every candidate it raised was re-verified against the code, and where possible reproduced, before it was accepted. It found **0 High, 2 Medium and 11 Low** severity issues and 4 documentation inaccuracies. Twelve of the thirteen are fixed in the pull request that added this section; the thirteenth is a UI routing change and ships as #122. Seven candidates were verified and declined and are listed with the reason, since the next pass will find them again.

### Between the audits

Fixes that landed between August and this pass and were not recorded here:

- **#79**: the dictation API key was reachable through the config object's `repr`.
- **#80**, five findings from an interim review: `main()` pinned the `HybridPredictor` logger to DEBUG, so the engine's candidate words (the merged pill row and every shortlist) were written to `alpha-osk.log` on every keystroke, the file bug reports attach (critical); PowerShell was invoked by bare name on the updater's trust gate, so a `powershell.exe` planted in the working directory could forge a `Valid` verdict; the prediction pill tooltip could render HTML from an imported dictionary; a bad model file broke the `_user_total` invariant durably; `NaN` survived a round trip into the saved model.
- **#81**: the downloaded installer is pinned (`FILE_SHARE_WRITE` and `FILE_SHARE_DELETE` withheld) from before the signature check until after the elevated launch, closing a same-user swap in the window between them.
- **#82**: the installer's SHA-256, hashed as it streamed in, is re-checked once the file is pinned.
- **#120**: the Linux synthesizer logged the whole `xdotool type` command line at ERROR when the tool stalled or failed, which for typed text is the text, password fields included, and the chorded key name at WARNING with no tool installed; macOS logged a chorded key with no keycode by its character. Redacted, with a second generation of the pre-fix log purge on those two platforms.

### Findings, by severity

| # | Severity | Area | Finding | Fix |
|---|----------|------|---------|-----|
| 1 | Medium (latent) | Installer | `customInstall` read the previous version's `UninstallString` from HKCU, which any process running as the user can write, and ran it with the installer's administrator token behind a prompt recommending Yes, shown even on a silent auto-update because it carried no `/SD` (measured: NSIS displays an un-defaulted `MessageBox` under `/S`). A planted string was one Yes away from running as administrator. The branch never fired, because the Install section wrote this install's own entry over the key first, in every revision in history, so no shipped installer was exploitable; the same ordering meant removing a previous version from another directory never worked either | Nothing read from the registry is executed. The entry is read before this install writes its own; `removePreviousInstallAt` uses only `InstallLocation`, normalises it with `GetFullPathName`, requires a per-user location to sit under Program Files and to hold our own `alpha-osk.exe` and `uninstall.exe`, runs that uninstaller and nothing else, and defaults to No when silent. The Add/Remove entry moves to HKLM, the hive matching the privilege that writes it; the uninstaller clears both hives. Verified with a compiled before/after harness under a test GUID: the old macro alone ran a planted uninstaller on a simulated Yes, the shipped ordering did not, and the new macro refuses a planted path and a `..` traversal, retires a stale entry, removes a genuine old install on Yes and does nothing on the silent default. `tests/test_windows_installer.py::TestThePreviousVersionCheckExecutesNothingFromTheRegistry`, including a `makensis` compile |
| 2 | Medium | Data import (model) | `NgramPredictor.load()` validated the keys of every count table but stored the values as found, so a Data Backup archive carrying `"unigrams": {"hello": "boom"}` loaded cleanly; the shipped corpus then failed to load (degrading every prediction, with only a log line to show for it), `predict` raised, and `learn` raised on the keystroke path the next time the word was completed, leaving the bridge's buffers out of step with the screen | `_clean_counts` keeps an entry only for a string key and a non-bool, finite, non-negative number, dropping the rest one at a time and logging only how many; applied to every count table and to `total_words` and `blacklist`. `tests/test_ngram_load_hardening.py` |
| 3 | Low | Data import | A corrupt compressed member escaped `_bounded_copy` as `zlib.error` (only `BadZipFile` was translated), so a bad second model file aborted the import after the first had already replaced a good one: the half-applied state August item 7 was meant to close | Every decompression failure (`zlib.error`, `EOFError`, `lzma.LZMAError`, bz2's bare `OSError`) is translated to `DataExportError`, and every member the import would write is first streamed in full against the same caps into a discard sink, before any live file is touched. `TestACorruptMemberCannotHalfApplyTheImport` |
| 4 | Low | Data import | Import flattened `\r` and `\n` in snippet values but kept a literal tab, which both synthesizers type as a real Tab keystroke that moves focus in the target app. Latent today, since a tile tap copies rather than types | Tabs are flattened on import too; locally authored snippets keep them |
| 5 | Low | Persistence | `KeyActionStore.save()` hand-rolled its tempfile write with no flush or fsync, against the documented rule that every store writes through `atomic_write` | Routed through `atomic_write_json` |
| 6 | Low | Privacy | The prediction pill's "Show more", "Show less" and "Remove" actions called the model with no password-field check and no privacy gate, unlike the two insert slots, so a pill still on the bar during the poll window after a password field took focus could be boosted, downweighted or blacklisted into the persisted model. The word is one the model already held, not typed content, so the exposure is a recorded preference | All three call `_check_password_field_sync()` and return while privacy mode is on. The Dashboard's roll-back actions are deliberately not gated: they undo recorded state rather than learn from typing. `TestWordSuppressionRespectsPrivacyMode` |
| 7 | Low | Privacy | The trailing Debug Console line in `pressPrediction` and `editPrediction` named the tapped word and its next-word pills outside the privacy guard every neighbouring call is inside. In-memory only and off by default | Moved inside the guard. `TestPredictionDebugLogRespectsPrivacyMode` |
| 8 | Low | UI routing | Edit mode has one shared flag and no owner: opening the Snippets window while the prediction-edit popup is open calls `setEditMode(false)`, so the next keystrokes typed at the still-open popup reach the app behind it instead. Opening editors the other way round delivers each keystroke to two fields. Traced, not reproduced. This is the ownership risk the architecture review of 10 September named | Fixed in #122: an edit session with an owner, where a release from anyone but the owner is ignored and a new owner closes the previous surface |
| 9 | Low | Telemetry backend | `/v1/forget` and the 365-day cron deleted only the `users` row and relied on the schema's `ON DELETE CASCADE` for `submissions_latest`. D1 enforces foreign keys by default (its documentation: "By default, D1 enforces that foreign key constraints are valid within all queries and migrations"), so the deletion did happen; the right to be forgotten simply rested on a PRAGMA | Both statements now delete the child row explicitly, in one D1 batch, child first |
| 10 | Low | CI | The telemetry aggregate job fed the worker's JSON through `jq` `@csv` with no type check, so a compromised worker returning a string beginning with `=`, `+`, `-` or `@` would have planted a spreadsheet formula in the committed CSV | Every field must be a number or the run fails |
| 11 | Low | Supply chain | Dependabot watched `pip` and `github-actions` but not the Cloudflare Worker's npm lockfile; the two advisories that did hit it (#112) needed a manual fix | `npm` ecosystem added for `backend/cf-worker`, grouped like pip |
| 12 | Low | Dev tooling | `python run.py --dashboard` bound its HTTP server to every interface, serving the templates directory to the LAN | Bound to `127.0.0.1` |
| 13 | Low | Robustness | `PointerModel.correct()` lacked the finite guard `observe()` has, so a non-finite offset from a bridge slot would have put `NaN` into the prefix beam's spatial scoring. Not reachable from the shipped QML, which guards the division | Non-finite reads as the key centre; offsets clamped as observations are |

### Documentation corrected

- `docs/build/WINDOWS.md` said every `.exe` and `.dll` in the bundle is signed. Only `.exe` files are (`sign_directory` defaults to `exe_only=True` and `sign_build` passes nothing); the bundled Python and Qt DLLs ship unsigned inside the signed installer. The paragraph now says so and names the switch. Whether to sign them, at one EV token operation per file, is an open decision.
- `docs/build/RELEASE.md` cited the `osv-scanner-action` pin at v2.3.8; CI is on a later pin that Dependabot keeps current.
- `docs/architecture/TELEMETRY.md` said the `anon_id` lives in `analytics.json`; it lives in `telemetry.json`.
- `docs/PRIVACY.md` described the first log leak and its fix; the second case (#120) is now recorded beside it.

### Verified and declined

- **The relauncher launches the freshly installed exe on an mtime and size heuristic without re-verifying its signature.** The relauncher runs and launches at the user's integrity level, so this grants no privilege; and a user-writable install directory already lets the exe be replaced at any time, the residual August item 4 records. A re-check would add cost without closing anything.
- **`KeySynthesizerBase._log_send` puts the full command, typed text included, into a DEBUG record with no privacy gate.** Sanctioned: the root logger is INFO and nothing in `main()` raises it (PR #80 removed the one place that did), and the platform layer cannot see privacy mode. Recorded in `keyboard_app.py`.
- **The Windows synthesizer's `Unknown key name` warning interpolates the key.** It fires only for a multi-character name that is not a known virtual key; a typed character is single and takes the Unicode fallback silently.
- **The token store admits letters-plus-digits shapes that could be a password.** A documented trade: every learned token is visible and individually removable under Saved Numbers & Addresses.
- **Branch protection requires no pull-request review and `enforce_admins` is off.** The setting of a solo-maintainer repository; flagged as a decision to revisit the day a collaborator or a token with push rights is added.
- **`create_shortcut` and its siblings in `windows.py` have no callers.** Dead code, not a defect; housekeeping.
- **The relauncher and the Unreal-style game hold are unrelated to this pass** and were not re-examined beyond the items above.

### Method

Static tracing by six reviewers, then verification by the lead: five probes (a poisoned model through `HybridPredictor`, a corrupted deflate member, NSIS `MessageBox` behaviour under `/S` with and without `/SD`, the compiled before/after installer harness, and D1's foreign-key documentation), plus `git` history for the installer ordering. The full suite, both mypy platforms and ruff pass on the branch. Not exercised: a live Linux desktop, real Quartz events, a signed build, or a live D1 instance.

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
