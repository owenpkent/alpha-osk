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
6. The NSIS installer kills the running instance, uninstalls the old version, installs the new one, and optionally relaunches.

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
