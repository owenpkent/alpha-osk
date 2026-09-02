"""The QSettings scope the headless QML tests write to is private per checkout."""

from __future__ import annotations

from pathlib import Path

from tests.qt_settings_scope import TEST_APP, TEST_ORG, scope_org


class TestTheScopeIsPrivateToTheRun:
    """Two runs that share a scope wipe each other's keys mid-test, and
    that surfaces as the persistence bug those tests exist to catch, not
    as an obvious collision.  These pin the two axes the scope is keyed
    on: the xdist worker, and the checkout the tests live in."""

    def test_two_checkouts_get_different_scopes(self, tmp_path: Path) -> None:
        a = tmp_path / "worktree-a"
        b = tmp_path / "worktree-b"
        assert scope_org(a, "gw0") != scope_org(b, "gw0")

    def test_two_workers_in_one_checkout_get_different_scopes(self, tmp_path: Path) -> None:
        assert scope_org(tmp_path, "gw0") != scope_org(tmp_path, "gw1")

    def test_the_scope_is_stable_for_one_pair(self, tmp_path: Path) -> None:
        assert scope_org(tmp_path, "gw3") == scope_org(tmp_path, "gw3")

    def test_the_serial_run_has_a_scope_of_its_own(self, tmp_path: Path) -> None:
        serial = scope_org(tmp_path, "")
        assert serial != scope_org(tmp_path, "gw0")
        assert not serial.endswith("-")

    def test_the_value_is_a_safe_registry_key_and_directory_name(self, tmp_path: Path) -> None:
        for org in (scope_org(tmp_path, ""), scope_org(tmp_path, "gw12"), TEST_ORG):
            assert org.replace("-", "").isalnum(), org
        assert TEST_APP.replace("-", "").isalnum()

    def test_this_checkout_is_in_the_live_scope(self) -> None:
        here = Path(__file__).resolve().parent.parent
        # Whatever worker this is running under, the checkout half is the same.
        assert TEST_ORG.startswith(scope_org(here, ""))
