"""The QML context properties every headless QML test needs registered.

``Main.qml`` reads two Python objects out of the QML root context:
``keyboard`` (the ``KeyboardBridge``) and, since telemetry moved off the
bridge (``docs/architecture/STRUCTURAL_REVIEW.md`` section 3.1),
``telemetry`` (a ``TelemetryBridge``). Seven fixtures across the headless
QML test modules each called ``setContextProperty("keyboard", bridge)``
by hand; without this helper, adding ``telemetry`` would have meant
editing all seven, and the next context property after that would mean
editing eight. One call here keeps it a one-edit change.

``install_context_properties`` builds a ``TelemetryBridge`` off the given
bridge when the caller does not already have one, and parents it to that
bridge. Parenting -- not returning it for the caller to hold in a local
variable -- is deliberate: several of these fixtures build the QML root
inside a closure (``qml_root_factory``, ``picker_factory``, the
``_boot`` helper in ``test_qml_ui_state_fuzz.py``) and only return
``(root, warnings, bridge)`` to the test. A ``TelemetryBridge`` with no
Python reference and no Qt parent is garbage-collected the moment
``install_context_properties`` returns, and QML's ``telemetry`` context
property goes null -- the same trap the ``_boot`` docstring already
documents for the bridge itself. Tying its lifetime to the bridge's,
the way ``QTimer(self)`` ties every timer's lifetime to its owner
throughout this codebase, means every fixture gets a live ``telemetry``
for free.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtQml import QQmlApplicationEngine

from src.keyboard_bridge import KeyboardBridge
from src.telemetry_bridge import TelemetryBridge


def install_context_properties(
    engine: QQmlApplicationEngine,
    bridge: KeyboardBridge,
    *,
    telemetry: Optional[TelemetryBridge] = None,
) -> TelemetryBridge:
    """Register ``keyboard`` and ``telemetry`` on ``engine``'s root context.

    Returns the ``TelemetryBridge`` that was registered (the one passed
    in, or a freshly built one parented to ``bridge``), so a test that
    wants to drive it directly (toggle consent, force a submit) doesn't
    have to reach back into ``bridge``, which no longer has any telemetry
    on it.
    """
    if telemetry is None:
        telemetry = TelemetryBridge(bridge.analytics.get_session_stats, parent=bridge)
    engine.rootContext().setContextProperty("keyboard", bridge)
    engine.rootContext().setContextProperty("telemetry", telemetry)
    return telemetry
