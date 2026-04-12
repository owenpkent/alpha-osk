"""
Alpha-OSK Windows Build Script
================================

End-to-end build pipeline for the Windows release:

1. Check prerequisites (Python, PyInstaller, NSIS, signtool, eToken).
2. Build standalone ``.exe`` with PyInstaller.
3. Sign all ``.exe`` and ``.dll`` files with the EV certificate.
4. Package into an NSIS installer.
5. Sign the installer itself.
6. Verify all signatures.

Usage::

    # Full signed release (eToken must be plugged in)
    python build/build_windows.py

    # Unsigned dev build (no eToken needed)
    python build/build_windows.py --no-sign

    # Skip PyInstaller, just re-package and sign
    python build/build_windows.py --skip-build

    # Verify signatures on existing build
    python build/build_windows.py --verify-only

Prerequisites
-------------
- Python 3.9+ with ``pyinstaller`` installed (``pip install pyinstaller``).
- NSIS 3.x installed and ``makensis.exe`` on PATH or in default location.
- Windows SDK with ``signtool.exe`` (for signing).
- SafeNet eToken plugged in (for EV signing).
- Run from a **normal** (non-elevated) shell — eToken is not visible to
  admin processes.

Follows the same signing patterns as ``gitconnect/windows-desktop``:
- ``build/sign.py`` handles retry logic for Defender file locks.
- Same EV certificate (OK Studio Inc., SHA1 thumbprint).
- Same DigiCert timestamp server.

See Also
--------
- ``build/sign.py`` — Signing script with retry logic.
- ``build/alpha-osk.spec`` — PyInstaller build specification.
- ``build/installer.nsh`` — NSIS installer customizations.
- ``docs/WINDOWS.md`` — Full Windows guide.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
#  Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_ROOT = SCRIPT_DIR.parent
DIST_DIR = PROJECT_ROOT / "dist" / "alpha-osk"
RELEASE_DIR = PROJECT_ROOT / "release"
SPEC_FILE = SCRIPT_DIR / "alpha-osk.spec"


# ---------------------------------------------------------------------------
#  Console output helpers (same style as gitconnect's run.py)
# ---------------------------------------------------------------------------

class Colors:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
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


def _safe_print(msg: str) -> None:
    """Print with fallback for terminals that can't render Unicode."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", errors="replace").decode("ascii"))


def step(msg: str) -> None:
    _safe_print(f"{Colors.CYAN}> {msg}{Colors.END}")


def success(msg: str) -> None:
    _safe_print(f"{Colors.GREEN}[OK] {msg}{Colors.END}")


def error(msg: str) -> None:
    _safe_print(f"{Colors.RED}[FAIL] {msg}{Colors.END}")


def warning(msg: str) -> None:
    _safe_print(f"{Colors.YELLOW}[WARN] {msg}{Colors.END}")


def info(msg: str) -> None:
    _safe_print(f"{Colors.BLUE}[INFO] {msg}{Colors.END}")


# ---------------------------------------------------------------------------
#  Prerequisite checks
# ---------------------------------------------------------------------------

def check_python() -> bool:
    """Verify Python 3.9+."""
    step("Checking Python version...")
    if sys.version_info >= (3, 9):
        success(f"Python {sys.version.split()[0]}")
        return True
    error(f"Python 3.9+ required, found {sys.version}")
    return False


def check_pyinstaller() -> bool:
    """Verify PyInstaller is installed."""
    step("Checking PyInstaller...")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--version"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            success(f"PyInstaller {result.stdout.strip()}")
            return True
    except Exception:
        pass
    error("PyInstaller not found. Install with: pip install pyinstaller")
    return False


def check_nsis() -> str | None:
    """
    Find ``makensis.exe``.

    Returns:
        Path to makensis, or None if not found.
    """
    step("Checking NSIS...")

    # Check PATH first
    on_path = shutil.which("makensis") or shutil.which("makensis.exe")
    if on_path:
        success(f"NSIS found: {on_path}")
        return on_path

    # Check common install locations
    candidates = [
        Path(r"C:\Program Files (x86)\NSIS\makensis.exe"),
        Path(r"C:\Program Files\NSIS\makensis.exe"),
    ]
    # Check via registry-style paths
    nsis_env = os.environ.get("NSIS_HOME")
    if nsis_env:
        candidates.insert(0, Path(nsis_env) / "makensis.exe")

    for p in candidates:
        if p.exists():
            success(f"NSIS found: {p}")
            return str(p)

    warning(
        "NSIS not found. Install from https://nsis.sourceforge.io/\n"
        "  Or: winget install NSIS.NSIS\n"
        "  The build will produce a portable .exe but no installer."
    )
    return None


