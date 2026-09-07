"""The Windows installer: the user's settings, and the research-invite page.

Two independent things this generated script has to get right, tested
together because they live in the same file and one is a near-miss for the
other.

The settings half: the uninstaller must not take the user's settings with
it. It used to delete the Qt organisation key from its Uninstall section
unconditionally, and the Install section runs the previous version's
uninstaller with /S before extracting, so every upgrade silently reset the
theme, layout, panels and window size. Registry keys are case-insensitive,
so the bug never had to mention the organisation by name to destroy its
key, which is why the assertions here expand the !defines before comparing
rather than matching the source line.

The invite half: the study-participation page must ship its checkbox
unchecked, must seed HKLM\\Software\\alpha-osk-setup and never the bare
organisation key next door, and must not run at all during a silent
install, which is what the auto-updater drives.
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


def _expand_defines(nsi_text: str, text: str) -> str:
    """Resolve ``${NAME}`` against the script's own ``!define``s.

    Comparing the raw line is not enough: the near-miss this guards
    against is a key spelled from ``${APP_NAME}`` (or some other define)
    that does not mention "alpha-osk" in the source at all and still
    resolves to it once NSIS expands the define.
    """
    defines = dict(re.findall(r'!define (\w+) "([^"]*)"', nsi_text))
    for _ in range(4):  # defines can reference defines
        expanded = re.sub(r"\$\{(\w+)\}", lambda m: defines.get(m.group(1), m.group(0)), text)
        if expanded == text:
            break
        text = expanded
    return text


def _pages_block(nsi_text: str) -> str:
    body = nsi_text.split("; --- Pages ---", 1)
    assert len(body) == 2, "no Pages block in the generated script"
    return body[1].split("; --- Uninstaller pages ---", 1)[0]


def _install_section(nsi_text: str) -> str:
    body = nsi_text.split('Section "Install"', 1)
    assert len(body) == 2, "no Install section in the generated script"
    return body[1].split("SectionEnd", 1)[0]


def _function_body(nsi_text: str, name: str) -> str:
    marker = f"Function {name}\n"
    body = nsi_text.split(marker, 1)
    assert len(body) == 2, f"no Function {name} in the generated script"
    return body[1].split("FunctionEnd", 1)[0]


def _uninstall_section(nsi: str) -> str:
    body = nsi.split('Section "Uninstall"', 1)
    assert len(body) == 2, "no Uninstall section in the generated script"
    return body[1].split("SectionEnd", 1)[0]


def _org_name() -> str:
    """The organisation Qt actually files the settings under."""
    source = (REPO_ROOT / "src" / "keyboard_app.py").read_text(encoding="utf-8")
    match = re.search(r'setOrganizationName\(\s*"([^"]+)"\s*\)', source)
    assert match, "src/keyboard_app.py no longer sets an organisation name"
    return match.group(1)


class TestTheInviteePageIsPlacedRight:
    """The page must run after shortcut options and before file copy."""

    def test_the_page_is_registered(self, nsi: str) -> None:
        pages = _pages_block(nsi)
        assert "Page custom StudyInvitePage StudyInviteLeave" in pages

    def test_it_sits_between_the_shortcut_page_and_instfiles(self, nsi: str) -> None:
        pages = _pages_block(nsi)
        shortcut = pages.find("Page custom ShortcutOptionsPage ShortcutOptionsLeave")
        invite = pages.find("Page custom StudyInvitePage StudyInviteLeave")
        instfiles = pages.find("MUI_PAGE_INSTFILES")
        assert shortcut != -1 and invite != -1 and instfiles != -1
        assert shortcut < invite < instfiles

    def test_the_page_content_names_what_is_sent(self, nsi: str) -> None:
        body = _function_body(nsi, "StudyInvitePage")
        assert "Help improve Alpha-OSK" in body
        assert "Ten numbers, once a week" in body
        assert "Never the words you type" in body
        assert "Read more about the study" in body


class TestTheCheckboxShipsUnchecked:
    """The single most important guard in this file.

    A pre-ticked consent checkbox is not consent: it is the one thing
    that would make the collected data unusable as research (participants
    never chose to be in the study) and non-compliant as a privacy
    control (opt-in telemetry that is opted in by default is not opt-in).
    This test fails if anyone "fixes" the checkbox to default to checked,
    which is the exact regression it exists to catch.
    """

    def test_no_set_state_checked_call_touches_the_checkbox(self, nsi: str) -> None:
        body = _function_body(nsi, "StudyInvitePage")
        checkbox_line = next(line for line in body.splitlines() if "NSD_CreateCheckbox" in line)
        assert "share anonymous usage statistics" in checkbox_line.lower()
        # Everything after the checkbox is created, up to the next Pop,
        # is where a SetState call for it would live.
        after = body.split(checkbox_line, 1)[1]
        after = after.split("Pop $StudyInviteCheckboxHwnd", 1)[1]
        next_control = after.split("${NSD_Create", 1)[0]
        assert "NSD_SetState" not in next_control, (
            "the invite checkbox must default to UNCHECKED -- do not add a "
            "${NSD_SetState} ... ${BST_CHECKED} call for it"
        )

    def test_no_set_state_checked_call_anywhere_in_the_page_function(self, nsi: str) -> None:
        # Belt and suspenders: even a misplaced SetState elsewhere in the
        # same function would still tick the box by the time it shows.
        # Comment lines are excluded -- the surrounding "don't do this"
        # warning names the very call this test forbids.
        body = _function_body(nsi, "StudyInvitePage")
        code_lines = [ln for ln in body.splitlines() if not ln.strip().startswith(";")]
        assert "NSD_SetState" not in "\n".join(code_lines)


class TestTheRegistrySeed:
    def test_written_to_the_setup_key_not_the_organisation_key(self, nsi: str) -> None:
        section = _install_section(nsi)
        seed_lines = [
            _expand_defines(nsi, line.strip())
            for line in section.splitlines()
            if "WriteRegStr HKLM" in line and "Invite" in line
        ]
        assert seed_lines, "no HKLM Invite write found in the Install section"
        for line in seed_lines:
            assert '"Software\\alpha-osk-setup"' in line

    def test_no_write_targets_the_bare_organisation_key(self, nsi: str) -> None:
        # The near-miss: colliding with "alpha-osk" (the Qt settings
        # organisation, see keyboard_app.py's setOrganizationName) would
        # repeat the exact uninstall bug CLAUDE.md documents, one rename
        # away. Checked against the expanded text, not the raw source,
        # because that bug's own spelling never mentioned the
        # organisation either.
        expanded = _expand_defines(nsi, nsi)
        assert 'HKLM "Software\\alpha-osk"' not in expanded
        assert "HKLM 'Software\\alpha-osk'" not in expanded

    def test_only_written_when_the_page_actually_set_a_value(self, nsi: str) -> None:
        section = _install_section(nsi)
        # Find the actual write, not the earlier explanatory comment that
        # also mentions the key name.
        idx = section.find('WriteRegStr HKLM "Software\\alpha-osk-setup"')
        assert idx != -1
        guard = section[:idx].rsplit("${If}", 1)
        assert len(guard) == 2, "the seed write is not inside an ${If} guard"
        assert "$StudyInvite" in guard[1]


class TestOnInitStartsEmpty:
    def test_study_invite_defaults_to_empty(self, nsi: str) -> None:
        body = _function_body(nsi, ".onInit")
        assert 'StrCpy $StudyInvite ""' in body

    def test_the_var_is_declared(self, nsi: str) -> None:
        assert "Var StudyInvite" in nsi


class TestTheUninstallerRemovesTheSeedKeyOnlyWhenInteractive:
    """A silent uninstall is what an auto-update runs (see
    src/updater.py::_launch_installer, which always passes /S), so the
    seed key removal must live behind the same guard the %APPDATA%
    removal already uses, or every upgrade would erase it.
    """

    def test_the_seed_key_is_removed_in_customuninstall(self, nsh: str) -> None:
        macro = nsh.split("!macro customUnInstall", 1)[1].split("!macroend", 1)[0]
        assert 'DeleteRegKey HKLM "Software\\alpha-osk-setup"' in macro

    def test_the_removal_sits_inside_the_ifsilent_guard(self, nsh: str) -> None:
        macro = nsh.split("!macro customUnInstall", 1)[1].split("!macroend", 1)[0]
        lines = [ln.strip() for ln in macro.splitlines()]
        silent = next(i for i, ln in enumerate(lines) if ln.startswith("IfSilent "))
        skip_label = lines[silent].split()[1].rstrip(":")
        deletion = next(i for i, ln in enumerate(lines) if ln.startswith("DeleteRegKey HKLM"))
        label = next(i for i, ln in enumerate(lines) if ln == f"{skip_label}:")
        assert silent < deletion < label, (
            "the seed-key deletion is not behind IfSilent, so a silent "
            "upgrade uninstall would erase it"
        )


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
