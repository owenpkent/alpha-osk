"""The Windows installer: settings, the research-invite page, the close prompt.

Three independent things this generated script has to get right, tested
together because they live in the same file and the first two are near-misses
for each other.

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

The close-prompt half: "Alpha-OSK is currently running, close it?" must be
asked over a window the user can see. It used to be raised from .onInit,
which runs before the installer window is created, so it was ownerless,
BringToFront had nothing to act on, and the box could sit behind whatever the
user was looking at with no wizard on screen to explain the wait.
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


def _macro_code(nsh_text: str, name: str) -> str:
    """One macro's body with its ``;`` comments stripped.

    These macros are heavily commented, and every word the assertions below
    look for (MessageBox, BringToFront, Abort, taskkill) appears in the
    comments explaining why it is or is not there. Matching the raw text
    means a test can pass on the prose while the code says the opposite.
    """
    body = nsh_text.split(f"!macro {name}", 1)
    assert len(body) == 2, f"no !macro {name} in installer.nsh"
    body = body[1].split("!macroend", 1)[0]

    lines = []
    for line in body.splitlines():
        in_string = False
        for i, ch in enumerate(line):
            if ch == '"':
                in_string = not in_string
            elif ch == ";" and not in_string:
                line = line[:i]
                break
        if line.strip():
            lines.append(line.rstrip())
    return "\n".join(lines)


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
        """Pins the page's four jobs, not its exact wording.

        This used to assert the literal opening sentence, which made an
        ordinary copy edit look like a regression while saying nothing about
        whether the page still explains itself. TestTheStudyPageSaysTheseAreCounts
        below covers the one claim the wording actually has to make.
        """
        body = _function_body(nsi, "StudyInvitePage")
        assert "Help improve Alpha-OSK" in body
        assert "ten numbers" in body.lower()
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


# The page area inside nsDialogs::Create 1018 is exactly this tall. Measured
# from the live dialog: the checkbox that shipped at 128u..140u had its bottom
# edge exactly on the parent's, and the link at 144u..156u was clipped 39px
# past it. A control placed below this is silently invisible -- Windows clips a
# child to its parent, but the control still reports itself visible, so nothing
# fails and nobody notices until they look at a screenshot.
PAGE_AREA_UNITS = 140

_NSD_CONTROL = re.compile(r"\$\{NSD_Create(\w+)\}\s+\S+\s+(\d+)u\s+\S+\s+(\d+)u\s+\"([^\"]*)\"")


def _controls(nsi_text: str, function_name: str):
    """(kind, top, height, text) for every control a page function creates."""
    body = _function_body(nsi_text, function_name)
    return [
        (m.group(1), int(m.group(2)), int(m.group(3)), m.group(4))
        for m in _NSD_CONTROL.finditer(body)
    ]


class TestTheStudyPageFitsItsDialog:
    """Every control has to end inside the page area or it never draws.

    This is the regression guard for a real defect: "Read more about the
    study" shipped at 144u, 39px below the page area, and was invisible in
    the installer while every text-matching test passed.
    """

    def test_the_page_creates_the_controls_it_is_supposed_to(self, nsi: str) -> None:
        kinds = [c[0] for c in _controls(nsi, "StudyInvitePage")]
        assert "Checkbox" in kinds, "the consent checkbox is gone"
        assert "Link" in kinds, "the read-more link is gone"
        assert kinds.count("Label") >= 3, f"expected the explanatory labels, got {kinds}"

    def test_no_control_ends_past_the_page_area(self, nsi: str) -> None:
        overflowing = [
            (kind, top, height, text[:40])
            for kind, top, height, text in _controls(nsi, "StudyInvitePage")
            if top + height > PAGE_AREA_UNITS
        ]
        assert not overflowing, (
            f"these end past {PAGE_AREA_UNITS}u and will be clipped invisible: {overflowing}"
        )

    def test_the_read_more_link_is_well_inside_the_page(self, nsi: str) -> None:
        """Named separately from the sweep above: this is the control that
        actually shipped broken, so it gets an assertion that says so."""
        link = [c for c in _controls(nsi, "StudyInvitePage") if c[0] == "Link"]
        assert len(link) == 1, f"expected exactly one link, got {link}"
        _, top, height, _ = link[0]
        assert top + height <= PAGE_AREA_UNITS, (
            f"the read-more link ends at {top + height}u, past the {PAGE_AREA_UNITS}u "
            "page area, so it is clipped and never renders"
        )

    def test_controls_do_not_overlap_each_other(self, nsi: str) -> None:
        """A control laid out on top of another hides it just as thoroughly."""
        ordered = sorted(_controls(nsi, "StudyInvitePage"), key=lambda c: c[1])
        for (k1, t1, h1, x1), (k2, t2, _, x2) in zip(ordered, ordered[1:]):
            assert t1 + h1 <= t2, (
                f"{k1} {x1[:30]!r} ({t1}u..{t1 + h1}u) overlaps {k2} {x2[:30]!r} starting at {t2}u"
            )


class TestTheCustomPagesSetTheirOwnHeader:
    """A custom page inherits the previous MUI page's header unless it sets one.

    Both of these read "Choose Install Location" over unrelated content until
    the MUI_HEADER_TEXT calls were added.
    """

    @pytest.mark.parametrize("function_name", ["ShortcutOptionsPage", "StudyInvitePage"])
    def test_the_page_sets_a_header(self, nsi: str, function_name: str) -> None:
        body = _function_body(nsi, function_name)
        assert "MUI_HEADER_TEXT" in body, (
            f"{function_name} sets no header, so it inherits the previous page's"
        )

    @pytest.mark.parametrize("function_name", ["ShortcutOptionsPage", "StudyInvitePage"])
    def test_the_header_is_set_before_the_dialog_is_created(
        self, nsi: str, function_name: str
    ) -> None:
        """MUI_HEADER_TEXT after nsDialogs::Show is never reached: Show blocks
        until the user leaves the page."""
        body = _function_body(nsi, function_name)
        assert body.index("MUI_HEADER_TEXT") < body.index("nsDialogs::Show")

    def test_neither_header_still_says_choose_install_location(self, nsi: str) -> None:
        """The inverse: a header that merely exists but repeats the directory
        page's text would satisfy the assertions above and fix nothing."""
        for function_name in ("ShortcutOptionsPage", "StudyInvitePage"):
            body = _function_body(nsi, function_name)
            header = re.search(r'MUI_HEADER_TEXT\s+"([^"]*)"', body)
            assert header, f"{function_name} has no header text"
            assert "install location" not in header.group(1).lower()


