"""Adversarial state fuzzing for the panel / compact-view / lock-bar branch.

The hand-written tests in `test_qml_compact_view.py` walk the happy paths a
reviewer thinks of: compact on, compact off, one round trip.  The failure
modes this branch is actually exposed to are *sequence* bugs, because the
new logic is a pile of mutually-recursive property change handlers:

    onCompactViewChanged  -> writes showNavigation / showNumpad
    onShowNavigationChanged -> writes root.width AND appSettings
    onWidthChanged        -> clamps against minimumWidth
    minimumWidth          <- derives from the layout compact just swapped

Any of those can be re-entered from inside another, and a bug there needs a
specific ordering to show up rather than a specific value.  So these drive
randomised operation sequences against the live root object and assert the
invariants after *every individual* operation, which means a failure names
the operation that broke it rather than the whole sequence.  Same reasoning
as the Hypothesis suites in `test_property_*.py`.

Seeds are fixed: a headless QML test that fails only on CI's random seed is
worse than no test.  Widen the seed list to search harder, don't randomise.
"""

from __future__ import annotations

import random

import pytest

pytest.importorskip("PySide6")

from pathlib import Path  # noqa: E402

from tests.test_qml_compact_view import (  # noqa: E402
    TEST_APP,
    TEST_ORG,
    QCoreApplication,
    QGuiApplication,
    QQmlApplicationEngine,
    QQuickItem,  # noqa: F401 - import side effect: without it, reading
    QSettings,  # root.contentItem() raises "Can't find converter"
    Qt,
    QTest,
    QUrl,
    _pump_until,
    _real_warnings,
    _theme_names,
)

# Reuse that module's `qapp` / `qml_root` fixtures without importing their
# names: an `import` would shadow every test parameter that asks for them.
# Loading it as a plugin registers the fixtures only, not its tests.
pytest_plugins = ("tests.test_qml_compact_view",)

_QML_MAIN = Path(__file__).resolve().parent.parent / "qml" / "Main.qml"

# Enough passes for a layout swap to settle: applyLayout() tears the rows
# down and the follow-up resize is queued through Qt.callLater, so the
# window width is not final until the event loop has run at least twice.
_SETTLE_PASSES = 4


def _settle() -> None:
    for _ in range(_SETTLE_PASSES):
        QCoreApplication.processEvents()


# Bound on _wait_until: 20 ms x 50 is a 1 s ceiling, generous next to the
# single pass the layout actually needs and short enough that a genuine
# hang still fails the test rather than the run.
_LAYOUT_WAIT_MS = 20
_LAYOUT_WAIT_TRIES = 50


def _wait_until(predicate) -> bool:
    """Spin the event loop until *predicate* holds, bounded. Returns whether
    it ended up true.

    Uses ``QTest.qWait``, NOT ``processEvents``, and that distinction is the
    whole point of this helper. ``QtQuick.Layouts`` sizes its children on a
    deferred polish pass driven by the event loop's timers, which
    ``processEvents`` alone never runs. Measured against the settings panel
    in this suite: its toggles sit at their unlaid-out implicit size
    (74x133) through any number of ``processEvents`` calls, and snap to
    their real 320x53 on the first ``qWait``. In the stuck state the "Number
    Pad" toggle's centre maps to y=588 in a 540 px window, i.e. outside it,
    so a synthetic click at that point lands nowhere. That silently turned
    "the disabled toggle refused the click" into "the click was never
    delivered", and made the enabled control case fail outright on the
    slower Windows CI runner.

    ``predicate`` must return a bool, never a QQuickItem (see the note on
    ``_pump_until`` in test_qml_compact_view).
    """
    for _ in range(_LAYOUT_WAIT_TRIES):
        if predicate():
            return True
        QTest.qWait(_LAYOUT_WAIT_MS)
    return predicate()


def _row_ids(root) -> list[str]:
    value = root.property("layoutRows")
    rows = value.toVariant() if hasattr(value, "toVariant") else value
    return [r.get("id", "") for r in (rows or [])]


