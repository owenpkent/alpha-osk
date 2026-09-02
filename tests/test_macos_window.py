"""Tests for ``src/platform/macos_window.py``.

The pyobjc/AppKit-specific window and activation-policy tuning that used to
live inline in ``src/keyboard_app.py`` (see that file's module docstring,
and this module's, for why it moved and why it is not gated by a literal
``sys.platform == "darwin"`` check).

pyobjc is never installed on the Windows/Linux hosts this suite runs on
(see CLAUDE.md's macOS build section: the pyobjc-framework-* extras are
macOS-only), so these tests exercise the real ``except ImportError`` path
rather than a monkeypatched one -- the same path a Windows or Linux user
hits at runtime.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.platform.macos_window import apply_activation_policy, apply_window_flags


def test_module_imports_cleanly() -> None:
    """A bare import must never touch AppKit -- every pyobjc call is inside
    a function body, guarded by its own try/except ImportError, so the
    module has to be importable on every OS with no side effects."""
    import src.platform.macos_window  # noqa: F401


class TestTheModuleIsASafeNoOpWithoutPyobjc:
    """Neither function is gated by ``sys.platform`` (see the module
    docstring: doing that would prune them under both of the project's
    required mypy runs, since neither is ``darwin``). Off macOS the guard
    is the ``except ImportError`` around the ``AppKit`` import instead,
    which this pins at runtime the same way the sys.platform guards in
    ``windows_window.py`` are pinned there.
    """

    def test_apply_activation_policy_is_a_no_op(self) -> None:
        # Absence of an exception is the assertion: AppKit genuinely isn't
        # installed on this host.
        apply_activation_policy()

    def test_apply_window_flags_is_a_no_op(self) -> None:
        root = MagicMock()

        apply_window_flags(root)

        # The AppKit import fails before root is ever touched.
        root.winId.assert_not_called()
