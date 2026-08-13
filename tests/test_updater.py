"""Tests for the auto-updater — focused on the security boundary.

The updater's job is to refuse to install anything that isn't a real,
signed Alpha-OSK release.  These tests pin the refusal paths: anything
that lets a downgrade, off-host download, or unsigned installer through
is a regression that ships arbitrary code on every user's machine.
"""

from __future__ import annotations

import json
import subprocess
import sys
from unittest import mock

import pytest

from src import updater
from src.updater import (
    EV_CERT_SHA1_THUMBPRINT,
    UpdateInfo,
    _file_version_matches,
    _install_target_dir,
    _is_safe_download_url,
    _parse_version,
    _ps_single_quote_escape,
    _sanitize_notes,
    check_for_update,
    is_newer,
)


class TestVersionComparison:
    """Strict semver — string compare would let 1.0.10 lose to 1.0.2."""

    def test_basic_newer(self):
        assert is_newer("1.0.3", "1.0.2")

    def test_basic_older(self):
        assert not is_newer("1.0.1", "1.0.2")

    def test_equal_is_not_newer(self):
        assert not is_newer("1.0.2", "1.0.2")

    def test_double_digit_patch_beats_single_digit(self):
        # The classic string-sort bug — "1.0.10" sorts before "1.0.2"
        # lexicographically.  Must compare numerically.
        assert is_newer("1.0.10", "1.0.2")

    def test_major_bump(self):
        assert is_newer("2.0.0", "1.99.99")

    def test_v_prefix_accepted(self):
        assert is_newer("v1.0.3", "1.0.2")
        assert is_newer("V1.0.3", "1.0.2")

    def test_prerelease_tag_refused(self):
        # Refusing pre-release tags blocks attackers from publishing
        # "1.0.3-evil" and having it satisfy a "newer than 1.0.2" check.
        assert not is_newer("1.0.3-rc1", "1.0.2")
        assert not is_newer("1.0.3+meta", "1.0.2")

    def test_garbage_tag_refused(self):
        assert not is_newer("definitely-not-a-version", "1.0.2")
        assert not is_newer("", "1.0.2")
        assert not is_newer("../../etc/passwd", "1.0.2")

    def test_garbage_baseline_refused(self):
        # A corrupted current-version string must not be treated as 0.0.0
        # (which would let any release "upgrade" us).  Refuse instead.
        assert not is_newer("1.0.3", "garbage")

    def test_parse_strips_only_leading_v(self):
        assert _parse_version("v1.2.3") == (1, 2, 3)
        assert _parse_version("1.2.3") == (1, 2, 3)
        assert _parse_version("1.2") is None
        assert _parse_version("1.2.3.4") is None


class TestDownloadUrlWhitelist:
    """Off-whitelist URLs must be rejected, even with valid HTTPS."""

    def test_github_com_allowed(self):
        assert _is_safe_download_url(
            "https://github.com/okstudio1/alpha-osk-releases/releases/download/v1.0.3/Alpha-OSK-Setup-1.0.3.exe"
        )

    def test_github_cdn_allowed(self):
        # Both historical and current CDN hostnames are accepted —
        # GitHub redirects release-asset downloads to the latter today
        # but older URLs / mirrors still use the former.
        assert _is_safe_download_url("https://objects.githubusercontent.com/foo/bar.exe")
        assert _is_safe_download_url(
            "https://release-assets.githubusercontent.com/github-production-release-asset/foo?sig=abc"
        )

    def test_attacker_host_rejected(self):
        assert not _is_safe_download_url("https://evil.example.com/setup.exe")

    def test_subdomain_attack_rejected(self):
        # An attacker registers github.com.evil.example to fool a naive
        # endswith check.  Hostname comparison is exact.
        assert not _is_safe_download_url("https://github.com.evil.example/setup.exe")
        assert not _is_safe_download_url("https://evil-github.com/setup.exe")

    def test_http_rejected(self):
        # No plaintext, even to GitHub.
        assert not _is_safe_download_url(
            "http://github.com/okstudio1/alpha-osk-releases/releases/download/v1.0.3/Alpha-OSK-Setup-1.0.3.exe"
        )

    def test_other_schemes_rejected(self):
        assert not _is_safe_download_url("file:///C:/Windows/System32/calc.exe")
        assert not _is_safe_download_url("ftp://github.com/setup.exe")

    def test_garbage_url_rejected(self):
        assert not _is_safe_download_url("not a url at all")
        assert not _is_safe_download_url("")


