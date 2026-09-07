"""Tests for ``TelemetryClient.apply_install_invite()``.

The Windows installer's research-participation page (see
``build/windows/build.py``'s ``StudyInvitePage``) seeds
``HKLM\\Software\\alpha-osk-setup`` \\ ``Invite`` with "accepted" or
"declined". ``apply_install_invite()`` is how the running app consumes
that seed exactly once, per ``src/telemetry.py``.

The registry is faked by monkeypatching ``sys.modules["winreg"]`` with an
in-test stand-in, never the real registry, which is also what keeps this
suite passing unmodified on Linux CI (``winreg`` does not exist there).
``sys.platform`` is monkeypatched too, so the tests are deterministic
regardless of the host they happen to run on.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Dict, Optional, Tuple

import pytest

from src.telemetry import TelemetryClient


def _fake_winreg(
    values: Optional[Dict[str, str]],
    *,
    raise_on_open: bool = False,
) -> types.ModuleType:
    """A minimal ``winreg`` stand-in.

    ``values`` is the fake ``Invite`` key's contents; ``None`` means the
    key itself does not exist. Real ``winreg`` raises ``FileNotFoundError``
    (an ``OSError`` subclass) for both a missing key and a missing value,
    which this mirrors rather than inventing a different exception type.
    """
    fake = types.ModuleType("winreg")
    fake.HKEY_LOCAL_MACHINE = object()  # type: ignore[attr-defined]

    class _Key:
        def __enter__(self) -> "_Key":
            return self

        def __exit__(self, *exc_info: object) -> bool:
            return False

    def open_key(hive: object, subkey: str, *args: object, **kwargs: object) -> _Key:
        if raise_on_open:
            raise OSError("simulated registry failure")
        if values is None:
            raise FileNotFoundError(f"no such key: {subkey}")
        return _Key()

    def query_value_ex(key: _Key, name: str) -> Tuple[str, int]:
        assert values is not None
        if name not in values:
            raise FileNotFoundError(f"no such value: {name}")
        return (values[name], 1)

    fake.OpenKey = open_key  # type: ignore[attr-defined]
    fake.QueryValueEx = query_value_ex  # type: ignore[attr-defined]
    return fake


def _win32_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    values: Optional[Dict[str, str]] = None,
    raise_on_open: bool = False,
) -> TelemetryClient:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(values, raise_on_open=raise_on_open))
    return TelemetryClient(
        state_path=tmp_path / "telemetry.json",
        endpoint="https://test.example/",
        analytics_provider=lambda: {},
        app_version="1.0.0",
        os_name="windows",
    )


class TestAnAcceptedSeed:
    def test_enables_telemetry_and_mints_an_anon_id(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        client = _win32_client(monkeypatch, tmp_path, values={"Invite": "accepted"})
        assert client.apply_install_invite() is True
        assert client.enabled is True
        assert client.anon_id is not None


class TestADeclinedSeed:
    def test_leaves_telemetry_disabled(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        client = _win32_client(monkeypatch, tmp_path, values={"Invite": "declined"})
        assert client.apply_install_invite() is True
        assert client.enabled is False
        assert client.anon_id is None


class TestItIsAppliedOnlyOnce:
    def test_a_second_call_is_a_no_op_even_if_the_registry_still_says_accepted(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        client = _win32_client(monkeypatch, tmp_path, values={"Invite": "accepted"})
        assert client.apply_install_invite() is True
        assert client.apply_install_invite() is False
        assert client.enabled is True

    def test_flipping_the_users_own_setting_afterward_is_not_undone_later(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        client = _win32_client(monkeypatch, tmp_path, values={"Invite": "accepted"})
        assert client.apply_install_invite() is True
        client.disable()  # the user's own later choice
        assert client.apply_install_invite() is False
        assert client.enabled is False


class TestAMissingKey:
    def test_changes_nothing_and_leaves_it_unapplied(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        client = _win32_client(monkeypatch, tmp_path, values=None)
        assert client.apply_install_invite() is False
        assert client.enabled is False
        assert client._invite_applied is False

        # Not marking it "answered" is what lets a machine installed
        # before this feature existed still be invited later, once a
        # seed does show up.
        monkeypatch.setitem(sys.modules, "winreg", _fake_winreg({"Invite": "accepted"}))
        assert client.apply_install_invite() is True
        assert client.enabled is True


class TestAnUnrecognisedValue:
    def test_changes_nothing(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        client = _win32_client(monkeypatch, tmp_path, values={"Invite": "maybe-later"})
        assert client.apply_install_invite() is False
        assert client.enabled is False
        assert client._invite_applied is False


class TestARaisingRegistryRead:
    def test_is_swallowed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        client = _win32_client(
            monkeypatch, tmp_path, values={"Invite": "accepted"}, raise_on_open=True
        )
        assert client.apply_install_invite() is False
        assert client.enabled is False
        assert client._invite_applied is False


class TestNonWindows:
    def test_returns_false_without_touching_state(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        client = TelemetryClient(
            state_path=tmp_path / "telemetry.json",
            endpoint="https://test.example/",
            analytics_provider=lambda: {},
            app_version="1.0.0",
            os_name="linux",
        )
        assert client.apply_install_invite() is False
        assert client.enabled is False
        assert client._invite_applied is False
