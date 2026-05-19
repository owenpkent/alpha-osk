# Auto-Update Strategy

## Current State

**Implemented in v1.0.3; updater endpoint corrected in v1.0.5.** Alpha-OSK checks GitHub Releases on startup (3 s after launch) and shows an in-app banner when a newer signed installer is available. Click *Install* and the app downloads the installer, verifies its Authenticode signature against our pinned EV-cert thumbprint, and runs it silently — the NSIS installer kills the running app, runs the previous uninstaller, and installs the new build.

Code lives in `src/updater.py` (network + signature verification), `src/keyboard_bridge.py` (`checkForUpdate` / `installUpdate` / `dismissUpdate` slots, `updateAvailable` / `updateUnavailable` / `updateInstallStarted` / `updateInstallFailed` signals), `qml/Main.qml` (banner + Connections), `qml/components/UnifiedSettingsPanel.qml` (Updates section). Tests in `tests/test_updater.py`.

### Source-vs-releases repo split

Source code lives at `owenpkent/alpha-osk`; release binaries live in a separate repo at `okstudio1/alpha-osk-releases`. Both are public as of 2026-05-16. The split is preserved for two reasons: (1) the auto-updater's API URL is hard-pinned to the releases repo (`src/updater.py::GITHUB_API_URL`), so flipping which repo holds releases would break every existing user's updater, and (2) keeping binaries out of the source repo keeps clone time small. **Historical note:** the source repo used to be private. v1.0.3 and v1.0.4 shipped with the wrong endpoint hard-coded (pointing at the then-private source repo), so their updater always saw "no update available". v1.0.5 fixed the endpoint; v1.0.3 / v1.0.4 users needed one final manual install of v1.0.5 to get on the working updater path.

## Threat model

The updater is the highest-value MITM target in the app — a successful attacker gets to ship arbitrary signed code on every user's machine. Defences are layered so no single layer compromise unlocks code execution:

| Threat                                                  | Defence                                                                                                |
|---------------------------------------------------------|--------------------------------------------------------------------------------------------------------|
| TLS strip / MITM                                        | `urllib` cert validation + scheme whitelist (https only)                                              |
| DNS hijack to attacker host                             | Authenticode pin — attacker can't sign with our key                                                   |
| Compromised GitHub asset                                | Authenticode pin: `Status == Valid` AND thumbprint matches `fc22b522…` AND signer CN matches `OK Studio Inc.` |
| Asset URL points off-host                               | Host whitelist: `github.com`, `objects.githubusercontent.com`, `release-assets.githubusercontent.com` (only)                                             |
| Post-redirect host swap                                 | Re-validate `resp.geturl()` after `urlopen` follows redirects                                          |
| Disk-fill                                               | `_MAX_DOWNLOAD_BYTES = 500 MB` aborts runaway downloads                                                |
| Downgrade attack                                        | Strict semver compare (`is_newer`); equal/older silently refused                                       |
| Pre-release/garbage tag confusion (`v1.0.3-evil`)       | Regex `^\d+\.\d+\.\d+$` only — pre-release/+build refused                                              |
| Misnamed asset                                          | Filename pattern locked to `Alpha-OSK-Setup-{version}.exe`                                             |
| Tag confusion across repos                              | Endpoint hard-pinned to `https://api.github.com/repos/okstudio1/alpha-osk-releases/releases/latest` and the `api_url` prefix is checked at call time |
| QML-side URL injection                                  | QML never sees the URL — it only triggers `installUpdate()`; the bridge holds `self._update_info`     |
| Release-notes injection                                 | `_sanitize_notes` strips C0 controls and caps length to 4 KB                                          |

What's **not** covered: compromise of the EV signing key. That's a build-pipeline / cert-rotation response, not a client-side fix.

## Original design

## Implemented: Option A — GitHub Releases + Silent Installer (with hardening)

The simplest path that leverages existing infrastructure.

### How It Works

