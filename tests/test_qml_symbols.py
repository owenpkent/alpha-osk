"""Headless QML tests for the Symbols & Emoji window.

Everything this window does lives in QML: the paging, the Recent list and
its persistence, which tab is showing. The Python suite reaches only
`insertGlyph` and the catalogue behind it (see
`test_keyboard_bridge.py::TestTypingAGlyphFromThePicker`), so these load the
real `qml/Main.qml` against a real `KeyboardBridge` under the `offscreen`
platform plugin and drive the window object directly, the same approach as
`test_qml_snippets.py`.

The failure mode they exist for is a QML binding error, which is a *runtime*
warning rather than an import failure and would otherwise ship as a picker
that renders blank.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6")

# Must be set before QGuiApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# See the note in test_qml_compact_view.py: QtGui dlopens the host's
# libEGL / libGL at module scope, and an ImportError there aborts the whole
# run as a collection error rather than failing this module.
try:
    from PySide6.QtCore import QCoreApplication, QObject, QSettings, Qt, QUrl  # noqa: E402
    from PySide6.QtGui import QGuiApplication  # noqa: E402
    from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402

    # Imported for the side effect: without QQuickItem somewhere in the
    # module, reading an item's `contentItem` raises "Can't find converter
    # for 'QQuickItem*'".
    from PySide6.QtQuick import QQuickItem  # noqa: E402
except ImportError as exc:  # pragma: no cover - environment-dependent
    pytest.skip(
        f"Qt GUI libraries unavailable ({exc}); install libegl1/libgl1 to run "
        "the headless QML tests",
        allow_module_level=True,
    )

from src.glyphs import MAX_RECENT  # noqa: E402
from src.keyboard_bridge import KeyboardBridge  # noqa: E402
from tests.qt_settings_scope import TEST_APP, TEST_ORG  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
QML_MAIN = REPO_ROOT / "qml" / "Main.qml"

IGNORED_WARNING_FRAGMENTS = ("does not support customization",)

RECENT_KEY = "ui/savedRecentGlyphs"


def _real_warnings(warnings: list[str]) -> list[str]:
    return [w for w in warnings if not any(frag in w for frag in IGNORED_WARNING_FRAGMENTS)]


@pytest.fixture(scope="module")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        QCoreApplication.setOrganizationName(TEST_ORG)
        QCoreApplication.setApplicationName(TEST_APP)
        app = QGuiApplication([])
    assert QCoreApplication.organizationName() == TEST_ORG, (
        "another test already created a QGuiApplication under a different "
        "organisation; these tests would write to the real user's settings"
    )
    return app


@pytest.fixture
def picker_factory(qapp):
    """Load Main.qml, optionally seeding settings first.

    Seeding has to happen before the load for anything read during
    `Component.onCompleted` or restored from `appSettings`, which the Recent
    list is.
    """
    engines: list[QQmlApplicationEngine] = []

    def _load(pre_settings: dict | None = None, open_now: bool = True):
        warnings: list[str] = []
        QSettings(TEST_ORG, TEST_APP).clear()

        settings = QSettings(TEST_ORG, TEST_APP)
        # Disarm the startup update check. See test_qml_compact_view.py,
        # where the live HTTPS request from a daemon thread outliving the
        # fixture surfaces as a hard crash.
        settings.setValue("ui/savedAutoCheckUpdates", False)
        for key, value in (pre_settings or {}).items():
            settings.setValue(key, value)
        settings.sync()

        with patch("src.keyboard_bridge.create_key_synthesizer") as factory:
            synth = MagicMock()
            synth.is_available.return_value = True
            synth.backend_name.return_value = "MockSynth"
            factory.return_value = synth
            bridge = KeyboardBridge()

        engine = QQmlApplicationEngine()
        engine.warnings.connect(lambda errs: warnings.extend(e.toString() for e in errs))
        engine.rootContext().setContextProperty("keyboard", bridge)
        engine.load(QUrl.fromLocalFile(str(QML_MAIN)))
        assert engine.rootObjects(), "qml/Main.qml failed to load:\n  " + "\n  ".join(warnings)
        engines.append(engine)

        root = engine.rootObjects()[0]
        window = root.findChild(QObject, "symbolsWindow")
        assert window is not None, "symbolsWindow not found in the loaded QML"
        if open_now:
            # Opened for real rather than merely shown: the catalogue is
            # fetched from the bridge on the first open, so a window that was
            # never opened has no glyphs and every assertion below would run
            # against an empty grid.
            window.openPicker()
            QCoreApplication.processEvents()
        return root, window, bridge, warnings

    yield _load
    for engine in engines:
        del engine


@pytest.fixture
def picker(picker_factory):
    return picker_factory()


def _catalogue(window) -> list[dict]:
    value = window.property("catalogue")
    return value.toVariant() if hasattr(value, "toVariant") else value


def _recent(window) -> list[str]:
    value = window.property("recent")
    return list(value.toVariant() if hasattr(value, "toVariant") else value)


def _typed(bridge) -> list[str]:
    return [call.args[0] for call in bridge._synth.send_text.call_args_list]


class TestTheWindowLoads:
    def test_the_catalogue_arrives_and_every_category_gets_a_tab(self, picker) -> None:
        """One tab per category plus Recent. Built from the catalogue rather
        than listed separately, so a category added to src/glyphs.py cannot
        end up with no way to reach it."""
        _root, window, _bridge, warnings = picker
        cats = _catalogue(window)
        assert cats, "the catalogue never reached QML"

        labels = window.property("tabLabels").toVariant()
        assert len(labels) == len(cats) + 1, "a category has no tab, or a tab has no category"
        assert labels[0] == "Recent"
        assert _real_warnings(warnings) == []

    def test_it_never_accepts_focus(self, picker) -> None:
        """The whole app rests on never taking focus from the window being
        typed into. A picker that activated on click would move the caret out
        of the field the glyph is meant for, so the glyph would land
        somewhere else or nowhere."""
        _root, window, _bridge, _warnings = picker
        flags = window.property("flags")
        assert flags & Qt.WindowDoesNotAcceptFocus, "the picker can take focus"
        assert flags & Qt.WindowStaysOnTopHint
        assert flags & Qt.FramelessWindowHint

    def test_it_opens_on_a_tab_that_has_something_in_it(self, picker) -> None:
        """With no history, Recent is empty, and opening on an empty page
        teaches the user the window is empty. The first catalogue tab is the
        opening view until there is a history to show."""
        _root, window, _bridge, _warnings = picker
        assert _recent(window) == [], "precondition: no history yet"
        assert window.property("categoryIndex") == 1


class TestTappingAGlyph:
    """A tap has to do two things, and the second one is what makes the
    window usable twice: type the glyph, and remember that it was used."""

    def test_it_types_the_glyph(self, picker) -> None:
        _root, window, bridge, warnings = picker
        glyph = _catalogue(window)[0]["glyphs"][0]

        bridge._synth.send_text.reset_mock()
        window.typeGlyph(glyph)
        QCoreApplication.processEvents()

        assert _typed(bridge) == [glyph]
        assert _real_warnings(warnings) == []

    def test_it_goes_to_the_head_of_recent_and_is_persisted(self, picker) -> None:
        """Newest first, and written through to the settings layer on the
        tap rather than on close: this window is closed by clicking away
        from it as often as by its own button."""
        root, window, _bridge, _warnings = picker
        first, second = _catalogue(window)[0]["glyphs"][:2]

        window.typeGlyph(first)
        window.typeGlyph(second)
        QCoreApplication.processEvents()

        assert _recent(window) == [second, first]

        # Asserted on the Settings property rather than on a fresh QSettings
        # read: the element batches its writes and flushes on its own
        # schedule, so a read straight after the tap cannot tell a deferred
        # write from one that never happened. The other half of the round
        # trip, that a stored list comes back, is the restore test below.
        stored = root.findChild(QObject, "appSettings").property("savedRecentGlyphs")
        assert json.loads(stored) == [second, first]

    def test_a_stored_list_comes_back_on_the_next_launch(self, picker_factory) -> None:
        """The other half of the round trip. Recent is worth having only
        across sessions: within one, the user can still see what they just
        tapped in the app they typed it into."""
        kept = ["…", "€", "°"]
        _root, window, _bridge, warnings = picker_factory({RECENT_KEY: json.dumps(kept)})

        assert _recent(window) == kept
        # And it opens on Recent this time, because there is now something
        # in it to open on.
        assert window.property("categoryIndex") == 0
        assert _real_warnings(warnings) == []

    def test_the_same_glyph_is_never_held_twice(self, picker) -> None:
        """Recent is a most-recently-used list, not a tally. A duplicate
        would spend one of a small number of slots saying something the list
        already says, and the slots are the whole value of the tab."""
        _root, window, _bridge, _warnings = picker
        first, second = _catalogue(window)[0]["glyphs"][:2]

        window.typeGlyph(first)
        window.typeGlyph(second)
        window.typeGlyph(first)
        QCoreApplication.processEvents()

        assert _recent(window) == [first, second]

    def test_recent_is_capped(self, picker) -> None:
        """Unbounded, this would grow into a settings value of no fixed size
        and a Recent tab that pages. The bound lives in src/glyphs.py so
        there is one number rather than one per side."""
        _root, window, _bridge, _warnings = picker
        glyphs = []
        for cat in _catalogue(window):
            glyphs.extend(cat["glyphs"])
        assert len(glyphs) > MAX_RECENT, "not enough glyphs to overflow the cap"

        for glyph in glyphs[: MAX_RECENT + 8]:
            window.typeGlyph(glyph)
        QCoreApplication.processEvents()

        recent = _recent(window)
        assert len(recent) == MAX_RECENT
        assert recent[0] == glyphs[MAX_RECENT + 7], "the newest tap fell off instead of the oldest"

    def test_a_refused_insert_is_not_remembered(self, picker) -> None:
        """`insertGlyph` returns False while an edit field owns the
        keystrokes, and nothing reached the app. Recording it anyway would
        fill the history with glyphs the user never actually typed."""
        _root, window, bridge, _warnings = picker
        glyph = _catalogue(window)[0]["glyphs"][0]
        bridge.setEditMode(True)

        window.typeGlyph(glyph)
        QCoreApplication.processEvents()

        assert _recent(window) == []


class TestTheGrid:
    @staticmethod
    def _cells(root) -> list:
        grid = root.findChild(QQuickItem, "symbolsGrid")
        assert grid is not None, "symbolsGrid not found"
        return [c for c in grid.childItems() if c.property("glyph") is not None]

    def test_a_short_category_still_renders_a_full_page(self, picker) -> None:
        """The Repeater's model is the page size, not the number of glyphs
        left over. A short last page that shrank would pull the pager up the
        window, under a pointer already travelling toward it, which is the
        same rule the Snippets grid follows.
        """
        root, window, _bridge, _warnings = picker
        page_size = window.property("pageSize")
        cells = self._cells(root)
        assert len(cells) == page_size

        filled = [c for c in cells if c.property("glyph")]
        assert 0 < len(filled) <= page_size, "the page rendered no glyphs at all"

    def test_an_empty_cell_is_not_a_target(self, picker) -> None:
        """An empty cell keeps the grid's shape and nothing else. Left
        clickable it would be a tap that silently does nothing, on a window
        whose whole feedback is the glyph appearing somewhere else."""
        root, window, _bridge, _warnings = picker
        # Pick the category with the shortest tail so a page has empties.
        cats = _catalogue(window)
        page_size = window.property("pageSize")
        index = min(
            range(len(cats)),
            key=lambda i: (len(cats[i]["glyphs"]) % page_size) or page_size,
        )
        window.selectCategory(index + 1)
        QCoreApplication.processEvents()
        window.setProperty("page", window.property("pageCount") - 1)
        QCoreApplication.processEvents()

        empties = [c for c in self._cells(root) if not c.property("glyph")]
        assert empties, "no empty cell to check; pick a category with a short last page"
        for cell in empties:
            areas = [a for a in cell.childItems() if a.property("containsMouse") is not None]
            assert areas, "the cell has no mouse area at all"
            assert not any(a.property("enabled") for a in areas)

    def test_changing_category_returns_to_the_first_page(self, picker) -> None:
        """Otherwise a tab switch lands on page 3 of a category with two,
        which the pager then clamps, so the user's first sight of a new
        category is an arbitrary page of it."""
        root, window, _bridge, _warnings = picker
        long_index = max(
            range(len(_catalogue(window))),
            key=lambda i: len(_catalogue(window)[i]["glyphs"]),
        )
        window.selectCategory(long_index + 1)
        QCoreApplication.processEvents()
        assert window.property("pageCount") > 1, "no multi-page category to test with"

        window.setProperty("page", 1)
        window.selectCategory(1)
        QCoreApplication.processEvents()

        assert window.property("page") == 0
        assert self._cells(root), "the grid emptied on the category change"


class TestRecentSurvivesABadSettingsValue:
    """The Recent list is the one piece of state this window reads back from
    outside itself, so it is the one that can arrive malformed."""

    @pytest.mark.parametrize("stored", ["", "not json at all", "{}", '["ok", 5, null]'])
    def test_a_malformed_value_costs_only_the_recent_tab(self, picker_factory, stored: str) -> None:
        """Dropped rather than reported: the catalogue underneath is intact
        and the user is one tap from refilling the list, so a dialog would
        cost more than the thing it is about."""
        root, window, _bridge, warnings = picker_factory({RECENT_KEY: stored})

        recent = _recent(window)
        assert all(isinstance(g, str) and g for g in recent)
        assert TestTheGrid._cells(root), "a bad Recent value emptied the whole grid"
        assert _real_warnings(warnings) == []


class TestTheEntryButtonIcon:
    """The smile is drawn from path data, not typeset.

    Two of its four paths are hand-converted from Feather's source (a
    <circle> written as arcs, and eyes that upstream draws as zero-length
    <line>s relying on the SVG round-cap rule). Both conversions are exactly
    the kind that fail *silently*: a path string the parser rejects paints
    nothing at all and leaves a blank circle on the suggestion bar. So this
    asserts there is ink, which is the property the conversion can break.
    """

    def test_the_icon_paints_something(self, picker) -> None:
        root, _window, _bridge, _warnings = picker
        root.show()
        QCoreApplication.processEvents()

        button = root.findChild(QQuickItem, "symbolsBarButton")
        assert button is not None, "symbolsBarButton not found"
        assert button.property("visible"), "the entry button is hidden"

        shot = root.grabWindow()
        if shot.isNull():  # pragma: no cover - depends on the Qt renderer
            pytest.skip("this Qt build cannot grab an offscreen window")

        top_left = button.mapToScene(button.boundingRect().topLeft())
        size = round(button.property("width"))
        crop = shot.copy(round(top_left.x()), round(top_left.y()), size, size)

        lit = 0
        for y in range(crop.height()):
            for x in range(crop.width()):
                colour = crop.pixelColor(x, y)
                luminance = 0.299 * colour.red() + 0.587 * colour.green() + 0.114 * colour.blue()
                if luminance > 110:
                    lit += 1
        assert lit > 20, f"the icon painted {lit} lit pixels; its path data did not parse"
