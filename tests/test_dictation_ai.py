"""Tests for AI cleanup, Write with AI, history capture and their settings.

Three layers, each with its collaborators faked at the seam:

* the **config** additions (round trips, damaged input, the new key never
  leaking);
* the **controller**, against a fake Deepgram stream and a fake Claude
  client, so no test needs a microphone, a network or an account;
* the **bridge** slots, against a history rooted in a temp dir.
"""

from __future__ import annotations

import json
import os
import wave
from pathlib import Path
from typing import Any, List
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal

from src.dictation import config as dictation_config
from src.dictation.claude import CLEANUP_MODEL, WRITING_MODEL
from src.dictation.config import MAX_SPEECH_NOTES_LEN, MAX_STYLE_LEN, DictationConfig
from src.dictation.controller import DictationController, DictationState
from src.dictation.history import DictationHistory
from src.keyboard_bridge import KeyboardBridge
from tests.test_dictation import _FakeStream

ANTHROPIC = "sk-ant-api03-averysecretanthropickey0123456789"


@pytest.fixture
def qapp():
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtGui import QGuiApplication

    from tests.qt_settings_scope import TEST_APP, TEST_ORG

    app = QGuiApplication.instance()
    if app is None:
        QCoreApplication.setOrganizationName(TEST_ORG)
        QCoreApplication.setApplicationName(TEST_APP)
        app = QGuiApplication([])
    assert QCoreApplication.organizationName() == TEST_ORG
    return app


# --- config ------------------------------------------------------------


@pytest.fixture
def cfg_path(tmp_path: Path) -> Path:
    return tmp_path / "dictation.json"


class TestConfigAdditions:
    def test_old_files_load_with_defaults(self, cfg_path: Path):
        cfg_path.write_text(json.dumps({"enabled": True, "api_key": "k" * 40}), encoding="utf-8")
        cfg = DictationConfig.load(cfg_path)
        assert cfg.enabled is True
        assert cfg.has_anthropic_key is False
        assert cfg.cleanup_enabled is False and cfg.number_format == "smart"
        assert cfg.writing_enabled is False and cfg.writing_style == ""
        assert cfg.history_enabled is True
        assert cfg.auto_copy is True

    def test_auto_copy_round_trips_off(self, cfg_path: Path):
        DictationConfig(auto_copy=False).save(cfg_path)
        assert DictationConfig.load(cfg_path).auto_copy is False

    def test_round_trip(self, cfg_path: Path):
        DictationConfig(
            anthropic_key=ANTHROPIC,
            cleanup_enabled=True,
            number_format="words",
            writing_enabled=True,
            writing_style="Warm.\nBrief.",
            history_enabled=False,
        ).save(cfg_path)
        again = DictationConfig.load(cfg_path)
        assert again.anthropic_key == ANTHROPIC
        assert again.cleanup_enabled is True and again.number_format == "words"
        assert again.writing_enabled is True and again.writing_style == "Warm.\nBrief."
        assert again.history_enabled is False

    def test_the_anthropic_key_is_not_plaintext_on_disk_on_windows(self, cfg_path: Path):
        if not dictation_config._dpapi_available():
            pytest.skip("DPAPI is Windows-only")
        DictationConfig(anthropic_key=ANTHROPIC, api_key="d" * 40).save(cfg_path)
        raw = cfg_path.read_text(encoding="utf-8")
        assert ANTHROPIC not in raw
        data = json.loads(raw)
        assert data["anthropic_key_protected"] is True and data["key_protected"] is True
        assert DictationConfig.load(cfg_path).anthropic_key == ANTHROPIC

    def test_each_key_keeps_its_own_protected_flag(self, cfg_path: Path):
        cfg_path.write_text(
            json.dumps(
                {
                    "api_key": "plain-deepgram-key",
                    "key_protected": False,
                    "anthropic_key": "bm90LWEtcmVhbC1ibG9i",
                    "anthropic_key_protected": True,
                }
            ),
            encoding="utf-8",
        )
        cfg = DictationConfig.load(cfg_path)
        assert cfg.api_key == "plain-deepgram-key"
        assert cfg.anthropic_key == ""  # unwrappable here, loads as unset

    def test_repr_and_masks_never_leak_the_key(self):
        cfg = DictationConfig(anthropic_key=ANTHROPIC, writing_style="private voice")
        assert ANTHROPIC not in repr(cfg) and "private voice" not in repr(cfg)
        masked = cfg.masked_anthropic_key()
        assert masked.startswith(ANTHROPIC[:4]) and ANTHROPIC not in masked
        assert DictationConfig().masked_anthropic_key() == ""

    @pytest.mark.parametrize(
        "bad",
        [None, 5, [], {}, "", "klingon", ["words"]],
    )
    def test_a_bad_number_format_falls_back(self, cfg_path: Path, bad: Any):
        cfg_path.write_text(json.dumps({"number_format": bad}), encoding="utf-8")
        assert DictationConfig.load(cfg_path).number_format == "smart"

    def test_the_style_is_cleaned_and_capped(self, cfg_path: Path):
        style = "a\x00b\x07c\nd" + "z" * (MAX_STYLE_LEN * 2)
        cfg_path.write_text(json.dumps({"writing_style": style}), encoding="utf-8")
        loaded = DictationConfig.load(cfg_path).writing_style
        assert loaded.startswith("abc\nd") and len(loaded) == MAX_STYLE_LEN
        cfg_path.write_text(json.dumps({"writing_style": 12}), encoding="utf-8")
        assert DictationConfig.load(cfg_path).writing_style == ""

    def test_cleanup_ready_needs_both(self):
        assert DictationConfig(cleanup_enabled=True).cleanup_ready is False
        assert DictationConfig(anthropic_key="k").cleanup_ready is False
        assert DictationConfig(cleanup_enabled=True, anthropic_key="k").cleanup_ready is True


# --- controller --------------------------------------------------------


class _FakeRequest(QObject):
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.aborted = False

    def abort(self) -> None:
        self.aborted = True


class _FakeClaude:
    def __init__(self) -> None:
        self.calls: List[dict[str, Any]] = []
        self.requests: List[_FakeRequest] = []

    def send(self, model, system, user_text, **kwargs):
        self.calls.append({"model": model, "system": system, "user": user_text, **kwargs})
        request = _FakeRequest()
        self.requests.append(request)
        return request

    @property
    def last(self) -> _FakeRequest:
        return self.requests[-1]


@pytest.fixture
def claude() -> _FakeClaude:
    return _FakeClaude()


@pytest.fixture
def history(tmp_path: Path) -> DictationHistory:
    return DictationHistory(tmp_path / "history")


def _make_controller(qapp, monkeypatch, claude, history, **cfg_fields) -> DictationController:
    cfg = DictationConfig(enabled=True, api_key="k" * 40, **cfg_fields)
    monkeypatch.setattr("src.dictation.controller.DeepgramStream", _FakeStream)
    ctl = DictationController(cfg, claude_client=claude, history=history)
    monkeypatch.setattr(ctl._capture, "start", lambda device="": True)
    monkeypatch.setattr(ctl._capture, "stop", lambda: None)
    return ctl


class _Spy:
    def __init__(self, ctl: DictationController) -> None:
        self.finals: List[str] = []
        self.drafts: List[str] = []
        self.errors: List[str] = []
        self.states: List[str] = []
        self.interims: List[str] = []
        self.runs: List[str] = []
        self.history_changes = 0
        ctl.finalReady.connect(self.finals.append)
        ctl.draftReady.connect(self.drafts.append)
        ctl.errorRaised.connect(self.errors.append)
        ctl.stateChanged.connect(self.states.append)
        ctl.interimChanged.connect(self.interims.append)
        ctl.runFinished.connect(self.runs.append)
        ctl.historyChanged.connect(self._bump)

    def _bump(self) -> None:
        self.history_changes += 1


def _speak(ctl: DictationController, *phrases: str, purpose: str = "dictate") -> None:
    """Start, say each phrase as a final, stop, and let the provider close."""
    ctl.start(purpose)
    _FakeStream.last.opened.emit()
    for phrase in phrases:
        _FakeStream.last.final.emit(phrase)
    ctl.stop()
    _FakeStream.last.closed.emit()