class TestNotesSanitisation:
    """Release notes flow into QML; control chars and excess length out."""

    def test_strips_control_chars(self):
        assert _sanitize_notes("hello\x00world\x01") == "helloworld"

    def test_preserves_newlines_and_tabs(self):
        assert _sanitize_notes("line1\nline2\tcol") == "line1\nline2\tcol"

    def test_caps_length(self):
        big = "x" * 5000
        out = _sanitize_notes(big)
        assert len(out) <= updater._MAX_NOTES_LENGTH + 1  # +1 for ellipsis

    def test_non_string_safe(self):
        assert _sanitize_notes(None) == ""
        assert _sanitize_notes(123) == ""


# ---------------------------------------------------------------------------
#  check_for_update
# ---------------------------------------------------------------------------


def _api_response(tag: str, asset_name: str, asset_url: str, notes: str = "release notes") -> bytes:
    return json.dumps(
        {
            "tag_name": tag,
            "body": notes,
            "assets": [
                {"name": asset_name, "browser_download_url": asset_url},
            ],
        }
    ).encode()


@pytest.fixture
def mock_urlopen():
    with mock.patch("src.updater.urllib.request.urlopen") as m:
        yield m


def _stub_response(body: bytes, headers: dict | None = None):
    """Build a context-manager mock that mimics urlopen()'s return."""
    cm = mock.MagicMock()
    cm.__enter__.return_value.read.return_value = body
    cm.__enter__.return_value.headers = headers or {}
    return cm