class _Invariants:
    """The properties that must hold after every single mutation."""

    @staticmethod
    def check(root, warnings, step: str) -> None:
        compact = root.property("compactView")
        nav = root.property("showNavigation")
        numpad = root.property("showNumpad")
        width = root.property("width")
        min_width = root.property("minimumWidth")
        key_w = root.property("keyW")
        units = root.property("totalKeyUnits")

        if compact:
            assert nav is False, f"{step}: compact view left the Navigation panel on"
            assert numpad is False, f"{step}: compact view left the Numpad on"

        # The window may legitimately be mid-resize for one pass, but it can
        # never end up narrower than the layout's own floor: below that the
        # rightmost keys render outside the window and become unclickable.
        assert width >= min_width - 1, f"{step}: width {width} below minimumWidth {min_width}"
        assert min_width > 0, f"{step}: minimumWidth collapsed to {min_width}"
        assert units > 0, f"{step}: totalKeyUnits collapsed to {units}"
        # 30 px is the documented smallest usable target for imprecise motor
        # input; keyW has a Math.max(30, ...) floor, so anything under it
        # means the floor itself was bypassed. NaN is the usual way that
        # happens, and it fails this same assertion: every comparison
        # against NaN is False, so `NaN >= 30` is False. No separate NaN
        # check is needed (and a `key_w == key_w` one alongside this can
        # never be the assertion that fires).
        assert key_w >= 30, f"{step}: keyW {key_w} under the 30 px floor (NaN also lands here)"

        # `showNumberRow` is derived from the layout rather than stored, so
        # the standalone panel must appear for exactly those layouts that
        # carry no `number` row of their own. Getting this wrong either
        # hides the digits entirely or stacks a second, narrower number row
        # on a full-size layout that already has one.
        ids = _row_ids(root)
        if ids:
            expected = "number" not in ids
            assert root.property("showNumberRow") is expected, (
                f"{step}: showNumberRow={root.property('showNumberRow')} but layout rows are {ids}"
            )

        assert _real_warnings(warnings) == [], f"{step}: QML warnings {_real_warnings(warnings)}"


class TestPanelStateMachineFuzz:
    """Randomised toggle sequences over the mutually-recursive handlers."""

    SEEDS = (0, 1, 7, 42, 1337)
    STEPS = 40
    LAYOUTS = ("qwerty", "dvorak", "colemak")

    @pytest.mark.parametrize("seed", SEEDS)
    def test_invariants_hold_across_random_toggle_sequences(self, qml_root, seed) -> None:
        root, warnings, _ = qml_root
        rng = random.Random(seed)

        ops = [
            ("compactView", lambda: rng.choice([True, False])),
            ("showNavigation", lambda: rng.choice([True, False])),
            ("showNumpad", lambda: rng.choice([True, False])),
            ("showFunctionRow", lambda: rng.choice([True, False])),
            ("currentLayout", lambda: rng.choice(self.LAYOUTS)),
            ("width", lambda: rng.randint(700, 1600)),
            ("activeLayer", lambda: rng.choice(["base", "sym", "sym2"])),
        ]
        # Turning a side panel on while compact is already active is not a
        # reachable operation: the Settings toggles are disabled in compact
        # (TestDisabledToggleIsInert) and the dispatch that drives them
        # refuses it. Compact enforces the exclusion when it is switched on,
        # not continuously, so writing the property behind its back models a
        # caller that does not exist and would only assert the absence of a
        # guard nothing needs.
        for i in range(self.STEPS):
            name, value_fn = rng.choice(ops)
            value = value_fn()
            if name in ("showNavigation", "showNumpad") and root.property("compactView"):
                continue
            root.setProperty(name, value)
            _settle()
            _Invariants.check(root, warnings, f"seed={seed} step={i} {name}={value!r}")


