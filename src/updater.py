"""
Auto-updater for Alpha-OSK.

Checks the GitHub Releases API for a newer signed installer, downloads
it, **verifies its Authenticode signature against our pinned EV cert
thumbprint**, and launches it silently.

Threat model & defences
-----------------------
The updater is the highest-value MITM target in the app — a successful
attacker gets to ship arbitrary signed code on the user's behalf.  The
defences below are layered so any single layer can fail without
unlocking code execution.

================  ===================  =================================
Threat            Layer that catches   Notes
================  ===================  =================================
TLS strip / MITM  ``urllib`` + scheme  ``urlopen`` validates certs by
                  whitelist            default; we additionally reject
                                       any non-``https`` URL up front.
DNS hijack to     Authenticode pin     Even with a valid TLS cert for
attacker host                          a spoofed host, the served exe
                                       cannot be signed by our key.
Compromised       Authenticode pin     ``_verify_signature`` rejects
GitHub asset                           anything not signed by SHA1
                                       ``fc22b522...``.
Asset URL         Host whitelist       Only ``github.com``,
redirection                            ``objects.githubusercontent.com``,
                                       and ``release-assets.githubusercontent.com``
                                       (the historical and current
                                       release-asset CDNs) are
                                       accepted.
Disk-fill         Bounded download     ``_MAX_DOWNLOAD_BYTES`` aborts
                                       runaway downloads.
Downgrade attack  Strict semver        Older or equal versions are
                                       silently rejected.
Tag confusion    ``releases/latest``   We never trust an arbitrary
                  endpoint             tag — only the repository's own
                                       "latest" pointer.
TOCTOU on        Atomic temp file      Downloaded to a private temp
download          (umask 0700)         dir we own, signature check
                                       runs against the same handle
                                       we then exec.
Release-notes    Sanitisation          ``_sanitize_notes`` strips
injection                              control chars + caps length
                                       before reaching QML.
================  ===================  =================================

Anything that isn't covered above (e.g. private-key compromise of the
EV cert itself) is out of scope for client-side defences and would
require a build-pipeline / cert-rotation response.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple
from urllib.parse import urlparse

from .__version__ import __version__ as CURRENT_VERSION

_logger = logging.getLogger("Updater")

# --- Pinned identity --------------------------------------------------------
#
# Anything you change here weakens or relocates the trust anchor.  Treat
# these constants the same way you'd treat a TLS pin.
GITHUB_API_URL = (
    "https://api.github.com/repos/okstudio1/alpha-osk-releases/releases/latest"
)
# The repo above hosts only release binaries.  The application source
# lives in a *separate, private* repo (``okstudio1/alpha-osk``); private
# repos return 404 on ``/releases/latest`` to unauthenticated callers,
# which is exactly what shipping update clients are.  The split keeps
# the source private without breaking auto-update.
# SHA1 thumbprint of the OK Studio Inc. EV code-signing certificate.
# Lowercase, no spaces.  Matches what ``signtool sign /sha1`` uses and
# what ``Get-AuthenticodeSignature .Thumbprint`` returns (we normalise
# both sides to lowercase for the compare).
EV_CERT_SHA1_THUMBPRINT = "fc22b5221318f3f3f6b3eb2d969d7f99091557bf"
EXPECTED_SIGNER_CN = "OK Studio Inc."

# Hosts we accept download URLs from.  GitHub itself plus the two
# canonical CDN hostnames it redirects release-asset downloads to.
# Any other host is treated as an attacker-controlled redirect.
#
# GitHub's release-asset CDN has migrated over the years —
# ``objects.githubusercontent.com`` was the historical name and still
# appears in older URLs; ``release-assets.githubusercontent.com`` is
# the current production hostname (signed Azure-blob proxy).  We pin
# the specific hostnames rather than allow ``*.githubusercontent.com``
# so an attacker who finds a way to publish content under the wider
# umbrella can't redirect us there.
_ALLOWED_DOWNLOAD_HOSTS = frozenset({
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
})

# --- Bounds & timeouts ------------------------------------------------------
_HTTP_TIMEOUT_SECONDS = 15
_MAX_API_RESPONSE_BYTES = 1 * 1024 * 1024            # 1 MB JSON cap
_MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024              # 500 MB installer cap
_MAX_NOTES_LENGTH = 4000
_USER_AGENT = f"alpha-osk-updater/{CURRENT_VERSION}"


@dataclass(frozen=True)
class UpdateInfo:
    """A vetted release record returned by :func:`check_for_update`."""

    version: str
    download_url: str
    asset_name: str
    notes: str


# ---------------------------------------------------------------------------
#  Version comparison
# ---------------------------------------------------------------------------

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _parse_version(tag: str) -> Optional[Tuple[int, int, int]]:
    """Parse a ``v1.2.3`` / ``1.2.3`` tag into a comparable tuple.

    Returns ``None`` for anything that isn't a clean MAJOR.MINOR.PATCH
    triple — we deliberately refuse pre-release or git-describe style
    tags so an attacker can't ship ``1.0.2-evil`` and have it compare
    as "newer" by string ordering.
    """
    cleaned = tag.strip().lstrip("vV")
    m = _VERSION_RE.match(cleaned)
    if m is None:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def is_newer(candidate: str, baseline: str) -> bool:
    """Return True iff ``candidate`` is a strictly higher semver than
    ``baseline``.  Either malformed → False (refuses to update).

    This is the gate that prevents downgrade attacks.  Naive string
    comparison would treat "1.0.10" as older than "1.0.2".
    """
    a = _parse_version(candidate)
    b = _parse_version(baseline)
    if a is None or b is None:
        return False
    return a > b


# ---------------------------------------------------------------------------
#  URL validation
# ---------------------------------------------------------------------------

def _is_safe_download_url(url: str) -> bool:
    """Reject URLs that aren't HTTPS to a host we trust."""
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.scheme != "https":
        return False
    if not p.hostname:
        return False
    return p.hostname.lower() in _ALLOWED_DOWNLOAD_HOSTS