def check_signtool() -> str | None:
    """
    Find ``signtool.exe``.

    Returns:
        Path to signtool, or None if not found.
    """
    step("Checking signtool...")
    try:
        # Import from our signing module
        sys.path.insert(0, str(SCRIPT_DIR))
        from sign import find_signtool
        path = find_signtool()
        success(f"signtool found: {path}")
        return path
    except (FileNotFoundError, ImportError):
        warning("signtool not found. Signing will be skipped.")
        return None


def check_certificate() -> bool:
    """
    Verify the EV certificate is visible in the Windows cert store.

    Uses ``certutil`` to check for our certificate thumbprint.
    """
    step("Checking EV certificate...")
    from sign import CERTIFICATE_SHA1

    try:
        result = subprocess.run(
            ["certutil", "-store", "-user", "My"],
            capture_output=True, text=True, timeout=30,
        )
        if CERTIFICATE_SHA1.lower() in result.stdout.lower():
            success("EV certificate found (OK Studio Inc.)")
            return True
        else:
            warning(
                "EV certificate not found in user store.\n"
                "  Ensure the SafeNet eToken is plugged in.\n"
                "  Verify with: certutil -store -user My"
            )
            return False
    except Exception as e:
        warning(f"Could not check certificate: {e}")
        return False


# ---------------------------------------------------------------------------
#  Build steps
# ---------------------------------------------------------------------------

def build_pyinstaller() -> bool:
    """
    Run PyInstaller to produce the standalone application directory.

    Output: ``dist/alpha-osk/`` containing ``alpha-osk.exe`` + dependencies.
    """
    header("Building with PyInstaller")

    if not SPEC_FILE.exists():
        error(f"Spec file not found: {SPEC_FILE}")
        return False

    step(f"Running PyInstaller with {SPEC_FILE.name}...")

    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", str(SPEC_FILE), "--noconfirm"],
        cwd=str(PROJECT_ROOT),
    )

    if result.returncode != 0:
        error("PyInstaller build failed")
        return False

    exe = DIST_DIR / "alpha-osk.exe"
    if not exe.exists():
        error(f"Expected output not found: {exe}")
        return False

    success(f"Build complete: {DIST_DIR}")

    # Count files
    file_count = sum(1 for _ in DIST_DIR.rglob("*") if _.is_file())
    total_size = sum(f.stat().st_size for f in DIST_DIR.rglob("*") if f.is_file())
    info(f"  {file_count} files, {total_size / 1024 / 1024:.1f} MB total")

    return True


def sign_build(signtool_path: str) -> bool:
    """
    Sign all ``.exe`` and ``.dll`` files in the build output.

    Uses ``build/sign.py`` for retry logic.
    """
    header("Signing Build Output")

    sys.path.insert(0, str(SCRIPT_DIR))
    from sign import sign_directory

    try:
        count = sign_directory(str(DIST_DIR), signtool_path)
        success(f"Signed {count} files")
        return True
    except RuntimeError as e:
        error(f"Signing failed: {e}")
        return False


def build_nsis_installer(makensis_path: str) -> Path | None:
    """
    Build the NSIS installer from the PyInstaller output.

    Generates a temporary ``.nsi`` script that:
    - Installs to ``C:\\Program Files\\Alpha-OSK`` (required for UIAccess).
    - Includes all files from ``dist/alpha-osk/``.
    - Creates Start Menu and Desktop shortcuts.
    - Handles uninstall (with optional AppData cleanup).
    - Includes the custom ``installer.nsh`` macros.

    Returns:
        Path to the generated installer ``.exe``, or None on failure.
    """
    header("Building NSIS Installer")

    RELEASE_DIR.mkdir(parents=True, exist_ok=True)

    version = "1.0.0"
    installer_name = f"Alpha-OSK-Setup-{version}"
    installer_exe = RELEASE_DIR / f"{installer_name}.exe"

    # Generate NSI script
    nsi_content = _generate_nsi_script(version, installer_name)
    nsi_file = SCRIPT_DIR / "_alpha-osk-installer.nsi"
    nsi_file.write_text(nsi_content, encoding="utf-8")

    step(f"Running makensis...")
    result = subprocess.run(
        [makensis_path, str(nsi_file)],
        cwd=str(PROJECT_ROOT),
    )

    # Clean up temp NSI file
    nsi_file.unlink(missing_ok=True)

    if result.returncode != 0:
        error("NSIS build failed")
        return None

    if not installer_exe.exists():
        error(f"Installer not found at: {installer_exe}")
        return None

    size_mb = installer_exe.stat().st_size / 1024 / 1024
    success(f"Installer built: {installer_exe} ({size_mb:.1f} MB)")
    return installer_exe