class TestPanelPreferenceSurvivesEverything:
    """The user's real panel choice must never be destroyed by compact view.

    Compact forces both panels off, and that forced state is not a
    preference.  The guard is a `if (!compactView)` around the two settings
    writes, which is exactly the kind of thing an interleaved sequence
    defeats: the panel toggle and the compact toggle both fire change
    handlers, and whether the guard sees the old or the new `compactView`
    depends on the order Qt happens to deliver them in.
    """

    @staticmethod
    def _set(root, name, value) -> None:
        root.setProperty(name, value)
        _settle()

    @pytest.mark.parametrize("seed", (0, 3, 11, 99))
    def test_repeated_compact_cycles_never_lose_the_preference(self, qml_root, seed) -> None:
        root, warnings, _ = qml_root
        rng = random.Random(seed)

        for i in range(12):
            # Establish a preference in full-size view, where the settings
            # writes are live.
            self._set(root, "compactView", False)
            want_nav = rng.choice([True, False])
            want_numpad = rng.choice([True, False])
            self._set(root, "showNavigation", want_nav)
            self._set(root, "showNumpad", want_numpad)

            # Round-trip through compact any number of times.
            for _ in range(rng.randint(1, 3)):
                self._set(root, "compactView", True)
                assert root.property("showNavigation") is False
                assert root.property("showNumpad") is False
                self._set(root, "compactView", False)

            assert root.property("showNavigation") is want_nav, (
                f"seed={seed} iter={i}: Navigation preference lost across compact cycles"
            )
            assert root.property("showNumpad") is want_numpad, (
                f"seed={seed} iter={i}: Numpad preference lost across compact cycles"
            )
        assert _real_warnings(warnings) == []

    def test_a_toggle_forced_while_compact_is_on_is_not_persisted(self, qml_root) -> None:
        """The Settings toggles are disabled in compact, but the underlying
        property is still writable (a stale binding, a future caller, the
        `settingChanged` path).  Writing it must not overwrite the stored
        preference, or the disabled-toggle contract is only skin deep."""
        root, _, _ = qml_root
        self._set(root, "compactView", False)
        self._set(root, "showNavigation", True)

        self._set(root, "compactView", True)
        self._set(root, "showNavigation", True)  # forced on behind compact's back
        self._set(root, "compactView", False)

        assert root.property("showNavigation") is True, (
            "the stored preference was clobbered by a write made while compact was on"
        )


class TestDisabledToggleIsInert:
    """A greyed-out toggle must actually refuse the click.

    This is the whole enforcement story for the panel exclusion: compact
    forces the panels off once, at the transition, and everything after that
    relies on the user being unable to turn them back on.  `SettingsToggle`
    only sets `opacity` for the disabled look and leans on Qt propagating
    `enabled` down to its MouseArea — so if that propagation ever stops
    holding (an explicit `enabled: true` on the MouseArea, a reparent, a
    Controls version bump), the setting becomes a dimmed control that still
    works, and compact view renders the panels it exists to remove.

    Asserted by clicking, not by reading `enabled`: reading the property back
    would only re-state what the QML says, and the question is whether the
    click gets through.
    """

    LABELS = ("Navigation Keys", "Number Pad")

    @staticmethod
    def _open_panels_settings(root):
        """Show the settings window and drill into Appearance -> Panels.

        The settings panel lives in a sibling `Window`, so it is a QObject
        child of the root rather than a visual one and neither
        `root.contentItem()` nor findChild reaches it.
        """
        root.setProperty("showSettings", True)

        def find_window():
            return next(
                (w for w in QGuiApplication.topLevelWindows() if w.title() == "Alpha-OSK Settings"),
                None,
            )

        # Wait rather than assume a fixed number of passes: this is a
        # separate top-level Window, so opening it is not synchronous and
        # how long it takes is host-dependent.
        _wait_until(lambda: find_window() is not None)
        window = find_window()
        assert window is not None, "the settings window never opened"

        def find_panel():
            return next(
                (i for i in _walk(window.contentItem()) if i.property("currentView") is not None),
                None,
            )

        # `_wait_until` must not be handed a QQuickItem (see its docstring),
        # hence the bool.
        _wait_until(lambda: find_panel() is not None)
        panel = find_panel()
        assert panel is not None, "no settings panel inside the settings window"
        panel.setProperty("currentView", "appearance")
        _settle()
        return window

    @staticmethod
    def _toggle(window, label: str):
        for item in _walk(window.contentItem()):
            if item.property("text") == label and item.property("checked") is not None:
                return item
        return None

    @staticmethod
    def _click(window, item) -> None:
        """Deliver a real synthetic click at *item*'s centre.

        Waits for layout before computing the point, and that wait is
        load-bearing rather than defensive. The settings window is a
        separate top-level `Window`: it opens, its QML instantiates, and
        its delegates get their geometry over the following event-loop
        passes. A fixed number of `processEvents()` calls is enough on
        some hosts and not others, and the Windows CI runner is one of
        the slow ones. An item that is found and reports `enabled` can
        still be sized 0x0 or sitting at the scene origin, and a click at
        that centre lands on whatever is at the window's top-left corner
        instead of the toggle, which surfaced as the control case failing
        with "the click never arrived" while Linux passed.
        """
        content = window.contentItem()

        def positioned() -> bool:
            if item.width() <= 0 or item.height() <= 0:
                return False
            point = item.mapToScene(item.boundingRect().center())
            return 0 <= point.x() < content.width() and 0 <= point.y() < content.height()

        assert _wait_until(positioned), (
            "the toggle never took a position inside the settings window, so "
            "a click at its centre would land somewhere else and this harness "
            "would be testing nothing"
        )

        centre = item.mapToScene(item.boundingRect().center())
        QTest.mouseClick(
            window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre.toPoint()
        )
        _settle()

    def test_the_same_click_does_work_when_compact_is_off(self, qml_root) -> None:
        """The control case, and it is the whole reason the test below means
        anything: a synthetic click that silently landed nowhere would make
        "the disabled toggle did not fire" pass by construction."""
        root, warnings, _ = qml_root
        root.setProperty("compactView", False)
        root.setProperty("showNavigation", False)
        _settle()
        window = self._open_panels_settings(root)

        toggle = self._toggle(window, "Navigation Keys")
        assert toggle is not None
        assert toggle.property("enabled") is True
        self._click(window, toggle)

        assert root.property("showNavigation") is True, (
            "the synthetic click did not reach an ENABLED toggle, so this "
            "harness cannot distinguish 'refused' from 'never delivered'"
        )
        assert _real_warnings(warnings) == []

    @pytest.mark.parametrize("label", LABELS)
    def test_clicking_it_in_compact_view_does_not_turn_the_panel_on(self, qml_root, label) -> None:
        root, warnings, _ = qml_root
        root.setProperty("compactView", True)
        _settle()
        window = self._open_panels_settings(root)

        toggle = self._toggle(window, label)
        assert toggle is not None, f"no {label!r} toggle found in the settings panel"
        assert toggle.property("enabled") is False, (
            f"the {label!r} toggle is still enabled while compact view is on"
        )

        before = (root.property("showNavigation"), root.property("showNumpad"))
        self._click(window, toggle)

        assert (root.property("showNavigation"), root.property("showNumpad")) == before, (
            f"clicking the disabled {label!r} toggle changed the panel state: "
            f"the greyed-out look is decorative and the setting is still live"
        )
        assert root.property("compactView") is True
        assert _real_warnings(warnings) == []