class TestCheckForUpdate:
    def test_returns_info_for_genuine_newer_release(self, mock_urlopen):
        mock_urlopen.return_value = _stub_response(
            _api_response(
                tag="v1.0.3",
                asset_name="Alpha-OSK-Setup-1.0.3.exe",
                asset_url="https://github.com/okstudio1/alpha-osk-releases/releases/download/v1.0.3/Alpha-OSK-Setup-1.0.3.exe",
            )
        )
        info = check_for_update(current_version="1.0.2")
        assert info is not None
        assert info.version == "1.0.3"
        assert info.asset_name == "Alpha-OSK-Setup-1.0.3.exe"

    def test_returns_none_when_already_latest(self, mock_urlopen):
        mock_urlopen.return_value = _stub_response(
            _api_response(
                tag="v1.0.2",
                asset_name="Alpha-OSK-Setup-1.0.2.exe",
                asset_url="https://github.com/okstudio1/alpha-osk-releases/releases/download/v1.0.2/Alpha-OSK-Setup-1.0.2.exe",
            )
        )
        assert check_for_update(current_version="1.0.2") is None

    def test_returns_none_on_downgrade_attempt(self, mock_urlopen):
        # Server claims latest is older than us.  Do not "update" backwards.
        mock_urlopen.return_value = _stub_response(
            _api_response(
                tag="v1.0.0",
                asset_name="Alpha-OSK-Setup-1.0.0.exe",
                asset_url="https://github.com/okstudio1/alpha-osk-releases/releases/download/v1.0.0/Alpha-OSK-Setup-1.0.0.exe",
            )
        )
        assert check_for_update(current_version="1.0.2") is None

    def test_rejects_off_host_asset(self, mock_urlopen):
        # Newer version, but the download URL points off-whitelist.
        mock_urlopen.return_value = _stub_response(
            _api_response(
                tag="v1.0.3",
                asset_name="Alpha-OSK-Setup-1.0.3.exe",
                asset_url="https://evil.example.com/Alpha-OSK-Setup-1.0.3.exe",
            )
        )
        assert check_for_update(current_version="1.0.2") is None

    def test_rejects_misnamed_asset(self, mock_urlopen):
        # Right tag, wrong filename — could be a sneaky asset upload.
        mock_urlopen.return_value = _stub_response(
            _api_response(
                tag="v1.0.3",
                asset_name="totally-legit.exe",
                asset_url="https://github.com/okstudio1/alpha-osk-releases/releases/download/v1.0.3/totally-legit.exe",
            )
        )
        assert check_for_update(current_version="1.0.2") is None

    def test_rejects_pre_release_tag(self, mock_urlopen):
        mock_urlopen.return_value = _stub_response(
            _api_response(
                tag="v1.0.3-evil",
                asset_name="Alpha-OSK-Setup-1.0.3.exe",
                asset_url="https://github.com/okstudio1/alpha-osk-releases/releases/download/v1.0.3/Alpha-OSK-Setup-1.0.3.exe",
            )
        )
        assert check_for_update(current_version="1.0.2") is None

    def test_network_error_returns_none(self, mock_urlopen):
        import urllib.error

        mock_urlopen.side_effect = urllib.error.URLError("nope")
        assert check_for_update(current_version="1.0.2") is None

    def test_malformed_json_returns_none(self, mock_urlopen):
        mock_urlopen.return_value = _stub_response(b"not json {{{{")
        assert check_for_update(current_version="1.0.2") is None

    def test_oversized_response_refused(self, mock_urlopen):
        # API responses larger than 1 MB are dropped — defensive cap so
        # a poisoned response can't burn memory parsing JSON.
        big = b"{" + (b'"x":"y",' * 200_000) + b'"end":1}'
        assert len(big) > updater._MAX_API_RESPONSE_BYTES
        mock_urlopen.return_value = _stub_response(big)
        assert check_for_update(current_version="1.0.2") is None

    def test_refuses_non_github_api_url(self, mock_urlopen):
        # The api_url itself is checked — passing in an attacker URL
        # short-circuits before we even touch the network.
        result = check_for_update(
            current_version="1.0.2",
            api_url="https://evil.example.com/releases/latest",
        )
        assert result is None
        mock_urlopen.assert_not_called()

    def test_refuses_other_github_repo(self, mock_urlopen):
        # The api_url prefix is pinned to our specific releases repo.
        # An attacker who could substitute the URL but only to another
        # *github.com* repo would otherwise be able to swap the upgrade
        # source for one they control.
        result = check_for_update(
            current_version="1.0.2",
            api_url="https://api.github.com/repos/attacker/alpha-osk-releases/releases/latest",
        )
        assert result is None
        mock_urlopen.assert_not_called()


# ---------------------------------------------------------------------------
#  Signature verification
# ---------------------------------------------------------------------------


