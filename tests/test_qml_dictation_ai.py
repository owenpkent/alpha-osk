"""Headless QML tests for the AI half of dictation.

Covers the Write with AI button (bar and title-bar twin), the draft preview
popup, and the new Dictation settings sections (AI Cleanup, Write with AI,
History).  Same harness as `tests/test_qml_dictation.py`: the real
`qml/Main.qml` under the offscreen platform plugin, a real `KeyboardBridge`
with a mocked synthesizer, and a failure on any QML warning.

Nothing here touches the network, the real config directory or the desktop:
the bridge's dictation config is edited in memory (never `save()`d), the
history store is replaced by a fake, and the clipboard / insert paths are
patched at the Python level.
"""

from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, QSettings, QTimer, QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
    from PySide6.QtQuick import QQuickItem  # noqa: F401
except ImportError as exc:  # pragma: no cover - environment-dependent
    pytest.skip(f"Qt GUI libraries unavailable ({exc})", allow_module_level=True)

from src.keyboard_bridge import KeyboardBridge  # noqa: E402
from tests.qml_context import install_context_properties  # noqa: E402
from tests.qt_settings_scope import TEST_APP, TEST_ORG  # noqa: E402
from tests.test_qml_dictation import QML_MAIN  # noqa: E402
from tests.test_qml_settings_panel import (  # noqa: E402
    OVERFLOW_TOLERANCE_PX,
    _find_settings_panel,
    _max_right_edge,
    _open_settings_window,
    _settle_view,
)

IGNORED_WARNING_FRAGMENTS = ("does not support customization",)


def _real_warnings(warnings: list[str]) -> list[str]:
    return [w for w in warnings if not any(f in w for f in IGNORED_WARNING_FRAGMENTS)]


@pytest.fixture(scope="module")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        QCoreApplication.setOrganizationName(TEST_ORG)
        QCoreApplication.setApplicationName(TEST_APP)
        app = QGuiApplication([])
    assert QCoreApplication.organizationName() == TEST_ORG, (
        "another test already created a QGuiApplication under a different "
        "organisation - these tests would write to the real user's settings"
    )
    return app


class FakeHistory:
    """Stands in for DictationHistory so no test reads the real config dir."""

    def __init__(self, entries):
        self._entries = list(entries)

    def entries(self):
        return list(self._entries)

    def delete(self, entry_id):
        self.deleted = getattr(self, "deleted", [])
        self.deleted.append(entry_id)
        self._entries = [e for e in self._entries if e["id"] != entry_id]
        return True

    def get(self, entry_id):
        return next((e for e in self._entries if e["id"] == entry_id), None)


@pytest.fixture
def qml_root(qapp):
    warnings: list[str] = []
    QSettings(TEST_ORG, TEST_APP).clear()
    settings = QSettings(TEST_ORG, TEST_APP)
    settings.setValue("ui/savedAutoCheckUpdates", False)
    settings.sync()

    with (
        patch("src.keyboard_bridge.create_key_synthesizer") as factory,
        patch("src.dictation.audio.available", return_value=True),
    ):
        synth = MagicMock()
        synth.is_available.return_value = True
        synth.backend_name.return_value = "MockSynth"
        factory.return_value = synth
        bridge = KeyboardBridge()
        # In-memory only: nothing below calls save(), so Owen's real
        # dictation.json is never read back or written.
        bridge._dictation_history = FakeHistory([])

        engine = QQmlApplicationEngine()
        engine.warnings.connect(lambda errs: warnings.extend(e.toString() for e in errs))
        install_context_properties(engine, bridge)
        engine.load(QUrl.fromLocalFile(str(QML_MAIN)))

        assert engine.rootObjects(), "qml/Main.qml failed to load:\n  " + "\n  ".join(warnings)
        root = engine.rootObjects()[0]
        try:
            yield root, warnings, bridge
        finally:
            del engine


def _child(root, name: str) -> QObject:
    obj = root.findChild(QObject, name)
    assert obj is not None, f"no object named {name!r} in Main.qml"
    return obj


def _evaluate(obj, expression: str):
    ctx = QQmlEngine.contextForObject(obj)
    value, is_undefined = QQmlExpression(ctx, obj, expression).evaluate()
    assert is_undefined is False, f"QML expression {expression!r} evaluated to undefined"
    return value


