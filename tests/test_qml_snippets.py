"""Headless QML tests for the snippets window.

The window's whole behaviour (which of its three views is showing, how the
tile grid pages, what the colour swatches offer) lives in QML, so the
Python suite cannot reach any of it. These load the real `qml/Main.qml`
against a real `KeyboardBridge` under the `offscreen` platform plugin and
drive the window object directly, the same approach as
`test_qml_compact_view.py`.

The failure mode they exist for is a QML binding error, which is a *runtime*
warning rather than an import failure and would otherwise ship as a snippets
window that renders blank or shows two views at once.
"""

from __future__ import annotations

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
    from PySide6.QtCore import QCoreApplication, QObject, QSettings, QUrl  # noqa: E402
    from PySide6.QtGui import QGuiApplication  # noqa: E402
    from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402

    # Imported for the side effect: without QQuickItem somewhere in the
    # module, reading an item's `contentItem` raises "Can't find converter
    # for 'QQuickItem*'". Walking the visual tree is the only way to reach a
    # Repeater's delegates.
    from PySide6.QtQuick import QQuickItem  # noqa: E402,F401
except ImportError as exc:  # pragma: no cover - environment-dependent
    pytest.skip(
        f"Qt GUI libraries unavailable ({exc}); install libegl1/libgl1 to run "
        "the headless QML tests",
        allow_module_level=True,
    )

from src.keyboard_bridge import KeyboardBridge  # noqa: E402
from src.snippets import MAX_SNIPPETS, SNIPPET_COLORS, SnippetStore  # noqa: E402
from tests.qt_settings_scope import TEST_APP, TEST_ORG  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
QML_MAIN = REPO_ROOT / "qml" / "Main.qml"

IGNORED_WARNING_FRAGMENTS = ("does not support customization",)


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
def snippets_window(qapp, tmp_path):
    """Load Main.qml and hand back (windowObject, bridge, warnings)."""
    warnings: list[str] = []
    QSettings(TEST_ORG, TEST_APP).clear()

    # Disarm the startup update check. See test_qml_compact_view.py, where
    # the live HTTPS request from a daemon thread outliving the fixture
    # surfaces as a hard crash.
    settings = QSettings(TEST_ORG, TEST_APP)
    settings.setValue("ui/savedAutoCheckUpdates", False)
    settings.sync()

    with patch("src.keyboard_bridge.create_key_synthesizer") as factory:
        synth = MagicMock()
        synth.is_available.return_value = True
        synth.backend_name.return_value = "MockSynth"
        factory.return_value = synth
        bridge = KeyboardBridge()

    # **Point the bridge at a throwaway store before anything mutates it.**
    # KeyboardBridge loads the developer's real snippets.json on
    # construction, and these tests add and delete snippets: without this
    # swap, running the suite would rewrite the user's own snippets.
    bridge._snippets = SnippetStore(tmp_path / "snippets.json")
    bridge._snippets.load()

    engine = QQmlApplicationEngine()
    engine.warnings.connect(lambda errs: warnings.extend(e.toString() for e in errs))
    engine.rootContext().setContextProperty("keyboard", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_MAIN)))

    assert engine.rootObjects(), "qml/Main.qml failed to load:\n  " + "\n  ".join(warnings)
    root = engine.rootObjects()[0]
    window = root.findChild(QObject, "snippetsWindow")
    assert window is not None, "snippetsWindow not found in the loaded QML"
    # Open it for real rather than merely refreshing: the Connections block
    # that keeps `snippetList` in step with the store is gated on
    # `snippetsWindow.visible`, so a hidden window never sees an add or a
    # delete and every paging assertion below would run against a stale
    # list that only ever grew.
    window.openList()
    try:
        yield window, bridge, warnings
    finally:
        del engine