class TestAHangUpBeforeTheFlushFinishesKeepsTheRun:
    """The real provider wired to the real controller.  A remote close after
    Stop is quiet only once Deepgram's Metadata says the flush finished;
    before that it must reach the controller as `failed`, which is the only
    path that keeps an untranscribed run's audio in History.  Qt reports the
    hang-up either through errorOccurred or as a plain disconnect, and both
    must end the same way."""

    @pytest.fixture(params=["error", "disconnect"])
    def hang_up(self, request):
        from tests.test_dictation import _remote_close

        def _hang_up() -> None:
            if request.param == "error":
                self._socket().errorOccurred.emit(_remote_close())
            else:
                self._socket().disconnected.emit()

        return _hang_up

    @pytest.fixture
    def ctl(self, qapp, monkeypatch, claude, history) -> DictationController:
        import PySide6.QtWebSockets

        from tests.test_dictation import _FakeSocket

        monkeypatch.setattr(PySide6.QtWebSockets, "QWebSocket", _FakeSocket)
        ctl = DictationController(
            DictationConfig(enabled=True, api_key="k" * 40), claude_client=claude, history=history
        )
        monkeypatch.setattr(ctl._capture, "start", lambda device="": True)
        monkeypatch.setattr(ctl._capture, "stop", lambda: None)
        return ctl

    @staticmethod
    def _socket():
        from tests.test_dictation import _FakeSocket

        return _FakeSocket.last

    def _stop_after(self, ctl: DictationController, *finals: str) -> None:
        from src.dictation.controller import _PCM_BYTES_PER_SECOND

        ctl.start()
        self._socket().connected.emit()
        ctl._on_chunk(b"\x01\x00" * (int(_PCM_BYTES_PER_SECOND * 3.0) // 2))
        for text in finals:
            self._socket().textMessageReceived.emit(
                json.dumps({"is_final": True, "channel": {"alternatives": [{"transcript": text}]}})
            )
        ctl.stop()

    def test_no_final_before_the_hang_up_keeps_the_audio(self, ctl, history, hang_up):
        spy = _Spy(ctl)
        self._stop_after(ctl)
        hang_up()
        assert len(spy.errors) == 1 and "saved in History" in spy.errors[0]
        entries = history.entries()
        assert len(entries) == 1
        assert entries[0]["raw"] == "" and entries[0]["hasAudio"] is True
        assert ctl.state is DictationState.IDLE

    def test_a_partial_transcript_is_delivered_and_the_drop_is_said(self, ctl, history, hang_up):
        spy = _Spy(ctl)
        self._stop_after(ctl, "the first half")
        hang_up()
        assert spy.finals == ["the first half"]
        assert spy.errors == ["Dictation stopped: the connection was lost."]
        assert history.entries()[0]["result"] == "the first half"

    def test_a_finished_flush_then_a_hang_up_is_quiet(self, ctl, history, hang_up):
        from tests.test_dictation import _metadata

        spy = _Spy(ctl)
        self._stop_after(ctl, "all of it")
        self._socket().textMessageReceived.emit(_metadata())
        hang_up()
        assert spy.errors == []
        assert spy.finals == ["all of it"]
        assert history.entries()[0]["result"] == "all of it"
        assert ctl.state is DictationState.IDLE


class TestAFailedRunKeepsWhatWasHeard:
    """Three lessons from MacroVox, applied here.

    A stream that fails after words were heard ends like a stop (history
    and the held-back text survive).  A stream that fails before anything
    was transcribed keeps its audio as an audio-only history entry, and
    the toast says so.  A flush timeout on an empty run says nothing.
    """

    def _spoken_audio(self, ctl: DictationController, seconds: float = 2.0) -> None:
        from src.dictation.controller import _PCM_BYTES_PER_SECOND

        ctl._on_chunk(b"\x01\x00" * (int(_PCM_BYTES_PER_SECOND * seconds) // 2))

    def test_a_streamed_run_that_drops_still_records_history(
        self, qapp, monkeypatch, claude, history
    ):
        ctl = _make_controller(qapp, monkeypatch, claude, history, stream_inserts=True)
        spy = _Spy(ctl)
        ctl.start()
        _FakeStream.last.opened.emit()
        _FakeStream.last.final.emit("typed as I spoke")
        self._spoken_audio(ctl)
        _FakeStream.last.failed.emit("Dictation stopped: the connection was lost.")
        assert spy.errors == ["Dictation stopped: the connection was lost."]
        # Streamed finals were already typed; the record is what was owed.
        assert spy.finals == ["typed as I spoke"]
        entries = history.entries()
        assert len(entries) == 1
        assert entries[0]["result"] == "typed as I spoke"
        assert entries[0]["hasAudio"] is True
        assert ctl.state is DictationState.IDLE

    def test_a_held_back_run_that_drops_is_delivered(self, qapp, monkeypatch, claude, history):
        ctl = _make_controller(qapp, monkeypatch, claude, history, stream_inserts=False)
        spy = _Spy(ctl)
        ctl.start()
        _FakeStream.last.opened.emit()
        _FakeStream.last.final.emit("held back")
        _FakeStream.last.failed.emit("Dictation stopped: the connection was lost.")
        assert spy.finals == ["held back"]
        assert history.entries()[0]["result"] == "held back"

    def test_a_run_that_never_transcribed_keeps_its_audio(self, qapp, monkeypatch, claude, history):
        ctl = _make_controller(qapp, monkeypatch, claude, history)
        spy = _Spy(ctl)
        ctl.start()
        self._spoken_audio(ctl, seconds=3.0)
        _FakeStream.last.failed.emit(
            "Could not make a secure connection to the dictation service: check your connection."
        )
        assert spy.errors == [
            "Could not make a secure connection to the dictation service: "
            "check your connection. The audio is saved in History."
        ]
        assert spy.finals == []
        entries = history.entries()
        assert len(entries) == 1
        assert entries[0]["raw"] == "" and entries[0]["result"] == ""
        assert entries[0]["hasAudio"] is True
        assert ctl.state is DictationState.IDLE

    def test_a_rejected_key_keeps_the_audio_too(self, qapp, monkeypatch, claude, history):
        ctl = _make_controller(qapp, monkeypatch, claude, history)
        spy = _Spy(ctl)
        ctl.start()
        self._spoken_audio(ctl)
        _FakeStream.last.closed.emit()
        assert spy.errors == [
            "Dictation could not start: check your API key in Settings. "
            "The audio is saved in History."
        ]
        assert history.entries()[0]["hasAudio"] is True

    def test_under_a_second_of_audio_is_not_worth_an_entry(
        self, qapp, monkeypatch, claude, history
    ):
        ctl = _make_controller(qapp, monkeypatch, claude, history)
        spy = _Spy(ctl)
        ctl.start()
        self._spoken_audio(ctl, seconds=0.4)
        _FakeStream.last.failed.emit("Dictation stopped: the connection was lost.")
        assert spy.errors == ["Dictation stopped: the connection was lost."]
        assert history.entries() == []

    def test_history_off_keeps_nothing_and_says_nothing_about_it(
        self, qapp, monkeypatch, claude, history
    ):
        ctl = _make_controller(qapp, monkeypatch, claude, history, history_enabled=False)
        spy = _Spy(ctl)
        ctl.start()
        self._spoken_audio(ctl)
        _FakeStream.last.failed.emit("Dictation stopped: the connection was lost.")
        assert spy.errors == ["Dictation stopped: the connection was lost."]
        assert history.entries() == []

    def test_a_flush_timeout_on_an_empty_run_is_silent(self, qapp, monkeypatch, claude, history):
        from src.dictation.providers import FLUSH_TIMEOUT_MESSAGE

        ctl = _make_controller(qapp, monkeypatch, claude, history)
        spy = _Spy(ctl)
        ctl.start()
        _FakeStream.last.opened.emit()
        ctl.stop()
        _FakeStream.last.failed.emit(FLUSH_TIMEOUT_MESSAGE)
        assert spy.errors == []
        assert ctl.state is DictationState.IDLE

    def test_a_flush_timeout_after_speech_delivers_and_warns(
        self, qapp, monkeypatch, claude, history
    ):
        from src.dictation.providers import FLUSH_TIMEOUT_MESSAGE

        ctl = _make_controller(qapp, monkeypatch, claude, history, stream_inserts=False)
        spy = _Spy(ctl)
        ctl.start()
        _FakeStream.last.opened.emit()
        _FakeStream.last.final.emit("most of it")
        ctl.stop()
        _FakeStream.last.failed.emit(FLUSH_TIMEOUT_MESSAGE)
        assert spy.errors == [FLUSH_TIMEOUT_MESSAGE]
        assert spy.finals == ["most of it"]

    def test_privacy_cancel_still_discards_everything(self, qapp, monkeypatch, claude, history):
        """The one path that may throw audio away is unchanged."""
        ctl = _make_controller(qapp, monkeypatch, claude, history)
        ctl.start()
        self._spoken_audio(ctl)
        ctl.cancel()
        assert history.entries() == []


@pytest.fixture
def cleanup_ctl(qapp, monkeypatch, claude, history):
    return _make_controller(
        qapp,
        monkeypatch,
        claude,
        history,
        anthropic_key=ANTHROPIC,
        cleanup_enabled=True,
        number_format="digits",
        language="fr",
    )


class TestAccumulateMode:
    def test_cleanup_holds_finals_back_and_shows_nothing_in_the_bar(self, cleanup_ctl):
        """The owner's call (2026-10-24): a held-back run previews nothing.
        The bar's own "Listening..." label and the level bars beside the mic
        are the feedback; the text appears once, when it is typed."""
        spy = _Spy(cleanup_ctl)
        cleanup_ctl.start()
        _FakeStream.last.opened.emit()
        _FakeStream.last.final.emit("hello world")
        _FakeStream.last.interim.emit("how are")
        assert spy.finals == []
        assert spy.interims == []
        assert cleanup_ctl.interim == ""
        _FakeStream.last.final.emit("how are you")
        assert spy.finals == []
        assert spy.interims == []
        # ...and nothing while Claude has it either: the raw words on screen
        # during polishing read as "this is being typed" (review of #212).
        cleanup_ctl.stop()
        _FakeStream.last.closed.emit()
        assert cleanup_ctl.state is DictationState.POLISHING
        assert cleanup_ctl.interim == ""
        assert "hello world how are you" not in spy.interims
        assert all(text == "" for text in spy.interims)

    def test_write_with_ai_previews_nothing_while_writing(self, qapp, monkeypatch, claude, history):
        ctl = _make_controller(
            qapp, monkeypatch, claude, history, anthropic_key=ANTHROPIC, writing_enabled=True
        )
        spy = _Spy(ctl)
        ctl.start("write")
        _FakeStream.last.opened.emit()
        _FakeStream.last.interim.emit("write a note to")
        _FakeStream.last.final.emit("write a note to the team")
        assert all(text == "" for text in spy.interims)
        ctl.stop()
        _FakeStream.last.closed.emit()
        assert ctl.state is DictationState.POLISHING
        assert ctl.interim == ""
        assert spy.drafts == []
        assert len(claude.requests) == 1

    def test_type_as_you_speak_off_previews_nothing_either(
        self, qapp, monkeypatch, claude, history
    ):
        ctl = _make_controller(qapp, monkeypatch, claude, history, stream_inserts=False)
        spy = _Spy(ctl)
        ctl.start()
        _FakeStream.last.opened.emit()
        _FakeStream.last.interim.emit("hello wor")
        _FakeStream.last.final.emit("hello world")
        assert spy.interims == []
        assert ctl.interim == ""
        ctl.stop()
        _FakeStream.last.closed.emit()
        assert spy.finals == ["hello world"]

    def test_streaming_without_cleanup_still_types_each_phrase(
        self, qapp, monkeypatch, claude, history
    ):
        ctl = _make_controller(qapp, monkeypatch, claude, history)
        spy = _Spy(ctl)
        ctl.start()
        _FakeStream.last.opened.emit()
        _FakeStream.last.final.emit("one")
        assert spy.finals == ["one"]

    def test_stream_inserts_off_without_cleanup_inserts_once_raw(
        self, qapp, monkeypatch, claude, history
    ):
        ctl = _make_controller(qapp, monkeypatch, claude, history, stream_inserts=False)
        spy = _Spy(ctl)
        _speak(ctl, "first part", "second part")
        assert spy.finals == ["first part second part"]
        assert claude.calls == []
        assert ctl.state is DictationState.IDLE

    def test_a_dropped_stream_still_delivers_the_held_back_words(
        self, qapp, monkeypatch, claude, history
    ):
        # Held-back finals exist only in the controller.  A network drop is
        # not a privacy cancel, so what was said must still arrive.
        ctl = _make_controller(qapp, monkeypatch, claude, history, stream_inserts=False)
        spy = _Spy(ctl)
        ctl.start()
        _FakeStream.last.opened.emit()
        _FakeStream.last.final.emit("said before the drop")
        _FakeStream.last.failed.emit("Connection lost.")
        assert spy.finals == ["said before the drop"]
        assert spy.errors == ["Connection lost."]
        assert ctl.state is DictationState.IDLE

    def test_a_dropped_stream_with_cleanup_on_still_polishes(self, cleanup_ctl, claude):
        spy = _Spy(cleanup_ctl)
        cleanup_ctl.start()
        _FakeStream.last.opened.emit()
        _FakeStream.last.final.emit("said before the drop")
        _FakeStream.last.failed.emit("Connection lost.")
        assert cleanup_ctl.state is DictationState.POLISHING
        assert len(claude.calls) == 1
        assert spy.finals == []

    def test_a_dropped_stream_with_nothing_said_just_ends(self, qapp, monkeypatch, claude, history):
        ctl = _make_controller(qapp, monkeypatch, claude, history, stream_inserts=False)
        spy = _Spy(ctl)
        ctl.start()
        _FakeStream.last.opened.emit()
        _FakeStream.last.failed.emit("Connection lost.")
        assert spy.finals == []
        assert ctl.state is DictationState.IDLE


class TestTheModeIsFixedAtStart:
    """A settings change mid-run must not change how the run ends.

    The accumulate decision was already made once at `start()`, but the
    finish read `cleanup_ready` live, so cleanup switched on after the first
    streamed final sent the whole run for polishing and typed the result on
    top of the phrases already on screen.
    """

    def test_cleanup_turned_on_mid_run_does_not_retype_the_streamed_phrases(
        self, qapp, monkeypatch, claude, history
    ):
        ctl = _make_controller(qapp, monkeypatch, claude, history, anthropic_key=ANTHROPIC)
        spy = _Spy(ctl)
        ctl.start()
        _FakeStream.last.opened.emit()
        _FakeStream.last.final.emit("first sentence")
        assert spy.finals == ["first sentence"]
        ctl._config.cleanup_enabled = True
        _FakeStream.last.final.emit("second sentence")
        ctl.stop()
        _FakeStream.last.closed.emit()
        # Each phrase arrived exactly once, raw, and Claude was never asked.
        assert spy.finals == ["first sentence", "second sentence"]
        assert claude.calls == []
        assert ctl.state is DictationState.IDLE
        (entry,) = history.entries()
        assert entry["raw"] == "first sentence second sentence" and entry["cleaned"] is False

    def test_cleanup_turned_off_mid_run_still_polishes_the_held_back_run(self, cleanup_ctl, claude):
        spy = _Spy(cleanup_ctl)
        cleanup_ctl.start()
        _FakeStream.last.opened.emit()
        _FakeStream.last.final.emit("held back")
        cleanup_ctl._config.cleanup_enabled = False
        cleanup_ctl.stop()
        _FakeStream.last.closed.emit()
        # The words were never typed, so the run it started is the run it
        # finishes: one request, and nothing inserted until it answers.
        assert cleanup_ctl.state is DictationState.POLISHING
        assert len(claude.calls) == 1 and spy.finals == []
        claude.last.succeeded.emit("Held back.")
        assert spy.finals == ["Held back."]


class TestTheMicrophoneFailing:
    """A microphone that goes away mid-run must not take the words with it.

    `_on_capture_failed` used to cancel outright, which cleared a held-back
    run nobody had typed yet and recorded nothing.  It now stops: the socket
    is still up, so the provider flushes the tail and the run ends the way a
    stop does.  Privacy mode's `cancel()` is untouched.
    """

    def test_a_held_back_run_is_still_delivered(self, qapp, monkeypatch, claude, history):
        ctl = _make_controller(qapp, monkeypatch, claude, history, stream_inserts=False)
        spy = _Spy(ctl)
        ctl.start()
        _FakeStream.last.opened.emit()
        _FakeStream.last.final.emit("said before the mic died")
        ctl._capture.failed.emit("Microphone disconnected.")
        assert spy.errors == ["Microphone disconnected."]
        # Stopped, not cancelled: the provider is asked for the tail.
        assert ctl.state is DictationState.FINISHING
        assert _FakeStream.last.finished is True
        _FakeStream.last.final.emit("and the tail")
        _FakeStream.last.closed.emit()
        assert spy.finals == ["said before the mic died and the tail"]
        assert ctl.state is DictationState.IDLE
        (entry,) = history.entries()
        assert entry["raw"] == "said before the mic died and the tail"

    def test_with_cleanup_on_the_run_is_still_polished(self, cleanup_ctl, claude):
        spy = _Spy(cleanup_ctl)
        cleanup_ctl.start()
        _FakeStream.last.opened.emit()
        _FakeStream.last.final.emit("said before the mic died")
        cleanup_ctl._capture.failed.emit("Microphone disconnected.")
        _FakeStream.last.closed.emit()
        assert cleanup_ctl.state is DictationState.POLISHING
        assert len(claude.calls) == 1 and spy.finals == []

    def test_a_streamed_run_is_recorded_and_not_retyped(self, qapp, monkeypatch, claude, history):
        ctl = _make_controller(qapp, monkeypatch, claude, history)
        spy = _Spy(ctl)
        ctl.start()
        _FakeStream.last.opened.emit()
        _FakeStream.last.final.emit("already typed")
        ctl._capture.failed.emit("Microphone disconnected.")
        _FakeStream.last.closed.emit()
        assert spy.finals == ["already typed"]
        assert ctl.state is DictationState.IDLE
        (entry,) = history.entries()
        assert entry["raw"] == "already typed"

    def test_with_nothing_said_the_run_is_torn_down(self, qapp, monkeypatch, claude, history):
        ctl = _make_controller(qapp, monkeypatch, claude, history, stream_inserts=False)
        spy = _Spy(ctl)
        ctl.start()
        ctl._capture.failed.emit("No microphone found.")
        assert ctl.state is DictationState.IDLE
        assert spy.finals == [] and history.entries() == []
        assert spy.errors == ["No microphone found."]


class TestCleanup:
    def test_success_inserts_the_cleaned_text_once(self, cleanup_ctl, claude, history):
        spy = _Spy(cleanup_ctl)
        _speak(cleanup_ctl, "um hello world", "i have 3 dogs")
        assert cleanup_ctl.state is DictationState.POLISHING
        assert cleanup_ctl.active is True
        assert len(claude.calls) == 1
        call = claude.calls[0]
        assert call["model"] == CLEANUP_MODEL and call["effort"] == "low"
        assert call["api_key"] == ANTHROPIC and call["timeout_ms"] == 20_000
        assert "um hello world i have 3 dogs" in call["user"]
        assert "<transcript>" in call["user"]
        assert "French" in call["system"] and "digits" in call["system"]
        assert 2048 <= call["max_tokens"] <= 8000
        assert spy.finals == []

        claude.last.succeeded.emit("Hello world. I have 3 dogs.")
        assert spy.finals == ["Hello world. I have 3 dogs."]
        assert spy.errors == []
        assert cleanup_ctl.state is DictationState.IDLE
        assert spy.states[-2:] == ["polishing", "idle"]
        (entry,) = history.entries()
        assert entry["raw"] == "um hello world i have 3 dogs"
        assert entry["result"] == "Hello world. I have 3 dogs."
        assert entry["cleaned"] is True and entry["purpose"] == "dictate"
        assert spy.history_changes == 1

    def test_failure_inserts_the_raw_text_and_says_so(self, cleanup_ctl, claude, history):
        spy = _Spy(cleanup_ctl)
        _speak(cleanup_ctl, "just the raw words")
        claude.last.failed.emit("Claude is busy, try again.")
        assert spy.finals == ["just the raw words"]
        assert len(spy.errors) == 1
        assert "AI cleanup failed" in spy.errors[0] and "Inserted as spoken" in spy.errors[0]
        assert "busy" in spy.errors[0]
        assert cleanup_ctl.state is DictationState.IDLE
        (entry,) = history.entries()
        assert entry["result"] == "just the raw words" and entry["cleaned"] is False

    def test_an_empty_transcript_sends_nothing(self, cleanup_ctl, claude, history):
        spy = _Spy(cleanup_ctl)
        _speak(cleanup_ctl)
        assert claude.calls == [] and spy.finals == []
        assert cleanup_ctl.state is DictationState.IDLE
        assert history.entries() == []

    def test_cleanup_off_never_calls_claude(self, qapp, monkeypatch, claude, history):
        ctl = _make_controller(
            qapp, monkeypatch, claude, history, anthropic_key=ANTHROPIC, cleanup_enabled=False
        )
        _speak(ctl, "hello")
        assert claude.calls == []

    def test_cleanup_on_without_a_key_stays_raw(self, qapp, monkeypatch, claude, history):
        ctl = _make_controller(qapp, monkeypatch, claude, history, cleanup_enabled=True)
        spy = _Spy(ctl)
        _speak(ctl, "hello")
        assert claude.calls == [] and spy.finals == ["hello"]


class TestPolishingState:
    def test_a_stray_toggle_does_nothing(self, cleanup_ctl, claude):
        spy = _Spy(cleanup_ctl)
        _speak(cleanup_ctl, "keep these words")
        cleanup_ctl.toggle()
        assert cleanup_ctl.state is DictationState.POLISHING
        assert claude.last.aborted is False
        claude.last.succeeded.emit("Keep these words.")
        assert spy.finals == ["Keep these words."]

    def test_cancel_aborts_the_request_and_delivers_nothing(self, cleanup_ctl, claude, history):
        spy = _Spy(cleanup_ctl)
        _speak(cleanup_ctl, "a password said aloud")
        cleanup_ctl.cancel()
        assert claude.last.aborted is True
        assert cleanup_ctl.state is DictationState.IDLE
        claude.last.succeeded.emit("late text")
        claude.last.failed.emit("late failure")
        assert spy.finals == [] and spy.errors == [] and spy.drafts == []
        assert history.entries() == []

    def test_a_cancelled_live_run_is_not_recorded(self, cleanup_ctl, history):
        cleanup_ctl.start()
        _FakeStream.last.opened.emit()
        _FakeStream.last.final.emit("words")
        cleanup_ctl.cancel()
        assert history.entries() == []


class TestWriting:
    @pytest.fixture
    def writer(self, qapp, monkeypatch, claude, history):
        return _make_controller(
            qapp,
            monkeypatch,
            claude,
            history,
            anthropic_key=ANTHROPIC,
            writing_enabled=True,
            writing_style="Short and kind.",
        )

    def test_success_yields_a_draft_and_never_a_final(self, writer, claude, history):
        spy = _Spy(writer)
        _speak(writer, "write an email to Sam", "about lunch", purpose="write")
        call = claude.calls[0]
        assert call["model"] == WRITING_MODEL and call["effort"] == "medium"
        assert call["max_tokens"] == 8000 and call["timeout_ms"] == 60_000
        assert "<instruction>" in call["user"] and "about lunch" in call["user"]
        assert "Short and kind." in call["system"]
        assert spy.finals == []  # not even while the request is out

        claude.last.succeeded.emit("Hi Sam, lunch?")
        assert spy.drafts == ["Hi Sam, lunch?"] and spy.finals == []
        (entry,) = history.entries()
        assert entry["purpose"] == "write" and entry["raw"].startswith("write an email")
        assert entry["result"] == "Hi Sam, lunch?"

    def test_failure_raises_an_error_and_keeps_the_request(self, writer, claude, history):
        spy = _Spy(writer)
        _speak(writer, "write a note to Alex", purpose="write")
        claude.last.failed.emit("Claude declined this request.")
        assert spy.drafts == [] and spy.finals == []
        assert len(spy.errors) == 1 and "declined" in spy.errors[0]
        assert "saved in History" in spy.errors[0]
        (entry,) = history.entries()
        assert entry["raw"] == "write a note to Alex" and entry["cleaned"] is False

    def test_failure_with_history_off_shows_the_request_instead(
        self, qapp, monkeypatch, claude, history
    ):
        # With history off the spoken instruction has no durable copy, so
        # the message must not promise one.  The preview is where it goes:
        # readable and copyable, and nothing written to disk against the
        # user's setting.
        ctl = _make_controller(
            qapp,
            monkeypatch,
            claude,
            history,
            anthropic_key=ANTHROPIC,
            writing_enabled=True,
            history_enabled=False,
        )
        spy = _Spy(ctl)
        _speak(ctl, "write a note to Alex", purpose="write")
        claude.last.failed.emit("The request timed out.")
        assert spy.drafts == ["write a note to Alex"] and spy.finals == []
        assert len(spy.errors) == 1 and "timed out" in spy.errors[0]
        assert "History" not in spy.errors[0] and "copy" in spy.errors[0]
        assert history.entries() == []
        assert ctl._run_text == "" and ctl.state is DictationState.IDLE

    def test_failure_when_the_store_refuses_does_not_claim_an_entry(
        self, writer, claude, history, monkeypatch
    ):
        monkeypatch.setattr(history, "add", lambda *a, **k: "")
        spy = _Spy(writer)
        _speak(writer, "write a note to Alex", purpose="write")
        claude.last.failed.emit("Claude declined this request.")
        assert spy.drafts == ["write a note to Alex"]
        assert "History" not in spy.errors[0]
        assert spy.history_changes == 0

    def test_it_needs_an_anthropic_key(self, qapp, monkeypatch, claude, history):
        ctl = _make_controller(qapp, monkeypatch, claude, history, writing_enabled=True)
        spy = _Spy(ctl)
        ctl.start("write")
        assert ctl.state is DictationState.IDLE
        assert spy.errors and "Anthropic" in spy.errors[0]

    def test_a_write_run_with_cleanup_on_is_not_also_cleaned(
        self, qapp, monkeypatch, claude, history
    ):
        ctl = _make_controller(
            qapp,
            monkeypatch,
            claude,
            history,
            anthropic_key=ANTHROPIC,
            writing_enabled=True,
            cleanup_enabled=True,
        )
        _speak(ctl, "write hello", purpose="write")
        assert [c["model"] for c in claude.calls] == [WRITING_MODEL]


class TestHistoryCapture:
    def test_a_streamed_run_is_recorded_raw_with_audio(self, qapp, monkeypatch, claude, history):
        ctl = _make_controller(qapp, monkeypatch, claude, history)
        spy = _Spy(ctl)
        ctl.start()
        _FakeStream.last.opened.emit()
        ctl._on_chunk(b"\x01\x00" * 800)
        _FakeStream.last.final.emit("hello")
        ctl.stop()
        _FakeStream.last.closed.emit()
        (entry,) = history.entries()
        assert entry["raw"] == "hello" and entry["result"] == "hello"
        assert entry["cleaned"] is False and entry["hasAudio"] is True
        assert spy.history_changes == 1
        path = history.audio_path(entry["id"])
        assert path is not None
        with wave.open(str(path), "rb") as wav:
            assert wav.getnframes() == 800

    def test_audio_is_capped_at_the_run_ceiling(self, qapp, monkeypatch, claude, history):
        ctl = _make_controller(qapp, monkeypatch, claude, history)
        ctl._config.max_seconds = 15
        ctl.start()
        cap = 15 * 32_000
        ctl._on_chunk(b"\x00" * (cap - 10))
        ctl._on_chunk(b"\x00" * 1000)
        ctl._on_chunk(b"\x00" * 1000)
        assert len(ctl._pcm) == cap

    def test_disabled_history_records_nothing(self, qapp, monkeypatch, claude, history):
        ctl = _make_controller(qapp, monkeypatch, claude, history, history_enabled=False)
        ctl.start()
        _FakeStream.last.opened.emit()
        ctl._on_chunk(b"\x00\x00" * 100)
        assert len(ctl._pcm) == 0
        _FakeStream.last.final.emit("hello")
        ctl.stop()
        _FakeStream.last.closed.emit()
        assert history.entries() == []


class TestReprocess:
    def _seed(self, history: DictationHistory, purpose: str = "dictate") -> str:
        return history.add(purpose, "raw um words", "raw um words", False, 2.0, None)

    def test_it_updates_the_entry(self, cleanup_ctl, claude, history):
        entry_id = self._seed(history)
        spy = _Spy(cleanup_ctl)
        assert cleanup_ctl.reprocess(entry_id) is True
        assert claude.calls[0]["model"] == CLEANUP_MODEL
        assert "raw um words" in claude.calls[0]["user"]
        claude.last.succeeded.emit("Raw words.")
        entry = history.get(entry_id)
        assert entry is not None and entry["result"] == "Raw words." and entry["cleaned"] is True
        assert spy.history_changes == 1 and spy.finals == []

    def test_failure_is_reported_and_leaves_the_entry(self, cleanup_ctl, claude, history):
        entry_id = self._seed(history)
        spy = _Spy(cleanup_ctl)
        cleanup_ctl.reprocess(entry_id)
        claude.last.failed.emit("Claude is busy, try again.")
        assert spy.errors and "AI cleanup failed" in spy.errors[0]
        entry = history.get(entry_id)
        assert entry is not None and entry["result"] == "raw um words"

    def test_refused_while_a_run_is_active(self, cleanup_ctl, claude, history):
        entry_id = self._seed(history)
        cleanup_ctl.start()
        assert cleanup_ctl.reprocess(entry_id) is False
        assert claude.calls == []

    def test_refused_without_a_key_for_unknown_ids_and_writes(
        self, qapp, monkeypatch, claude, history
    ):
        keyless = _make_controller(qapp, monkeypatch, claude, history)
        entry_id = self._seed(history)
        assert keyless.reprocess(entry_id) is False
        ready = _make_controller(
            qapp, monkeypatch, claude, history, anthropic_key=ANTHROPIC, cleanup_enabled=True
        )
        assert ready.reprocess("f" * 16) is False
        assert ready.reprocess("../x") is False
        assert ready.reprocess(self._seed(history, "write")) is False
        assert claude.calls == []

    def test_refused_with_the_cleanup_toggle_off_even_with_a_key(
        self, qapp, monkeypatch, claude, history
    ):
        # The privacy page promises that with both AI features off Anthropic
        # is never contacted.  A key left in Settings is not consent, so the
        # toggle gates this the way it gates a live run.
        keyed = _make_controller(qapp, monkeypatch, claude, history, anthropic_key=ANTHROPIC)
        spy = _Spy(keyed)
        entry_id = self._seed(history)
        assert keyed.reprocess(entry_id) is False
        assert claude.calls == []
        assert spy.errors and "AI cleanup" in spy.errors[0]
        entry = history.get(entry_id)
        assert entry is not None and entry["result"] == "raw um words"

    def test_cancel_aborts_a_reprocess(self, cleanup_ctl, claude, history):
        entry_id = self._seed(history)
        cleanup_ctl.reprocess(entry_id)
        cleanup_ctl.cancel()
        assert claude.last.aborted is True
        claude.last.succeeded.emit("late")
        entry = history.get(entry_id)
        assert entry is not None and entry["result"] == "raw um words"


# --- bridge --------------------------------------------------------------


@pytest.fixture
def bridge(tmp_path: Path, qapp, claude) -> KeyboardBridge:
    with patch("src.keyboard_bridge.create_key_synthesizer") as mock_factory:
        mock_synth = MagicMock()
        mock_synth.busy = False  # a bare mock's busy is truthy
        mock_synth.is_available.return_value = True
        mock_synth.backend_name.return_value = "MockSynth"
        mock_factory.return_value = mock_synth
        b = KeyboardBridge()
        b._synth = mock_synth
        from src.snippets import SnippetStore

        b._snippets = SnippetStore(tmp_path / "snippets.json")
        b._snippets.load()
        b._dictation_history = DictationHistory(tmp_path / "bridge-history")
        b._claude_client = claude
        return b


def _sent(bridge: KeyboardBridge) -> str:
    return "".join(call.args[0] for call in bridge._synth.send_text.call_args_list)


class TestBridgeSettings:
    def test_new_keys_in_the_settings_map(self, bridge: KeyboardBridge):
        data = bridge.getDictationSettings()
        for key in (
            "hasAnthropicKey",
            "maskedAnthropicKey",
            "cleanupEnabled",
            "numberFormat",
            "numberFormats",
            "writingEnabled",
            "writingStyle",
            "historyEnabled",
            "autoCopy",
        ):
            assert key in data
        assert data["numberFormats"] == [
            {"id": "smart", "label": "Smart"},
            {"id": "digits", "label": "Digits"},
            {"id": "words", "label": "Words"},
        ]
        assert data["hasAnthropicKey"] is False and data["historyEnabled"] is True

    def test_the_key_never_comes_back_in_the_clear(self, bridge: KeyboardBridge):
        assert bridge.setDictationAnthropicKey(f"  {ANTHROPIC[:20]}\n{ANTHROPIC[20:]} ") is True
        data = bridge.getDictationSettings()
        assert data["hasAnthropicKey"] is True
        assert ANTHROPIC not in json.dumps(data)
        assert bridge._dictation_config.anthropic_key == ANTHROPIC

    def test_setters_persist(self, bridge: KeyboardBridge):
        bridge.setDictationAnthropicKey(ANTHROPIC)
        bridge.setDictationCleanupEnabled(True)
        bridge.setDictationNumberFormat("words")
        bridge.setDictationWritingEnabled(True)
        assert bridge.setDictationWritingStyle("Plain\x00 and warm") is True
        bridge.setDictationHistoryEnabled(False)
        bridge.setDictationAutoCopy(False)
        again = DictationConfig.load()
        assert again.auto_copy is False
        assert again.anthropic_key == ANTHROPIC and again.cleanup_enabled is True
        assert again.number_format == "words" and again.writing_enabled is True
        assert again.writing_style == "Plain and warm" and again.history_enabled is False

    def test_an_unknown_number_format_is_refused(self, bridge: KeyboardBridge):
        bridge.setDictationNumberFormat("roman")
        assert bridge._dictation_config.number_format == "smart"

    def test_clearing_the_key_cancels_a_request_in_flight(
        self, bridge: KeyboardBridge, claude, monkeypatch
    ):
        bridge.setDictationAnthropicKey(ANTHROPIC)
        bridge._dictation_config.cleanup_enabled = True
        bridge._dictation_config.enabled = True
        bridge._dictation_config.api_key = "k" * 40
        monkeypatch.setattr("src.dictation.controller.DeepgramStream", _FakeStream)
        ctl = bridge._dictation_controller()
        monkeypatch.setattr(ctl._capture, "start", lambda device="": True)
        monkeypatch.setattr(ctl._capture, "stop", lambda: None)
        _speak(ctl, "words")
        assert bridge.clearDictationAnthropicKey() is True
        assert claude.last.aborted is True
        assert bridge.getDictationSettings()["hasAnthropicKey"] is False


class TestToggleAiWriting:
    def _errors(self, bridge: KeyboardBridge) -> List[str]:
        errors: List[str] = []
        bridge.dictationError.connect(errors.append)
        return errors

    def test_refused_in_privacy_mode(self, bridge: KeyboardBridge):
        errors = self._errors(bridge)
        bridge.setPrivacyMode(True)
        bridge.toggleAiWriting()
        assert errors and "password" in errors[0].lower()
        assert bridge._dictation is None

    def test_refused_when_disabled(self, bridge: KeyboardBridge):
        errors = self._errors(bridge)
        bridge.toggleAiWriting()
        assert errors and "Write with AI" in errors[0]
        assert bridge._dictation is None

    def test_refused_without_the_anthropic_key(self, bridge: KeyboardBridge):
        errors = self._errors(bridge)
        bridge.setDictationWritingEnabled(True)
        bridge.toggleAiWriting()
        assert errors and "Anthropic" in errors[0]

    def test_refused_without_the_deepgram_key(self, bridge: KeyboardBridge):
        errors = self._errors(bridge)
        bridge.setDictationWritingEnabled(True)
        bridge.setDictationAnthropicKey(ANTHROPIC)
        bridge.toggleAiWriting()
        assert errors and "Deepgram" in errors[0]

    def test_starts_a_write_run(self, bridge: KeyboardBridge, monkeypatch):
        bridge.setDictationWritingEnabled(True)
        bridge.setDictationAnthropicKey(ANTHROPIC)
        bridge.setDictationApiKey("k" * 40)
        bridge.setDictationEnabled(True)
        monkeypatch.setattr("src.dictation.controller.DeepgramStream", _FakeStream)
        ctl = bridge._dictation_controller()
        monkeypatch.setattr(ctl._capture, "start", lambda device="": True)
        monkeypatch.setattr(ctl._capture, "stop", lambda: None)
        drafts: List[str] = []
        bridge.dictationDraftReady.connect(drafts.append)
        bridge.toggleAiWriting()
        assert ctl.state is DictationState.CONNECTING and ctl._purpose == "write"
        _FakeStream.last.opened.emit()
        _FakeStream.last.final.emit("write hello")
        bridge.toggleAiWriting()
        _FakeStream.last.closed.emit()
        bridge._claude_client.last.succeeded.emit("Hello!")
        assert drafts == ["Hello!"]
        assert _sent(bridge) == ""  # a draft is never typed on its own

    def _dictating(self, bridge: KeyboardBridge, monkeypatch) -> DictationController:
        bridge.setDictationWritingEnabled(True)
        bridge.setDictationAnthropicKey(ANTHROPIC)
        bridge.setDictationApiKey("k" * 40)
        bridge.setDictationEnabled(True)
        bridge._dictation_config.stream_inserts = False
        monkeypatch.setattr("src.dictation.controller.DeepgramStream", _FakeStream)
        ctl = bridge._dictation_controller()
        monkeypatch.setattr(ctl._capture, "start", lambda device="": True)
        monkeypatch.setattr(ctl._capture, "stop", lambda: None)
        return ctl

    def test_switching_it_off_leaves_an_ordinary_dictation_alone(
        self, bridge: KeyboardBridge, monkeypatch
    ):
        # The controller is shared.  A held-back dictation keeps what has
        # been said in its buffer until the run ends, and the Write with
        # AI switch used to cancel it, buffer and all, for a feature the
        # run was not using.
        ctl = self._dictating(bridge, monkeypatch)
        ctl.start("dictate")
        _FakeStream.last.opened.emit()
        _FakeStream.last.final.emit("a whole paragraph")
        bridge.setDictationWritingEnabled(False)
        assert ctl.active and ctl.purpose == "dictate"
        ctl.stop()
        _FakeStream.last.closed.emit()
        assert _sent(bridge) == "a whole paragraph"
        assert [e["raw"] for e in bridge.getDictationHistory()] == ["a whole paragraph"]

    def test_switching_it_off_still_cancels_a_write_run(self, bridge: KeyboardBridge, monkeypatch):
        ctl = self._dictating(bridge, monkeypatch)
        drafts: List[str] = []
        bridge.dictationDraftReady.connect(drafts.append)
        ctl.start("write")
        _FakeStream.last.opened.emit()
        _FakeStream.last.final.emit("write hello")
        bridge.setDictationWritingEnabled(False)
        assert ctl.state is DictationState.IDLE
        assert drafts == [] and _sent(bridge) == ""

    def test_privacy_mode_mid_request_drops_the_draft(self, bridge: KeyboardBridge, monkeypatch):
        bridge.setDictationWritingEnabled(True)
        bridge.setDictationAnthropicKey(ANTHROPIC)
        bridge.setDictationApiKey("k" * 40)
        bridge.setDictationEnabled(True)
        monkeypatch.setattr("src.dictation.controller.DeepgramStream", _FakeStream)
        ctl = bridge._dictation_controller()
        monkeypatch.setattr(ctl._capture, "start", lambda device="": True)
        monkeypatch.setattr(ctl._capture, "stop", lambda: None)
        drafts: List[str] = []
        bridge.dictationDraftReady.connect(drafts.append)
        _speak(ctl, "write something private", purpose="write")
        assert ctl.state is DictationState.POLISHING
        bridge.setPrivacyMode(True)
        assert bridge._claude_client.last.aborted is True
        bridge._claude_client.last.succeeded.emit("leaked")
        assert drafts == []


class TestInsertAndCopyDraft:
    def test_insert_types_verbatim_and_returns_true(self, bridge: KeyboardBridge):
        assert bridge.insertDictationDraft("Dear Sam,\nlunch?") is True
        assert _sent(bridge) == "Dear Sam,\nlunch?"

    def test_it_spaces_after_existing_text_like_a_dictated_phrase(self, bridge: KeyboardBridge):
        bridge._insert_dictated_text("hello")
        bridge.insertDictationDraft("world")
        assert _sent(bridge) == "hello world"

    def test_empty_text_and_edit_sessions_refuse(self, bridge: KeyboardBridge):
        assert bridge.insertDictationDraft("   ") is False
        bridge.beginEditSession("snippets")
        assert bridge.insertDictationDraft("text") is False
        assert _sent(bridge) == ""

    def test_not_gated_on_privacy_mode_and_does_not_learn(self, bridge: KeyboardBridge):
        bridge.setPrivacyMode(True)
        learned = MagicMock()
        bridge._predictor.learn_from_selection = learned
        assert bridge.insertDictationDraft("hello") is True
        assert _sent(bridge) == "hello"
        learned.assert_not_called()

    def test_copy_goes_through_the_clipboard_helper(self, bridge: KeyboardBridge, monkeypatch):
        copied: List[str] = []
        monkeypatch.setattr(bridge, "_copy_to_clipboard", lambda t: copied.append(t) or True)
        assert bridge.copyDictationDraft("draft") is True
        assert copied == ["draft"]


class _FakePlayer:
    def __init__(self) -> None:
        self.sources: List[str] = []
        self.played = 0
        self.stopped = 0

    def setSource(self, url) -> None:
        self.sources.append(url.toLocalFile())

    def play(self) -> None:
        self.played += 1

    def stop(self) -> None:
        self.stopped += 1


class TestBridgeHistory:
    def _seed(self, bridge: KeyboardBridge, pcm: bytes | None = b"\x00\x00" * 400) -> str:
        return bridge._history_store().add("dictate", "raw text", "Clean text.", True, 1.0, pcm)

    def test_the_list_is_newest_first_without_paths(self, bridge: KeyboardBridge, tmp_path):
        a = self._seed(bridge)
        b = self._seed(bridge)
        listed = bridge.getDictationHistory()
        assert [e["id"] for e in listed] == [b, a]
        assert str(tmp_path) not in json.dumps(listed)

    def test_copy_copies_the_result(self, bridge: KeyboardBridge, monkeypatch):
        entry_id = self._seed(bridge)
        copied: List[str] = []
        monkeypatch.setattr(bridge, "_copy_to_clipboard", lambda t: copied.append(t) or True)
        assert bridge.copyDictationHistory(entry_id) is True
        assert copied == ["Clean text."]
        assert bridge.copyDictationHistory("0" * 16) is False
        assert bridge.copyDictationHistory("../x") is False

    def test_open_folder_opens_the_history_folder_even_before_the_first_run(
        self, bridge: KeyboardBridge, tmp_path, monkeypatch
    ):
        opened: List[str] = []

        class _FakeDesktopServices:
            @staticmethod
            def openUrl(url) -> bool:
                opened.append(url.toLocalFile())
                return True

        monkeypatch.setattr("PySide6.QtGui.QDesktopServices", _FakeDesktopServices, raising=True)
        folder = tmp_path / "bridge-history"
        assert not folder.exists()
        assert bridge.openDictationHistoryFolder() is True
        assert folder.is_dir()
        assert opened == [str(folder).replace("\\", "/")]

    def test_open_folder_reports_a_failure(self, bridge: KeyboardBridge, monkeypatch):
        class _FakeDesktopServices:
            @staticmethod
            def openUrl(url) -> bool:
                return False

        monkeypatch.setattr("PySide6.QtGui.QDesktopServices", _FakeDesktopServices, raising=True)
        assert bridge.openDictationHistoryFolder() is False

    def test_delete_and_clear_signal_the_change(self, bridge: KeyboardBridge):
        changes: List[int] = []
        bridge.dictationHistoryChanged.connect(lambda: changes.append(1))
        a = self._seed(bridge)
        self._seed(bridge)
        assert bridge.deleteDictationHistory(a) is True
        assert bridge.deleteDictationHistory(a) is False
        assert len(bridge.getDictationHistory()) == 1
        bridge.clearDictationHistory()
        assert bridge.getDictationHistory() == []
        assert len(changes) == 2

    def test_reprocess_goes_to_the_controller(self, bridge: KeyboardBridge, claude):
        bridge.setDictationAnthropicKey(ANTHROPIC)
        bridge.setDictationCleanupEnabled(True)
        entry_id = self._seed(bridge)
        changes: List[int] = []
        bridge.dictationHistoryChanged.connect(lambda: changes.append(1))
        assert bridge.reprocessDictationHistory(entry_id) is True
        claude.last.succeeded.emit("Better.")
        assert changes == [1]
        assert bridge.getDictationHistory()[0]["result"] == "Better."

    def test_reprocess_is_refused_in_privacy_mode(self, bridge: KeyboardBridge, claude):
        # Same gate as starting a run: the bridge is the only place that
        # knows privacy mode is on, so it refuses before the controller.
        bridge.setDictationAnthropicKey(ANTHROPIC)
        bridge.setDictationCleanupEnabled(True)
        entry_id = self._seed(bridge)
        errors: List[str] = []
        bridge.dictationError.connect(errors.append)
        bridge.setPrivacyMode(True)
        assert bridge.reprocessDictationHistory(entry_id) is False
        assert claude.calls == []
        assert errors and "password" in errors[0]
        bridge.setPrivacyMode(False)
        assert bridge.reprocessDictationHistory(entry_id) is True

    def test_play_and_stop(self, bridge: KeyboardBridge, monkeypatch):
        player = _FakePlayer()
        monkeypatch.setattr(bridge, "_ensure_player", lambda: player)
        bridge._player = player
        playing: List[str] = []
        bridge.dictationPlaybackChanged.connect(playing.append)
        entry_id = self._seed(bridge)
        assert bridge.playDictationHistory(entry_id) is True
        assert player.played == 1 and player.sources[0].endswith(f"{entry_id}.wav")
        assert playing == [entry_id]
        bridge.stopDictationPlayback()
        assert playing == [entry_id, ""]

    def test_play_refuses_entries_without_audio_and_bad_ids(
        self, bridge: KeyboardBridge, monkeypatch
    ):
        player = _FakePlayer()
        monkeypatch.setattr(bridge, "_ensure_player", lambda: player)
        silent = self._seed(bridge, pcm=None)
        assert bridge.playDictationHistory(silent) is False
        assert bridge.playDictationHistory("../../x") is False
        assert player.played == 0

    def test_playing_another_entry_replaces_the_first(self, bridge: KeyboardBridge, monkeypatch):
        player = _FakePlayer()
        monkeypatch.setattr(bridge, "_ensure_player", lambda: player)
        bridge._player = player
        a, b = self._seed(bridge), self._seed(bridge)
        bridge.playDictationHistory(a)
        bridge.playDictationHistory(b)
        assert player.stopped >= 1 and bridge._playing_id == b

    def test_shutdown_stops_playback(self, bridge: KeyboardBridge, monkeypatch):
        player = _FakePlayer()
        monkeypatch.setattr(bridge, "_ensure_player", lambda: player)
        bridge._player = player
        bridge.playDictationHistory(self._seed(bridge))
        bridge.shutdown()
        assert player.stopped >= 1 and bridge._playing_id == ""


class TestTypeIntoAppOff:
    """With typing off, a dictate run types nothing, but still reaches the bar
    and clipboard (`runFinished`) and history."""

    def test_old_files_type_into_the_app(self, cfg_path: Path):
        cfg_path.write_text(json.dumps({"enabled": True}), encoding="utf-8")
        assert DictationConfig.load(cfg_path).type_into_app is True

    def test_the_setting_round_trips_off(self, cfg_path: Path):
        DictationConfig(type_into_app=False).save(cfg_path)
        assert DictationConfig.load(cfg_path).type_into_app is False

    def test_a_run_types_nothing_and_is_still_announced(self, qapp, monkeypatch, claude, history):
        ctl = _make_controller(qapp, monkeypatch, claude, history, type_into_app=False)
        spy = _Spy(ctl)
        _speak(ctl, "first part", "second part")
        assert spy.finals == []
        assert spy.runs == ["first part second part"]
        assert history.entries()[0]["result"] == "first part second part"

    def test_a_cleaned_run_types_nothing(self, qapp, monkeypatch, claude, history):
        ctl = _make_controller(
            qapp,
            monkeypatch,
            claude,
            history,
            anthropic_key=ANTHROPIC,
            cleanup_enabled=True,
            type_into_app=False,
        )
        spy = _Spy(ctl)
        _speak(ctl, "hello wurld")
        claude.last.succeeded.emit("Hello world.")
        assert spy.finals == []
        assert spy.runs == ["Hello world."]

    def test_a_failed_cleanup_says_kept_not_inserted(self, qapp, monkeypatch, claude, history):
        ctl = _make_controller(
            qapp,
            monkeypatch,
            claude,
            history,
            anthropic_key=ANTHROPIC,
            cleanup_enabled=True,
            type_into_app=False,
        )
        spy = _Spy(ctl)
        _speak(ctl, "hello wurld")
        claude.last.failed.emit("Claude is busy, try again.")
        assert spy.finals == []
        assert spy.runs == ["hello wurld"]
        assert spy.errors and spy.errors[-1].endswith("Kept as spoken.")

    def test_switching_it_off_mid_run_does_not_change_that_run(
        self, qapp, monkeypatch, claude, history
    ):
        ctl = _make_controller(qapp, monkeypatch, claude, history)
        spy = _Spy(ctl)
        ctl.start()
        _FakeStream.last.opened.emit()
        _FakeStream.last.final.emit("typed already")
        ctl._config.type_into_app = False
        _FakeStream.last.final.emit("and this")
        ctl.stop()
        _FakeStream.last.closed.emit()
        assert spy.finals == ["typed already", "and this"]


class TestRunFinished:
    """`runFinished` carries the delivered text of a *dictate* run, once, after
    the words were handed over; never for Write with AI, never for a cancel."""

    @pytest.fixture
    def writer(self, qapp, monkeypatch, claude, history):
        return _make_controller(
            qapp,
            monkeypatch,
            claude,
            history,
            anthropic_key=ANTHROPIC,
            writing_enabled=True,
        )

    def test_a_streamed_run_announces_its_whole_text_once(self, qapp, monkeypatch, claude, history):
        ctl = _make_controller(qapp, monkeypatch, claude, history)
        spy = _Spy(ctl)
        _speak(ctl, "first part", "second part")
        assert spy.finals == ["first part", "second part"]
        assert spy.runs == ["first part second part"]

    def test_a_held_back_run_announces_after_the_insert(self, qapp, monkeypatch, claude, history):
        ctl = _make_controller(qapp, monkeypatch, claude, history, stream_inserts=False)
        order: List[str] = []
        ctl.finalReady.connect(lambda _t: order.append("final"))
        ctl.runFinished.connect(lambda _t: order.append("run"))
        spy = _Spy(ctl)
        _speak(ctl, "first part", "second part")
        assert spy.runs == ["first part second part"]
        assert order == ["final", "run"]

    def test_a_cleaned_run_announces_the_cleaned_text(self, cleanup_ctl, claude):
        spy = _Spy(cleanup_ctl)
        _speak(cleanup_ctl, "hello wurld")
        assert spy.runs == []  # Claude still has it
        claude.last.succeeded.emit("Hello world.")
        assert spy.finals == ["Hello world."]
        assert spy.runs == ["Hello world."]

    def test_a_failed_cleanup_announces_the_raw_words_that_were_inserted(self, cleanup_ctl, claude):
        spy = _Spy(cleanup_ctl)
        _speak(cleanup_ctl, "hello wurld")
        claude.last.failed.emit("Claude is busy, try again.")
        assert spy.finals == ["hello wurld"]
        assert spy.runs == ["hello wurld"]

    def test_a_dropped_stream_with_words_still_announces(self, qapp, monkeypatch, claude, history):
        ctl = _make_controller(qapp, monkeypatch, claude, history)
        spy = _Spy(ctl)
        ctl.start()
        _FakeStream.last.opened.emit()
        _FakeStream.last.final.emit("said before the drop")
        _FakeStream.last.failed.emit("Connection lost.")
        assert spy.runs == ["said before the drop"]

    def test_write_runs_never_announce(self, writer, claude):
        spy = _Spy(writer)
        _speak(writer, "write a note to Alex", purpose="write")
        claude.last.succeeded.emit("Hi Alex.")
        assert spy.drafts == ["Hi Alex."]
        assert spy.runs == []

    def test_a_failed_write_never_announces_either(self, writer, claude):
        spy = _Spy(writer)
        _speak(writer, "write a note to Alex", purpose="write")
        claude.last.failed.emit("Claude declined this request.")
        assert spy.runs == []

    def test_a_cancel_announces_nothing(self, qapp, monkeypatch, claude, history):
        ctl = _make_controller(qapp, monkeypatch, claude, history, stream_inserts=False)
        spy = _Spy(ctl)
        ctl.start()
        _FakeStream.last.opened.emit()
        _FakeStream.last.final.emit("never delivered")
        ctl.cancel()
        assert spy.runs == [] and spy.finals == []

    def test_a_run_with_nothing_said_announces_nothing(self, qapp, monkeypatch, claude, history):
        ctl = _make_controller(qapp, monkeypatch, claude, history)
        spy = _Spy(ctl)
        _speak(ctl)
        assert spy.runs == []


class TestTheBridgeAutoCopies:
    """The bridge copies a finished run when the setting is on and exposes the
    text, and whether the copy worked, until the next key."""

    def _wire(self, bridge: KeyboardBridge, monkeypatch, *, copy_ok: bool = True):
        copied: List[str] = []

        def fake_copy(text: str) -> bool:
            copied.append(text)
            return copy_ok

        monkeypatch.setattr(bridge, "_copy_to_clipboard", fake_copy)
        monkeypatch.setattr("src.dictation.controller.DeepgramStream", _FakeStream)
        bridge._dictation_config.enabled = True
        bridge._dictation_config.api_key = "k" * 40
        ctl = bridge._dictation_controller()
        monkeypatch.setattr(ctl._capture, "start", lambda device="": True)
        monkeypatch.setattr(ctl._capture, "stop", lambda: None)
        return ctl, copied

    def test_a_finished_run_is_copied_and_shown(self, bridge: KeyboardBridge, monkeypatch):
        ctl, copied = self._wire(bridge, monkeypatch)
        bridge._dictation_config.auto_copy = True
        _speak(ctl, "hello world")
        assert copied == ["hello world"]
        assert bridge.dictationResultText == "hello world"
        assert bridge.dictationResultCopied is True

    def test_auto_copy_off_still_shows_but_never_touches_the_clipboard(
        self, bridge: KeyboardBridge, monkeypatch
    ):
        ctl, copied = self._wire(bridge, monkeypatch)
        bridge._dictation_config.auto_copy = False
        _speak(ctl, "hello world")
        assert copied == []
        assert bridge.dictationResultText == "hello world"
        assert bridge.dictationResultCopied is False

    def test_a_failed_copy_is_not_reported_as_copied(self, bridge: KeyboardBridge, monkeypatch):
        ctl, _ = self._wire(bridge, monkeypatch, copy_ok=False)
        bridge._dictation_config.auto_copy = True
        _speak(ctl, "hello world")
        assert bridge.dictationResultText == "hello world"
        assert bridge.dictationResultCopied is False

    def test_a_write_run_is_not_copied_or_shown(self, bridge: KeyboardBridge, monkeypatch, claude):
        ctl, copied = self._wire(bridge, monkeypatch)
        bridge.setDictationAnthropicKey(ANTHROPIC)
        bridge._dictation_config.writing_enabled = True
        _speak(ctl, "write a note", purpose="write")
        claude.last.succeeded.emit("Hi.")
        assert copied == []
        assert bridge.dictationResultText == ""

    def test_a_key_clears_it_and_a_new_run_clears_it(self, bridge: KeyboardBridge, monkeypatch):
        ctl, _ = self._wire(bridge, monkeypatch)
        _speak(ctl, "hello world")
        assert bridge.dictationResultText != ""
        bridge.pressKey("a")
        assert bridge.dictationResultText == ""

        _speak(ctl, "again")
        assert bridge.dictationResultText == "again"
        ctl.start()
        assert bridge.dictationResultText == ""
        ctl.cancel()

    def test_the_text_never_reaches_the_log(self, bridge: KeyboardBridge, monkeypatch, caplog):
        import logging

        ctl, _ = self._wire(bridge, monkeypatch)
        with caplog.at_level(logging.DEBUG):
            _speak(ctl, "a very private sentence")
        assert "private" not in caplog.text


class TestSpeechNotes:
    def test_old_files_have_none(self, cfg_path: Path):
        cfg_path.write_text(json.dumps({"enabled": True}), encoding="utf-8")
        assert DictationConfig.load(cfg_path).speech_notes == ""

    def test_round_trip_cleaned_and_capped(self, cfg_path: Path):
        DictationConfig(speech_notes="  I say \x00Owen\nsoft r  ").save(cfg_path)
        assert DictationConfig.load(cfg_path).speech_notes == "I say Owen\nsoft r"
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        data["speech_notes"] = "x" * (MAX_SPEECH_NOTES_LEN + 500)
        cfg_path.write_text(json.dumps(data), encoding="utf-8")
        assert len(DictationConfig.load(cfg_path).speech_notes) == MAX_SPEECH_NOTES_LEN
        data["speech_notes"] = 5
        cfg_path.write_text(json.dumps(data), encoding="utf-8")
        assert DictationConfig.load(cfg_path).speech_notes == ""

    def test_the_repr_shows_a_length_only(self):
        assert "slurred" not in repr(DictationConfig(speech_notes="slurred s"))

    def test_an_empty_note_leaves_both_prompts_unchanged(self):
        from src.dictation import prompts

        plain = prompts.cleanup_system_prompt("en", "smart")
        assert prompts.cleanup_system_prompt("en", "smart", "  \n ") == plain
        assert "speech_notes" not in plain
        writing = prompts.writing_system_prompt("Warm.")
        assert prompts.writing_system_prompt("Warm.", "") == writing
        assert "speech_notes" not in writing

    def test_a_note_is_framed_as_data_before_the_reply_rule(self):
        from src.dictation import prompts

        prompt = prompts.cleanup_system_prompt("en", "smart", "I say Owen as Oh-wen.")
        assert "<speech_notes>\nI say Owen as Oh-wen.\n</speech_notes>" in prompt
        assert "not instructions" in prompt
        assert prompt.index("</speech_notes>") < prompt.index("Reply with only")
        assert "<speech_notes>" in prompts.writing_system_prompt("", "I say Owen as Oh-wen.")

    def test_cleanup_write_and_reprocess_all_carry_it(self, qapp, monkeypatch, claude, history):
        ctl = _make_controller(
            qapp,
            monkeypatch,
            claude,
            history,
            anthropic_key=ANTHROPIC,
            cleanup_enabled=True,
            writing_enabled=True,
            speech_notes="drops the t in just",
        )
        _speak(ctl, "hello")
        assert "drops the t in just" in claude.calls[-1]["system"]
        claude.last.succeeded.emit("Hello.")
        _speak(ctl, "write a note", purpose="write")
        assert claude.calls[-1]["model"] == WRITING_MODEL
        assert "drops the t in just" in claude.calls[-1]["system"]
        claude.last.succeeded.emit("A note.")
        entry_id = history.add("dictate", "raw um", "raw um", False, 1.0, None)
        assert ctl.reprocess(entry_id) is True
        assert claude.calls[-1]["model"] == CLEANUP_MODEL
        assert "drops the t in just" in claude.calls[-1]["system"]

    def test_the_bridge_saves_and_reports_it(self, bridge: KeyboardBridge):
        assert bridge.setDictationSpeechNotes("soft r\x00") is True
        assert bridge.getDictationSettings()["speechNotes"] == "soft r"
        assert DictationConfig.load().speech_notes == "soft r"


class TestHistoryLimit:
    def test_choices_and_default(self):
        assert dictation_config.HISTORY_LIMITS_MB == (50, 100, 200, 500, 0)
        assert DictationConfig().history_limit_mb == 200

    @pytest.mark.parametrize("bad", [None, "100", 75, -1, True, 1.5, 10**9, []])
    def test_values_outside_the_choices_load_as_the_default(self, cfg_path: Path, bad):
        cfg_path.write_text(json.dumps({"history_limit_mb": bad}), encoding="utf-8")
        assert DictationConfig.load(cfg_path).history_limit_mb == 200

    @pytest.mark.parametrize("good", [50, 100, 200, 500, 0])
    def test_each_choice_round_trips(self, cfg_path: Path, good):
        DictationConfig(history_limit_mb=good).save(cfg_path)
        assert DictationConfig.load(cfg_path).history_limit_mb == good

    def test_the_bridge_reports_choices_and_usage(self, bridge: KeyboardBridge):
        data = bridge.getDictationSettings()
        assert data["historyLimitMb"] == 200 and data["historyLimits"] == [50, 100, 200, 500, 0]
        assert bridge.getDictationHistoryUsageBytes() >= 0

    def test_choosing_a_limit_applies_it_and_prunes_the_oldest(self, bridge: KeyboardBridge):
        store = bridge._history_store()
        pcm = b"\x01\x00" * 16000 * 30
        store.limit_bytes = 0
        for i in range(4):
            store.add("dictate", f"r{i}", f"x{i}", False, 1.0, pcm)
        assert bridge.getDictationHistoryUsageBytes() > 4 * len(pcm)
        seen: list[int] = []
        bridge.dictationHistoryChanged.connect(lambda: seen.append(1))
        # 50 MB holds four 0.96 MB recordings, so nothing is dropped.
        assert bridge.setDictationHistoryLimit(50) is True
        assert bridge._dictation_config.history_limit_mb == 50
        assert store.limit_bytes == 50 << 20
        assert len(store.entries()) == 4 and seen == []
        store.set_limit_bytes(len(pcm) + 500)
        assert len(store.entries()) == 1

    def test_no_limit_is_stored_and_disables_pruning(self, bridge: KeyboardBridge):
        assert bridge.setDictationHistoryLimit(0) is True
        assert bridge._dictation_config.history_limit_mb == 0
        assert bridge._history_store().limit_bytes == 0
        assert bridge.setDictationHistoryLimit(77) is True
        assert bridge._dictation_config.history_limit_mb == 200

    def test_a_new_store_takes_the_saved_limit(self, bridge: KeyboardBridge):
        bridge._dictation_history = None
        bridge._dictation_config.history_limit_mb = 100
        assert bridge._history_store().limit_bytes == 100 << 20

    def test_a_run_is_recorded_under_the_configured_limit(self, qapp, monkeypatch, claude, history):
        ctl = _make_controller(qapp, monkeypatch, claude, history, history_limit_mb=0)
        _speak(ctl, "hello there")
        assert history.limit_bytes == 0


class TestMoreLanguages:
    ADDED = ("ko", "zh", "ru", "uk", "pl", "tr", "sv", "da", "no", "fi")

    def test_the_added_languages_are_offered_once_each(self):
        codes = [c for c, _ in dictation_config.LANGUAGES]
        assert len(codes) == len(set(codes))
        for code in self.ADDED:
            assert code in codes
        assert codes[-1] == "multi" and codes[:2] == ["en", "en-GB"]

    def test_arabic_is_left_out_because_nova_2_lacks_it(self):
        assert "ar" not in {c for c, _ in dictation_config.LANGUAGES}

    @pytest.mark.parametrize("code", ADDED)
    def test_a_saved_language_loads_and_unknown_ones_fall_back(self, cfg_path: Path, code):
        cfg_path.write_text(json.dumps({"language": code}), encoding="utf-8")
        assert DictationConfig.load(cfg_path).language == code
        cfg_path.write_text(json.dumps({"language": "xx"}), encoding="utf-8")
        assert DictationConfig.load(cfg_path).language == "en"

    def test_the_cleanup_prompt_names_the_language(self):
        from src.dictation import prompts

        assert "Korean" in prompts.cleanup_system_prompt("ko", "smart")


class TestHistoryPageSlots:
    def test_the_page_and_count_slots_read_the_store(self, bridge: KeyboardBridge, tmp_path):
        from src.dictation.history import DictationHistory

        store = DictationHistory(tmp_path / "h")
        for i in range(5):
            store.add("dictate", f"raw {i}", f"res {i}", False, 1.0, None)
        bridge._dictation_history = store
        assert bridge.getDictationHistoryCount() == 5
        assert [e["raw"] for e in bridge.getDictationHistoryPage(2)] == ["raw 4", "raw 3"]
        assert bridge.getDictationHistoryPage(-3) == []
        assert len(bridge.getDictationHistory()) == 5
