"""
Alpha-OSK Linux Build Script
=============================

End-to-end build pipeline for the Linux release:

1. Check prerequisites (Python, PyInstaller, optional packagers).
2. Build the standalone bundle with PyInstaller (``alpha-osk.spec``).
3. Optionally wrap the bundle into any combination of:

   - ``--appimage``  — portable single-file AppImage
   - ``--deb``       — Debian/Ubuntu ``.deb`` package
   - ``--tarball``   — portable ``.tar.gz`` with ``install.sh``

Usage
-----
    # PyInstaller bundle only (always runs)
    python build/linux/build.py

    # AppImage (requires appimagetool on PATH or auto-download)
    python build/linux/build.py --appimage --fetch-appimagetool

    # Debian/Ubuntu .deb (requires dpkg-deb, pre-installed on Debian)
    python build/linux/build.py --deb

    # Portable tar.gz with install.sh/uninstall.sh
    python build/linux/build.py --tarball

    # All three + the bundle in one go
    python build/linux/build.py --all --fetch-appimagetool

    # Skip PyInstaller, only re-package an existing dist/
    python build/linux/build.py --skip-build --deb --tarball

Prerequisites
-------------
- Python 3.9+ with ``pyinstaller`` installed (``pip install pyinstaller``).
- For ``--appimage``: ``appimagetool`` on PATH, or pass
  ``--fetch-appimagetool`` and the script will download the upstream
  x86_64 build into ``~/.cache/alpha-osk-build/``.
- For ``--deb``: ``dpkg-deb`` on PATH (pre-installed on Debian/Ubuntu;
  install ``dpkg`` on other distros).
- Runtime (users of the built binary): ``xdotool`` (X11) or ``ydotool``
  (Wayland) on the target system — they're not bundled because they're
  OS-level tools. The ``.deb`` control file lists both as Recommends.

See Also
--------
- ``build/linux/alpha-osk.spec`` — PyInstaller build specification.
- ``build/linux/AppRun`` — AppImage entry-point wrapper.
- ``build/linux/alpha-osk.desktop`` — desktop integration file.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import urllib.request
from pathlib import Path


SCRIPT_DIR = Path(__file__).parent.resolve()        # build/linux/
PROJECT_ROOT = SCRIPT_DIR.parent.parent              # repo root
SPEC_FILE = SCRIPT_DIR / "alpha-osk.spec"
DIST_DIR = PROJECT_ROOT / "dist" / "alpha-osk"
RELEASE_DIR = PROJECT_ROOT / "release"
LINUX_DIR = SCRIPT_DIR                               # AppRun + .desktop live here
ICON_SOURCE = PROJECT_ROOT / "assets" / "logo-1024.png"
CACHE_DIR = Path.home() / ".cache" / "alpha-osk-build"
APPIMAGETOOL_URL = (
    "https://github.com/AppImage/appimagetool/releases/download/"
    "continuous/appimagetool-x86_64.AppImage"
)


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
    exe_path = DIST_DIR / "alpha-osk"
    if not exe_path.exists():
        fail(f"Expected binary missing: {exe_path}")
        return False
    ok(f"Bundle built at {DIST_DIR}")
    return True


def freeze_lockfile(version: str) -> Path | None:
    """Capture ``pip freeze`` output as a release-time lockfile.

    Writes ``release/Alpha-OSK-{version}-linux-requirements.lock.txt``
    containing every Python package + exact version resolved in the
    build venv.  PyInstaller bundles whatever pip resolved at build
    time, so this is the reproducible record of what actually went into
    the frozen AppImage / .deb / tarball.

    Not a CycloneDX / SPDX SBOM (no licenses, no purls), but it's the
    cheapest possible answer to "what shipped in version X.Y.Z?" --
    a single text file, no new build dep.  The proper SBOM upgrade
    path is documented in docs/LINUX.md alongside docs/WINDOWS.md.

    Returns the lockfile path on success, None on failure.
    """
    header("Capturing Python Dependency Lockfile")
    RELEASE_DIR.mkdir(exist_ok=True)
    lockfile = RELEASE_DIR / f"Alpha-OSK-{version}-linux-requirements.lock.txt"

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
        f"# Alpha-OSK {version} (Linux) -- Python dependency lockfile\n"
        f"# Generated by build/linux/build.py via `pip freeze --all`.\n"
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
    """Generate a CycloneDX 1.6 SBOM of the build venv (Linux).

    Mirror of the Windows build's ``emit_sbom``.  See
    ``build/windows/build.py::emit_sbom`` and
    ``docs/WINDOWS.md`` § *Dependency Lockfile & SBOM* for the
    rationale on shipping both the plaintext lockfile and the
    structured SBOM alongside each release.

    Soft-fails (returns None + warning) if ``cyclonedx-bom`` isn't
    installed -- dev builds without it still produce a working
    AppImage / .deb / tarball, they just skip the SBOM.  Production
    release builds should have it via ``requirements-dev.txt``.
    """
    header("Generating CycloneDX SBOM")
    RELEASE_DIR.mkdir(exist_ok=True)
    sbom = RELEASE_DIR / f"Alpha-OSK-{version}-linux-sbom.cyclonedx.json"

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


def find_appimagetool(auto_fetch: bool) -> Path | None:
    step("Locating appimagetool")
    on_path = shutil.which("appimagetool")
    if on_path:
        ok(f"Found on PATH: {on_path}")
        return Path(on_path)

    cached = CACHE_DIR / "appimagetool-x86_64.AppImage"
    if cached.exists():
        ok(f"Using cached copy: {cached}")
        return cached

    if not auto_fetch:
        fail(
            "appimagetool not found. Install it or pass "
            "--fetch-appimagetool to download the upstream build."
        )
        return None

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    step(f"Downloading appimagetool -> {cached}")
    try:
        urllib.request.urlretrieve(APPIMAGETOOL_URL, str(cached))
    except Exception as exc:
        fail(f"Download failed: {exc}")
        return None
    cached.chmod(cached.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    ok(f"Downloaded to {cached}")
    return cached


def build_appimage(appimagetool: Path, version: str) -> bool:
    header("Assembling AppDir")
    appdir = PROJECT_ROOT / "dist" / "AppDir"
    if appdir.exists():
        shutil.rmtree(appdir)
    bin_dir = appdir / "usr" / "bin"
    bin_dir.mkdir(parents=True)

    step("Copying PyInstaller bundle into AppDir/usr/bin")
    for entry in DIST_DIR.iterdir():
        dest = bin_dir / entry.name
        if entry.is_dir():
            shutil.copytree(entry, dest)
        else:
            shutil.copy2(entry, dest)

    step("Installing AppRun, .desktop, icons")
    shutil.copy2(LINUX_DIR / "AppRun", appdir / "AppRun")
    (appdir / "AppRun").chmod(0o755)
    shutil.copy2(LINUX_DIR / "alpha-osk.desktop", appdir / "alpha-osk.desktop")

    if ICON_SOURCE.exists():
        # AppImage expects both a top-level icon and one under the
        # hicolor theme so desktop environments can pick it up after
        # integration.
        shutil.copy2(ICON_SOURCE, appdir / "alpha-osk.png")
        icon_theme_dir = appdir / "usr" / "share" / "icons" / "hicolor" / "1024x1024" / "apps"
        icon_theme_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ICON_SOURCE, icon_theme_dir / "alpha-osk.png")
    else:
        warn(f"Icon source missing: {ICON_SOURCE} — AppImage will have no icon")

    RELEASE_DIR.mkdir(exist_ok=True)
    out_path = RELEASE_DIR / f"Alpha-OSK-{version}-x86_64.AppImage"
    if out_path.exists():
        out_path.unlink()

    header(f"Packaging AppImage -> {out_path.name}")
    env = os.environ.copy()
    # appimagetool embeds a version string into the filename when ARCH is set.
    env.setdefault("ARCH", "x86_64")
    cmd = [str(appimagetool), "--no-appstream", str(appdir), str(out_path)]
    step("Running: " + " ".join(cmd))
    result = subprocess.run(cmd, env=env)
    if result.returncode != 0:
        fail("appimagetool failed")
        return False
    ok(f"AppImage created: {out_path}")
    return True


# ---------------------------------------------------------------------------
#  .deb package
# ---------------------------------------------------------------------------

# Debian policy: Package names must be lowercase, version must not contain
# uppercase or colons. We use the raw __version__ string (already clean:
# "1.0.7") and amd64 as the target arch — x86_64 PyInstaller builds only
# run on amd64 systems.
DEB_PACKAGE = "alpha-osk"
DEB_ARCH = "amd64"
DEB_SECTION = "utils"
DEB_MAINTAINER = "Owen Kent <Owenpkent@gmail.com>"
DEB_HOMEPAGE = "https://github.com/okstudio1/alpha-osk"


def _write_deb_control(staging: Path, version: str, installed_bytes: int) -> None:
    # Installed-Size is in KB per Debian policy.
    installed_kb = max(1, installed_bytes // 1024)
    control = f"""\