def _call(obj, expression: str) -> None:
    """Run a QML statement for its side effect (a void function is undefined)."""
    ctx = QQmlEngine.contextForObject(obj)
    expr = QQmlExpression(ctx, obj, expression)
    expr.evaluate()
    assert not expr.hasError(), expr.error().toString()


def _wait_ms(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _configure(
    root, bridge, *, writing=True, anthropic=True, deepgram=True, enabled=True, cleanup=False
):
    """Put the bridge's in-memory config in a state and let QML pull it."""
    cfg = bridge._dictation_config
    cfg.enabled = enabled
    cfg.api_key = "d" * 40 if deepgram else ""
    cfg.anthropic_key = "a" * 40 if anthropic else ""
    cfg.writing_enabled = writing
    cfg.cleanup_enabled = cleanup
    root.setProperty("dictationEnabled", enabled)
    _call(root, "root.refreshDictation(false)")
    QCoreApplication.processEvents()


def _descendants(item):
    for child in item.childItems():
        yield child
        yield from _descendants(child)


def _named(root, name: str):
    return [i for i in _descendants(root.property("contentItem")) if i.objectName() == name]


class TestTheWriteButtonNeedsEverythingInPlace:
    def test_hidden_by_default(self, qml_root):
        root, warnings, _ = qml_root
        assert _child(root, "writeWithAiBarButton").property("visible") is False
        assert root.property("writeAvailable") is False
        assert _real_warnings(warnings) == []

    def test_visible_when_all_of_it_is_on(self, qml_root):
        root, _, bridge = qml_root
        _configure(root, bridge)
        assert root.property("writeAvailable") is True
        assert _child(root, "writeWithAiBarButton").property("visible") is True

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"writing": False},
            {"anthropic": False},
            {"deepgram": False},
            {"enabled": False},
        ],
    )
    def test_each_missing_piece_hides_it(self, qml_root, kwargs):
        root, _, bridge = qml_root
        _configure(root, bridge)
        assert _child(root, "writeWithAiBarButton").property("visible") is True
        _configure(root, bridge, **kwargs)
        assert _child(root, "writeWithAiBarButton").property("visible") is False

    def test_dictation_setting_off_hides_it_even_with_keys(self, qml_root):
        root, _, bridge = qml_root
        _configure(root, bridge)
        root.setProperty("dictationEnabled", False)
        QCoreApplication.processEvents()
        assert _child(root, "writeWithAiBarButton").property("visible") is False


class TestTheWriteButtonSitsInTheBar:
    """Its scan target id is covered in tests/test_qml_scan_bar.py."""

    def test_the_bar_reserve_counts_it(self, qml_root):
        root, _, bridge = qml_root
        row = _child(root, "predictionRow")
        base = _evaluate(row, "predBar.predButtonCount")
        reserve_before = _evaluate(row, "predBar.clearCtxReserve")
        _configure(root, bridge)
        assert _evaluate(row, "predBar.predButtonCount") == base + 1
        assert _evaluate(row, "predBar.clearCtxReserve") > reserve_before

    def test_the_button_is_inside_the_right_hand_row_and_first(self, qml_root):
        root, _, bridge = qml_root
        _configure(root, bridge)
        write = _child(root, "writeWithAiBarButton")
        symbols = _child(root, "symbolsBarButton")
        clear = _child(root, "clearContextButton")
        scene = root.property("contentItem")
        w = write.mapToItem(scene, 0, 0).x()
        assert w + write.width() <= symbols.mapToItem(scene, 0, 0).x() + 1
        assert w < clear.mapToItem(scene, 0, 0).x()


class TestTheTitleBarTwin:
    def test_it_replaces_the_bar_button_when_suggestions_are_off(self, qml_root):
        root, _, bridge = qml_root
        _configure(root, bridge)
        root.setProperty("suggestionsEnabled", False)
        QCoreApplication.processEvents()
        twin = _child(root, "writeWithAiTitleBarButton")
        assert twin.property("visible") is True
        assert twin.property("width") > 0

    def test_never_both_at_once(self, qml_root):
        root, _, bridge = qml_root
        _configure(root, bridge)
        assert _child(root, "writeWithAiTitleBarButton").property("visible") is False

    def test_absent_when_writing_is_off(self, qml_root):
        root, _, bridge = qml_root
        _configure(root, bridge, writing=False)
        root.setProperty("suggestionsEnabled", False)
        QCoreApplication.processEvents()
        assert _child(root, "writeWithAiTitleBarButton").property("visible") is False


