"""
Alpha-OSK macOS Build Script
=============================

End-to-end build pipeline for the macOS release:

1. Check prerequisites (Python, PyInstaller).
2. Build the standalone bundle with PyInstaller (``alpha-osk.spec``).
3. Capture a pip-freeze lockfile and CycloneDX SBOM (same format as
   the Linux / Windows pipelines).
4. Optionally wrap the ``.app`` into a ``.dmg`` for distribution
   (``--dmg``).

Usage
-----
::

    # PyInstaller .app only (always runs)
    python build/macos/build.py

    # Plus a .dmg disk image
    python build/macos/build.py --dmg

    # Skip the build step, repackage an existing dist/
    python build/macos/build.py --skip-build --dmg

Prerequisites
-------------
- Python 3.9+ with ``pyinstaller`` installed.
- For ``--dmg``: ``hdiutil`` (preinstalled on every macOS).
- Code signing & notarization are NOT handled here yet; an unsigned
  ``.app`` opens via right-click → Open.  Wire in
  ``codesign_identity`` on the ``BUNDLE()`` call in ``alpha-osk.spec``
  + a ``notarytool submit`` step once a Developer ID cert is in
  hand.  See docs/MACOS.md (TODO) when that lands.

Output
------
- ``dist/alpha-osk/``                              — raw bundle
- ``dist/Alpha-OSK.app/``                          — macOS app
- ``release/Alpha-OSK-{version}.dmg``              — (if --dmg)
- ``release/Alpha-OSK-{version}-macos-requirements.lock.txt``
- ``release/Alpha-OSK-{version}-macos-sbom.cyclonedx.json``

See Also
--------
- ``build/macos/alpha-osk.spec`` — PyInstaller build specification.
- ``build/linux/build.py``       — sibling pipeline (Linux).
- ``build/windows/build.py``     — sibling pipeline (Windows).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).parent.resolve()        # build/macos/
PROJECT_ROOT = SCRIPT_DIR.parent.parent              # repo root
SPEC_FILE = SCRIPT_DIR / "alpha-osk.spec"
DIST_DIR = PROJECT_ROOT / "dist" / "alpha-osk"
APP_DIR = PROJECT_ROOT / "dist" / "Alpha-OSK.app"
RELEASE_DIR = PROJECT_ROOT / "release"


class Colors:
    HEADER = "\033[95m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    END = "\033[0m"
    BOLD = "\033[1m"


def header(msg: str) -> None:
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'=' * 60}{Colors.END}")
    print(f"{Colors.HEADER}{Colors.BOLD}{msg.center(60)}{Colors.END}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'=' * 60}{Colors.END}\n")


def step(msg: str) -> None:
    print(f"{Colors.CYAN}> {msg}{Colors.END}")


def ok(msg: str) -> None:
    print(f"{Colors.GREEN}  OK: {msg}{Colors.END}")


def warn(msg: str) -> None:
    print(f"{Colors.YELLOW}  WARN: {msg}{Colors.END}")


def fail(msg: str) -> None:
    print(f"{Colors.RED}  FAIL: {msg}{Colors.END}")


def read_version() -> str:
    ns: dict = {}
    exec((PROJECT_ROOT / "src" / "__version__.py").read_text(), ns)
    return ns["__version__"]


def check_pyinstaller() -> bool:
    step("Checking PyInstaller")
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        fail("PyInstaller not installed. Run: pip install pyinstaller")
        return False
    ok("PyInstaller available")
    return True


def run_pyinstaller() -> bool:
    header("Building PyInstaller bundle")
    if DIST_DIR.exists():
        step(f"Removing previous bundle at {DIST_DIR}")
        shutil.rmtree(DIST_DIR)
    if APP_DIR.exists():
        step(f"Removing previous .app at {APP_DIR}")
        shutil.rmtree(APP_DIR)

    build_cache = PROJECT_ROOT / "build-cache"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--distpath", str(PROJECT_ROOT / "dist"),
        "--workpath", str(build_cache),
        str(SPEC_FILE),
    ]
    step("Running: " + " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        fail("PyInstaller build failed")
        return False
    if not (DIST_DIR / "alpha-osk").exists():
        fail(f"Expected binary missing: {DIST_DIR / 'alpha-osk'}")
        return False
    if not APP_DIR.exists():
        fail(f"Expected .app missing: {APP_DIR}")
        return False
    ok(f"Bundle: {DIST_DIR}")
    ok(f".app:   {APP_DIR}")
    return True


def freeze_lockfile(version: str) -> Path | None:
    """Capture pip freeze as a release-time lockfile (macOS).

    Mirror of the Linux build's ``freeze_lockfile`` — see that file
    for the full rationale.  Reproducible install of the same Python
    deps the bundle was built against.
    """
    header("Capturing Python Dependency Lockfile")
    RELEASE_DIR.mkdir(exist_ok=True)
    lockfile = RELEASE_DIR / f"Alpha-OSK-{version}-macos-requirements.lock.txt"

    step("Running pip freeze")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "freeze", "--all"],
            capture_output=True, text=True, timeout=60, check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        fail(f"pip freeze failed: {exc}")
        return None

    body = (
        f"# Alpha-OSK {version} (macOS) -- Python dependency lockfile\n"
        f"# Generated by build/macos/build.py via `pip freeze --all`.\n"
        f"# Reproduce the exact build venv: `pip install -r <this file>`.\n"
        f"# This is a build-reproducibility record, NOT a CycloneDX/SPDX SBOM.\n"
        f"\n"
        + result.stdout
    )
    lockfile.write_text(body, encoding="utf-8")

    package_count = sum(
        1 for line in result.stdout.splitlines()
        if line.strip() and not line.startswith("#")
    )
    ok(f"Lockfile written: {lockfile.name} ({package_count} packages)")
    return lockfile


def emit_sbom(version: str) -> Path | None:
    """Generate a CycloneDX 1.6 SBOM of the build venv (macOS)."""
    header("Generating CycloneDX SBOM")
    RELEASE_DIR.mkdir(exist_ok=True)
    sbom = RELEASE_DIR / f"Alpha-OSK-{version}-macos-sbom.cyclonedx.json"

    step("Running cyclonedx-py environment")
    try:
        subprocess.run(
            [
                sys.executable, "-m", "cyclonedx_py", "environment",
                "--of", "JSON",
                "--sv", "1.6",
                "--output-reproducible",
                "-o", str(sbom),
            ],
            check=True, timeout=120,
        )
    except FileNotFoundError:
        warn(
            "cyclonedx-bom not installed -- SBOM skipped.\n"
            "  Install with: pip install -r requirements-dev.txt"
        )
        return None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        warn(f"SBOM generation failed: {exc}")
        return None

    if not sbom.exists():
        warn(f"SBOM file not created: {sbom}")
        return None

    size_kb = sbom.stat().st_size / 1024
    ok(f"SBOM written: {sbom.name} ({size_kb:.1f} KB, CycloneDX 1.6)")
    return sbom


def build_dmg(version: str) -> bool:
    """Wrap ``Alpha-OSK.app`` into a distributable ``.dmg``.

    Uses ``hdiutil create`` with a /Applications symlink layout so the
    user can drag the .app into Applications on first mount.  No
    backgrounds / custom icons / window-position trickery for now —
    that's a polish pass once the bundle launches cleanly.

    Unsigned .dmgs trip Gatekeeper on first open; ``codesign`` +
    ``notarytool`` integration belongs in a follow-up alongside the
    BUNDLE() codesign_identity arg.
    """
    header(f"Packaging .dmg for Alpha-OSK {version}")
    if not shutil.which("hdiutil"):
        fail("hdiutil not found — required for .dmg packaging")
        return False
    if not APP_DIR.exists():
        fail(f".app missing: {APP_DIR}")
        return False

    staging = PROJECT_ROOT / "dist" / "dmg-staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    step("Copying Alpha-OSK.app into DMG staging dir")
    shutil.copytree(APP_DIR, staging / "Alpha-OSK.app", symlinks=True)
    # /Applications shortcut so the user can drag-install.
    (staging / "Applications").symlink_to("/Applications")

    RELEASE_DIR.mkdir(exist_ok=True)
    out_path = RELEASE_DIR / f"Alpha-OSK-{version}.dmg"
    if out_path.exists():
        out_path.unlink()

    cmd = [
        "hdiutil", "create",
        "-volname", f"Alpha-OSK {version}",
        "-srcfolder", str(staging),
        "-ov",                  # overwrite if exists (we already unlinked)
        "-format", "UDZO",      # compressed read-only image
        str(out_path),
    ]
    step("Running: " + " ".join(cmd))
    result = subprocess.run(cmd)
    if result.returncode != 0:
        fail("hdiutil failed")
        return False
    ok(f".dmg created: {out_path}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Alpha-OSK for macOS")
    parser.add_argument(
        "--skip-build", action="store_true",
        help="Skip PyInstaller, reuse existing dist/Alpha-OSK.app/",
    )
    parser.add_argument(
        "--dmg", action="store_true",
        help="Wrap the .app into a .dmg disk image",
    )
    args = parser.parse_args()

    header(f"Alpha-OSK macOS Build v{read_version()}")

    if sys.platform != "darwin":
        fail(
            f"build/macos/build.py must run on macOS (current: {sys.platform}). "
            "macOS .app bundles can only be created on macOS hosts because "
            "PyInstaller links against the local Cocoa frameworks."
        )
        return 1

    if not args.skip_build:
        if not check_pyinstaller():
            return 1
        if not run_pyinstaller():
            return 1
    else:
        if not APP_DIR.exists():
            fail(f"--skip-build passed but {APP_DIR} missing")
            return 1
        warn("Skipping PyInstaller — reusing existing .app")

    version = read_version()

    lockfile_path = freeze_lockfile(version)
    if lockfile_path is None:
        warn("Lockfile generation failed -- continuing without it")

    sbom_path = emit_sbom(version)
    if sbom_path is None:
        warn("SBOM generation failed -- continuing without it")

    if args.dmg:
        if not build_dmg(version):
            return 1

    header("Build complete")
    print(f"  Bundle:  {DIST_DIR}")
    print(f"  .app:    {APP_DIR}")
    if args.dmg:
        print(f"  Release: {RELEASE_DIR}/")
    if lockfile_path and lockfile_path.exists():
        print(f"  Lockfile: {lockfile_path}")
    if sbom_path and sbom_path.exists():
        print(f"  SBOM:     {sbom_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
