"""Tests for app-level wiring in ``src/keyboard_app.py``.

Currently covers the one-time purge of diagnostic logs written before
the typed-content fix.  Those files could hold a transcript of what the
user typed (including a password typed while privacy mode was active),
so removing them on upgrade is part of that fix rather than cleanup.
"""

from __future__ import annotations

from pathlib import Path

from src.keyboard_app import _LOG_PURGE_SENTINEL, _purge_pre_fix_logs


def _seed_logs(config_dir: Path) -> None:
    """Write a main log plus the three rotations the handler can produce."""
    (config_dir / "alpha-osk.log").write_text("secret typed text", encoding="utf-8")
    for n in (1, 2, 3):
        (config_dir / f"alpha-osk.log.{n}").write_text(f"rotation {n}", encoding="utf-8")


class TestPurgePreFixLogs:
    def test_removes_the_log_and_every_rotation(self, tmp_path: Path) -> None:
        _seed_logs(tmp_path)

        removed = _purge_pre_fix_logs(tmp_path)

        assert removed == 4
        assert sorted(tmp_path.glob("alpha-osk.log*")) == []

    def test_writes_a_sentinel_so_it_runs_once(self, tmp_path: Path) -> None:
        _seed_logs(tmp_path)

        _purge_pre_fix_logs(tmp_path)

        assert (tmp_path / _LOG_PURGE_SENTINEL).is_file()

    def test_second_run_is_a_no_op(self, tmp_path: Path) -> None:
        """A user who wants logs kept across restarts must not fight us."""
        _seed_logs(tmp_path)
        _purge_pre_fix_logs(tmp_path)

        # Logs written by the *fixed* build are safe and must survive.
        (tmp_path / "alpha-osk.log").write_text("post-fix, no typed content", encoding="utf-8")

        removed = _purge_pre_fix_logs(tmp_path)

        assert removed == 0
        assert (tmp_path / "alpha-osk.log").exists()

    def test_no_logs_present_still_marks_done(self, tmp_path: Path) -> None:
        """A fresh install has nothing to purge but must not re-check forever."""
        removed = _purge_pre_fix_logs(tmp_path)

        assert removed == 0
        assert (tmp_path / _LOG_PURGE_SENTINEL).is_file()

    def test_leaves_unrelated_files_alone(self, tmp_path: Path) -> None:
        """The glob must not reach the model, snippets or analytics files."""
        _seed_logs(tmp_path)
        (tmp_path / "snippets.json").write_text("{}", encoding="utf-8")
        (tmp_path / "analytics.json").write_text("{}", encoding="utf-8")

        _purge_pre_fix_logs(tmp_path)

        assert (tmp_path / "snippets.json").exists()
        assert (tmp_path / "analytics.json").exists()

    def test_unreadable_config_dir_does_not_raise(self, tmp_path: Path) -> None:
        """Logging setup must never be the reason the keyboard fails to start."""
        missing = tmp_path / "does-not-exist"

        removed = _purge_pre_fix_logs(missing)

        assert removed == 0