Package: {DEB_PACKAGE}
Version: {version}
Section: {DEB_SECTION}
Priority: optional
Architecture: {DEB_ARCH}
Installed-Size: {installed_kb}
Maintainer: {DEB_MAINTAINER}
Homepage: {DEB_HOMEPAGE}
Recommends: xdotool | ydotool
Description: AI-powered on-screen keyboard for accessibility
 Alpha-OSK is an on-screen keyboard designed for users with motor
 disabilities. It features AI-enabled predictive text, adaptive layouts,
 and pluggable key-synthesis backends (xdotool on X11, ydotool on
 Wayland). The keyboard stays on top of other windows without stealing
 keyboard focus.
"""
    (staging / "DEBIAN").mkdir(parents=True, exist_ok=True)
    (staging / "DEBIAN" / "control").write_text(control)


def _write_deb_wrapper(staging: Path) -> None:
    # Small shim on $PATH that execs the real binary inside /opt.
    # Defaults QT_QPA_PLATFORM=xcb for the xdotool backend; users on
    # native Wayland can override before launch.
    wrapper = """\
#!/bin/sh
: "${QT_QPA_PLATFORM:=xcb}"
export QT_QPA_PLATFORM
exec /opt/alpha-osk/alpha-osk "$@"
"""
    bin_dir = staging / "usr" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper_path = bin_dir / "alpha-osk"
    wrapper_path.write_text(wrapper)
    wrapper_path.chmod(0o755)


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        if p.is_file() and not p.is_symlink():
            total += p.stat().st_size
    return total


def build_deb(version: str) -> bool:
    header(f"Packaging .deb for {DEB_PACKAGE} {version}")
    if not shutil.which("dpkg-deb"):
        fail(
            "dpkg-deb not found. Install it with "
            "`sudo apt install dpkg` (Debian/Ubuntu) or skip --deb."
        )
        return False

    staging = PROJECT_ROOT / "dist" / "deb-staging"
    if staging.exists():
        shutil.rmtree(staging)

    # 1. Drop the PyInstaller bundle under /opt/alpha-osk — keeps the
    #    app self-contained and out of /usr (FHS says third-party apps
    #    belong in /opt).
    opt_dir = staging / "opt" / "alpha-osk"
    step(f"Copying PyInstaller bundle -> {opt_dir}")
    shutil.copytree(DIST_DIR, opt_dir)

    # 2. Install the /usr/bin/alpha-osk launcher shim.
    step("Installing /usr/bin/alpha-osk wrapper")
    _write_deb_wrapper(staging)

    # 3. Desktop entry — .deb version references the wrapper on PATH.
    apps_dir = staging / "usr" / "share" / "applications"
    apps_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(LINUX_DIR / "alpha-osk.desktop", apps_dir / "alpha-osk.desktop")

    # 4. Icon under hicolor theme so GNOME/KDE pick it up automatically.
    if ICON_SOURCE.exists():
        icon_dir = (
            staging / "usr" / "share" / "icons" / "hicolor" / "1024x1024" / "apps"
        )
        icon_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ICON_SOURCE, icon_dir / "alpha-osk.png")
    else:
        warn(f"Icon source missing: {ICON_SOURCE} — .deb will ship without an icon")

    # 5. Control file (needs Installed-Size of everything above).
    step("Writing DEBIAN/control")
    _write_deb_control(staging, version, _dir_size(staging))

    # 6. Ask dpkg-deb to build the archive. --root-owner-group stamps
    #    files as root:root without needing fakeroot.
    RELEASE_DIR.mkdir(exist_ok=True)
    out_path = RELEASE_DIR / f"{DEB_PACKAGE}_{version}_{DEB_ARCH}.deb"
    if out_path.exists():
        out_path.unlink()

    cmd = ["dpkg-deb", "--root-owner-group", "--build", str(staging), str(out_path)]
    step("Running: " + " ".join(cmd))
    result = subprocess.run(cmd)
    if result.returncode != 0:
        fail("dpkg-deb failed")
        return False
    ok(f".deb created: {out_path}")
    return True


# ---------------------------------------------------------------------------
#  tar.gz portable archive
# ---------------------------------------------------------------------------

TARBALL_INSTALL_SH = """\
#!/bin/sh
# Alpha-OSK portable installer — copies the bundle into ~/.local so the
# user can launch alpha-osk without root and without polluting /usr.
set -e

