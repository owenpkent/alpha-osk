# Auto-Update Strategy

## Current State

**Implemented in v1.0.3; updater endpoint corrected in v1.0.5.** Alpha-OSK checks GitHub Releases on startup (3 s after launch) and shows an in-app banner when a newer signed installer is available. Click *Install* and the app downloads the installer, verifies its Authenticode signature against our pinned EV-cert thumbprint, and runs it silently — the NSIS installer kills the running app, runs the previous uninstaller, and installs the new build.

Code lives in `src/updater.py` (network + signature verification), `src/keyboard_bridge.py` (`checkForUpdate` / `installUpdate` / `dismissUpdate` slots, `updateAvailable` / `updateUnavailable` / `updateInstallStarted` / `updateInstallFailed` signals), `qml/Main.qml` (banner + Connections), `qml/components/UnifiedSettingsPanel.qml` (Updates section). Tests in `tests/test_updater.py`.

### Public-vs-private repo split

The source repo (`okstudio1/alpha-osk`) is **private**; release binaries live in a separate **public** repo (`okstudio1/alpha-osk-releases`). The auto-updater queries the public repo's `/releases/latest` endpoint — private repos return 404 to the unauthenticated requests update clients are. v1.0.3 and v1.0.4 shipped with the wrong endpoint hard-coded (the private source repo), so their updater always saw "no update available". v1.0.5 fixes the endpoint; v1.0.3 / v1.0.4 users need one final manual install of v1.0.5 to get on the working updater path.

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
   This is the **public release-binaries** repo; the source repo (`okstudio1/alpha-osk`) is private and would 404 unauthenticated callers.
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
PackageUrl: https://github.com/okstudio1/alpha-osk
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

## Update progress indicator (planned, T40)

### Problem

Reported after a 1.0.15 → 1.0.17 update: the new keyboard does come back (the relauncher fix from this release works), but there's a ~15-60 s gap between the installer's `taskkill /F /IM alpha-osk.exe` and the relauncher launching the new exe. During that gap **no UI is alive at all** (intentional per `_update_relauncher.py`'s docstring: "Failures are deliberately silent. There is no UI surface to report into."). For an accessibility audience that just lost their primary input method, that silence reads as "the update broke the keyboard." The user reported thinking it was broken until it eventually came back.

The on-startup ✓ Updated toast we added in this release fires *after* the gap and helps confirm "yes, that worked", but it can't bridge the silence during the gap itself.

### Where the gap comes from

`_update_relauncher.run_relauncher` has three sequential waits, all silent:
1. `_wait_for_parent_exit` — up to 60 s for the installer's taskkill to land (usually < 1 s).
2. `time.sleep(_INSTALLER_GRACE_S)` — fixed 5 s for the installer to finish file copies.
3. `_wait_for_new_exe` — up to 180 s polling for `$INSTDIR\alpha-osk.exe` mtime to advance past parent-death.

So the floor is ~5 s and the ceiling is ~245 s. Real installs land at ~15-30 s on a healthy machine; AV scanning of the freshly-extracted DLLs can push it higher.

### Proposed fix (two layers, tiered by effort)

**Layer 1: pre-update expectation-setting in the live OSK** (small, ship first)
Before `updater.py::download_and_install` spawns the installer, surface a non-modal toast in the running OSK: *"Installing v1.0.X. The keyboard will reappear in about 30 seconds."* The toast is visible for the few seconds until the installer's taskkill arrives. This alone resolves most of the "I thought it was broken" framing because the user knows the silence is expected. Implementation is a sibling to `updateAppliedToast` in `Main.qml` (call it `updateStartingToast`); fire it from a new `KeyboardBridge.notifyUpdateStarting(version)` slot called by `updater.py` immediately before the installer Popen.

**Layer 2: visible relauncher splash during the gap** (larger, defer if Layer 1 is enough)
Have `_update_relauncher` show a small always-on-top window for the duration of the wait. Three implementation options:

- **A. PySide6 QSplashScreen**. The relauncher already runs from the same `alpha-osk.exe` that has Qt bundled, so no new dependencies. QSplashScreen is the canonical use case. Cost: ~1-2 s extra startup latency for `QApplication.__init__` in the relauncher process. Refactor the polling loops to fire from `QTimer.singleShot` so the splash gets a real event loop.
- **B. ctypes Win32 native window**. Zero dependencies, no event-loop overhead. Cost: ~150 lines of CreateWindowExW + DefWindowProcW glue from scratch, easy to get wrong on HiDPI / dark-mode.
- **C. Windows toast notification**. Fire-and-forget via PowerShell / BurntToast — appears in Action Center, no continuous status. Cheapest, but doesn't bridge the silence; just punctuates it.

Recommendation: A, since Qt is already loaded, the visual matches the rest of the app, and the splash window can show a real progress message that updates as the relauncher transitions through its three phases ("Waiting for installer to finish…" → "Installing files…" → "Launching new keyboard…").

### Acceptance criteria

- A user updating from 1.0.17 to the next version sees a toast *before* the keyboard disappears that explicitly tells them the keyboard is about to be unavailable for ~30 s.
- (Layer 2) During the gap, a small "Updating Alpha-OSK…" window is visible on screen with a progress message reflecting the current relauncher phase.
- The post-update ✓ Updated toast still fires after relaunch (no regression).
- If the relauncher fails (Popen raises, timeout), the splash from Layer 2 surfaces an error message + a path to the install log instead of vanishing silently.

### Why not just make the installer non-silent?

The interactive NSIS UI would solve the visibility problem trivially, but at the cost of a UAC prompt + a Next/Next/Next dialog the user has to drive *without a keyboard*. Silent install is the right default for our audience; the fix has to live around it, not replace it.