class TestTheStudyPageSaysTheseAreCounts:
    """The numbers are totals, not content, and the page has to say so.

    The first wording opened with a bare list ("keystrokes, words, ...") that
    read as though the typed text itself is sent. That is the single most
    important thing this page communicates, so it is pinned.
    """

    def test_it_says_the_numbers_are_totals_or_counts(self, nsi: str) -> None:
        text = " ".join(c[3].lower() for c in _controls(nsi, "StudyInvitePage"))
        assert "total" in text or "count" in text, (
            "the page never says the ten numbers are counts, so a bare list of "
            "'keystrokes, words' reads as though the text is sent"
        )

    def test_it_says_nothing_typed_is_included(self, nsi: str) -> None:
        text = " ".join(c[3].lower() for c in _controls(nsi, "StudyInvitePage"))
        assert "never the words you type" in text
        assert "anything you typed" in text or "nothing you type" in text


class TestTheCloseAppPromptIsAskedOverAVisibleWindow:
    """The reported bug: the close confirmation appeared behind other windows.

    Its cause was where it was asked, not how it was worded. ``.onInit``
    runs before NSIS creates the installer window, so ``$HWNDPARENT`` is
    still 0: a MessageBox raised there has no owner and no window on screen
    to draw the eye, and ``BringToFront`` is a no-op. The prompt now belongs
    to the INSTFILES page's pre-function, which runs over a window that is
    up and in front.
    """

    def test_on_init_raises_no_message_box(self, nsh: str) -> None:
        """The whole of the fix. An ownerless box is the bug."""
        assert "MessageBox" not in _macro_code(nsh, "customInit")

    def test_the_prompt_still_exists(self, nsh: str) -> None:
        """The inverse: deleting the prompt would satisfy the test above."""
        macro = _macro_code(nsh, "customCloseRunningApp")
        assert "MessageBox" in macro
        assert "Alpha-OSK is currently running" in macro

    def test_it_is_hooked_to_the_page_that_writes_the_files(self, nsi: str) -> None:
        pages = _pages_block(nsi)
        hook = "!define MUI_PAGE_CUSTOMFUNCTION_PRE ConfirmCloseRunningApp"
        assert hook in pages
        # Immediately before INSTFILES: MUI applies a page define to the next
        # page it is given, so the line it sits in front of is the whole of
        # what decides when this runs.
        after = pages.split(hook, 1)[1].strip().splitlines()
        assert after[0] == "!insertmacro MUI_PAGE_INSTFILES"

    def test_the_hook_function_defers_to_the_macro(self, nsi: str) -> None:
        body = _function_body(nsi, "ConfirmCloseRunningApp")
        assert "!insertmacro customCloseRunningApp" in body

    def test_the_box_asks_for_the_foreground_as_well(self, nsh: str) -> None:
        """Belt and braces over the owner window.

        MB_SETFOREGROUND asks; MB_TOPMOST keeps the box above non-topmost
        windows even where Windows refuses to hand the foreground over.
        """
        macro = _macro_code(nsh, "customCloseRunningApp")
        line = next(ln for ln in macro.splitlines() if "MessageBox" in ln)
        assert "MB_TOPMOST" in line
        assert "MB_SETFOREGROUND" in line

    def test_cancel_quits_rather_than_aborting(self, nsh: str) -> None:
        """The near-miss with teeth.

        ``Abort`` in a page pre-function skips the page, not the install, so
        cancelling would have walked on to the finish page reporting success
        over an install that never ran. Only ``Quit`` stops the installer.
        """
        macro = _macro_code(nsh, "customCloseRunningApp")
        assert "Quit" in macro.split()
        assert "Abort" not in macro