class TestPolishingIsABusyState:
    def test_polishing_reads_as_active_and_busy(self, qml_root):
        root, _, bridge = qml_root
        _configure(root, bridge)
        bridge.dictationStateChanged.emit("polishing")
        QCoreApplication.processEvents()
        assert root.property("dictationActive") is True
        assert root.property("dictationBusy") is True
        assert _child(root, "dictationMicButton").property("busy") is True

    def test_idle_clears_the_write_run(self, qml_root):
        root, _, bridge = qml_root
        root.setProperty("writeRunPending", True)
        bridge.dictationStateChanged.emit("idle")
        QCoreApplication.processEvents()
        assert root.property("writeRunPending") is False

    def test_a_write_run_does_not_light_the_mic(self, qml_root):
        root, _, bridge = qml_root
        _configure(root, bridge)
        root.setProperty("writeRunPending", True)
        bridge.dictationStateChanged.emit("listening")
        QCoreApplication.processEvents()
        assert _child(root, "dictationMicButton").property("listening") is False
        assert _child(root, "writeWithAiBarButton").property("active") is True


class TestErrorsSurfaceTheSameWay:
    def test_a_cleanup_failure_reaches_the_dictation_toast(self, qml_root):
        root, _, bridge = qml_root
        message = "AI cleanup failed (Claude is busy, try again). Inserted as spoken."
        bridge.dictationError.emit(message)
        QCoreApplication.processEvents()
        toast = _child(root, "dictationErrorToast")
        assert toast.property("message") == message
        assert toast.property("opened") is True
        assert toast.property("height") >= 32


class TestTheDraftPopup:
    def test_opens_on_draft_ready_without_taking_an_edit_session(self, qml_root):
        root, warnings, bridge = qml_root
        popup = _child(root, "aiDraftPopup")
        assert popup.property("opened") is False
        bridge.dictationDraftReady.emit("Dear Sam,\nThanks for the update.")
        QCoreApplication.processEvents()
        assert popup.property("opened") is True
        assert popup.property("draft") == "Dear Sam,\nThanks for the update."
        assert popup.property("modal") is False
        assert bridge.editOwner == ""
        assert _real_warnings(warnings) == []

    def test_the_draft_is_plain_text(self, qml_root):
        root, _, bridge = qml_root
        bridge.dictationDraftReady.emit("<img src=x> hello")
        QCoreApplication.processEvents()
        text = _child(root, "aiDraftText")
        assert _evaluate(text, "textFormat === Text.PlainText") is True

    def test_only_escape_closes_it(self, qml_root):
        root, _, _ = qml_root
        popup = _child(root, "aiDraftPopup")
        assert _evaluate(popup, "closePolicy === Popup.CloseOnEscape") is True

    def test_insert_sends_the_draft_and_closes(self, qml_root):
        root, _, bridge = qml_root
        popup = _child(root, "aiDraftPopup")
        bridge.dictationDraftReady.emit("Hello there")
        QCoreApplication.processEvents()
        with patch.object(bridge, "_insert_spoken_text", return_value=True) as insert:
            _call(popup, "insertDraft()")
        insert.assert_called_once_with("Hello there")
        QCoreApplication.processEvents()
        assert popup.property("opened") is False

    def test_a_refused_insert_keeps_it_open_and_flashes_the_problem_toast(self, qml_root):
        root, _, bridge = qml_root
        popup = _child(root, "aiDraftPopup")
        bridge.dictationDraftReady.emit("Hello there")
        QCoreApplication.processEvents()
        with patch.object(bridge, "_insert_spoken_text", return_value=False):
            _call(popup, "insertDraft()")
        QCoreApplication.processEvents()
        assert popup.property("opened") is True
        assert _child(root, "snippetProblemToast").property("opened") is True

    def test_copy_uses_the_clipboard_path_and_stays_open(self, qml_root):
        root, _, bridge = qml_root
        popup = _child(root, "aiDraftPopup")
        bridge.dictationDraftReady.emit("Copy me")
        QCoreApplication.processEvents()
        with patch.object(bridge, "_copy_to_clipboard", return_value=True) as copy:
            _call(popup, "copyDraft()")
        copy.assert_called_once_with("Copy me")
        assert popup.property("opened") is True

    def test_there_are_three_comfortable_buttons(self, qml_root):
        root, _, bridge = qml_root
        bridge.dictationDraftReady.emit("x")
        QCoreApplication.processEvents()
        # Delegates are not QObject children of the popup; reach them by name
        # through the engine.
        for name in ("insert", "copy", "discard"):
            area = _evaluate(_child(root, "aiDraftPopup"), "contentItem").childItems()
            assert area, "the popup has no content"
            found = [
                i
                for i in _descendants(_evaluate(_child(root, "aiDraftPopup"), "contentItem"))
                if i.objectName() == f"aiDraft_{name}"
            ]
            assert found, f"no {name} button in the draft popup"
            assert found[0].height() >= 40

    def test_the_buttons_take_their_colours_from_the_theme(self, qml_root):
        # No literal hue, the rule the keys follow: Insert wears the accent
        # wash, the other two are plain keys, and all three ink with the
        # theme's text colour.
        root, _, bridge = qml_root
        bridge.dictationDraftReady.emit("x")
        QCoreApplication.processEvents()
        buttons = {
            i.objectName()[len("aiDraft_") :]: i
            for i in _descendants(_evaluate(_child(root, "aiDraftPopup"), "contentItem"))
            if i.objectName().startswith("aiDraft_")
        }
        assert set(buttons) == {"insert", "copy", "discard"}
        assert buttons["insert"].property("restFill") == root.property("accentKeyColor")
        for name in ("copy", "discard"):
            assert buttons[name].property("restFill") == root.property("themeKeyColor")
        for button in buttons.values():
            (label,) = [c for c in button.childItems() if c.property("text") is not None]
            assert label.property("color") == root.property("themeTextColor")

    def test_the_error_toast_parks_under_an_open_preview(self, qml_root):
        # A failed writing run with history off shows the request in the
        # preview and explains itself in the toast; both default to y=36.
        root, _, bridge = qml_root
        popup = _child(root, "aiDraftPopup")
        toast = _child(root, "dictationErrorToast")
        bridge.dictationError.emit("AI cleanup failed (timed out). Inserted as spoken.")
        QCoreApplication.processEvents()
        assert toast.property("opened") is True and toast.property("y") == 36
        toast.close()
        bridge.dictationDraftReady.emit("write a note to Alex")
        bridge.dictationError.emit(
            "Write with AI failed (timed out). Your spoken request is shown."
        )
        QCoreApplication.processEvents()
        assert popup.property("opened") is True and toast.property("opened") is True
        assert toast.property("y") >= popup.property("y") + popup.property("height")