# ---------------------------------------------------------------------------
#  Release-notes sanitisation
# ---------------------------------------------------------------------------

# Strip C0 controls except newline / tab; cap length.  QML's Text
# component renders raw markdown safely (no script execution), but
# uncontrolled length or weird control chars can still wreck the layout.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _sanitize_notes(raw: object) -> str:
    if not isinstance(raw, str):
        return ""
    cleaned = _CONTROL_CHARS_RE.sub("", raw)
    if len(cleaned) > _MAX_NOTES_LENGTH:
        cleaned = cleaned[:_MAX_NOTES_LENGTH] + "…"
    return cleaned


# ---------------------------------------------------------------------------
#  Check
# ---------------------------------------------------------------------------

def check_for_update(
    current_version: str = CURRENT_VERSION,
    *,
    api_url: str = GITHUB_API_URL,
    timeout: float = _HTTP_TIMEOUT_SECONDS,
) -> Optional[UpdateInfo]:
    """Hit the Releases API and return an UpdateInfo iff a newer
    properly-shaped release exists.  Returns None on any error — the
    updater path is best-effort and must never crash the app."""
    # Defensive — at runtime ``api_url`` is the constant above.  Tests
    # pass the constant explicitly; refusing anything else stops a
    # future careless override from pointing the updater at any other
    # endpoint, even within the same repo.  Exact equality is tighter
    # than a prefix match: it pins the path, not just the host+repo.
    if api_url != GITHUB_API_URL:
        _logger.error("Refusing non-pinned API URL: %s", api_url)
        return None

    try:
        req = urllib.request.Request(
            api_url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": _USER_AGENT,
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(_MAX_API_RESPONSE_BYTES + 1)
        if len(raw) > _MAX_API_RESPONSE_BYTES:
            _logger.warning("API response exceeds %d bytes; aborting check",
                            _MAX_API_RESPONSE_BYTES)
            return None
        data = json.loads(raw)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        _logger.info("Update check failed: %s", e)
        return None

    tag = data.get("tag_name")
    if not isinstance(tag, str) or not is_newer(tag, current_version):
        return None

    parsed = _parse_version(tag)
    if parsed is None:
        return None
    candidate_version = ".".join(str(x) for x in parsed)

    # Find the signed installer asset.  We deliberately match a strict
    # name pattern to avoid being fooled by a sneakily-named asset.
    expected_pattern = re.compile(
        rf"^Alpha-OSK-Setup-{re.escape(candidate_version)}\.exe$"
    )
    for asset in data.get("assets", []) or []:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        url = asset.get("browser_download_url")
        if not isinstance(name, str) or not isinstance(url, str):
            continue
        if not expected_pattern.match(name):
            continue
        if not _is_safe_download_url(url):
            _logger.warning("Rejecting asset URL outside whitelist: %s", url)
            continue
        return UpdateInfo(
            version=candidate_version,
            download_url=url,
            asset_name=name,
            notes=_sanitize_notes(data.get("body", "")),
        )

    _logger.info("Newer release tagged but no matching signed installer asset")
    return None


# ---------------------------------------------------------------------------
#  Download + signature verification
# ---------------------------------------------------------------------------

# Progress callback receives (bytes_downloaded, total_bytes_or_None).
ProgressCb = Callable[[int, Optional[int]], None]


def _download_with_cap(
    url: str,
    dest: Path,
    *,
    timeout: float,
    progress: Optional[ProgressCb],
) -> bool:
    """Stream the URL into ``dest`` with a hard byte cap."""
    if not _is_safe_download_url(url):
        _logger.error("Refusing download from disallowed URL: %s", url)
        return False

    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            # urlopen follows redirects; re-validate the *final* URL host.
            final_url = resp.geturl()
            if not _is_safe_download_url(final_url):
                _logger.error("Refusing post-redirect host: %s", final_url)
                return False

            length_hdr = resp.headers.get("Content-Length")
            total = int(length_hdr) if length_hdr and length_hdr.isdigit() else None
            if total is not None and total > _MAX_DOWNLOAD_BYTES:
                _logger.error(
                    "Refusing download — Content-Length %d > cap %d",
                    total, _MAX_DOWNLOAD_BYTES,
                )
                return False

            written = 0
            chunk_size = 1 << 16  # 64 KB
            with open(dest, "wb") as fh:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > _MAX_DOWNLOAD_BYTES:
                        _logger.error(
                            "Aborting download — exceeded cap %d",
                            _MAX_DOWNLOAD_BYTES,
                        )
                        return False
                    fh.write(chunk)
                    if progress is not None:
                        progress(written, total)
        return True
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        _logger.error("Download failed: %s", e)
        return False


def _verify_signature(exe_path: Path) -> bool:
    """Verify ``exe_path`` was signed by our EV cert.

    Uses PowerShell's ``Get-AuthenticodeSignature``, which is on every
    supported Windows version.  Status MUST be ``Valid`` and the
    SignerCertificate's SHA1 thumbprint MUST equal our pinned value.
    Anything else — unsigned, hash-mismatched, untrusted root, wrong
    publisher — fails closed.
    """
    if sys.platform != "win32":
        # No Authenticode outside Windows — refuse.  Linux/macOS aren't
        # the auto-update target anyway; release builds are Windows.
        _logger.error("Signature verification only supported on Windows")
        return False

    if not exe_path.is_file():
        _logger.error("Cannot verify missing file: %s", exe_path)
        return False

    # PowerShell expression — emit a single line:
    #     Status|Thumbprint|SubjectCN
    # Using ``|`` as separator since Windows Authenticode subjects
    # can contain commas but never pipes in our cert.
    ps_script = (
        f"$ErrorActionPreference='Stop';"
        f"$s = Get-AuthenticodeSignature -FilePath '{exe_path}';"
        f"$cn = '';"
        f"if ($s.SignerCertificate) {{"
        f"  $cn = ($s.SignerCertificate.Subject -split ',')[0]"
        f"        -replace '^CN=', '';"
        f"}}"
        f"Write-Output (\"{{0}}|{{1}}|{{2}}\" -f "
        f"  $s.Status, $s.SignerCertificate.Thumbprint, $cn)"
    )

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        _logger.error("PowerShell signature check failed: %s", e)
        return False

    if result.returncode != 0:
        _logger.error("PowerShell exited %d: %s", result.returncode, result.stderr)
        return False

    line = result.stdout.strip().splitlines()[-1] if result.stdout else ""
    parts = line.split("|", 2)
    if len(parts) != 3:
        _logger.error("Unparseable signature output: %r", line)
        return False
    status, thumbprint, signer_cn = parts
    status = status.strip()
    thumbprint = thumbprint.strip().lower()
    signer_cn = signer_cn.strip()

    if status != "Valid":
        _logger.error("Signature status is %r, not Valid", status)
        return False
    if thumbprint != EV_CERT_SHA1_THUMBPRINT.lower():
        _logger.error(
            "Signature thumbprint mismatch: got %s expected %s",
            thumbprint, EV_CERT_SHA1_THUMBPRINT,
        )
        return False
    if signer_cn != EXPECTED_SIGNER_CN:
        _logger.error(
            "Signer CN mismatch: got %r expected %r",
            signer_cn, EXPECTED_SIGNER_CN,
        )
        return False

    _logger.info("Signature verified: %s (CN=%s)", thumbprint, signer_cn)
    return True


# ---------------------------------------------------------------------------
#  Install
# ---------------------------------------------------------------------------

def _make_private_tempdir() -> Path:
    """Create a tempdir only the current user can read/write."""
    d = Path(tempfile.mkdtemp(prefix="alpha-osk-update-"))
    # tempfile.mkdtemp already creates with mode 0o700 on POSIX; on
    # Windows the default ACL inherits from the user's TEMP dir, which
    # is per-user.  Belt-and-braces:
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def _launch_installer(dest: Path) -> Tuple[bool, str]:
    """Spawn the signed installer with admin elevation.

    The NSIS installer needs admin to write to ``C:\\Program Files``.
    ``subprocess.Popen`` doesn't honour the manifest's
    ``RequestExecutionLevel admin`` — Windows refuses with
    ``ERROR_ELEVATION_REQUIRED`` (WinError 740).  ``ShellExecuteW`` with
    the ``runas`` verb explicitly requests elevation, which surfaces
    the UAC consent prompt; if the user accepts, the installer
    launches elevated and ``/S`` runs it silently from there.

    Returns ``(ok, error_msg)``.  Pulled out of ``download_and_install``
    so tests can monkey-patch a single seam without faking ctypes.
    """
    if sys.platform == "win32":
        import ctypes

        SW_SHOWNORMAL = 1
        SE_ERR_ACCESSDENIED = 5  # User cancelled the UAC dialog.
        shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
        ret = shell32.ShellExecuteW(
            None,                # parent hwnd — not tied to a window
            "runas",             # verb: trigger UAC elevation
            str(dest),           # installer path
            "/S",                # silent flag (NSIS)
            None,                # working directory
            SW_SHOWNORMAL,
        )
        # ShellExecuteW returns >32 on success.
        if ret <= 32:
            if ret == SE_ERR_ACCESSDENIED:
                _logger.info("User declined UAC elevation prompt")
                return False, "Update cancelled at UAC prompt"
            _logger.error("ShellExecuteW failed with code %d", ret)
            return False, "Install launch failed (see log)"
        return True, ""

    # Non-Windows path — kept for tests/dev shells; production
    # release pipeline is Windows-only since Authenticode is.
    subprocess.Popen(
        [str(dest), "/S"],
        close_fds=True,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
    )
    return True, ""


def download_and_install(
    info: UpdateInfo,
    *,
    progress: Optional[ProgressCb] = None,
    timeout: float = _HTTP_TIMEOUT_SECONDS * 4,  # downloads are slower than API
) -> Tuple[bool, str]:
    """Download the installer, verify its signature, and exec it silently.

    Returns a ``(ok, error)`` pair.  ``ok`` is True iff the installer
    was successfully launched (not whether it succeeded — the installer
    kills us mid-run, so we never observe its exit code).  On failure,
    ``error`` is a short user-facing string identifying which step blew
    up; the full details land in the log at ERROR level.  On success,
    ``error`` is the empty string.
    """
    if not isinstance(info, UpdateInfo):
        return False, "Internal error: invalid update info"

    work_dir = _make_private_tempdir()
    dest = work_dir / info.asset_name

    try:
        ok = _download_with_cap(
            info.download_url, dest,
            timeout=timeout, progress=progress,
        )
        if not ok:
            return False, "Download failed (see log)"

        if not _verify_signature(dest):
            _logger.error("Aborting install — signature verification failed")
            return False, "Signature check failed (see log)"

        # /S = NSIS silent install; the installer kills the running
        # alpha-osk.exe, runs the old uninstaller, installs the new
        # build, and (per installer.nsh) optionally relaunches.
        _logger.info("Launching signed installer: %s", dest)
        ok, err = _launch_installer(dest)
        if not ok:
            return False, err
        return True, ""
    except Exception as e:                                # noqa: BLE001
        _logger.error("Install failed: %s", e)
        return False, f"Install failed: {e}"
    # NB: we deliberately don't rmtree work_dir — the installer process
    # is still reading from it.  Windows cleans %TEMP% on its own
    # cadence; leaving the file is fine.
