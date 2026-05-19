# Security Audit

*Last audited: April 2026*

This document summarizes the security posture of Alpha-OSK based on a comprehensive audit of the codebase, dependencies, and architecture.

---

## Summary

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