class TestGeometryExtremes:
    """Hostile window widths.

    `keyW` divides by `totalKeyUnits` and `minimumWidth` multiplies by it, so
    a degenerate width is the shortest path to a NaN that propagates into
    every key rectangle and renders a blank keyboard.
    """

    HOSTILE = (0, 1, -1, -100000, 200, 319, 320, 4096, 20000, 100000)

    @pytest.mark.parametrize("compact", (False, True))
    def test_hostile_widths_never_break_the_sizing_math(self, qml_root, compact) -> None:
        root, warnings, _ = qml_root
        root.setProperty("compactView", compact)
        _settle()

        for width in self.HOSTILE:
            root.setProperty("width", width)
            _settle()
            _Invariants.check(root, warnings, f"compact={compact} width={width}")

    @pytest.mark.parametrize("compact", (False, True))
    def test_keys_stay_inside_the_window_at_the_minimum_width(self, qml_root, compact) -> None:
        """At minimumWidth the rows are at their tightest. A key that lands
        outside the window is not merely ugly, it is unclickable."""
        root, warnings, _ = qml_root
        root.setProperty("compactView", compact)
        _settle()
        root.setProperty("width", root.property("minimumWidth"))
        _pump_until(lambda: root.property("keyW") > 0)
        _settle()

        rows = _rendered_key_rows(root)
        assert rows, "no keyboard rows rendered"
        window_w = root.property("width")
        for row in rows:
            assert row.width() <= window_w + 1, (
                f"compact={compact}: a row is {row.width()} px wide in a {window_w} px window"
            )
        assert _real_warnings(warnings) == []


def _rendered_key_rows(root) -> list:
    """Keyboard rows, found by their `rowData` property.

    A Repeater's delegates are re-parented as *visual* children, so
    findChildren cannot see them; walking childItems() is the only way in
    (see the note in test_qml_prediction_bar.py).
    """
    found: list = []

    def walk(item) -> None:
        for child in item.childItems():
            if child.property("rowData") is not None:
                found.append(child)
            walk(child)

    walk(root.contentItem())
    return found


