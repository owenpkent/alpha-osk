"""
Alpha-OSK Code Signing Script
==============================

Signs Windows executables with the OK Studio Inc. EV code signing certificate.
Implements retry logic to handle Windows Defender file locks (same pattern as
gitconnect's ``build/sign.js``).

Usage::

    python build/windows/sign.py path/to/file.exe
    python build/windows/sign.py path/to/file.exe --verify

The script:

1. Locates ``signtool.exe`` across common Windows SDK paths.
2. Signs the file with SHA-256 + RFC 3161 timestamp.
3. Retries up to 5 times with exponential backoff (handles Defender locks).
4. Exits non-zero on failure so the build pipeline fails loudly.

Certificate Details
-------------------
| Field            | Value                                              |
|------------------|----------------------------------------------------|
| Issued to        | OK Studio Inc.                                     |
| Type             | EV (Extended Validation) — immediate SmartScreen    |
| Thumbprint       | ``fc22b5221318f3f3f6b3eb2d969d7f99091557bf``       |
| Timestamp server | ``http://timestamp.digicert.com``                  |

Prerequisites
-------------
- SafeNet Authentication Client installed (comes with the USB eToken).
- USB eToken physically plugged in.
- Certificate visible in Windows cert store::

      certutil -store -user My
      # Look for "OK Studio Inc." with the thumbprint above.

- Run from a **normal** (non-elevated) PowerShell — the eToken is not
  visible to elevated (admin) processes.

See Also
--------
- ``gitconnect/windows-desktop/build/sign.js`` — The original JS version
  this script is modeled after.
- ``docs/WINDOWS.md`` — Full Windows build and deployment guide.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
#  Configuration — matches gitconnect's EV certificate
# ---------------------------------------------------------------------------

# SHA-1 thumbprint of the OK Studio Inc. EV code signing certificate.
# Using thumbprint (not subject name) avoids ambiguity if multiple certs exist.
CERTIFICATE_SHA1 = "fc22b5221318f3f3f6b3eb2d969d7f99091557bf"

# RFC 3161 timestamp server. DigiCert is fast and reliable.
# Fallback: http://timestamp.sectigo.com
TIMESTAMP_SERVER = "http://timestamp.digicert.com"

# Maximum signing attempts before giving up.
MAX_RETRIES = 5

# Base delay between retries in seconds (multiplied by attempt number).
RETRY_BASE_DELAY = 2.0


# ---------------------------------------------------------------------------
#  Locate signtool.exe
# ---------------------------------------------------------------------------

def find_signtool() -> str:
    """
    Find ``signtool.exe`` on the system.

    Checks these locations in order:

    1. Common Windows SDK paths under ``Program Files (x86)\\Windows Kits``.
    2. ``signtool.exe`` on ``$PATH``.
    3. App Certification Kit path.

    Returns:
        Absolute path to ``signtool.exe``.

    Raises:
        FileNotFoundError: If signtool cannot be found anywhere.
    """
    program_files_x86 = os.environ.get(
        "ProgramFiles(x86)", r"C:\Program Files (x86)"
    )
    kits_base = Path(program_files_x86) / "Windows Kits" / "10" / "bin"

    candidates: list[Path] = []

    # Enumerate all SDK versions (e.g. 10.0.22621.0, 10.0.19041.0)
    if kits_base.exists():
        for sdk_dir in sorted(kits_base.iterdir(), reverse=True):
            x64 = sdk_dir / "x64" / "signtool.exe"
            if x64.exists():
                candidates.append(x64)

    # App Certification Kit
    ack = (
        Path(program_files_x86)
        / "Windows Kits" / "10" / "App Certification Kit" / "signtool.exe"
    )
    if ack.exists():
        candidates.append(ack)

    # Check PATH
    import shutil
    on_path = shutil.which("signtool.exe") or shutil.which("signtool")
    if on_path:
        candidates.append(Path(on_path))

    if not candidates:
        raise FileNotFoundError(
            "signtool.exe not found. Install the Windows SDK:\n"
            "  https://developer.microsoft.com/en-us/windows/downloads/windows-sdk/\n"
            "Or add signtool.exe to your PATH."
        )

    chosen = candidates[0]
    print(f"[Sign] Using signtool: {chosen}")
    return str(chosen)


# ---------------------------------------------------------------------------
#  Sign a single file
# ---------------------------------------------------------------------------

def sign_file(file_path: str, signtool: str | None = None) -> None:
    """
    Sign a single file with the EV certificate.

    Retries up to ``MAX_RETRIES`` times with exponential backoff to handle
    Windows Defender temporarily locking the file during scanning.

    Args:
        file_path: Absolute path to the ``.exe`` or ``.dll`` to sign.
        signtool: Path to ``signtool.exe``. Auto-detected if ``None``.

    Raises:
        RuntimeError: If signing fails after all retry attempts.
    """
    if signtool is None:
        signtool = find_signtool()

    file_name = Path(file_path).name

    cmd = [
        signtool, "sign",
        "/sha1", CERTIFICATE_SHA1,
        "/fd", "sha256",
        "/tr", TIMESTAMP_SERVER,
        "/td", "sha256",
        file_path,
    ]

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(f"[Sign] Attempt {attempt}/{MAX_RETRIES}: {file_name}")
            subprocess.run(
                cmd,
                check=True,
                timeout=60,
                capture_output=False,  # Let signtool output flow through
            )
            print(f"[Sign] Success: {file_name}")
            return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * attempt
                print(
                    f"[Sign] Retry in {delay:.0f}s "
                    f"(file may be locked by antivirus)..."
                )
                time.sleep(delay)
            else:
                raise RuntimeError(
                    f"Failed to sign {file_path} after {MAX_RETRIES} attempts: {e}"
                ) from e


# ---------------------------------------------------------------------------
#  Verify a signature
# ---------------------------------------------------------------------------

def verify_file(file_path: str, signtool: str | None = None) -> bool:
    """
    Verify the Authenticode signature on a file.

    Args:
        file_path: Path to the signed file.
        signtool: Path to ``signtool.exe``. Auto-detected if ``None``.

    Returns:
        True if the signature is valid.
    """
    if signtool is None:
        signtool = find_signtool()

    cmd = [signtool, "verify", "/pa", "/v", file_path]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        print(f"[Sign] Verified: {Path(file_path).name} — signature valid")
        return True
    else:
        print(f"[Sign] FAILED to verify: {Path(file_path).name}")
        print(result.stdout)
        print(result.stderr)
        return False


# ---------------------------------------------------------------------------
#  Sign all executables in a directory
# ---------------------------------------------------------------------------

def sign_directory(dir_path: str, signtool: str | None = None,
                   exe_only: bool = True) -> int:
    """
    Sign executables in a directory tree.

    Args:
        dir_path: Root directory to scan.
        signtool: Path to ``signtool.exe``. Auto-detected if ``None``.
        exe_only: If True, only sign ``.exe`` files (default). If False,
                  also sign ``.dll`` files.

    Returns:
        Number of files signed.
    """
    if signtool is None:
        signtool = find_signtool()

    signed = 0
    root = Path(dir_path)

    exts = ["*.exe"] if exe_only else ["*.exe", "*.dll"]
    for ext in exts:
        for file in root.rglob(ext):
            sign_file(str(file), signtool)
            signed += 1

    print(f"[Sign] Signed {signed} files in {dir_path}")
    return signed


# ---------------------------------------------------------------------------
#  CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    """
    CLI interface.

    Usage::

        python build/windows/sign.py file.exe           # Sign a single file
        python build/windows/sign.py file.exe --verify   # Verify a signature
        python build/windows/sign.py dist/alpha-osk/     # Sign all .exe/.dll in dir
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Sign Windows executables with OK Studio Inc. EV certificate"
    )
    parser.add_argument(
        "path",
        help="File or directory to sign",
    )
    parser.add_argument(
        "--verify", "-v",
        action="store_true",
        help="Verify signature instead of signing",
    )
    args = parser.parse_args()

    target = Path(args.path)

    if not target.exists():
        print(f"[Sign] ERROR: {target} does not exist")
        return 1

    try:
        signtool = find_signtool()
    except FileNotFoundError as e:
        print(f"[Sign] ERROR: {e}")
        return 1

    if args.verify:
        ok = verify_file(str(target), signtool)
        return 0 if ok else 1

    if target.is_dir():
        sign_directory(str(target), signtool)
    else:
        sign_file(str(target), signtool)

    return 0


if __name__ == "__main__":
    sys.exit(main())
