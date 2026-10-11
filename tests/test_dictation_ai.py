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
from src.dictation.config import MAX_STYLE_LEN, DictationConfig
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
        self.history_changes = 0
        ctl.finalReady.connect(self.finals.append)
        ctl.draftReady.connect(self.drafts.append)
        ctl.errorRaised.connect(self.errors.append)
        ctl.stateChanged.connect(self.states.append)
        ctl.interimChanged.connect(self.interims.append)
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
    def test_cleanup_holds_finals_back_and_shows_progress(self, cleanup_ctl):
        spy = _Spy(cleanup_ctl)
        cleanup_ctl.start()
        _FakeStream.last.opened.emit()
        _FakeStream.last.final.emit("hello world")
        _FakeStream.last.interim.emit("how are")
        assert spy.finals == []
        assert spy.interims[-1] == "hello world how are"
        _FakeStream.last.final.emit("how are you")
        assert spy.finals == []
        assert spy.interims[-1] == "hello world how are you"

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
        (entry,) = history.entries()
        assert entry["raw"] == "write a note to Alex" and entry["cleaned"] is False

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
        keyed = _make_controller(qapp, monkeypatch, claude, history, anthropic_key=ANTHROPIC)
        assert keyed.reprocess("f" * 16) is False
        assert keyed.reprocess("../x") is False
        assert keyed.reprocess(self._seed(history, "write")) is False
        assert claude.calls == []

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
        again = DictationConfig.load()
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
        entry_id = self._seed(bridge)
        changes: List[int] = []
        bridge.dictationHistoryChanged.connect(lambda: changes.append(1))
        assert bridge.reprocessDictationHistory(entry_id) is True
        claude.last.succeeded.emit("Better.")
        assert changes == [1]
        assert bridge.getDictationHistory()[0]["result"] == "Better."

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