class TestTheSilentInstallStillClosesTheRunningApp:
    """The auto-updater's path, which has no window and must not grow a prompt.

    ``src/updater.py`` drives ``/S`` and relies on the installer closing the
    running keyboard before the files under it are replaced. Moving the
    prompt out of .onInit must not take the kill with it.
    """

    def test_on_init_closes_it_behind_a_silent_guard(self, nsh: str) -> None:
        macro = _macro_code(nsh, "customInit")
        assert "IfSilent 0 skipSilentClose" in macro
        guarded = macro.split("IfSilent 0 skipSilentClose", 1)[1]
        assert "Call CloseAlphaOsk" in guarded.split("skipSilentClose:", 1)[0]

    def test_an_interactive_install_closes_nothing_there(self, nsh: str) -> None:
        """The inverse: an unguarded close would satisfy the test above.

        Interactively the app must survive .onInit, or a user who cancels at
        any page has already lost the keyboard they were typing with.
        """
        macro = _macro_code(nsh, "customInit")
        before_guard = macro.split("IfSilent 0 skipSilentClose", 1)[0]
        assert "CloseAlphaOsk" not in before_guard

    def test_the_prompt_stands_down_when_silent(self, nsh: str) -> None:
        macro = _macro_code(nsh, "customCloseRunningApp")
        assert "IfSilent skipClosePrompt" in macro
        assert "skipClosePrompt:" in macro


class TestTheInstallerBringsItselfToTheFront:
    """The same fault one window over, and the reason the comment was wrong.

    ``BringToFront`` acts on ``$HWNDPARENT``, so calling it from .onInit did
    nothing at all, including the thing its comment claimed: rescuing the
    wizard from behind another window after UAC elevation.
    """

    def test_it_is_not_called_before_the_window_exists(self, nsh: str) -> None:
        assert "BringToFront" not in _macro_code(nsh, "customInit")

    def test_it_is_called_from_gui_init(self, nsi: str, nsh: str) -> None:
        assert "BringToFront" in _macro_code(nsh, "customGuiInit")
        assert "!insertmacro customGuiInit" in _function_body(nsi, "AlphaOskGuiInit")

    def test_mui_is_told_to_call_it(self, nsi: str) -> None:
        """And told before MUI_LANGUAGE, which is what emits .onGUIInit.

        Defined afterwards it compiles cleanly and is simply never called.
        """
        define = "!define MUI_CUSTOMFUNCTION_GUIINIT AlphaOskGuiInit"
        assert define in nsi
        assert nsi.index(define) < nsi.index('!insertmacro MUI_LANGUAGE "English"')