class TestSignatureVerification:
    """The Authenticode pin is the last line of defence, so pin
    Status==Valid AND the exact thumbprint AND the publisher CN AND
    (see TestEmbeddedVersionPin below) the exe's own embedded
    version."""

    EXPECTED_VERSION = "1.0.3"

    def _ps_output(
        self, status: str, thumbprint: str, cn: str, file_version: str = "1.0.3.0"
    ) -> str:
        return f"{status}|{thumbprint}|{cn}|{file_version}\n"

    @pytest.fixture
    def fake_exe(self, tmp_path):
        p = tmp_path / "installer.exe"
        p.write_bytes(b"MZ\x90\x00")  # bare PE-ish header; content irrelevant
        return p

    def _patch_run(self, monkeypatch, stdout: str, returncode: int = 0):
        def _fake_run(*args, **kwargs):
            return mock.Mock(stdout=stdout, stderr="", returncode=returncode)

        monkeypatch.setattr(updater.subprocess, "run", _fake_run)

    def test_accepts_valid_pinned_signature(self, monkeypatch, fake_exe):
        monkeypatch.setattr(updater.sys, "platform", "win32")
        self._patch_run(
            monkeypatch, self._ps_output("Valid", EV_CERT_SHA1_THUMBPRINT.upper(), "OK Studio Inc.")
        )
        assert updater._verify_signature(fake_exe, self.EXPECTED_VERSION) is True

    def test_rejects_wrong_thumbprint(self, monkeypatch, fake_exe):
        monkeypatch.setattr(updater.sys, "platform", "win32")
        self._patch_run(
            monkeypatch,
            self._ps_output(
                "Valid",
                "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
                "OK Studio Inc.",
            ),
        )
        assert updater._verify_signature(fake_exe, self.EXPECTED_VERSION) is False

    def test_rejects_wrong_signer_cn(self, monkeypatch, fake_exe):
        monkeypatch.setattr(updater.sys, "platform", "win32")
        self._patch_run(monkeypatch, self._ps_output("Valid", EV_CERT_SHA1_THUMBPRINT, "Evil Inc."))
        assert updater._verify_signature(fake_exe, self.EXPECTED_VERSION) is False

    def test_rejects_invalid_status(self, monkeypatch, fake_exe):
        monkeypatch.setattr(updater.sys, "platform", "win32")
        self._patch_run(
            monkeypatch, self._ps_output("HashMismatch", EV_CERT_SHA1_THUMBPRINT, "OK Studio Inc.")
        )
        assert updater._verify_signature(fake_exe, self.EXPECTED_VERSION) is False

    def test_rejects_unsigned(self, monkeypatch, fake_exe):
        monkeypatch.setattr(updater.sys, "platform", "win32")
        self._patch_run(monkeypatch, self._ps_output("NotSigned", "", "", ""))
        assert updater._verify_signature(fake_exe, self.EXPECTED_VERSION) is False

    def test_rejects_powershell_failure(self, monkeypatch, fake_exe):
        monkeypatch.setattr(updater.sys, "platform", "win32")
        self._patch_run(monkeypatch, "", returncode=1)
        assert updater._verify_signature(fake_exe, self.EXPECTED_VERSION) is False

    def test_rejects_on_non_windows(self, monkeypatch, fake_exe):
        monkeypatch.setattr(updater.sys, "platform", "linux")
        # Authenticode isn't a thing here — fail closed instead of
        # blindly running the .exe.
        assert updater._verify_signature(fake_exe, self.EXPECTED_VERSION) is False

    def test_rejects_missing_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr(updater.sys, "platform", "win32")
        assert updater._verify_signature(tmp_path / "nope.exe", self.EXPECTED_VERSION) is False


class TestEmbeddedVersionPin:
    """FIX: the asset filename (`Alpha-OSK-Setup-{version}.exe`) is an
    asset *selector*, not a trust boundary. A genuinely-signed but
    OLDER installer re-served under a NEWER version's filename must be
    rejected even though its Authenticode signature is perfectly
    valid, or a compromised release-publish token (without any
    signing capability) could downgrade every user silently."""

    def _ps_output(self, status: str, thumbprint: str, cn: str, file_version: str) -> str:
        return f"{status}|{thumbprint}|{cn}|{file_version}\n"

    @pytest.fixture
    def fake_exe(self, tmp_path):
        p = tmp_path / "installer.exe"
        p.write_bytes(b"MZ\x90\x00")
        return p

    def _patch_run(self, monkeypatch, stdout: str):
        monkeypatch.setattr(
            updater.subprocess,
            "run",
            lambda *a, **kw: mock.Mock(stdout=stdout, stderr="", returncode=0),
        )

    def test_rejects_genuinely_signed_older_installer(self, monkeypatch, fake_exe):
        # Valid signature, correct thumbprint, correct CN, but the exe
        # itself reports an older FileVersion than the release we asked
        # to install. Must fail closed even though everything else checks out.
        monkeypatch.setattr(updater.sys, "platform", "win32")
        self._patch_run(
            monkeypatch,
            self._ps_output("Valid", EV_CERT_SHA1_THUMBPRINT, "OK Studio Inc.", "1.0.2.0"),
        )
        assert updater._verify_signature(fake_exe, "1.0.3") is False

    def test_accepts_four_part_file_version_matching_first_three(self, monkeypatch, fake_exe):
        # Windows FileVersion is commonly four-part; our semver is three.
        # A matching build/revision suffix must not cause a false reject.
        monkeypatch.setattr(updater.sys, "platform", "win32")
        self._patch_run(
            monkeypatch,
            self._ps_output("Valid", EV_CERT_SHA1_THUMBPRINT, "OK Studio Inc.", "1.0.3.42"),
        )
        assert updater._verify_signature(fake_exe, "1.0.3") is True

    def test_rejects_unparseable_embedded_version(self, monkeypatch, fake_exe):
        monkeypatch.setattr(updater.sys, "platform", "win32")
        self._patch_run(
            monkeypatch,
            self._ps_output("Valid", EV_CERT_SHA1_THUMBPRINT, "OK Studio Inc.", ""),
        )
        assert updater._verify_signature(fake_exe, "1.0.3") is False