class TestLockIndicatorIsAlwaysVisible:
    """The lock bar replaced a gold ring + padlock badge with a bar whose
    colour is derived from the fill underneath it.

    That derivation is the risk: the bar is drawn with `_onFillColor`, which
    is picked from the *accent* colour, while the keycap underneath is only
    filled with the accent when `isActive` is true.  Locked is supposed to
    imply active — if that ever desyncs, the bar is painted with the ink
    chosen for a fill the key is not wearing, and a held modifier can go
    invisible on a themed board.  A held Ctrl the user cannot see is a
    keyboard that types garbage into whatever app is focused.
    """

    MIN_RATIO = 3.0  # WCAG non-text contrast (SC 1.4.11) for a UI indicator.

    @staticmethod
    def _lum(color) -> float:
        def channel(v: float) -> float:
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

        return (
            0.2126 * channel(color.redF())
            + 0.7152 * channel(color.greenF())
            + 0.0722 * channel(color.blueF())
        )

    @classmethod
    def _contrast(cls, a, b) -> float:
        la, lb = cls._lum(a), cls._lum(b)
        return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)

    @staticmethod
    def _shift_key(root):
        """The Shift keycap.

        Found by walking childItems(): the keys are Repeater delegates, so
        they are visual children and findChildren() cannot see them.
        """
        for item in _all_items(root):
            if item.property("keyText") == "shift" and item.property("isLocked") is not None:
                return item
        return None

    @classmethod
    def _lock_shift(cls, root, bridge):
        """Lock Shift through the real bridge slot, not by poking the
        delegate: the whole question is whether `isLocked` and `isActive`
        arrive together, and setting them by hand would assume the answer."""
        bridge.lockModifier("shift")
        _settle()
        key = cls._shift_key(root)
        assert key is not None, "no Shift KeyButton found"
        return key

    def test_locking_a_modifier_also_marks_it_active(self, qml_root) -> None:
        """`_onFillColor` picks the bar's ink from the accent colour, which
        is only what the keycap is actually filled with when isActive is
        true. If lock ever arrives without active, the bar is painted for a
        fill the key is not wearing."""
        root, warnings, bridge = qml_root
        key = self._lock_shift(root, bridge)

        assert key.property("isLocked") is True, "lockModifier did not reach the keycap"
        assert key.property("isActive") is True, (
            "a locked modifier is not marked active, so the lock bar is drawn "
            "in the ink chosen for the accent fill on top of the resting key colour"
        )
        assert _real_warnings(warnings) == []

    def test_bar_contrasts_with_its_fill_on_every_theme(self, qml_root) -> None:
        root, warnings, bridge = qml_root
        themes = _theme_names(root)
        assert len(themes) == 9, f"expected 9 themes, found {themes}"

        key = self._lock_shift(root, bridge)
        failures = []
        for theme in themes:
            root.setProperty("currentTheme", theme)
            _settle()

            bar = key.findChild(QQuickItem, "keyLockBar")
            assert bar is not None, f"{theme}: no lock bar in the locked key"
            assert bar.property("visible") is True, f"{theme}: lock bar hidden while locked"

            ratio = self._contrast(bar.property("color"), key.property("accentColor"))
            if ratio < self.MIN_RATIO:
                failures.append(f"{theme}: lock bar vs accent fill is {ratio:.2f}:1")

        assert not failures, "the lock indicator is not visible on:\n  " + "\n  ".join(failures)
        assert _real_warnings(warnings) == []

    @pytest.mark.parametrize("compact", (False, True))
    def test_bar_has_positive_width_on_the_narrowest_key(self, qml_root, compact) -> None:
        """The bar insets itself horizontally by `max(4, radius * 0.75)` per
        side to clear the keycap's rounded corners. On a narrow key those two
        margins can meet in the middle and the indicator disappears."""
        root, warnings, bridge = qml_root
        root.setProperty("compactView", compact)
        _settle()
        root.setProperty("width", root.property("minimumWidth"))
        _pump_until(lambda: root.property("keyW") > 0)
        _settle()

        key = self._lock_shift(root, bridge)
        bar = key.findChild(QQuickItem, "keyLockBar")
        assert bar is not None
        assert bar.width() > 0, (
            f"compact={compact}: lock bar collapsed to {bar.width()} px on a "
            f"{key.width()} px key at the minimum window width"
        )
        assert _real_warnings(warnings) == []


def _walk(item) -> list:
    """Every visual descendant of *item*.

    Repeater delegates are re-parented as visual children, so findChildren()
    cannot see them; childItems() is the only way in.
    """
    out: list = []
    for child in item.childItems():
        out.append(child)
        out.extend(_walk(child))
    return out


def _all_items(root) -> list:
    return _walk(root.contentItem())