PREFIX="${PREFIX:-$HOME/.local}"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "Installing Alpha-OSK into $PREFIX ..."

mkdir -p "$PREFIX/opt" "$PREFIX/bin" \\
         "$PREFIX/share/applications" \\
         "$PREFIX/share/icons/hicolor/1024x1024/apps"

# Remove any prior install so we don't mix files across versions.
rm -rf "$PREFIX/opt/alpha-osk"
cp -r "$HERE/alpha-osk" "$PREFIX/opt/alpha-osk"

# Wrapper on PATH. Use an unquoted heredoc so $PREFIX expands NOW (at
# install time) to the chosen install root. Runtime variables that must
# survive into the launcher are escaped with \$ so the shell only
# expands them when the launcher itself runs.
cat > "$PREFIX/bin/alpha-osk" <<LAUNCHER
#!/bin/sh
: "\${QT_QPA_PLATFORM:=xcb}"
export QT_QPA_PLATFORM
exec "$PREFIX/opt/alpha-osk/alpha-osk" "\$@"
LAUNCHER
chmod +x "$PREFIX/bin/alpha-osk"

# Desktop entry + icon (so GNOME/KDE launchers find it).
cp "$HERE/alpha-osk.desktop" "$PREFIX/share/applications/alpha-osk.desktop"
if [ -f "$HERE/alpha-osk.png" ]; then
    cp "$HERE/alpha-osk.png" \\
       "$PREFIX/share/icons/hicolor/1024x1024/apps/alpha-osk.png"