class TestFileVersionMatches:
    """Unit coverage for the three-vs-four-part comparison in isolation
    from the PowerShell plumbing."""

    def test_exact_three_part_match(self):
        assert _file_version_matches("1.0.3", "1.0.3") is True

    def test_four_part_matches_three_part_expected(self):
        assert _file_version_matches("1.0.3.0", "1.0.3") is True
        assert _file_version_matches("1.0.3.42", "1.0.3") is True

    def test_mismatched_version_rejected(self):
        assert _file_version_matches("1.0.2.0", "1.0.3") is False

    def test_garbage_file_version_rejected(self):
        assert _file_version_matches("not.a.version", "1.0.3") is False
        assert _file_version_matches("", "1.0.3") is False
        assert _file_version_matches("1.0", "1.0.3") is False


class TestPowerShellSingleQuoteEscaping:
    """FIX: a Windows username containing an apostrophe (O'Brien,
    D'Angelo) produces a %TEMP% path that terminates the single-quoted
    PowerShell literal `_verify_signature` builds early, which fails
    closed but permanently and silently disables auto-update for that
    user. The fix doubles embedded apostrophes."""

    def test_escape_doubles_embedded_apostrophe(self):
        assert _ps_single_quote_escape("O'Brien") == "O''Brien"
        assert _ps_single_quote_escape("plain/path") == "plain/path"
        assert _ps_single_quote_escape("a''b") == "a''''b"

    def test_verify_signature_escapes_apostrophe_in_path(self, monkeypatch, tmp_path):
        tricky_dir = tmp_path / "O'Brien"
        tricky_dir.mkdir()
        exe = tricky_dir / "installer.exe"
        exe.write_bytes(b"MZ\x90\x00")
        monkeypatch.setattr(updater.sys, "platform", "win32")

        captured: dict[str, str] = {}

        def _fake_run(cmd, **kwargs):
            captured["script"] = cmd[-1]
            return mock.Mock(
                stdout=f"Valid|{EV_CERT_SHA1_THUMBPRINT}|OK Studio Inc.|1.0.3.0\n",
                stderr="",
                returncode=0,
            )

        monkeypatch.setattr(updater.subprocess, "run", _fake_run)
        assert updater._verify_signature(exe, "1.0.3") is True
        # The apostrophe must be doubled, not left to terminate the
        # single-quoted literal early. "O'Brien" (single apostrophe) is
        # not a substring of the correctly-escaped "O''Brien", so this
        # also catches a regression back to the unescaped form.
        assert "O''Brien" in captured["script"]
        assert "O'Brien" not in captured["script"]

    @pytest.mark.skipif(sys.platform != "win32", reason="requires real powershell.exe")
    def test_real_powershell_round_trips_apostrophe_path(self, tmp_path):
        # Prove the escaping is actually valid PowerShell syntax, not
        # just "looks right" in a Python f-string: round-trip through
        # the real interpreter the production code shells out to.
        tricky = str(tmp_path / "O'Brien's Files" / "installer.exe")
        escaped = _ps_single_quote_escape(tricky)
        script = f"Write-Output '{escaped}'"
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == tricky


# ---------------------------------------------------------------------------
#  download_and_install
# ---------------------------------------------------------------------------