def _labels_on_screen(window) -> list[str]:
    """Return the label text of every *rendered, visible* tile.

    Walks `childItems()` rather than using `findChildren`: a Repeater's
    delegates are re-parented as *visual* children, so their QObject parent
    is the delegate model and `findChildren` returns an empty list (the trap
    documented in test_qml_prediction_bar.py, which produced a truncation
    test that could never fail).

    Returns strings, never items: a Repeater frees its delegates on every
    model change, and a retained PySide wrapper for a freed QQuickItem
    segfaulted CI in the prediction-bar version of this walk.
    """
    labels: list[str] = []

    def walk(item):
        if item is None:
            return
        for child in item.childItems():
            if child.objectName() == "snippetTile" and child.property("visible"):
                for grandchild in child.childItems():
                    for text in grandchild.childItems():
                        if text.objectName() == "snippetTileLabel":
                            labels.append(text.property("text"))
            walk(child)

    walk(window.property("contentItem"))
    return labels


def _find_named(window, name: str):
    """First item with *name* in the window's visual tree, or None.

    `findChild` does not reach these: items declared in a Window's body are
    reparented under its contentItem, so the QObject parent chain the
    QObject-level search walks is not the tree they live in.
    """
    found = []

    def walk(item):
        if item is None or found:
            return
        for child in item.childItems():
            if child.objectName() == name:
                found.append(child)
                return
            walk(child)

    walk(window.property("contentItem"))
    return found[0] if found else None


def _count_named(window, name: str) -> int:
    total = 0

    def walk(item):
        nonlocal total
        if item is None:
            return
        for child in item.childItems():
            if child.objectName() == name:
                total += 1
            walk(child)

    walk(window.property("contentItem"))
    return total


def _fill(bridge, window, count: int) -> None:
    """Make the store hold exactly *count* snippets, named s0..sN."""
    while len(bridge.getSnippets()) > 0:
        bridge.deleteSnippet(0)
    for i in range(count):
        bridge.addSnippet()
        bridge.setSnippet(i, "s%d" % i, "value %d" % i)
    window.refresh()


class TestTheWindowLoads:
    def test_it_opens_on_the_grid_with_no_qml_warnings(self, snippets_window):
        window, _bridge, warnings = snippets_window
        window.openList()
        assert window.property("editingIndex") == -1
        assert window.property("menuIndex") == -1
        assert window.property("page") == 0
        assert _real_warnings(warnings) == [], "\n".join(_real_warnings(warnings))

    def test_only_one_view_is_ever_showing(self, snippets_window):
        """The three views are siblings gated on the same two indices, so a
        botched condition shows two at once rather than failing loudly."""
        window, _bridge, _w = snippets_window
        grid = _find_named(window, "snipGridView")
        sheet = _find_named(window, "snipSheetView")
        assert grid is not None and sheet is not None

        window.openList()
        assert grid.property("visible") and not sheet.property("visible")

        window.openMenu(1)
        assert sheet.property("visible") and not grid.property("visible")

        window.closeMenu()
        assert grid.property("visible") and not sheet.property("visible")


