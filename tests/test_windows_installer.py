"""The Windows installer must not take the user's settings with it.

Every user setting (theme, layout, panels, opacity, window size, the lot)
lives in Qt's settings layer, which on Windows is the registry key
``HKCU\\Software\\<organization>``. The uninstaller used to delete that key
from its Uninstall section unconditionally, and the Install section runs the
previous version's uninstaller silently before extracting, so **every**
reinstall and every auto-update reset the whole application to defaults.

Two things make that easy to reintroduce, which is why it is pinned here
rather than left to a comment:

- Registry keys are case-insensitive, so the key was spelled from
  ``${APP_NAME}`` ("Alpha-OSK") and still resolved to the organisation key
  ("alpha-osk") and took the whole tree under it.
- The deletion reads as ordinary uninstaller hygiene. It is only wrong
  because an *upgrade* runs the uninstaller, which is not visible from the
  line itself.

The paired inverse matters as much: a rule that simply never deleted the key
would leak a settings tree past an uninstall the user explicitly asked to
clean up, so the interactive branch is asserted too.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_PY = REPO_ROOT / "build" / "windows" / "build.py"
INSTALLER_NSH = REPO_ROOT / "build" / "windows" / "installer.nsh"


@pytest.fixture(scope="module")
def nsi() -> str:
    """The generated .nsi script, exactly as makensis would see it."""
    spec = importlib.util.spec_from_file_location("_alpha_osk_build", BUILD_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["_alpha_osk_build"] = module
    try:
        spec.loader.exec_module(module)
        return module._generate_nsi_script("9.9.9", "Alpha-OSK-Setup-9.9.9")
    finally:
        sys.modules.pop("_alpha_osk_build", None)


@pytest.fixture(scope="module")
def nsh() -> str:
    return INSTALLER_NSH.read_text(encoding="utf-8")


def _uninstall_section(nsi: str) -> str:
    body = nsi.split('Section "Uninstall"', 1)
    assert len(body) == 2, "no Uninstall section in the generated script"
    return body[1].split("SectionEnd", 1)[0]


def _expand_defines(nsi: str, text: str) -> str:
    """Resolve ``${NAME}`` against the script's own ``!define``s.

    Comparing the raw line is not enough, and that is the whole point: the
    bug shipped as ``DeleteRegKey HKCU "Software\\${APP_NAME}"``, which does
    not mention the organisation anywhere and still deletes its key.
    """
    defines = dict(re.findall(r'!define (\w+) "([^"]*)"', nsi))
    for _ in range(4):  # defines can reference defines
        expanded = re.sub(r"\$\{(\w+)\}", lambda m: defines.get(m.group(1), m.group(0)), text)
        if expanded == text:
            break
        text = expanded
    return text


def _org_name() -> str:
    """The organisation Qt actually files the settings under."""
    source = (REPO_ROOT / "src" / "keyboard_app.py").read_text(encoding="utf-8")
    match = re.search(r'setOrganizationName\(\s*"([^"]+)"\s*\)', source)
    assert match, "src/keyboard_app.py no longer sets an organisation name"
    return match.group(1)


class TestTheSettingsKeySurvivesAnUpgrade:
    def test_the_uninstall_section_does_not_delete_it(self, nsi: str) -> None:
        section = _uninstall_section(nsi)
        org = _org_name()
        for line in section.splitlines():
            stripped = line.strip()
            if not stripped.startswith("DeleteRegKey"):
                continue
            key = stripped.split('"')[1] if '"' in stripped else stripped
            # Defines expanded and compared case-insensitively, because that
            # is how the registry reads it and how this bug shipped: the key
            # was spelled from ${APP_NAME}, which mentions neither the
            # organisation nor its casing.
            key = _expand_defines(nsi, key)
            assert key.lower() != f"software\\{org}".lower(), (
                "the Uninstall section deletes the Qt settings key, which the "
                "Install section's silent same-directory cleanup runs on every "
                "upgrade: this wipes every user setting on reinstall"
            )

    def test_the_add_remove_entry_is_still_cleaned_up(self, nsi: str) -> None:
        # The inverse: an uninstaller that deleted no registry key at all
        # would satisfy the case above and leave a dead Add/Remove Programs
        # entry pointing at a directory that no longer exists.
        section = _uninstall_section(nsi)
        assert "DeleteRegKey HKCU" in section
        assert "CurrentVersion\\Uninstall" in section

    def test_the_org_define_matches_the_running_application(self, nsi: str) -> None:
        # The key is only removable correctly if the installer and the app
        # agree on where it is.
        match = re.search(r'!define APP_ORG "([^"]+)"', nsi)
        assert match, "the generated script has no APP_ORG define"
        assert match.group(1) == _org_name()


class TestTheInteractiveUninstallStillOffersToRemoveIt:
    def test_it_is_deleted_behind_the_prompt(self, nsh: str) -> None:
        macro = nsh.split("!macro customUnInstall", 1)[1].split("!macroend", 1)[0]
        assert 'DeleteRegKey HKCU "Software\\${APP_ORG}"' in macro, (
            "an interactive uninstall the user answered yes to should still remove their settings"
        )

    def test_a_silent_uninstall_skips_the_whole_branch(self, nsh: str) -> None:
        # An upgrade runs this with /S, and that is the only thing standing
        # between the user's settings and the same wipe as before.
        macro = nsh.split("!macro customUnInstall", 1)[1].split("!macroend", 1)[0]
        lines = [ln.strip() for ln in macro.splitlines()]
        silent = next(i for i, ln in enumerate(lines) if ln.startswith("IfSilent "))
        skip_label = lines[silent].split()[1].rstrip(":")
        deletion = next(i for i, ln in enumerate(lines) if ln.startswith("DeleteRegKey"))
        appdata = next(i for i, ln in enumerate(lines) if ln.startswith("RMDir /r"))
        label = next(i for i, ln in enumerate(lines) if ln == f"{skip_label}:")
        assert silent < deletion < label, "the settings deletion is not behind IfSilent"
        assert silent < appdata < label, "the AppData removal is not behind IfSilent"