class TestDownloadAndInstall:
    def test_aborts_when_signature_check_fails(self, monkeypatch, tmp_path):
        # Stand up a successful download but a failing signature — the
        # installer must NOT be launched.
        info = UpdateInfo(
            version="1.0.3",
            download_url="https://github.com/okstudio1/alpha-osk-releases/releases/download/v1.0.3/Alpha-OSK-Setup-1.0.3.exe",
            asset_name="Alpha-OSK-Setup-1.0.3.exe",
            notes="",
        )
        monkeypatch.setattr(
            updater,
            "_download_with_cap",
            lambda *a, **kw: True,
        )
        monkeypatch.setattr(updater, "_verify_signature", lambda p, v: False)
        popen_calls = []
        monkeypatch.setattr(
            updater.subprocess,
            "Popen",
            lambda *a, **kw: popen_calls.append((a, kw)),
        )

        ok, err = updater.download_and_install(info)
        assert ok is False
        assert "signature" in err.lower(), f"error should name the failed step, got {err!r}"
        assert popen_calls == [], "installer must NOT launch on bad signature"

    def test_launches_only_after_signature_passes(self, monkeypatch, tmp_path):
        info = UpdateInfo(
            version="1.0.3",
            download_url="https://github.com/okstudio1/alpha-osk-releases/releases/download/v1.0.3/Alpha-OSK-Setup-1.0.3.exe",
            asset_name="Alpha-OSK-Setup-1.0.3.exe",
            notes="",
        )
        monkeypatch.setattr(
            updater,
            "_download_with_cap",
            lambda *a, **kw: True,
        )
        monkeypatch.setattr(updater, "_verify_signature", lambda p, v: True)
        launch_calls = []

        def fake_launch(dest):
            launch_calls.append(dest)
            return True, ""

        monkeypatch.setattr(updater, "_launch_installer", fake_launch)

        ok, err = updater.download_and_install(info)
        assert ok is True
        assert err == ""
        assert len(launch_calls) == 1
        # The launch helper receives the path to the downloaded asset.
        assert str(launch_calls[0]).endswith(info.asset_name)

    def test_propagates_launch_failure(self, monkeypatch):
        info = UpdateInfo(
            version="1.0.3",
            download_url="https://github.com/okstudio1/alpha-osk-releases/releases/download/v1.0.3/Alpha-OSK-Setup-1.0.3.exe",
            asset_name="Alpha-OSK-Setup-1.0.3.exe",
            notes="",
        )
        monkeypatch.setattr(updater, "_download_with_cap", lambda *a, **kw: True)
        monkeypatch.setattr(updater, "_verify_signature", lambda p, v: True)
        # Simulate the user declining the UAC prompt.
        monkeypatch.setattr(
            updater,
            "_launch_installer",
            lambda dest: (False, "Update cancelled at UAC prompt"),
        )

        ok, err = updater.download_and_install(info)
        assert ok is False
        assert "UAC" in err

    def test_aborts_when_download_fails(self, monkeypatch):
        info = UpdateInfo(
            version="1.0.3",
            download_url="https://github.com/okstudio1/alpha-osk-releases/releases/download/v1.0.3/Alpha-OSK-Setup-1.0.3.exe",
            asset_name="Alpha-OSK-Setup-1.0.3.exe",
            notes="",
        )
        monkeypatch.setattr(
            updater,
            "_download_with_cap",
            lambda *a, **kw: False,
        )
        verify_calls = []
        monkeypatch.setattr(
            updater,
            "_verify_signature",
            lambda p, v: verify_calls.append(p) or True,
        )
        ok, err = updater.download_and_install(info)
        assert ok is False
        assert "download" in err.lower(), f"error should name the failed step, got {err!r}"
        assert verify_calls == [], "must not verify a failed download"