fi

# Refresh the desktop database if the tool is available (ignore errors).
command -v update-desktop-database >/dev/null 2>&1 && \\
    update-desktop-database "$PREFIX/share/applications" 2>/dev/null || true

echo
echo "Installed. Launch with: alpha-osk"
echo "(Make sure $PREFIX/bin is on your PATH.)"
echo "Uninstall with: $HERE/uninstall.sh"
"""

TARBALL_UNINSTALL_SH = """\
#!/bin/sh
# Alpha-OSK portable uninstaller — reverses install.sh.
set -e

PREFIX="${PREFIX:-$HOME/.local}"

echo "Removing Alpha-OSK from $PREFIX ..."
rm -rf "$PREFIX/opt/alpha-osk"
rm -f  "$PREFIX/bin/alpha-osk"
rm -f  "$PREFIX/share/applications/alpha-osk.desktop"
rm -f  "$PREFIX/share/icons/hicolor/1024x1024/apps/alpha-osk.png"

command -v update-desktop-database >/dev/null 2>&1 && \\
    update-desktop-database "$PREFIX/share/applications" 2>/dev/null || true

echo "Uninstalled. Note: user data under ~/.config/alpha-osk is preserved."
"""

TARBALL_README = """\
Alpha-OSK — Portable Linux Build
=================================

This archive contains a self-contained PyInstaller bundle of Alpha-OSK.

Install
-------
    ./install.sh

By default files go under ~/.local. To install elsewhere:
    PREFIX=/opt/alpha-osk ./install.sh

Runtime dependencies
--------------------
The bundle includes all Python / Qt dependencies but NOT the OS-level
key-synthesis tools:

    sudo apt install xdotool       # X11 (most desktops)
    sudo apt install ydotool       # Wayland (needs user-space daemon)

