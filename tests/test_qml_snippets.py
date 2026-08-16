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
    from PySide6.QtCore import (  # noqa: E402
        QCoreApplication,
        QObject,
        QPointF,
        QSettings,
        Qt,
        QUrl,
    )
    from PySide6.QtGui import QGuiApplication  # noqa: E402
    from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402

    # Imported for the side effect: without QQuickItem somewhere in the
    # module, reading an item's `contentItem` raises "Can't find converter
    # for 'QQuickItem*'". Walking the visual tree is the only way to reach a
    # Repeater's delegates.
    from PySide6.QtQuick import QQuickItem  # noqa: E402,F401
    from PySide6.QtTest import QTest  # noqa: E402
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


def _qml_root(window):
    """The Main.qml root Window.

    ``parent()`` is null: a QML ``Window`` declared inside another
    Window's body is still a top-level object.  Qt does set the outer one
    as its *transient* parent, which is the only link back.
    """
    root = window.transientParent()
    assert root is not None, "snippetsWindow has no transient parent"
    return root


class TestTheTileDispatchesOnMouseButton:
    """Left copies, right opens the sheet, and neither had any coverage.

    Every other test in this file calls ``openMenu()`` / ``primaryTap()``
    on the window object directly, so none of them route through the
    delegate's own handler: swapping the two branches of the tile's
    ``onClicked``, or dropping ``Qt.RightButton`` from
    ``acceptedButtons`` so right-click does nothing at all, left the
    whole suite green.  That is the same shape as the
    ``findChildren``-returns-nothing trap this file's docstring warns
    about, and it was hiding the interaction the CHANGELOG leads with.

    A synthetic click cannot be delivered to a Repeater delegate
    reliably here (the offscreen window's layout has not settled, so
    every tile maps to the same scene point), so the branch was lifted
    into ``tileClicked`` and the delegate reduced to a pass-through.
    What is left uncovered is that one line, and ``acceptedButtons``
    covers the half of it that can silently break.
    """

    def test_right_click_opens_the_actions_sheet_for_that_tile(self, snippets_window):
        window, bridge, _w = snippets_window
        _fill(bridge, window, 3)
        window.openList()
        window.tileClicked(0, Qt.MouseButton.RightButton)
        assert window.property("menuIndex") == 0

    def test_left_click_does_not_open_the_sheet(self, snippets_window):
        """The inverse half: it is what catches the two branches swapped."""
        window, bridge, _w = snippets_window
        _fill(bridge, window, 3)
        window.openList()
        window.tileClicked(0, Qt.MouseButton.LeftButton)
        assert window.property("menuIndex") == -1

    def test_left_click_on_a_filled_tile_hides_the_window(self, snippets_window):
        """Copy is invisible, so the hide is its observable half."""
        window, bridge, _w = snippets_window
        _fill(bridge, window, 3)
        window.openList()
        assert window.property("visible")
        window.tileClicked(0, Qt.MouseButton.LeftButton)
        assert not window.property("visible")

    def test_left_click_on_an_empty_slot_opens_the_editor(self, snippets_window):
        """An empty seeded slot is never a dead tap."""
        window, bridge, _w = snippets_window
        _fill(bridge, window, 2)
        bridge.setSnippet(0, "blank", "")
        window.refresh()
        window.tileClicked(0, Qt.MouseButton.LeftButton)
        assert window.property("editingIndex") == 0

    def test_manage_mode_makes_a_left_click_open_the_sheet(self, snippets_window):
        """The left-click-only route to editing, recolouring and deleting.

        Right-click is the only other way in and press-and-hold
        deliberately is not one, so without this a pointer that can emit
        only left clicks could copy a snippet and nothing else: never
        edit, recolour, reorder or delete one, and never free a slot at
        the cap.
        """
        window, bridge, _w = snippets_window
        _fill(bridge, window, 3)
        window.openList()
        window.toggleManage()
        assert window.property("manageMode")
        window.tileClicked(0, Qt.MouseButton.LeftButton)
        assert window.property("menuIndex") == 0

    def test_manage_mode_does_not_copy_or_hide_the_window(self, snippets_window):
        """Copy is unreachable in the mode, so a mis-tap destroys nothing."""
        window, bridge, _w = snippets_window
        _fill(bridge, window, 3)
        window.openList()
        window.toggleManage()
        window.tileClicked(0, Qt.MouseButton.LeftButton)
        assert window.property("visible")

    def test_it_is_off_by_default_and_toggles_back(self, snippets_window):
        """The inverse: copying is what the window is for."""
        window, bridge, _w = snippets_window
        _fill(bridge, window, 3)
        window.openList()
        assert not window.property("manageMode")
        window.toggleManage()
        window.toggleManage()
        assert not window.property("manageMode")
        window.tileClicked(0, Qt.MouseButton.LeftButton)
        assert window.property("menuIndex") == -1

    def test_reopening_the_window_leaves_manage_mode(self, snippets_window):
        """It is an errand, not a preference."""
        window, bridge, _w = snippets_window
        _fill(bridge, window, 3)
        window.toggleManage()
        window.openList()
        assert not window.property("manageMode")

    def test_the_toggle_does_not_resize_when_it_flips(self, snippets_window):
        """A shrinking button slides the close ✕ under a moving pointer.

        Sized to the live label it went from "Manage" to the 24 px
        narrower "Done", which drags every control to its right along the
        header at the moment the user is most likely to be reaching for
        one of them.
        """
        window, bridge, _w = snippets_window
        _fill(bridge, window, 3)
        window.openList()
        button = _find_named(window, "snipManageButton")
        assert button is not None
        before = button.width()
        window.toggleManage()
        assert button.width() == before

    def test_the_toggle_is_hidden_outside_the_grid(self, snippets_window):
        """Nothing to manage from inside the sheet or the editor."""
        window, bridge, _w = snippets_window
        _fill(bridge, window, 3)
        window.openList()
        button = _find_named(window, "snipManageButton")
        assert button is not None and button.property("visible")
        window.openMenu(0)
        assert not button.property("visible")
        window.closeMenu()
        window.beginEdit(0)
        assert not button.property("visible")

    def test_the_tile_still_accepts_the_right_button(self, snippets_window):
        """The half the pass-through cannot state on its own.

        Drop ``Qt.RightButton`` from ``acceptedButtons`` and the sheet
        becomes unreachable while every other assertion here still
        passes, since they call the function the event would have.
        """
        window, bridge, _w = snippets_window
        _fill(bridge, window, 3)
        window.openList()
        area = _find_named(window, "snippetTileMouse")
        assert area is not None, "no tile MouseArea rendered"
        accepted = area.property("acceptedButtons")
        assert accepted & Qt.MouseButton.RightButton
        assert accepted & Qt.MouseButton.LeftButton