class TestInstallerLaunchingCallback:
    """``on_installer_launching`` is the hook the bridge uses to fire
    the pre-update toast in the live OSK so the user knows why the
    keyboard is about to disappear. See docs/build/AUTO_UPDATE.md § Update
    progress indicator."""

    def _info(self) -> UpdateInfo:
        return UpdateInfo(
            version="1.2.3",
            download_url="https://github.com/okstudio1/alpha-osk-releases/releases/download/v1.2.3/Alpha-OSK-Setup-1.2.3.exe",
            asset_name="Alpha-OSK-Setup-1.2.3.exe",
            notes="",
        )

    def _stub_happy_path(self, monkeypatch):
        monkeypatch.setattr(updater, "_download_with_cap", lambda *a, **kw: True)
        monkeypatch.setattr(updater, "_verify_signature", lambda p, v: True)
        monkeypatch.setattr(updater, "_spawn_relauncher", lambda v: True)
        monkeypatch.setattr(updater, "_launch_installer", lambda dest: (True, ""))

    def test_callback_fires_with_version_before_installer_launch(
        self,
        monkeypatch,
    ):
        self._stub_happy_path(monkeypatch)
        events: list[tuple[str, object]] = []

        monkeypatch.setattr(
            updater,
            "_launch_installer",
            lambda dest: events.append(("launch", dest)) or (True, ""),
        )

        def cb(version: str) -> None:
            events.append(("callback", version))

        ok, err = updater.download_and_install(
            self._info(),
            on_installer_launching=cb,
        )
        assert ok is True
        # Callback must run BEFORE the installer launches — otherwise
        # the installer's taskkill can land before the toast paints.
        kinds = [e[0] for e in events]
        assert kinds == ["callback", "launch"], (
            f"callback must precede installer launch, got {kinds}"
        )
        assert events[0][1] == "1.2.3"

    def test_callback_does_not_fire_when_signature_fails(self, monkeypatch):
        monkeypatch.setattr(updater, "_download_with_cap", lambda *a, **kw: True)
        monkeypatch.setattr(updater, "_verify_signature", lambda p, v: False)
        called: list[str] = []
        monkeypatch.setattr(updater, "_spawn_relauncher", lambda v: True)
        monkeypatch.setattr(updater, "_launch_installer", lambda dest: (True, ""))

        ok, err = updater.download_and_install(
            self._info(),
            on_installer_launching=lambda v: called.append(v),
        )
        assert ok is False
        assert called == [], "no toast should fire on a rejected signature"

    def test_callback_raise_does_not_abort_install(self, monkeypatch):
        # A misbehaving UI signal must never block an install — better
        # to silently miss the toast than to leave the user without an
        # update they accepted.
        self._stub_happy_path(monkeypatch)

        def bad_cb(version: str) -> None:
            raise RuntimeError("UI signal exploded")

        ok, err = updater.download_and_install(
            self._info(),
            on_installer_launching=bad_cb,
        )
        assert ok is True
        assert err == ""

    def test_callback_is_optional(self, monkeypatch):
        # Existing callers that don't pass the kwarg keep working.
        self._stub_happy_path(monkeypatch)
        ok, err = updater.download_and_install(self._info())
        assert ok is True


# ---------------------------------------------------------------------------
#  Install directory resolution + the /D= installer parameter
# ---------------------------------------------------------------------------


class TestInstallTargetDir:
    """FIX: the installer must never resolve $INSTDIR from a
    user-writable registry value (HKCU\\Software\\Alpha-OSK, nothing
    in the build ever writes it). ``_install_target_dir`` is the
    trusted value we compute ourselves and pass via ``/D=`` instead."""

    def test_prefers_frozen_executable_directory(self, monkeypatch, tmp_path):
        # A user who installed somewhere other than Program Files must
        # still be updated in place, not silently relocated.
        fake_exe = tmp_path / "CustomLocation" / "alpha-osk.exe"
        monkeypatch.setattr(updater.sys, "frozen", True, raising=False)
        monkeypatch.setattr(updater.sys, "executable", str(fake_exe))
        assert _install_target_dir() == str(fake_exe.parent)

    def test_falls_back_to_program_files_when_not_frozen(self, monkeypatch):
        # Dev/test invocations aren't frozen and have no real install
        # to preserve, so the Program Files default is correct there.
        monkeypatch.setattr(updater.sys, "frozen", False, raising=False)
        monkeypatch.delenv("ProgramW6432", raising=False)
        monkeypatch.setenv("ProgramFiles", r"C:\Program Files")
        assert _install_target_dir() == r"C:\Program Files\Alpha-OSK"

    def test_prefers_programw6432_over_programfiles(self, monkeypatch):
        # On 64-bit Windows, a 32-bit process would see %ProgramFiles%
        # pointing at the (x86) tree; %ProgramW6432% is the real one.
        monkeypatch.setattr(updater.sys, "frozen", False, raising=False)
        monkeypatch.setenv("ProgramW6432", r"D:\Apps")
        monkeypatch.setenv("ProgramFiles", r"C:\Program Files (x86)")
        assert _install_target_dir() == r"D:\Apps\Alpha-OSK"