SETTINGS_WIDTH_SLACK = OVERFLOW_TOLERANCE_PX


def _dictation_panel(root):
    window = _open_settings_window(root)
    panel = _find_settings_panel(window.property("contentItem"))
    assert panel is not None
    return window, panel


def _by_name(panel, name):
    return [i for i in _descendants(panel) if i.objectName() == name]


class TestTheSettingsSections:
    def test_the_new_sections_render_with_no_warnings(self, qml_root):
        root, warnings, bridge = qml_root
        _configure(root, bridge)
        window, panel = _dictation_panel(root)
        _settle_view(window, panel, "dictation")
        for name in (
            "dictationCleanupToggle",
            "dictationWritingToggle",
            "dictationHistoryToggle",
            "anthropicKeyPasteButton",
            "dictationStylePasteButton",
            "dictationKeyPasteButton",
        ):
            assert _by_name(panel, name), f"{name} missing from the Dictation view"
        assert _real_warnings(warnings) == []

    def test_the_view_still_fits_the_window(self, qml_root):
        root, warnings, bridge = qml_root
        _configure(root, bridge)
        window, panel = _dictation_panel(root)
        _settle_view(window, panel, "dictation")
        worst = _max_right_edge(panel, window.property("contentItem"))
        assert worst <= window.width() + SETTINGS_WIDTH_SLACK
        assert _real_warnings(warnings) == []

    def test_cleanup_and_writing_wait_for_the_anthropic_key(self, qml_root):
        root, _, bridge = qml_root
        _configure(root, bridge, anthropic=False)
        window, panel = _dictation_panel(root)
        _settle_view(window, panel, "dictation")
        assert panel.property("dictationHasAnthropicKey") is False
        assert _by_name(panel, "dictationCleanupToggle")[0].property("enabled") is False
        assert _by_name(panel, "dictationWritingToggle")[0].property("enabled") is False
        _configure(root, bridge)
        assert panel.property("dictationHasAnthropicKey") is True
        assert _by_name(panel, "dictationCleanupToggle")[0].property("enabled") is True

    def test_the_number_formats_come_from_the_bridge(self, qml_root):
        root, _, bridge = qml_root
        _configure(root, bridge)
        _, panel = _dictation_panel(root)
        formats = panel.property("dictationNumberFormats")
        assert [f["id"] for f in formats] == ["smart", "digits", "words"]

    def test_the_real_key_never_reaches_the_panel(self, qml_root):
        root, _, bridge = qml_root
        _configure(root, bridge)
        _, panel = _dictation_panel(root)
        shown = panel.property("dictationMaskedAnthropicKey")
        assert "a" * 10 not in shown

    def test_the_paste_buttons_are_large_labelled_targets(self, qml_root):
        root, _, bridge = qml_root
        _configure(root, bridge)
        window, panel = _dictation_panel(root)
        _settle_view(window, panel, "dictation")
        for name in (
            "dictationKeyPasteButton",
            "anthropicKeyPasteButton",
            "dictationStylePasteButton",
        ):
            button = _by_name(panel, name)[0]
            assert button.property("height") >= 38
            assert button.property("width") >= 72
            assert button.property("text") == "Paste and Save"

    def test_paste_then_save_sends_the_clipboard_text(self, qml_root):
        root, _, bridge = qml_root
        _configure(root, bridge, anthropic=False)
        window, panel = _dictation_panel(root)
        _settle_view(window, panel, "dictation")
        seen = []
        panel.settingChanged.connect(lambda name, value: seen.append((name, value)))
        QGuiApplication.clipboard().setText("sk-ant-test-clipboard")
        _call(_by_name(panel, "anthropicKeyPasteButton")[0], "clicked()")
        assert ("dictationAnthropicKey", "sk-ant-test-clipboard") in seen