class TestRestartPersistence:
    """A cold start must reproduce the state the user quit in.

    `Component.onCompleted` now ANDs the stored panel preferences with
    `!compactView`, which is the only place the two settings meet on a cold
    path.  Getting it wrong is invisible in-session and only shows up after a
    restart, which is the worst kind of bug to leave to manual testing.
    """

    @staticmethod
    def _boot(warnings: list):
        """Load Main.qml against the CURRENT settings store, without clearing
        it, so the previous instance's persisted state is what gets read.

        The bridge is returned to the caller and must be held for as long as
        the root object is used. `setContextProperty` does not take
        ownership, so letting it fall out of scope collects it and QML's
        `keyboard` goes null — which silently short-circuits every handler
        guarded on it (`if (!_loaded || !keyboard) return`) and makes compact
        view look broken when it is only unreferenced.
        """
        from unittest.mock import MagicMock, patch

        from src.keyboard_bridge import KeyboardBridge

        with patch("src.keyboard_bridge.create_key_synthesizer") as factory:
            synth = MagicMock()
            synth.is_available.return_value = True
            synth.backend_name.return_value = "MockSynth"
            factory.return_value = synth
            bridge = KeyboardBridge()

        # The one key this helper does write to the store it otherwise
        # only reads: `savedAutoCheckUpdates` defaults to true, so a load
        # that is not disarmed arms a 3-second timer firing a real HTTPS
        # request to the GitHub releases API, then emits the result back
        # into a bridge this test has already dropped. The shared
        # `qml_root` fixture disarms it before its own load; `_boot`
        # deliberately bypasses that fixture, and clears the store first,
        # so it has to do the same thing itself. Unrelated to any panel
        # or compact-view state, so it cannot skew what these tests read.
        settings = QSettings(TEST_ORG, TEST_APP)
        settings.setValue("ui/savedAutoCheckUpdates", False)
        settings.sync()

        engine = QQmlApplicationEngine()
        engine.warnings.connect(lambda errs: warnings.extend(e.toString() for e in errs))
        engine.rootContext().setContextProperty("keyboard", bridge)
        engine.load(QUrl.fromLocalFile(str(_QML_MAIN)))
        assert engine.rootObjects(), "Main.qml failed to reload"
        _settle()
        root = engine.rootObjects()[0]
        assert root.property("_loaded") is True, "Main.qml never finished loading"
        return engine, root, bridge

    def test_quitting_in_compact_comes_back_in_compact_with_panels_off(self, qapp) -> None:
        QSettings(TEST_ORG, TEST_APP).clear()
        warnings: list[str] = []

        # Session 1: user has Navigation on, then switches to compact.
        engine1, root1, bridge1 = self._boot(warnings)
        root1.setProperty("showNavigation", True)
        _settle()
        root1.setProperty("compactView", True)
        _settle()
        assert root1.property("showNavigation") is False
        _flush_settings()
        del engine1, bridge1

        # Session 2: cold start.
        engine2, root2, bridge2 = self._boot(warnings)
        assert root2.property("compactView") is True, "compact view did not persist"
        assert root2.property("showNavigation") is False, (
            "a cold start in compact view brought the Navigation panel back"
        )

        # Leaving compact in the new session must restore the real preference.
        root2.setProperty("compactView", False)
        _settle()
        assert root2.property("showNavigation") is True, (
            "the Navigation preference did not survive a restart in compact view"
        )
        assert _real_warnings(warnings) == []
        del engine2, bridge2

    def test_window_width_does_not_grow_across_restarts(self, qapp) -> None:
        """The panel change handlers add 220 / 250 px to the window, and
        `Component.onCompleted` assigns those same properties on every cold
        start.  If the `_loaded` guard ever stops covering that, the window
        gains half a panel's width every single launch."""
        QSettings(TEST_ORG, TEST_APP).clear()
        warnings: list[str] = []

        widths = []
        for _ in range(3):
            engine, root, bridge = self._boot(warnings)
            widths.append(root.property("width"))
            _flush_settings()
            del engine, bridge
            _settle()

        assert widths[0] == widths[1] == widths[2], (
            f"the window width drifted across restarts: {widths}"
        )
        assert _real_warnings(warnings) == []


def _flush_settings() -> None:
    """QML `Settings` writes are debounced; give the timer a chance to run."""
    for _ in range(6):
        QCoreApplication.processEvents()
    QSettings(TEST_ORG, TEST_APP).sync()