1. On startup (3 s after launch, on a background thread), the app calls the GitHub Releases API:
   ```
   GET https://api.github.com/repos/okstudio1/alpha-osk-releases/releases/latest
   ```
   This is the **public release-binaries** repo. The source repo (`owenpkent/alpha-osk`) is also public as of 2026-05-16, but the split is preserved because every shipped client is hard-pinned to the releases-repo URL.
2. Compare the release tag (e.g., `v1.0.2`) against the running version.
3. If newer, show a notification in the system tray: "Alpha-OSK v1.0.2 available — click to update."
4. User clicks → app downloads the installer `.exe` from the release assets to `%TEMP%`.
5. App launches the installer silently: `Alpha-OSK-Setup-1.0.2.exe /S`
6. The NSIS installer kills the running instance (`taskkill /F /IM alpha-osk.exe` in `customInit`), uninstalls the old version, installs the new one, and **relaunches the new app** via `explorer.exe` (drops admin IL → user IL — see `build/windows/installer.nsh`'s `customInstall`). The `IfSilent` gate scopes auto-relaunch to the auto-update path only; an interactive install does not auto-launch the app on completion.

### Implementation Plan

**New file: `src/updater.py`**
```python
import json
import urllib.request
import subprocess
import tempfile
import logging
from pathlib import Path

GITHUB_API = "https://api.github.com/repos/okstudio1/alpha-osk-releases/releases/latest"
CURRENT_VERSION = "1.0.1"  # or read from a version file

def check_for_update() -> dict | None:
    """Check GitHub for a newer release. Returns release info or None."""
    try:
        req = urllib.request.Request(GITHUB_API, headers={"Accept": "application/vnd.github.v3+json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        tag = data.get("tag_name", "").lstrip("v")
        if tag > CURRENT_VERSION:
            # Find the installer asset
            for asset in data.get("assets", []):
                if asset["name"].endswith(".exe") and "Setup" in asset["name"]:
                    return {
                        "version": tag,
                        "url": asset["browser_download_url"],
                        "name": asset["name"],
                        "notes": data.get("body", ""),
                    }
    except Exception:
        pass
    return None

def download_and_install(url: str, filename: str) -> None:
    """Download installer to temp dir and run it silently."""
    dest = Path(tempfile.gettempdir()) / filename
    urllib.request.urlretrieve(url, dest)
    # /S = silent, installer handles kill + uninstall + install + relaunch
    subprocess.Popen([str(dest), "/S"])
```

**Integration points:**
- `keyboard_app.py`: call `check_for_update()` on startup (in a background thread)
- System tray menu: add "Update Available (v1.0.2)" action when update found
- Settings panel: "Check for Updates" button + "Auto-check on startup" toggle

**Version tracking:**
- Add `VERSION = "1.0.1"` to a `src/__version__.py` file
- Read it in the updater and in `build_windows.py` (single source of truth)
- The NSIS installer already writes `DisplayVersion` to the registry

### Pros
- Zero new infrastructure — uses GitHub Releases you already have
- Installer is already EV-signed — no SmartScreen issues
- NSIS silent upgrade already works (tested)
- Simple code (~50 lines)

### Cons
- Downloads the full ~164MB installer every time (no delta updates)
- Requires internet access on startup
- GitHub API rate limit: 60 requests/hour unauthenticated (plenty for this)

### Security
- Download over HTTPS (GitHub CDN)
- Installer is EV-signed — Windows verifies Authenticode before executing
- No code execution from the update check itself (just JSON parsing)
- User must explicitly click to install (no silent background installs)

---

## Alternative Options (for future consideration)

### Option B — Delta Updates

Ship a small updater binary alongside the app. Maintain a version manifest on S3/CloudFront. Compute binary diffs between releases. Updater downloads only changed files.

- **Pros**: Much smaller downloads (~5-20MB vs 164MB)
- **Cons**: Complex — need diff computation, manifest hosting, rollback logic, a separate updater exe that survives the update process
- **When**: If user base grows large enough that bandwidth matters

### Option C — WinGet

Publish to [WinGet](https://github.com/microsoft/winget-pkgs) (Microsoft's package manager). Users update via `winget upgrade alpha-osk`.

- **Pros**: OS-integrated, familiar to developers, free hosting
- **Cons**: Requires submitting a manifest PR to microsoft/winget-pkgs for each release (or setting up a WinGet REST source). Not discoverable for non-technical users.
- **When**: Good to add alongside Option A — it's just a YAML manifest per release

**WinGet manifest example** (`manifests/o/OKStudio/AlphaOSK/1.0.1/`):
```yaml
PackageIdentifier: OKStudio.AlphaOSK
PackageVersion: 1.0.1
InstallerType: nsis
Installers:
  - Architecture: x64
    InstallerUrl: https://github.com/okstudio1/alpha-osk-releases/releases/download/v1.0.1/Alpha-OSK-Setup-1.0.1.exe
    InstallerSha256: <sha256>
    InstallerSwitches:
      Silent: /S
      SilentWithProgress: /S
DefaultLocale: en-US
PackageName: Alpha-OSK
Publisher: OK Studio Inc.
ShortDescription: AI-powered on-screen keyboard for accessibility
License: Proprietary
PackageUrl: https://github.com/owenpkent/alpha-osk
```

### Option D — Microsoft Store

Publish as an MSIX package to the Microsoft Store.

- **Pros**: Auto-updates handled by Windows, trusted distribution channel, discoverable
- **Cons**: MSIX packaging is different from NSIS, Store review process, 15% revenue cut on paid apps, UIAccess may not work in MSIX sandbox
- **When**: If targeting mainstream non-technical users. UIAccess compatibility needs testing first.

---

## Recommendation

Start with **Option A** (GitHub Releases check). Add **Option C** (WinGet) as a low-effort bonus. Defer B and D until the user base warrants it.

---

## Update progress indicator (T40, shipped)

### Problem

Reported after a 1.0.15 → 1.0.17 update: the new keyboard does come back (the relauncher fix from 1.0.17 works), but there's a ~15-60 s gap between the installer's `taskkill /F /IM alpha-osk.exe` and the relauncher launching the new exe. During that gap **no UI was alive at all** (intentional per `_update_relauncher.py`'s original docstring: "Failures are deliberately silent. There is no UI surface to report into."). For an accessibility audience that just lost their primary input method, that silence read as "the update broke the keyboard." The user reported thinking it was broken until it eventually came back.

The on-startup ✓ Updated toast added in 1.0.17 fires *after* the gap and helps confirm "yes, that worked", but it can't bridge the silence during the gap itself.

### Where the gap comes from

`_update_relauncher.run_relauncher` has three sequential waits, all silent in the original implementation:
1. `_wait_for_parent_exit` — up to 60 s for the installer's taskkill to land (usually < 1 s).
2. `time.sleep(_INSTALLER_GRACE_S)` — fixed 5 s for the installer to finish file copies.
3. `_wait_for_new_exe` — up to 180 s polling for `$INSTDIR\alpha-osk.exe` mtime to advance past parent-death.

So the floor is ~5 s and the ceiling is ~245 s. Real installs land at ~15-30 s on a healthy machine; AV scanning of the freshly-extracted DLLs can push it higher.

### Implementation (two layers, both shipping together)

**Layer 1: pre-update expectation-setting in the live OSK.** Before `updater.download_and_install` spawns the installer (after download + signature verify succeed), it invokes a new optional `on_installer_launching` callback. The bridge wires this to emit `updateInstallHandoffPending(version)` and then sleep 1.8 s in the worker thread, so the toast paints and is legible before the installer's taskkill arrives. The QML side adds an `updateStartingToast` Popup ("Installing v1.0.X. The keyboard will disappear briefly and come back.") modeled on the existing `updateAppliedToast`. Worth noting: the toast deliberately has no auto-close timer, because the installer's taskkill closes the whole process within ~1-2 s anyway, and a timer that fires just before the taskkill would leave the user with the same silence we're trying to avoid.

**Layer 2: visible relauncher splash during the gap.** `_update_relauncher` grew a `--show-splash` flag that the production caller (`updater._spawn_relauncher`) always passes. When the flag is set, `run_relauncher` dispatches to `_run_with_splash` instead of the original `_run_headless`. The splash is a frameless `WindowStaysOnTopHint` `QWidget` (not `QSplashScreen` — the latter's image-background model didn't fit the text-and-progress display we wanted). The polling logic was refactored into a `QTimer` state machine driven by `_poll_parent` → `_start_new_exe_phase` → `_poll_new_exe` → `_launch`, with a new `_new_exe_ready` single-shot helper replacing the blocking `_wait_for_new_exe` poll loop so the event loop can repaint between checks. Phase-aware messages: "Waiting for the installer to finish…" → "Installing files…" → "Launching the new keyboard…" → "Done!" (800 ms dwell so the splash doesn't vanish a frame before the new OSK draws its first window). Failure paths surface a "Find Alpha-OSK in your Start Menu" message for 6 s instead of vanishing silently. Splash colours match the in-app toast (`#1e3354` background, `#4a8eff` border, `#7ec8ff` title, `#cfe0ff` body) so it visually belongs to Alpha-OSK rather than looking like a stray system dialog.

If the splash path raises (PySide6 import error, no display server), `run_relauncher` logs and falls back to `_run_headless` rather than aborting the relaunch — better to silently relaunch than to leave the user with nothing.

**Headless path preserved.** `_run_headless` is the original blocking-poll implementation, kept intact. Tests target it (so they don't have to stand up a `QApplication`), and it serves as the splash-failure fallback. Production never reaches it on a healthy machine because `--show-splash` is always passed.

**Dismiss button.** The splash has a small ✕ in the top-right corner that *hides* the splash without aborting the relaunch — the user is dismissing the visual, not the work. Polling continues invisibly so the new OSK still launches when ready. A real-world test session left a splash stuck at "Installing files…" for the full `_NEW_EXE_TIMEOUT_S` window because dev mode (see below) had no escape; the dismiss button is the user-facing safety valve.

**Dev-mode short-circuit.** `updater._spawn_relauncher` passes `--target-exe sys.executable` in dev mode (since there's no real install dir to poll). The splash's `_new_exe_ready` check then waits for `python.exe`'s mtime to advance past parent-death, which never happens, so the splash would sit at "Installing files…" until the 180 s timeout. New `_is_dev_target()` helper detects target paths whose basename starts with `python` / `pythonw` and routes those straight to headless. The check is gated only on the target-exe basename, so a real production install (which always points at `alpha-osk.exe`) is unaffected. This was the original cause of the stuck-splash incident — discovered immediately after the initial commit and patched the same session.

### Why not just make the installer non-silent?

The interactive NSIS UI would solve the visibility problem trivially, but at the cost of a UAC prompt + a Next/Next/Next dialog the user has to drive *without a keyboard*. Silent install is the right default for our audience; the fix has to live around it, not replace it.

### Files

- `src/updater.py` — `download_and_install` accepts `on_installer_launching: Optional[HandoffCb]`; `_spawn_relauncher` adds `--show-splash` to the relauncher cmd in both frozen and dev paths.
- `src/keyboard_bridge.py` — `updateInstallHandoffPending = Signal(str)`; `installUpdate` worker passes a callback that emits the signal then sleeps `_PRE_INSTALL_TOAST_DWELL_S` (1.8 s).
- `src/_update_relauncher.py` — `run_relauncher` dispatch, `_run_headless` (legacy + fallback), `_run_with_splash` (Qt path), `_build_splash_widget`, `_new_exe_ready`, `_is_dev_target`.
- `qml/Main.qml` — `updateStartingToast` Popup + the `Connections.onUpdateInstallHandoffPending` handler that flashes it.
- Tests: `tests/test_updater.py::TestInstallerLaunchingCallback` (4), `tests/test_update_relauncher.py::TestNewExeReady` (5), `TestShowSplashFlag` (4 incl. dev-mode skip), `TestIsDevTarget` (4).

### Download progress + splash progress bar (v1.0.19 follow-up)

The T40 work covered the gap *after* the user clicked Install. Two more silent surfaces remained: the download phase before the splash spawns (silent on the in-app side), and the install phase inside the splash (silent on the splash side because NSIS `/S` suppresses its own UI). v1.0.19 fills both.

**Live download progress in the in-app update popup.** A new `KeyboardBridge.updateDownloadProgress(bytes, total)` Signal is emitted from the install worker thread; `installUpdate` passes a throttled callback into `download_and_install(progress=...)`. The throttle is the load-bearing detail: the downloader's 64 KB chunk size would fire ~1300 signals on an 85 MB installer, so the callback coalesces to one emit per 256 KB and always forces the final chunk (so the bar lands at 100 % rather than at 99.4 %). QML wires `onUpdateDownloadProgress` into two new properties on the root (`updateDownloadBytes`, `updateDownloadTotal`) and the existing update popup now shows `Downloading X.X / Y.Y MB (N%)` plus a determinate `ProgressBar`. When the server omits `Content-Length` (`total` is `-1`) the popup falls back to an indeterminate bar and shows only the byte count. `onUpdateInstallStarted` zeros both properties so a retry doesn't briefly show the previous attempt's numbers. **The download URL still never reaches QML** — the bridge only ever emits primitive ints, the URL stays behind `self._update_info`.

**Indeterminate progress bar on the relauncher splash.** `_build_splash_widget` grew a `QProgressBar` under the message label, sized to 10 px tall, range `(0, 0)` (Qt's marquee mode). The splash window's fixed height bumped from 140 to 170 px to fit it without overlapping. A new closure `_settle_progress(full)` is wired into the terminal phases — `_launch`'s success path calls `_settle_progress(full=True)` to pin the bar full on Done, and both failure paths in `_poll_new_exe` and `_launch` call `_settle_progress(full=False)` to empty it. Without that, the bar would still be sliding the instant the splash vanished, which reads as "still working". The stylesheet matches the existing splash palette (`#4a8eff` chunk on `#14233a` track with a `#2a4570` border) so it visually belongs to Alpha-OSK.

**Why a marquee, not real %?** NSIS `/S` suppresses the installer's own UI, so we have no real percentage to report from outside. Reading installer state from the splash would require either parsing NSIS's log output (not stable across NSIS versions and the silent installer doesn't log to a known path anyway) or shelling a sidecar that watches `$INSTDIR\alpha-osk.exe` for mtime + size growth, which is already approximately what `_new_exe_ready` does. The marquee is the same trade-off every commercial installer makes for the silent phase: constant motion signals liveness without lying about progress.

**Files added/changed in v1.0.19:**
- `src/keyboard_bridge.py` — `updateDownloadProgress = Signal(int, int)`; throttled `_on_progress` callback in `installUpdate`'s worker (256 KB cadence + always-final-chunk).
- `src/_update_relauncher.py` — `QProgressBar` import + insert into splash; `_settle_progress(full)` helper wired into all three terminal-phase branches; splash height 140 → 170 px.
- `qml/Main.qml` — `updateDownloadBytes` / `updateDownloadTotal` root properties; `onUpdateDownloadProgress` handler; `Connections.onUpdateInstallStarted` zeros the counters; popup text computes MB/% from the two properties; new `ProgressBar` inside the popup `contentItem`, indeterminate when `updateDownloadTotal <= 0`.