class TestLaunchInstaller:
    """NSIS's ``/D=`` switch has two strict rules: it must be the LAST
    parameter on the command line, and the path must be UNQUOTED even
    when it contains spaces. Quoting it, or putting it before ``/S``,
    makes NSIS treat the literal quote characters as part of the path
    and silently breaks the install. These tests pin the exact
    parameter string ``_launch_installer`` builds."""

    @pytest.mark.skipif(sys.platform != "win32", reason="ShellExecuteW is Windows-only")
    def test_windows_launch_passes_unquoted_d_flag_as_last_param(self, monkeypatch, tmp_path):
        import ctypes

        dest = tmp_path / "Alpha-OSK-Setup-1.2.3.exe"
        dest.write_bytes(b"MZ")
        monkeypatch.setattr(updater.sys, "platform", "win32")
        monkeypatch.setattr(updater, "_install_target_dir", lambda: r"C:\Program Files\Alpha-OSK")

        calls = []

        def fake_shell_execute(hwnd, verb, file, params, directory, show):
            calls.append((hwnd, verb, file, params, directory, show))
            return 42  # ShellExecuteW: > 32 means success

        monkeypatch.setattr(ctypes.windll.shell32, "ShellExecuteW", fake_shell_execute)

        ok, err = updater._launch_installer(dest)

        assert ok is True
        assert len(calls) == 1
        _, verb, file, params, _, _ = calls[0]
        assert verb == "runas"
        assert file == str(dest)
        # The exact NSIS parameter string: /S first, /D=<dir> last,
        # unquoted, even though the directory contains a space.
        assert params == r"/S /D=C:\Program Files\Alpha-OSK"
        assert '"' not in params
        assert params.index("/D=") > params.index("/S")

    @pytest.mark.skipif(sys.platform != "win32", reason="ShellExecuteW is Windows-only")
    def test_windows_launch_reports_uac_decline(self, monkeypatch, tmp_path):
        import ctypes

        dest = tmp_path / "Alpha-OSK-Setup-1.2.3.exe"
        dest.write_bytes(b"MZ")
        monkeypatch.setattr(updater.sys, "platform", "win32")
        monkeypatch.setattr(updater, "_install_target_dir", lambda: r"C:\Program Files\Alpha-OSK")
        SE_ERR_ACCESSDENIED = 5
        monkeypatch.setattr(ctypes.windll.shell32, "ShellExecuteW", lambda *a: SE_ERR_ACCESSDENIED)

        ok, err = updater._launch_installer(dest)
        assert ok is False
        assert "UAC" in err

    def test_non_windows_fallback_includes_d_flag(self, monkeypatch, tmp_path):
        dest = tmp_path / "Alpha-OSK-Setup-1.2.3.exe"
        dest.write_bytes(b"MZ")
        monkeypatch.setattr(updater.sys, "platform", "linux")
        monkeypatch.setattr(updater, "_install_target_dir", lambda: "/opt/alpha-osk")
        popen_calls = []
        monkeypatch.setattr(
            updater.subprocess,
            "Popen",
            lambda *a, **kw: popen_calls.append((a, kw)),
        )

        ok, err = updater._launch_installer(dest)

        assert ok is True
        assert len(popen_calls) == 1
        args = popen_calls[0][0][0]
        assert args == [str(dest), "/S", "/D=/opt/alpha-osk"]