class TestPaging:
    def test_six_tiles_fill_a_page(self, snippets_window):
        window, bridge, _w = snippets_window
        _fill(bridge, window, 6)
        assert window.property("pageCount") == 1
        _fill(bridge, window, 7)
        assert window.property("pageCount") == 2
        _fill(bridge, window, 14)
        assert window.property("pageCount") == 3

    def test_the_grid_shows_only_the_current_page(self, snippets_window):
        window, bridge, _w = snippets_window
        _fill(bridge, window, 14)

        assert _labels_on_screen(window) == ["s0", "s1", "s2", "s3", "s4", "s5"]
        window.setProperty("page", 1)
        assert _labels_on_screen(window) == ["s6", "s7", "s8", "s9", "s10", "s11"]
        window.setProperty("page", 2)
        assert _labels_on_screen(window) == ["s12", "s13"]

    def test_a_short_last_page_keeps_its_full_height(self, snippets_window):
        """The pager and the Add button sit below the grid, so a last page
        that shrank to its two real tiles would pull both up the window,
        under a pointer already travelling toward one of them."""
        window, bridge, _w = snippets_window
        _fill(bridge, window, 14)
        window.setProperty("page", 2)

        assert len(_labels_on_screen(window)) == 2
        assert _count_named(window, "snippetCell") == window.property("pageSize")

    def test_a_single_page_does_not_pad(self, snippets_window):
        """The inverse: padding unconditionally would leave a four-snippet
        window with two rows of empty space under it."""
        window, bridge, _w = snippets_window
        _fill(bridge, window, 4)
        assert _count_named(window, "snippetCell") == 4

    def test_deleting_the_last_page_does_not_strand_the_grid(self, snippets_window):
        """Regression: `page` is a plain int, not a binding, so a shrinking
        list used to leave it pointing past the end: six empty cells and a
        pager counting "Page 3 of 2"."""
        window, bridge, _w = snippets_window
        _fill(bridge, window, 14)
        window.setProperty("page", 2)

        for _ in range(10):  # take the last page away
            bridge.deleteSnippet(len(bridge.getSnippets()) - 1)

        assert window.property("pageCount") == 1
        assert window.property("page") == 0
        assert _labels_on_screen(window) == ["s0", "s1", "s2", "s3"]


class TestTheActionsSheet:
    def test_moving_follows_the_snippet(self, snippets_window):
        """The sheet is about one snippet. Staying on the index would
        silently retarget it at whichever one swapped in."""
        window, bridge, _w = snippets_window
        _fill(bridge, window, 6)
        window.openMenu(3)

        window.moveSnippet(-1)

        assert window.property("menuIndex") == 2
        assert [s["label"] for s in bridge.getSnippets()][:4] == ["s0", "s1", "s3", "s2"]

    def test_moving_past_either_end_is_refused(self, snippets_window):
        window, bridge, _w = snippets_window
        _fill(bridge, window, 3)

        window.openMenu(0)
        assert window.property("canMoveEarlier") is False
        window.moveSnippet(-1)
        assert [s["label"] for s in bridge.getSnippets()] == ["s0", "s1", "s2"]

        window.openMenu(2)
        assert window.property("canMoveLater") is False
        window.moveSnippet(1)
        assert [s["label"] for s in bridge.getSnippets()] == ["s0", "s1", "s2"]

    def test_a_move_across_a_page_boundary_brings_the_grid_with_it(self, snippets_window):
        window, bridge, _w = snippets_window
        _fill(bridge, window, 8)
        window.openMenu(6)  # first snippet of page 2

        window.moveSnippet(-1)

        assert window.property("menuIndex") == 5
        assert window.property("page") == 0, "the grid would return to a page the snippet left"

    def test_delete_always_asks_first(self, snippets_window):
        """Two steps, always: a snippet has no undo behind it and this window
        is operated with an imprecise pointer."""
        window, bridge, _w = snippets_window
        _fill(bridge, window, 4)

        window.openMenu(1)
        assert window.property("confirmingDelete") is False

        window.setProperty("confirmingDelete", True)
        window.closeMenu()
        assert window.property("confirmingDelete") is False, (
            "a half-answered delete prompt must not be waiting when the sheet reopens"
        )