def _generate_nsi_script(version: str, installer_name: str) -> str:
    """
    Generate the NSIS ``.nsi`` script content.

    This creates a proper Windows installer that:
    - Defaults to ``C:\\Program Files\\Alpha-OSK`` (for UIAccess).
    - Lets the user change the install directory.
    - Creates Start Menu + Desktop shortcuts.
    - Registers an uninstaller.
    - Includes the ``installer.nsh`` custom macros.
    """
    dist_dir_nsis = str(DIST_DIR).replace("/", "\\")
    installer_nsh = str(SCRIPT_DIR / "installer.nsh").replace("/", "\\")
    release_dir_nsis = str(RELEASE_DIR).replace("/", "\\")
    # Icon path (if it exists)
    icon_path = str(SCRIPT_DIR / "alpha-osk.ico").replace("/", "\\")
    has_icon = (SCRIPT_DIR / "alpha-osk.ico").exists()

    # Build file list from dist directory
    file_install_lines = []
    file_uninstall_lines = []
    if DIST_DIR.exists():
        for f in sorted(DIST_DIR.rglob("*")):
            if f.is_file():
                rel = f.relative_to(DIST_DIR)
                parent = str(rel.parent).replace("/", "\\")
                if parent == ".":
                    file_install_lines.append(
                        f'  File "{f}"'
                    )
                    file_uninstall_lines.append(
                        f'  Delete "$INSTDIR\\{rel.name}"'
                    )
                else:
                    file_install_lines.append(
                        f'  SetOutPath "$INSTDIR\\{parent}"'
                    )
                    file_install_lines.append(
                        f'  File "{f}"'
                    )
                    file_uninstall_lines.append(
                        f'  Delete "$INSTDIR\\{rel}"'
                    )

    files_block = "\n".join(file_install_lines)
    uninstall_files = "\n".join(file_uninstall_lines)

    icon_line = f'!define MUI_ICON "{icon_path}"' if has_icon else "; No icon file found"

    return f"""; Alpha-OSK NSIS Installer
; Auto-generated by build/build_windows.py
; Do not edit — regenerated on every build.

!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"

; --- App metadata ---
!define APP_NAME "Alpha-OSK"
!define APP_VERSION "{version}"
!define APP_PUBLISHER "OK Studio Inc."
!define APP_URL "https://github.com/owenpkent/alpha-osk"
!define APP_EXE "alpha-osk.exe"
!define APP_GUID "alpha-osk-keyboard"
!define INSTALL_DIR "$PROGRAMFILES64\\Alpha-OSK"

; --- Installer metadata ---
Name "${{APP_NAME}} ${{APP_VERSION}}"
OutFile "{release_dir_nsis}\\{installer_name}.exe"
InstallDir "${{INSTALL_DIR}}"
InstallDirRegKey HKCU "Software\\${{APP_NAME}}" "InstallLocation"
RequestExecutionLevel admin
{icon_line}

; --- Variables for shortcut options ---
Var CreateDesktopShortcut
Var CreateStartMenuShortcut

; --- MUI Settings ---
!define MUI_ABORTWARNING
; Launch as the original (non-elevated) user via Explorer shell
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Launch ${{APP_NAME}}"
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchAsUser

; --- Pages ---
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
Page custom ShortcutOptionsPage ShortcutOptionsLeave
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

; --- Uninstaller pages ---
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

; --- Language ---
!insertmacro MUI_LANGUAGE "English"

; --- Include custom macros ---
!include "{installer_nsh}"

; ============================================================
;  Shortcut Options Page
; ============================================================
Function ShortcutOptionsPage
  nsDialogs::Create 1018
  Pop $0
  ${{If}} $0 == error
    Abort
  ${{EndIf}}

  ${{NSD_CreateLabel}} 0 0 100% 20u "Choose which shortcuts to create:"

  ${{NSD_CreateCheckbox}} 20u 30u 100% 15u "Create Desktop shortcut"
  Pop $1
  ${{NSD_SetState}} $1 ${{BST_CHECKED}}

  ${{NSD_CreateCheckbox}} 20u 50u 100% 15u "Create Start Menu shortcut"
  Pop $2
  ${{NSD_SetState}} $2 ${{BST_CHECKED}}

  nsDialogs::Show
FunctionEnd

Function ShortcutOptionsLeave
  ${{NSD_GetState}} $1 $CreateDesktopShortcut
  ${{NSD_GetState}} $2 $CreateStartMenuShortcut
FunctionEnd

; ============================================================
;  .onInit — runs on installer start
; ============================================================
Function .onInit
  !insertmacro customInit

  ; Default shortcut options to checked
  StrCpy $CreateDesktopShortcut ${{BST_CHECKED}}
  StrCpy $CreateStartMenuShortcut ${{BST_CHECKED}}
FunctionEnd

; ============================================================
;  Launch as original (non-elevated) user
; ============================================================
Function LaunchAsUser
  ; Use Explorer to launch so the app runs as the normal user, not admin
  Exec '"$WINDIR\\explorer.exe" "$INSTDIR\\${{APP_EXE}}"'
FunctionEnd

; ============================================================
;  Install Section
; ============================================================
Section "Install"
  SetOutPath "$INSTDIR"

  ; Install all files from PyInstaller dist
{files_block}

  ; Write uninstaller
  WriteUninstaller "$INSTDIR\\uninstall.exe"

  ; Write registry keys for Add/Remove Programs
  WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${{APP_GUID}}" "DisplayName" "${{APP_NAME}}"
  WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${{APP_GUID}}" "DisplayVersion" "${{APP_VERSION}}"
  WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${{APP_GUID}}" "Publisher" "${{APP_PUBLISHER}}"
  WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${{APP_GUID}}" "URLInfoAbout" "${{APP_URL}}"
  WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${{APP_GUID}}" "UninstallString" "$INSTDIR\\uninstall.exe"
  WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${{APP_GUID}}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${{APP_GUID}}" "DisplayIcon" "$INSTDIR\\${{APP_EXE}}"

  ; Calculate installed size
  ${{GetSize}} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${{APP_GUID}}" "EstimatedSize" "$0"

  ; Run custom install macros (old version cleanup)
  !insertmacro customInstall

  ; Create shortcuts — use All Users context so they appear for everyone
  ; and resolve correctly under admin elevation
  SetShellVarContext all

  ${{If}} $CreateStartMenuShortcut == ${{BST_CHECKED}}
    CreateDirectory "$SMPROGRAMS\\Alpha-OSK"
    CreateShortCut "$SMPROGRAMS\\Alpha-OSK\\Alpha-OSK.lnk" "$INSTDIR\\${{APP_EXE}}" "" "$INSTDIR\\${{APP_EXE}}" 0
    CreateShortCut "$SMPROGRAMS\\Alpha-OSK\\Uninstall Alpha-OSK.lnk" "$INSTDIR\\uninstall.exe"
  ${{EndIf}}

  ${{If}} $CreateDesktopShortcut == ${{BST_CHECKED}}
    CreateShortCut "$DESKTOP\\Alpha-OSK.lnk" "$INSTDIR\\${{APP_EXE}}" "" "$INSTDIR\\${{APP_EXE}}" 0
  ${{EndIf}}
SectionEnd

; ============================================================
;  Uninstall Section
; ============================================================
Section "Uninstall"
  ; Run custom uninstall macros
  !insertmacro customUnInstall

  ; Clean up shortcuts (All Users context to match install)
  SetShellVarContext all
  Delete "$DESKTOP\\Alpha-OSK.lnk"
  Delete "$SMPROGRAMS\\Alpha-OSK\\Alpha-OSK.lnk"
  Delete "$SMPROGRAMS\\Alpha-OSK\\Uninstall Alpha-OSK.lnk"
  RMDir "$SMPROGRAMS\\Alpha-OSK"

  ; Remove files
{uninstall_files}
  Delete "$INSTDIR\\uninstall.exe"

  ; Remove install directory
  RMDir /r "$INSTDIR"

  ; Remove registry keys
  DeleteRegKey HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${{APP_GUID}}"
  DeleteRegKey HKCU "Software\\${{APP_NAME}}"
SectionEnd
"""