class TestTheSheetTracksItsOwnSnippet:
    """An index is not an identity.

    A Data Backup import replaces the whole list underneath an open
    sheet (``importUserData`` reloads the store and emits
    ``snippetsChanged``), and the sheet used to keep its index: Delete,
    Edit and the colour swatches then acted on whatever the import had
    put there, and an index past the new end opened a blank editor whose
    save was a silent no-op behind a green "Saved".
    """

    def _replace_the_store(self, bridge, tmp_path, labels):
        """What importUserData does: swap the file, reload, announce."""
        store = SnippetStore(tmp_path / "imported.json")
        store.load()
        while len(store.get_all()) > 0:
            store.delete(0)
        for i, label in enumerate(labels):
            store.add()
            store.set(i, label, "imported %s" % label)
        bridge._snippets = store
        bridge.snippetsChanged.emit(store.get_all())

    def test_an_import_closes_a_sheet_pointing_at_a_different_snippet(
        self, snippets_window, tmp_path
    ):
        window, bridge, _w = snippets_window
        _fill(bridge, window, 4)
        window.openMenu(3)
        assert window.property("menuIndex") == 3
        self._replace_the_store(bridge, tmp_path, ["a", "b", "c", "d"])
        assert window.property("menuIndex") == -1

    def test_an_import_ends_an_edit_pointing_at_a_different_snippet(
        self, snippets_window, tmp_path
    ):
        window, bridge, _w = snippets_window
        _fill(bridge, window, 4)
        window.beginEdit(3)
        assert window.property("editingIndex") == 3
        self._replace_the_store(bridge, tmp_path, ["a", "b", "c", "d"])
        assert window.property("editingIndex") == -1

    def test_setting_a_colour_keeps_the_sheet_open(self, snippets_window):
        """The inverse: the sheet's own mutations must not close it.

        Recolouring is meant to leave the sheet up, so an identity that
        included the colour would close it on every swatch tap.
        """
        window, bridge, _w = snippets_window
        _fill(bridge, window, 3)
        window.openMenu(1)
        bridge.setSnippetColor(1, "red")
        assert window.property("menuIndex") == 1

    def test_moving_follows_the_snippet_and_keeps_the_sheet_open(self, snippets_window):
        window, bridge, _w = snippets_window
        _fill(bridge, window, 3)
        window.openMenu(0)
        window.moveSnippet(1)
        assert window.property("menuIndex") == 1
        assert bridge.getSnippets()[1]["label"] == "s0"