HISTORY = [
    {
        "id": "0123456789abcdef",
        "created": "2026-10-10T12:30:00+00:00",
        "purpose": "dictate",
        "raw": "um hello there",
        "result": "Hello there.",
        "cleaned": True,
        "seconds": 2.5,
        "hasAudio": True,
    },
    {
        "id": "fedcba9876543210",
        "created": "2026-10-09T08:00:00+00:00",
        "purpose": "write",
        "raw": "write a note",
        "result": "Hi all,\nA longer written result that runs onto a second line and beyond.",
        "cleaned": False,
        "seconds": 4.0,
        "hasAudio": False,
    },
]


class TestTheHistoryList:
    def _open(self, qml_root, entries=HISTORY):
        root, warnings, bridge = qml_root
        _configure(root, bridge)
        bridge._dictation_history = FakeHistory(entries)
        bridge.dictationHistoryChanged.emit()
        QCoreApplication.processEvents()
        window, panel = _dictation_panel(root)
        _settle_view(window, panel, "dictation")
        return root, warnings, bridge, window, panel

    def test_rows_render_from_the_list(self, qml_root):
        _, warnings, _, _, panel = self._open(qml_root)
        rows = _by_name(panel, "dictationHistoryRow")
        assert len(rows) == 2
        labels = [_by_name(r, "dictationHistoryLabel")[0].property("text") for r in rows]
        assert labels == ["Dictated", "Written"]
        assert _by_name(panel, "dictationHistoryEmpty")[0].property("visible") is False
        assert _real_warnings(warnings) == []

    def test_text_is_plain_and_clipped_to_two_lines(self, qml_root):
        _, _, _, _, panel = self._open(qml_root)
        for text in _by_name(panel, "dictationHistoryText"):
            assert _evaluate(text, "textFormat === Text.PlainText") is True
            assert text.property("maximumLineCount") == 2

    def test_play_shows_only_with_audio_and_follows_playback(self, qml_root):
        _, _, bridge, _, panel = self._open(qml_root)
        rows = _by_name(panel, "dictationHistoryRow")
        with_audio = _by_name(rows[0], "dictationHistoryPlay")[0]
        without_audio = _by_name(rows[1], "dictationHistoryPlay")[0]
        assert with_audio.property("visible") is True
        assert without_audio.property("visible") is False
        assert with_audio.property("text") == "Play"
        bridge.dictationPlaybackChanged.emit("0123456789abcdef")
        QCoreApplication.processEvents()
        assert with_audio.property("text") == "Stop"
        bridge.dictationPlaybackChanged.emit("")
        QCoreApplication.processEvents()
        assert with_audio.property("text") == "Play"

    def test_reprocess_needs_a_dictation_entry_a_key_and_cleanup_on(self, qml_root):
        root, _, bridge, _, panel = self._open(qml_root)
        rows = _by_name(panel, "dictationHistoryRow")
        # A stored key alone does not offer it: the button sends text to
        # Anthropic, and only the cleanup toggle is consent to that.
        assert _by_name(rows[0], "dictationHistoryReprocess")[0].property("visible") is False
        _configure(root, bridge, cleanup=True)
        assert _by_name(rows[0], "dictationHistoryReprocess")[0].property("visible") is True
        assert _by_name(rows[1], "dictationHistoryReprocess")[0].property("visible") is False
        _configure(root, bridge, anthropic=False, cleanup=True)
        assert _by_name(rows[0], "dictationHistoryReprocess")[0].property("visible") is False

    def test_clear_all_takes_two_taps(self, qml_root):
        _, _, _, _, panel = self._open(qml_root)
        seen = []
        panel.settingChanged.connect(lambda name, value: seen.append(name))
        clear = _by_name(panel, "dictationHistoryClearAll")[0]
        _call(clear, "clicked()")
        QCoreApplication.processEvents()
        assert "dictationHistoryClear" not in seen
        assert clear.property("text") == "Yes, delete everything"
        _call(clear, "clicked()")
        assert "dictationHistoryClear" in seen

    def test_delete_takes_two_taps(self, qml_root):
        _, _, bridge, _, panel = self._open(qml_root)
        store = bridge._dictation_history
        row = _by_name(panel, "dictationHistoryRow")[0]
        button = _by_name(row, "dictationHistoryDelete")[0]
        assert button.property("text") == "Delete"
        _call(button, "clicked()")
        QCoreApplication.processEvents()
        assert getattr(store, "deleted", []) == []
        assert button.property("text") == "Confirm delete"
        _call(button, "clicked()")
        QCoreApplication.processEvents()
        assert store.deleted == ["0123456789abcdef"]

    def test_cancel_reverts_the_delete(self, qml_root):
        _, _, bridge, _, panel = self._open(qml_root)
        row = _by_name(panel, "dictationHistoryRow")[0]
        button = _by_name(row, "dictationHistoryDelete")[0]
        cancel = _by_name(row, "dictationHistoryDeleteCancel")[0]
        assert cancel.property("visible") is False
        _call(button, "clicked()")
        QCoreApplication.processEvents()
        assert cancel.property("visible") is True
        _call(cancel, "clicked()")
        QCoreApplication.processEvents()
        assert button.property("text") == "Delete"
        assert cancel.property("visible") is False
        assert getattr(bridge._dictation_history, "deleted", []) == []

    def test_a_pending_delete_reverts_on_its_own(self, qml_root):
        _, _, _, _, panel = self._open(qml_root)
        row = _by_name(panel, "dictationHistoryRow")[0]
        button = _by_name(row, "dictationHistoryDelete")[0]
        _call(button, "clicked()")
        QCoreApplication.processEvents()
        assert button.property("text") == "Confirm delete"
        # The revert timer is 4 s. Poll for it rather than sleeping a fixed
        # 4.4 s: Qt's coarse timers may fire 5% either side of their
        # interval, so on a loaded CI runner the sleep could end before the
        # revert fired, and this test failed on a release PR for exactly
        # that reason.
        deadline = time.monotonic() + 15.0
        while button.property("text") != "Delete" and time.monotonic() < deadline:
            _wait_ms(100)
        assert button.property("text") == "Delete"

    def test_an_empty_history_says_so(self, qml_root):
        _, _, _, _, panel = self._open(qml_root, entries=[])
        assert _by_name(panel, "dictationHistoryRow") == []
        assert _by_name(panel, "dictationHistoryEmpty")[0].property("visible") is True

    def test_the_list_fits_the_window(self, qml_root):
        _, warnings, _, window, panel = self._open(qml_root)
        worst = _max_right_edge(panel, window.property("contentItem"))
        assert worst <= window.width() + SETTINGS_WIDTH_SLACK
        assert _real_warnings(warnings) == []