class TestTheAppIsAskedToCloseBeforeItIsForced:
    """``taskkill /F`` is TerminateProcess: nothing in the app gets to run.

    Alpha-OSK writes the learned vocabulary and the analytics counters from
    ``aboutToQuit`` (``keyboard_app.py``), and releases any OS-held modifier
    from ``KeyboardBridge.shutdown``. Forcing the process skipped all of it,
    on every automatic update, so each one cost whatever had been learned
    since the last save. ``taskkill`` without ``/F`` posts WM_CLOSE instead,
    which a window carrying this app's flags answers in well under a second.

    The forced kill stays as the fallback: a wedged process must not be able
    to block the install for ever.
    """

    def test_the_polite_kill_comes_first(self, nsh: str) -> None:
        macro = _macro_code(nsh, "customCloseAlphaOsk")
        kills = [ln.strip() for ln in macro.splitlines() if "taskkill" in ln]
        assert len(kills) == 2, kills
        assert "/F" not in kills[0]
        assert "/F /IM" in kills[1]

    def test_it_waits_for_the_exit_rather_than_guessing(self, nsh: str) -> None:
        """A flat Sleep would be either too short to save or slow every update.

        The loop leaves as soon as the process is gone, which is why the
        common path is quicker than the 1.5 s sleep it replaces.
        """
        macro = _macro_code(nsh, "customCloseAlphaOsk")
        wait = macro.split("waitForAppExit:", 1)
        assert len(wait) == 2, "no wait loop"
        loop = wait[1]
        assert "Call AlphaOskIsRunning" in loop
        assert "Goto appClosed" in loop
        assert "Goto waitForAppExit" in loop

    def test_the_force_is_still_there_as_a_fallback(self, nsh: str) -> None:
        """The inverse: dropping it would satisfy the ordering test above.

        Without it a process that never answers WM_CLOSE holds its own files
        open and the install fails on a locked exe instead.
        """
        macro = _macro_code(nsh, "customCloseAlphaOsk")
        after_loop = macro.split("Goto waitForAppExit", 1)[1]
        assert "taskkill /F /IM" in after_loop

    def test_both_close_paths_go_through_the_one_function(self, nsh: str) -> None:
        """Parallel copies of a kill are how the two paths drift apart.

        The silent path is the one that runs on every update and the
        interactive one is the only one anybody watches, so a fix applied to
        whichever was being read at the time would miss the other.
        """
        for name in ("customInit", "customCloseRunningApp"):
            macro = _macro_code(nsh, name)
            assert "Call CloseAlphaOsk" in macro, name
            assert "taskkill" not in macro, name

    def test_the_running_check_has_one_definition(self, nsi: str, nsh: str) -> None:
        assert "tasklist" in _macro_code(nsh, "customAlphaOskIsRunning")
        for name in ("customInit", "customCloseRunningApp", "customCloseAlphaOsk"):
            macro = _macro_code(nsh, name)
            assert "tasklist" not in macro, name
            assert "Call AlphaOskIsRunning" in macro, name

    def test_the_helpers_are_functions_not_macros_at_the_call_sites(self, nsi: str) -> None:
        """A macro inserted at both call sites would declare its labels twice.

        Which is a compile error rather than a silent fault, but it is the
        reason these two are shaped differently from every other custom hook
        in installer.nsh, so it is worth pinning.
        """
        assert "!insertmacro customAlphaOskIsRunning" in _function_body(nsi, "AlphaOskIsRunning")
        assert "!insertmacro customCloseAlphaOsk" in _function_body(nsi, "CloseAlphaOsk")
        assert nsi.count("!insertmacro customCloseAlphaOsk") == 1
        assert nsi.count("!insertmacro customAlphaOskIsRunning") == 1