class TestSavingReportsWhetherItSaved:
    """``setSnippet`` returned nothing, so the editor always said "Saved".

    The same failure ``acceptSnippetOffer`` was given a bool return for:
    the store refuses and the interface confirms anyway, which is the one
    kind of feedback worse than none.
    """

    def test_an_out_of_range_index_reports_failure(self, snippets_window):
        _window, bridge, _w = snippets_window
        assert bridge.setSnippet(99, "nope", "nope") is False

    def test_a_real_index_reports_success(self, snippets_window):
        window, bridge, _w = snippets_window
        _fill(bridge, window, 2)
        assert bridge.setSnippet(0, "hello", "world") is True
        assert bridge.getSnippets()[0]["value"] == "world"


class TestTheEditorSupportsOrdinaryTextEditing:
    """Reported: double-click does not select, and Tab does nothing.

    This window cannot hold OS focus (``WindowDoesNotAcceptFocus``), so
    every text interaction is either routed through the edit-mode signals
    or handled by the field itself, and both halves had a hole.

    The mouse half is the one worth remembering. ``selectByMouse`` was
    true the whole time and ``selectWord()`` worked when invoked
    directly, so nothing was missing except the events: each field
    carried a ``MouseArea`` filling it, whose entire job is to take the
    press. Caret placement, double-click word selection, triple-click
    and drag-select were all dead behind it, and no test noticed because
    the suite drove the fields through their QML API.
    """

    @staticmethod
    def _editor(window, bridge, value: str = "hello world here"):
        _fill(bridge, window, 3)
        bridge.setSnippet(1, "Email", value)
        window.refresh()
        window.beginEdit(1)
        QCoreApplication.processEvents()
        field = _find_named(window, "snipValueField")
        assert field is not None, "snipValueField not found in the editor"
        return field

    def test_a_double_click_selects_a_word(self, snippets_window):
        window, bridge, warnings = snippets_window
        field = self._editor(window, bridge)

        # Over the first word rather than the middle of the box: the text
        # is left-aligned, so the centre of a 300-odd px field is past
        # the end of a short value and would select nothing even when
        # this works.
        point = field.mapToScene(QPointF(24.0, field.height() / 2)).toPoint()
        QTest.mouseDClick(window, Qt.LeftButton, Qt.NoModifier, point)
        QCoreApplication.processEvents()

        assert field.property("selectedText") == "hello", (
            "double-click selected "
            f"{field.property('selectedText')!r}; the MouseArea over the "
            "field is taking the press before the text input sees it"
        )
        assert _real_warnings(warnings) == []

    def test_a_single_click_places_the_caret(self, snippets_window):
        """The same swallowed press, in its quieter form.

        Clicking into the middle of a value to fix one character put the
        caret nowhere: it stayed wherever it happened to be.
        """
        window, bridge, _w = snippets_window
        field = self._editor(window, bridge)
        field.setProperty("cursorPosition", 0)

        point = field.mapToScene(QPointF(60.0, field.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
        QCoreApplication.processEvents()

        assert field.property("cursorPosition") > 0, "the caret did not move to the click"

    def test_clicking_a_field_still_aims_the_keyboard_at_it(self, snippets_window):
        """The inverse of the fix: passing the press through must not lose
        the bookkeeping the press was there for in the first place."""
        window, bridge, _w = snippets_window
        self._editor(window, bridge)
        window.setProperty("editTarget", "value")

        label = _find_named(window, "snipLabelField")
        point = label.mapToScene(QPointF(24.0, label.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
        QCoreApplication.processEvents()

        assert window.property("editTarget") == "label"

    def test_tab_moves_to_the_other_field(self, snippets_window):
        window, bridge, _w = snippets_window
        self._editor(window, bridge)
        assert window.property("editTarget") == "value"

        bridge.pressSpecialKey("tab")
        QCoreApplication.processEvents()
        assert window.property("editTarget") == "label"

        bridge.pressSpecialKey("tab")
        QCoreApplication.processEvents()
        assert window.property("editTarget") == "value", "Tab does not cycle back"

    def test_tab_selects_the_field_it_lands_on(self, snippets_window):
        """So the common edit, replacing a value outright, is one gesture
        rather than holding Backspace over an address."""
        window, bridge, _w = snippets_window
        self._editor(window, bridge)

        bridge.pressSpecialKey("tab")
        QCoreApplication.processEvents()
        label = _find_named(window, "snipLabelField")
        assert label.property("selectedText") == "Email"

    def test_typing_after_tab_replaces_the_selection(self, snippets_window):
        """The end-to-end reason the selection matters."""
        window, bridge, _w = snippets_window
        self._editor(window, bridge)

        bridge.pressSpecialKey("tab")
        QCoreApplication.processEvents()
        bridge.pressKey("x")
        QCoreApplication.processEvents()

        label = _find_named(window, "snipLabelField")
        assert label.property("text") == "x"

    def test_shift_and_arrow_extends_the_selection(self, snippets_window):
        window, bridge, _w = snippets_window
        field = self._editor(window, bridge)
        field.setProperty("cursorPosition", 0)

        bridge.toggleShift()
        for _ in range(5):
            bridge.pressSpecialKey("right")
        QCoreApplication.processEvents()

        assert field.property("selectedText") == "hello", (
            "Shift with an arrow key moved the caret instead of selecting"
        )

    def test_ctrl_a_selects_everything(self, snippets_window):
        """Ctrl+A used to insert the letter "a"."""
        window, bridge, _w = snippets_window
        field = self._editor(window, bridge)

        bridge.toggleCtrl()
        bridge.pressKey("a")
        QCoreApplication.processEvents()

        assert field.property("selectedText") == "hello world here"
        assert field.property("text") == "hello world here", (
            "the chord was typed into the field as well as acted on"
        )

    def test_ctrl_v_pastes_the_clipboard(self, snippets_window):
        """The one that earns the feature: a long address costs a click
        per character to type, and nothing per character to paste."""
        window, bridge, _w = snippets_window
        field = self._editor(window, bridge, value="")
        QGuiApplication.clipboard().setText("128 Juniper Lane")

        bridge.toggleCtrl()
        bridge.pressKey("v")
        QCoreApplication.processEvents()

        assert field.property("text") == "128 Juniper Lane"

    def test_ctrl_c_then_ctrl_v_round_trips(self, snippets_window):
        window, bridge, _w = snippets_window
        field = self._editor(window, bridge, value="repeat")
        QGuiApplication.clipboard().setText("")

        bridge.toggleCtrl()
        bridge.pressKey("a")
        bridge.toggleCtrl()
        bridge.pressKey("c")
        QCoreApplication.processEvents()
        field.setProperty("cursorPosition", field.property("length"))
        bridge.toggleCtrl()
        bridge.pressKey("v")
        QCoreApplication.processEvents()

        assert field.property("text") == "repeatrepeat"

    def test_an_arrow_without_shift_still_just_moves(self, snippets_window):
        """The inverse half: selection must not become the default."""
        window, bridge, _w = snippets_window
        field = self._editor(window, bridge)
        field.setProperty("cursorPosition", 0)

        bridge.pressSpecialKey("right")
        QCoreApplication.processEvents()

        assert field.property("selectedText") == ""
        assert field.property("cursorPosition") == 1


class TestTheRestoredPositionIsClampedToTheWholeDesktop:
    """The clamp used the primary screen's 0-origin dimensions.

    A window saved at x=2400 on a second monitor came back at 1560 on the
    primary one every launch, and a monitor to the *left* of the primary
    has negative coordinates that collapsed to 0 the same way.  That is
    worse than not persisting at all, because the window lands nowhere
    near the keyboard it belongs to.

    **The multi-monitor case cannot be exercised here**: the offscreen
    platform plugin gives exactly one screen.  What these pin is that the
    clamp reads the union of ``Qt.application.screens`` rather than
    ``Screen.width``, and that a position already on-screen survives
    untouched.  The second-monitor arithmetic follows by construction
    once the bounds come from the screen list.
    """

    @staticmethod
    def _js(value, *keys):
        """Read numbers out of a QJSValue.

        A QML function returning a JS object hands back a QJSValue, which
        is not subscriptable from Python.
        """
        return tuple(value.property(k).toNumber() for k in keys)

    def _bounds(self, window):
        b = _qml_root(window).desktopBounds()
        left, top, right, bottom = self._js(b, "left", "top", "right", "bottom")
        return {"left": left, "top": top, "right": right, "bottom": bottom}

    def test_the_bounds_are_the_union_of_every_screen(self, snippets_window, qapp):
        window, _bridge, _w = snippets_window
        geometries = [s.geometry() for s in qapp.screens()]
        bounds = self._bounds(window)
        assert bounds["left"] == min(g.x() for g in geometries)
        assert bounds["top"] == min(g.y() for g in geometries)
        assert bounds["right"] == max(g.x() + g.width() for g in geometries)
        assert bounds["bottom"] == max(g.y() + g.height() for g in geometries)

    def test_a_position_already_on_screen_is_returned_unchanged(self, snippets_window):
        window, _bridge, _w = snippets_window
        root = _qml_root(window)
        b = self._bounds(window)
        x, y = self._js(root.clampedWindowPos(b["left"] + 20, b["top"] + 30, 360, 400), "x", "y")
        assert x == b["left"] + 20
        assert y == b["top"] + 30

    def test_a_position_past_the_right_edge_is_pulled_fully_back_on(self, snippets_window):
        window, _bridge, _w = snippets_window
        root = _qml_root(window)
        b = self._bounds(window)
        (x,) = self._js(root.clampedWindowPos(b["right"] + 5000, b["top"], 360, 400), "x")
        assert x == b["right"] - 360

    def test_the_left_edge_is_the_desktop_origin_not_zero(self, snippets_window):
        """With one screen at x=0 these agree; the point is which is read."""
        window, _bridge, _w = snippets_window
        root = _qml_root(window)
        b = self._bounds(window)
        (x,) = self._js(root.clampedWindowPos(b["left"] - 5000, b["top"], 360, 400), "x")
        assert x == b["left"]