def sign_installer(installer_path: Path, signtool_path: str) -> bool:
    """Sign the final NSIS installer .exe."""
    header("Signing Installer")

    sys.path.insert(0, str(SCRIPT_DIR))
    from sign import sign_file

    try:
        sign_file(str(installer_path), signtool_path)
        success(f"Installer signed: {installer_path.name}")
        return True
    except RuntimeError as e:
        error(f"Failed to sign installer: {e}")
        return False


def verify_build(signtool_path: str) -> bool:
    """Verify signatures on the main exe and installer."""
    header("Verifying Signatures")

    sys.path.insert(0, str(SCRIPT_DIR))
    from sign import verify_file

    all_ok = True

    # Verify main exe
    main_exe = DIST_DIR / "alpha-osk.exe"
    if main_exe.exists():
        if not verify_file(str(main_exe), signtool_path):
            all_ok = False
    else:
        warning(f"Main exe not found: {main_exe}")

    # Verify installer(s)
    if RELEASE_DIR.exists():
        for installer in RELEASE_DIR.glob("*.exe"):
            if not verify_file(str(installer), signtool_path):
                all_ok = False

    if all_ok:
        success("All signatures verified")
    else:
        error("Some signatures failed verification")

    return all_ok


# ---------------------------------------------------------------------------
#  Main pipeline
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Alpha-OSK Windows Build Pipeline"
    )
    parser.add_argument(
        "--no-sign", action="store_true",
        help="Skip code signing (dev builds without eToken)",
    )
    parser.add_argument(
        "--skip-build", action="store_true",
        help="Skip PyInstaller build (re-package/re-sign existing dist)",
    )
    parser.add_argument(
        "--verify-only", action="store_true",
        help="Only verify existing signatures",
    )
    parser.add_argument(
        "--no-installer", action="store_true",
        help="Skip NSIS installer (produce portable build only)",
    )
    args = parser.parse_args()

    header("Alpha-OSK Windows Build")
    info(f"Project root: {PROJECT_ROOT}")
    info(f"Build output: {DIST_DIR}")
    info(f"Release output: {RELEASE_DIR}")

    # --- Prerequisites ---
    header("Checking Prerequisites")

    if not check_python():
        return 1

    if not args.verify_only and not args.skip_build:
        if not check_pyinstaller():
            return 1

    makensis = None
    if not args.no_installer and not args.verify_only:
        makensis = check_nsis()

    signtool = None
    can_sign = False
    if not args.no_sign:
        signtool = check_signtool()
        if signtool:
            can_sign = check_certificate()
            if not can_sign:
                warning("Signing will be skipped (no certificate found)")
        else:
            warning("Signing will be skipped (no signtool)")

    # --- Verify only mode ---
    if args.verify_only:
        if not signtool:
            error("Cannot verify without signtool")
            return 1
        ok = verify_build(signtool)
        return 0 if ok else 1

    # --- Build ---
    if not args.skip_build:
        if not build_pyinstaller():
            return 1
    else:
        info("Skipping PyInstaller build (--skip-build)")
        if not DIST_DIR.exists():
            error(f"Dist directory not found: {DIST_DIR}")
            return 1

    # --- Sign build output ---
    if can_sign and signtool:
        if not sign_build(signtool):
            return 1
    elif not args.no_sign:
        warning("Skipping signing (no certificate or signtool)")

    # --- Build NSIS installer ---
    installer_path = None
    if makensis and not args.no_installer:
        installer_path = build_nsis_installer(makensis)
        if not installer_path:
            warning("Installer build failed, but portable build is available")
    else:
        if not args.no_installer:
            info("Skipping installer (NSIS not available)")

    # --- Sign installer ---
    if installer_path and can_sign and signtool:
        sign_installer(installer_path, signtool)

    # --- Verify ---
    if can_sign and signtool:
        verify_build(signtool)

    # --- Summary ---
    header("Build Complete")

    portable = DIST_DIR / "alpha-osk.exe"
    if portable.exists():
        size = portable.stat().st_size / 1024 / 1024
        success(f"Portable: {portable} ({size:.1f} MB)")

    if installer_path and installer_path.exists():
        size = installer_path.stat().st_size / 1024 / 1024
        success(f"Installer: {installer_path} ({size:.1f} MB)")

    if can_sign:
        success("All outputs are EV code-signed")
    else:
        warning("Outputs are UNSIGNED (dev build)")

    print()
    info("Next steps:")
    if not can_sign:
        info("  • Plug in eToken and re-run without --no-sign for a signed release")
    if installer_path:
        info(f"  • Test installer: {installer_path}")
    info(f"  • Test portable: {portable}")
    info("  • After installing to Program Files, verify UIAccess works")
    info("    (type into an elevated Command Prompt)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