class TestTappingATileCopies:
    """The tile tap puts the value on the clipboard; it does not type it.

    Typing was the original behaviour and is gone, not demoted: it only
    landed correctly when the caret was already in the right field and the
    app took synthetic keystrokes cleanly, and when it missed it did so
    silently, into whichever window happened to be focused.
    """

    def test_it_copies_and_does_not_type(self, snippets_window):
        window, bridge, _w = snippets_window
        _fill(bridge, window, 3)
        bridge._synth.send_text.reset_mock()

        window.primaryTap(1)

        assert QGuiApplication.clipboard().text() == "value 1"
        bridge._synth.send_text.assert_not_called()

    def test_the_toast_names_the_snippet(self, snippets_window):
        """With colour-tagged near-duplicates on screen, a confirmation that
        does not say *which* one was taken is worth very little. It is also
        the only feedback a clipboard write has: unlike typing, nothing
        appears anywhere."""
        window, bridge, _w = snippets_window
        _fill(bridge, window, 3)
        toast = window.transientParent().findChild(QObject, "snippetCopiedToast")
        assert toast is not None

        window.primaryTap(2)

        assert toast.property("visible") is True
        assert toast.property("snippetLabel") == "s2"

    def test_an_empty_snippet_never_clobbers_the_clipboard(self, snippets_window):
        """The one way this can destroy something: copying "nothing" over
        whatever the user had already put there."""
        window, bridge, _w = snippets_window
        _fill(bridge, window, 2)
        bridge.setSnippet(1, "Blank", "")
        QGuiApplication.clipboard().setText("something the user copied earlier")

        assert bridge.copySnippet(1) is False
        assert QGuiApplication.clipboard().text() == "something the user copied earlier"

    def test_an_out_of_range_index_never_clobbers_the_clipboard(self, snippets_window):
        _window, bridge, _w = snippets_window
        QGuiApplication.clipboard().setText("sentinel")
        assert bridge.copySnippet(999) is False
        assert QGuiApplication.clipboard().text() == "sentinel"

    def test_tapping_an_empty_tile_still_opens_the_editor(self, snippets_window):
        """An empty slot is never a dead tap, copy or no copy."""
        window, bridge, _w = snippets_window
        _fill(bridge, window, 2)
        bridge.setSnippet(1, "Blank", "")
        window.refresh()

        window.primaryTap(1)

        assert window.property("editingIndex") == 1

    def test_privacy_mode_does_not_block_it(self, snippets_window):
        """Same rule as the insert path it replaced: privacy mode is about
        not *learning* from typing, and the user may well need their own
        address in a sensitive form."""
        window, bridge, _w = snippets_window
        _fill(bridge, window, 2)
        bridge.setPrivacyMode(True)
        try:
            assert bridge.copySnippet(0) is True
            assert QGuiApplication.clipboard().text() == "value 0"
        finally:
            bridge.setPrivacyMode(False)


class TestColourTags:
    def test_the_swatches_come_from_the_store(self, snippets_window):
        window, bridge, _w = snippets_window
        assert list(window.property("tagNames")) == bridge.getSnippetColors()
        assert list(window.property("tagNames")) == list(SNIPPET_COLORS)

    def test_every_tag_name_has_an_ink(self, snippets_window):
        """The names live in Python and the hexes in QML, so the two can
        drift: a name added to the store without an ink here would render as
        a tagged snippet with an invisible tag."""
        window, _bridge, _w = snippets_window
        untagged = window.tagColor("")
        inks = {}
        for name in SNIPPET_COLORS:
            if name == "":
                continue
            ink = window.tagColor(name)
            assert ink != untagged, "%s has no ink in Main.qml's tagInks" % name
            assert ink not in inks, "%s and %s share an ink" % (name, inks.get(ink))
            inks[ink] = name

    def test_an_unknown_tag_draws_nothing_rather_than_black(self, snippets_window):
        """A tag from a store newer than this QML must not render as an
        opaque black bar across the tile."""
        window, _bridge, _w = snippets_window
        untagged = window.tagColor("")
        assert window.tagColor("chartreuse") == untagged
        assert window.tagColor(None) == untagged


class TestTheAddButton:
    def test_it_goes_inert_at_the_cap(self, snippets_window):
        """SnippetStore.add refuses past the cap by returning False, which
        the old button could not tell from success: it opened the editor on
        "the last snippet" either way, which at the cap is an existing
        snippet the user never asked to edit."""
        window, bridge, _w = snippets_window
        add = _find_named(window, "snipAddButton")
        assert add is not None

        window.setProperty("snippetList", [{"label": "x", "value": "y", "color": ""}] * 49)
        assert add.property("atCap") is False

        window.setProperty("snippetList", [{"label": "x", "value": "y", "color": ""}] * 50)
        assert add.property("atCap") is True
        assert window.property("snippetLimit") == MAX_SNIPPETS