Uninstall
---------
    ./uninstall.sh

Run without installing
----------------------
    ./alpha-osk/alpha-osk
"""


def build_tarball(version: str) -> bool:
    header(f"Packaging portable tarball v{version}")
    import tarfile

    staging = PROJECT_ROOT / "dist" / "tarball-staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    step("Copying bundle, scripts, .desktop, icon")
    shutil.copytree(DIST_DIR, staging / "alpha-osk")
    shutil.copy2(LINUX_DIR / "alpha-osk.desktop", staging / "alpha-osk.desktop")
    if ICON_SOURCE.exists():
        shutil.copy2(ICON_SOURCE, staging / "alpha-osk.png")

    install_sh = staging / "install.sh"
    uninstall_sh = staging / "uninstall.sh"
    install_sh.write_text(TARBALL_INSTALL_SH)
    uninstall_sh.write_text(TARBALL_UNINSTALL_SH)
    install_sh.chmod(0o755)
    uninstall_sh.chmod(0o755)
    (staging / "README.txt").write_text(TARBALL_README)

    RELEASE_DIR.mkdir(exist_ok=True)
    out_path = RELEASE_DIR / f"alpha-osk-{version}-linux-x86_64.tar.gz"
    if out_path.exists():
        out_path.unlink()

    step(f"Writing {out_path}")
    with tarfile.open(out_path, "w:gz") as tf:
        # arcname pins everything under a single top-level dir so users
        # who `tar xf ...` don't explode files into their cwd.
        tf.add(str(staging), arcname=f"alpha-osk-{version}")

    ok(f"Tarball created: {out_path}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Alpha-OSK for Linux")
    parser.add_argument(
        "--skip-build", action="store_true",
        help="Skip PyInstaller, reuse existing dist/alpha-osk/",
    )
    parser.add_argument(
        "--appimage", action="store_true",
        help="Also wrap the bundle into an AppImage",
    )
    parser.add_argument(
        "--fetch-appimagetool", action="store_true",
        help="Download appimagetool into ~/.cache if not on PATH",
    )
    parser.add_argument(
        "--deb", action="store_true",
        help="Also build a .deb package (requires dpkg-deb)",
    )
    parser.add_argument(
        "--tarball", action="store_true",
        help="Also build a portable tar.gz with install.sh",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Shorthand for --appimage --deb --tarball",
    )
    args = parser.parse_args()

    if args.all:
        args.appimage = True
        args.deb = True
        args.tarball = True

    header(f"Alpha-OSK Linux Build v{read_version()}")

    if not args.skip_build:
        if not check_pyinstaller():
            return 1
        if not run_pyinstaller():
            return 1
    else:
        if not DIST_DIR.exists():
            fail(f"--skip-build passed but {DIST_DIR} missing")
            return 1
        warn("Skipping PyInstaller — reusing existing bundle")

    version = read_version()

    # Lockfile + SBOM reflect the build venv state; both always emit
    # (even on --skip-build, since bumping the version is what
    # --skip-build is often for and the filenames encode the version).
    # Lockfile = pip freeze (human/pip-friendly).
    # SBOM     = CycloneDX 1.6 (machine/scanner-friendly).
    lockfile_path = freeze_lockfile(version)
    if lockfile_path is None:
        warn("Lockfile generation failed -- continuing without it")

    sbom_path = emit_sbom(version)
    if sbom_path is None:
        warn("SBOM generation failed -- continuing without it")

    if args.appimage:
        tool = find_appimagetool(auto_fetch=args.fetch_appimagetool)
        if tool is None:
            return 1
        if not build_appimage(tool, version):
            return 1

    if args.deb:
        if not build_deb(version):
            return 1

    if args.tarball:
        if not build_tarball(version):
            return 1

    header("Build complete")
    print(f"  Bundle:   {DIST_DIR}")
    if args.appimage or args.deb or args.tarball:
        print(f"  Release:  {RELEASE_DIR}/")
    if lockfile_path and lockfile_path.exists():
        print(f"  Lockfile: {lockfile_path}")
    if sbom_path and sbom_path.exists():
        print(f"  SBOM:     {sbom_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
