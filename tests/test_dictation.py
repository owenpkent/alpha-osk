"""Tests for voice dictation.

Three areas, and the split is deliberate:

* the **config**, whose interesting cases are all hostile or damaged input
  and the one property that has to survive refactoring, namely that the
  API key never reaches the Data Backup archive;
* the **controller** state machine, driven against a fake provider so no
  test needs a microphone, a network, or a Deepgram account;
* the **bridge insert path**, which has to mirror ``insertSnippet``
  exactly, and where every one of the assertions below corresponds to a
  way a verbatim insert has historically gone wrong in this file.

Nothing here opens an audio device.  ``MicrophoneCapture`` is stubbed at
the controller boundary, because the one thing a real device would test
(that Qt hands us 16 kHz mono PCM) is Qt's contract rather than ours, and
buying it would cost the suite a hard dependency on the CI runner having
a sound card.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch

import pytest

# Must be set before any QGuiApplication is constructed, including one
# this module creates for a worker that has not run a QML test yet.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.dictation import config as dictation_config
from src.dictation.config import DictationConfig
from src.dictation.controller import DictationController, DictationState
from src.dictation.providers import _friendly_error
from src.keyboard_bridge import KeyboardBridge


@pytest.fixture
def cfg_path(tmp_path: Path) -> Path:
    return tmp_path / "dictation.json"


class TestConfigRoundTrips:
    def test_defaults_are_off_and_keyless(self, cfg_path: Path):
        cfg = DictationConfig.load(cfg_path)
        assert cfg.enabled is False
        assert cfg.has_key is False
        assert cfg.model == "nova-3"

    def test_a_saved_key_comes_back(self, cfg_path: Path):
        DictationConfig(api_key="k" * 40, enabled=True, language="fr").save(cfg_path)
        again = DictationConfig.load(cfg_path)
        assert again.api_key == "k" * 40
        assert again.enabled is True
        assert again.language == "fr"

    def test_the_key_is_not_plaintext_on_disk_on_windows(self, cfg_path: Path):
        """DPAPI-wrapped where the platform offers it.

        Skipped rather than asserted-away off Windows: the fallback there
        is plaintext by design (see the module docstring), so asserting
        the negative would be asserting the fallback is broken.
        """
        if not dictation_config._dpapi_available():
            pytest.skip("DPAPI is Windows-only")
        secret = "sk-thisisthesecret0123456789"
        DictationConfig(api_key=secret).save(cfg_path)
        raw = cfg_path.read_text(encoding="utf-8")
        assert secret not in raw
        assert json.loads(raw)["key_protected"] is True
        assert DictationConfig.load(cfg_path).api_key == secret

    def test_an_unwrappable_key_loads_as_no_key_rather_than_raising(self, cfg_path: Path):
        """A config copied from another machine must not break startup."""
        cfg_path.write_text(
            json.dumps({"api_key": "bm90LWEtcmVhbC1ibG9i", "key_protected": True, "enabled": True}),
            encoding="utf-8",
        )
        cfg = DictationConfig.load(cfg_path)
        assert cfg.enabled is True
        assert cfg.has_key is False

    def test_the_masked_key_never_shows_the_middle(self):
        cfg = DictationConfig(api_key="abcd" + "x" * 32 + "wxyz")
        masked = cfg.masked_key()
        assert masked.startswith("abcd")
        assert masked.endswith("wxyz")
        assert "x" * 8 not in masked

    def test_a_short_key_is_masked_entirely(self):
        assert set(DictationConfig(api_key="abc").masked_key()) == {"*"}


class TestConfigRejectsDamagedInput:
    """Every case here is paired with the near-miss it must still accept."""

    def test_a_missing_file_is_defaults(self, tmp_path: Path):
        assert DictationConfig.load(tmp_path / "nope.json").model == "nova-3"

    def test_an_oversized_file_is_ignored(self, cfg_path: Path):
        cfg_path.write_text(
            json.dumps({"enabled": True, "pad": "x" * (dictation_config.MAX_FILE_BYTES + 10)}),
            encoding="utf-8",
        )
        assert DictationConfig.load(cfg_path).enabled is False

    def test_a_file_just_under_the_cap_is_still_read(self, cfg_path: Path):
        cfg_path.write_text(json.dumps({"enabled": True, "pad": "x" * 100}), encoding="utf-8")
        assert DictationConfig.load(cfg_path).enabled is True

    def test_malformed_json_is_defaults_not_an_exception(self, cfg_path: Path):
        cfg_path.write_text("{not json at all", encoding="utf-8")
        assert DictationConfig.load(cfg_path).enabled is False

    def test_a_json_list_is_defaults(self, cfg_path: Path):
        cfg_path.write_text("[1, 2, 3]", encoding="utf-8")
        assert DictationConfig.load(cfg_path).enabled is False

    def test_an_unknown_model_falls_back(self, cfg_path: Path):
        cfg_path.write_text(json.dumps({"model": "../../etc/passwd"}), encoding="utf-8")
        assert DictationConfig.load(cfg_path).model == "nova-3"

    def test_a_known_model_is_kept(self, cfg_path: Path):
        cfg_path.write_text(json.dumps({"model": "nova-2"}), encoding="utf-8")
        assert DictationConfig.load(cfg_path).model == "nova-2"

    @pytest.mark.parametrize("bad", [True, False, "12", None, float("nan"), [], {}])
    def test_a_non_number_duration_falls_back(self, cfg_path: Path, bad):
        """``bool`` is in that list on purpose: it is an ``int`` subclass.

        The same trap :mod:`src.analytics` documents on its own loader.
        ``True`` would otherwise clamp to the minimum and silently become
        a 15 second ceiling.
        """
        cfg_path.write_text(json.dumps({"max_seconds": bad}), encoding="utf-8")
        assert DictationConfig.load(cfg_path).max_seconds == dictation_config.DEFAULT_MAX_SECONDS

    def test_a_number_out_of_range_is_clamped_not_rejected(self, cfg_path: Path):
        cfg_path.write_text(json.dumps({"max_seconds": 99999}), encoding="utf-8")
        assert DictationConfig.load(cfg_path).max_seconds == dictation_config.MAX_MAX_SECONDS

    def test_keyterms_are_bounded_and_deduplicated(self, cfg_path: Path):
        terms = ["alpha", "alpha", "beta\nwith newline", "x" * 500]
        terms += [f"t{i}" for i in range(dictation_config.MAX_KEYTERMS + 20)]
        cfg_path.write_text(json.dumps({"keyterms": terms}), encoding="utf-8")
        loaded = DictationConfig.load(cfg_path).keyterms
        assert len(loaded) <= dictation_config.MAX_KEYTERMS
        assert loaded.count("alpha") == 1
        assert all("\n" not in t and len(t) <= dictation_config.MAX_KEYTERM_LEN for t in loaded)

    def test_a_non_list_keyterms_field_is_empty(self, cfg_path: Path):
        cfg_path.write_text(json.dumps({"keyterms": "alpha"}), encoding="utf-8")
        assert DictationConfig.load(cfg_path).keyterms == []


class TestTheKeyNeverLeavesTheMachine:
    """The API key must not ride in the Data Backup archive.

    The archive exists to be carried between machines and handed around,
    which is the opposite of what a credential wants; ``telemetry.json``
    is excluded for the same class of reason.  Asserted here rather than
    left to a code comment because the failure is silent: adding
    ``dictation.json`` to ``_MODEL_FILES`` would look like a completeness
    fix and would ship a key inside every backup a user makes.
    """

    def test_dictation_json_is_not_in_the_export_manifest(self):
        from src import data_export

        assert not any("dictation" in name for name in data_export._MODEL_FILES)
        assert not any("dictation" in str(rel) for rel in data_export._MODEL_FILES.values())

    def test_an_archive_naming_it_cannot_write_it(self, tmp_path: Path):
        """The allow-list extractor ignores a hand-edited archive.

        The mirror of ``TestImport::test_telemetry_not_restored``: an
        archive that smuggles the name past the manifest must not land it
        on disk, or a malicious backup could plant a key of the
        attacker's choosing and quietly bill the user's dictation to it.
        """
        from src import data_export

        assert "dictation.json" not in data_export._MODEL_FILES.values()


class _FakeStream:
    """Stands in for :class:`DeepgramStream`, with the same signals.

    A real ``QObject`` subclass rather than a ``MagicMock`` because the
    controller connects to five signals and a mock would happily accept
    ``.connect`` on a name that does not exist, which is exactly the
    typo this stands the best chance of catching.
    """

    def __init__(self, *args, **kwargs):
        from PySide6.QtCore import QObject, Signal

        class _Impl(QObject):
            interim = Signal(str)
            final = Signal(str)
            opened = Signal()
            closed = Signal()
            failed = Signal(str)

        self._impl = _Impl()
        self.audio: List[bytes] = []
        self.started = False
        self.finished = False
        self.aborted = False
        _FakeStream.last = self

    def __getattr__(self, name):
        return getattr(self._impl, name)

    def start(self):
        self.started = True

    def send_audio(self, pcm):
        self.audio.append(pcm)

    def finish(self):
        self.finished = True

    def abort(self):
        self.aborted = True
        self._impl.closed.emit()


@pytest.fixture
def controller(qapp, monkeypatch, cfg_path: Path):
    """A controller wired to a fake provider and a stubbed microphone."""
    cfg = DictationConfig(enabled=True, api_key="k" * 40)
    monkeypatch.setattr("src.dictation.controller.DeepgramStream", _FakeStream)
    ctl = DictationController(cfg)
    monkeypatch.setattr(ctl._capture, "start", lambda device="": True)
    monkeypatch.setattr(ctl._capture, "stop", lambda: None)
    return ctl


@pytest.fixture
def qapp():
    """A Qt application for the signal/timer machinery these tests drive.

    Two things here are not incidental, and both were learned by breaking
    them.

    **It must be a ``QGuiApplication``, not a ``QCoreApplication``.**  Only
    the tests in this file need Qt at all, and a plain core app is enough
    for the ``QObject`` / ``Signal`` / ``QTimer`` work below.  But an
    application cannot be upgraded once created, and under ``-n auto`` this
    module and the headless QML modules land in the same worker process
    often enough: creating a core app here left every QML test that ran
    afterwards unable to build the ``QGuiApplication`` its engine needs.

    **It must adopt the shared test QSettings scope.**  ``Main.qml``
    persists through a QML ``Settings`` element, which resolves to a
    process-external store (a key under HKCU on Windows), so an app created
    with no organisation name points that store at the *real user's*
    settings.  The QML modules assert their scope for exactly this reason,
    and creating an unnamed app here tripped that assertion rather than
    silently writing, which is the guard working as intended.
    """
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtGui import QGuiApplication

    from tests.qt_settings_scope import TEST_APP, TEST_ORG

    app = QGuiApplication.instance()
    if app is None:
        QCoreApplication.setOrganizationName(TEST_ORG)
        QCoreApplication.setApplicationName(TEST_APP)
        app = QGuiApplication([])
    assert QCoreApplication.organizationName() == TEST_ORG, (
        "another test created a Qt application under a different "
        "organisation - QML Settings would write to the real user's store"
    )
    return app


class TestTheRunLifecycle:
    def test_a_run_reaches_listening_once_the_socket_opens(self, controller):
        states: List[str] = []
        controller.stateChanged.connect(states.append)
        controller.start()
        assert states == ["connecting"]
        _FakeStream.last.opened.emit()
        assert states == ["connecting", "listening"]
        assert controller.active is True

    def test_stopping_flushes_rather_than_dropping(self, controller):
        """The tail of the last sentence arrives *after* the stop.

        Closing the socket on stop would throw away the final fragment
        for the words the user just said, which is the part they are most
        likely to be watching for.
        """
        controller.start()
        _FakeStream.last.opened.emit()
        controller.stop()
        assert _FakeStream.last.finished is True
        assert _FakeStream.last.aborted is False
        assert controller.state is DictationState.FINISHING

        finals: List[str] = []
        controller.finalReady.connect(finals.append)
        _FakeStream.last.final.emit("the last thing I said")
        assert finals == ["the last thing I said"]

    def test_cancelling_drops_what_is_in_flight(self, controller):
        """What privacy mode calls, and the difference from ``stop``."""
        controller.start()
        _FakeStream.last.opened.emit()
        controller.cancel()
        assert _FakeStream.last.aborted is True
        assert _FakeStream.last.finished is False
        assert controller.state is DictationState.IDLE

        finals: List[str] = []
        controller.finalReady.connect(finals.append)
        _FakeStream.last.final.emit("a password, probably")
        assert finals == [], "a cancelled run must not deliver one more phrase"

    def test_toggle_starts_then_stops(self, controller):
        controller.toggle()
        assert controller.active is True
        controller.toggle()
        assert controller.state is DictationState.FINISHING

    def test_starting_twice_is_a_no_op(self, controller):
        controller.start()
        first = _FakeStream.last
        controller.start()
        assert _FakeStream.last is first

    def test_audio_reaches_the_stream(self, controller):
        controller.start()
        controller._on_chunk(b"\x00\x01" * 100)
        assert _FakeStream.last.audio == [b"\x00\x01" * 100]

    def test_no_key_refuses_with_a_next_step(self, qapp, monkeypatch, cfg_path: Path):
        monkeypatch.setattr("src.dictation.controller.DeepgramStream", _FakeStream)
        ctl = DictationController(DictationConfig(enabled=True, api_key=""))
        errors: List[str] = []
        ctl.errorRaised.connect(errors.append)
        ctl.start()
        assert ctl.state is DictationState.IDLE
        assert len(errors) == 1 and "Settings" in errors[0]

    def test_a_close_before_any_transcript_is_reported(self, controller):
        """A rejected key closes the socket with nothing said.

        Without this the button spins back to idle and the user is told
        nothing at all, which is indistinguishable from a click that did
        not register.
        """
        errors: List[str] = []
        controller.errorRaised.connect(errors.append)
        controller.start()
        _FakeStream.last.closed.emit()
        assert errors and "key" in errors[0].lower()
        assert controller.state is DictationState.IDLE

    def test_a_close_after_a_successful_run_is_silent(self, controller):
        errors: List[str] = []
        controller.errorRaised.connect(errors.append)
        controller.start()
        _FakeStream.last.opened.emit()
        _FakeStream.last.final.emit("hello there")
        controller.stop()
        _FakeStream.last.closed.emit()
        assert errors == []


class TestTheInterimIsWhatHasNotBeenTypedYet:
    def test_an_interim_shows_and_a_final_clears_it(self, controller):
        seen: List[str] = []
        controller.interimChanged.connect(seen.append)
        controller.start()
        _FakeStream.last.opened.emit()
        _FakeStream.last.interim.emit("hello wor")
        assert controller.interim == "hello wor"
        _FakeStream.last.final.emit("hello world")
        assert controller.interim == ""
        assert seen == ["hello wor", ""]

    def test_the_interim_is_empty_at_rest(self, controller):
        controller.start()
        _FakeStream.last.opened.emit()
        _FakeStream.last.interim.emit("something")
        controller.cancel()
        assert controller.interim == ""


class TestTheSilenceTimeout:
    def test_speech_keeps_the_run_alive(self, controller):
        controller.start()
        _FakeStream.last.opened.emit()
        controller._on_level(0.5)
        assert controller._silence_timer.isActive()
        assert controller.state is DictationState.LISTENING

    def test_firing_stops_the_run(self, controller):
        controller.start()
        _FakeStream.last.opened.emit()
        controller._on_silence()
        assert controller.state is DictationState.FINISHING

    def test_zero_disables_it(self, qapp, monkeypatch):
        monkeypatch.setattr("src.dictation.controller.DeepgramStream", _FakeStream)
        cfg = DictationConfig(enabled=True, api_key="k" * 40, silence_seconds=0)
        ctl = DictationController(cfg)
        monkeypatch.setattr(ctl._capture, "start", lambda device="": True)
        monkeypatch.setattr(ctl._capture, "stop", lambda: None)
        ctl.start()
        ctl._on_level(0.001)
        assert not ctl._silence_timer.isActive()

    def test_the_wall_clock_ceiling_stops_a_run_that_silence_never_ends(self, controller):
        """The backstop for the silence detector itself failing.

        A noisy room never reads as silence, and on a metered API a
        microphone left live is a bill rather than an annoyance.
        """
        controller.start()
        _FakeStream.last.opened.emit()
        controller._on_max_duration()
        assert controller.state is DictationState.FINISHING


class TestTheProviderUrl:
    def test_it_carries_the_fixed_capture_format(self):
        from src.dictation.providers import DeepgramStream

        url = DeepgramStream("k", model="nova-3", language="en")._url()
        assert "encoding=linear16" in url
        assert "sample_rate=16000" in url
        assert "channels=1" in url
        assert "interim_results=true" in url

    def test_custom_words_go_out_as_keyterm_not_keywords(self):
        """nova-3 rejects the legacy ``keywords`` parameter."""
        from src.dictation.providers import DeepgramStream

        url = DeepgramStream("k", keyterms=["Owen Kent", "alpha-osk"])._url()
        assert "keyterm=Owen%20Kent" in url
        assert "keyterm=alpha-osk" in url
        assert "keywords=" not in url

    def test_the_key_is_never_in_the_url(self):
        from src.dictation.providers import DeepgramStream

        assert "supersecret" not in DeepgramStream("supersecret")._url()

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Error 401 unauthorized", "key"),
            ("Host api.deepgram.com not found", "connection"),
            ("The operation timed out", "respond"),
            ("", "connection was lost"),
        ],
    )
    def test_errors_name_a_next_step(self, raw, expected):
        assert expected in _friendly_error(raw).lower()


@pytest.fixture
def bridge(tmp_path: Path) -> KeyboardBridge:
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
        return b


def _sent_text(bridge: KeyboardBridge) -> str:
    """Everything the synthesizer was asked to type, concatenated."""
    return "".join(call.args[0] for call in bridge._synth.send_text.call_args_list)


class TestDictatedTextIsInsertedLikeASnippet:
    """The insert path has to mirror ``insertSnippet`` exactly.

    Every assertion here corresponds to a way a verbatim insert has gone
    wrong in ``keyboard_bridge.py`` before.
    """

    def test_a_phrase_is_typed_verbatim(self, bridge: KeyboardBridge):
        bridge._insert_dictated_text("hello world")
        assert _sent_text(bridge) == "hello world"

    def test_a_held_shift_is_released_before_the_insert(self, bridge: KeyboardBridge):
        """Otherwise the whole sentence arrives in capitals.

        ``_make_char_scancode_events`` only knows not to *add* a
        redundant Shift wrap; it cannot cancel a standing hold.
        """
        bridge.toggleShift()
        assert bridge._shift_active is True
        bridge._insert_dictated_text("hello world")
        assert bridge._shift_active is False
        bridge._synth.release_modifier.assert_any_call("shift")

    def test_a_locked_modifier_survives_the_insert(self, bridge: KeyboardBridge):
        """A right-click lock outranks the sticky release.

        ``_without_held_modifiers`` drops the hold for the duration and
        restores it, which is what keeps a deliberate lock intact.
        """
        bridge.lockModifier("ctrl")
        assert bridge._ctrl_locked is True
        bridge._insert_dictated_text("hello")
        assert bridge._ctrl_locked is True
        assert bridge._ctrl_active is True

    def test_consecutive_phrases_are_spaced(self, bridge: KeyboardBridge):
        """Deepgram hands back phrases with no surrounding whitespace."""
        bridge._insert_dictated_text("hello world")
        bridge._insert_dictated_text("how are you")
        assert _sent_text(bridge) == "hello world how are you"

    def test_no_leading_space_at_the_start_of_a_field(self, bridge: KeyboardBridge):
        bridge._insert_dictated_text("hello")
        assert _sent_text(bridge) == "hello"

    def test_no_leading_space_after_an_opening_bracket(self, bridge: KeyboardBridge):
        bridge._context_buffer = "see ("
        bridge._insert_dictated_text("footnote")
        assert _sent_text(bridge) == "footnote"

    def test_a_space_follows_text_the_user_typed_by_hand(self, bridge: KeyboardBridge):
        """The spacing rule reads the screen, not a "have I inserted yet" flag."""
        bridge.pressKey("h")
        bridge.pressKey("i")
        bridge._synth.send_text.reset_mock()
        bridge._insert_dictated_text("there you are")
        assert _sent_text(bridge) == " there you are"

    def test_the_context_buffer_mirrors_what_was_typed(self, bridge: KeyboardBridge):
        """The insert path measures against these buffers.

        A buffer that disagrees with the screen is how a later pill tap
        eats text the user typed, so this is not merely about prediction
        quality.
        """
        bridge._insert_dictated_text("hello world")
        assert bridge._context_buffer == "hello world"
        assert bridge._current_word == ""
        assert bridge._raw_token == ""

    def test_a_deferred_space_is_mirrored_once_not_twice(self, bridge: KeyboardBridge):
        """``_take_deferred_space`` already appends it to the buffer.

        Adding it again put two spaces in the buffer where the screen has
        one, and the buffers are what the insert path measures against, so
        a later pill tap would select the wrong number of characters.  The
        assertion is buffer-equals-screen, which is the property, rather
        than a space count, which is the symptom.
        """
        for ch in "total 42":
            if ch == " ":
                bridge.pressSpecialKey("space")
            else:
                bridge.pressKey(ch)
        bridge.pressKey(".")  # bare digit run: the space is deferred, not sent
        assert bridge._deferred_auto_space == ".", "precondition: a space is owed"
        bridge._synth.send_text.reset_mock()

        bridge._insert_dictated_text("Then we left")
        screen_tail = _sent_text(bridge)
        # `endswith` rather than an equality against the whole buffer,
        # deliberately: the buffer is not a byte-perfect mirror of the
        # screen in general and never has been (the punctuation branch of
        # `_press_char` drops a bare digit run before dictation is
        # involved, so the buffer already reads "total ." here).  What
        # this pins is dictation's own contribution.
        assert bridge._context_buffer.endswith(screen_tail), (
            f"buffer {bridge._context_buffer!r} disagrees with what was typed {screen_tail!r}"
        )
        assert "  " not in bridge._context_buffer

    def test_privacy_mode_leaves_no_trace_in_the_buffers(self, bridge: KeyboardBridge):
        """Same rule as ``pressPrediction``'s own suppressed append.

        The buffer feeds ``predict()`` and ``learn_from_selection()`` on
        the next call, so nothing spoken into a password field may linger
        in it.  The insert still happens; only the memory of it is
        withheld.
        """
        bridge.setPrivacyMode(True)
        bridge._insert_dictated_text("my voice is my passport")
        assert _sent_text(bridge) == "my voice is my passport"
        assert bridge._context_buffer == ""
        assert bridge._sentence_buffer == ""

    def test_an_empty_phrase_does_nothing(self, bridge: KeyboardBridge):
        bridge._insert_dictated_text("   ")
        assert _sent_text(bridge) == ""

    def test_edit_mode_swallows_it_rather_than_typing_behind_the_popup(
        self, bridge: KeyboardBridge
    ):
        bridge.setEditMode(True)
        bridge._insert_dictated_text("hello")
        assert _sent_text(bridge) == ""

    def test_an_armed_auto_capital_is_spent(self, bridge: KeyboardBridge):
        """A dictated phrase is the next thing typed, so it takes the capital.

        Leaving the flag armed hands the same capital to a later
        unrelated character, which is the bug the three
        ``_consume_auto_cap`` call sites each exist to prevent.
        """
        bridge._pending_auto_cap = True
        bridge._insert_dictated_text("hello")
        assert bridge._pending_auto_cap is False

    def test_predictions_are_cleared(self, bridge: KeyboardBridge):
        emitted: List[list] = []
        bridge.predictionsChanged.connect(emitted.append)
        bridge._insert_dictated_text("hello")
        assert emitted[-1] == []


class TestDictationAndPrivacyMode:
    def test_privacy_mode_cancels_a_live_run(self, bridge: KeyboardBridge, monkeypatch):
        """Streaming a spoken password to a third party is the thing to stop.

        Privacy mode has to mean "stop doing this", not "stop starting
        new ones", the same rule ``_withdraw_snippet_offer`` follows.
        """
        monkeypatch.setattr("src.dictation.controller.DeepgramStream", _FakeStream)
        controller = bridge._dictation_controller()
        bridge._dictation_config.enabled = True
        bridge._dictation_config.api_key = "k" * 40
        monkeypatch.setattr(controller._capture, "start", lambda device="": True)
        monkeypatch.setattr(controller._capture, "stop", lambda: None)
        controller.start()
        assert controller.active is True

        bridge.setPrivacyMode(True)
        assert controller.state is DictationState.IDLE
        assert _FakeStream.last.aborted is True

    def test_toggling_while_private_refuses_with_a_reason(self, bridge: KeyboardBridge):
        errors: List[str] = []
        bridge.dictationError.connect(errors.append)
        bridge.setPrivacyMode(True)
        bridge.toggleDictation()
        assert errors and "password" in errors[0].lower()

    def test_the_insert_itself_is_not_gated_on_privacy_mode(self, bridge: KeyboardBridge):
        """Same rule as every other insert path.

        A phrase only reaches here because a run was already under way,
        and if one somehow is, the words the user spoke must still land
        rather than vanishing.  Privacy is enforced by stopping the run,
        upstream of this.
        """
        bridge.setPrivacyMode(True)
        bridge._insert_dictated_text("hello")
        assert _sent_text(bridge) == "hello"


class TestTheBridgeSettingsSurface:
    def test_the_settings_map_never_returns_the_key_in_the_clear(self, bridge: KeyboardBridge):
        bridge.setDictationApiKey("sk-averyrealsecretkey12345")
        data = bridge.getDictationSettings()
        assert data["hasKey"] is True
        assert "averyrealsecret" not in json.dumps(data)

    def test_whitespace_is_stripped_from_a_pasted_key(self, bridge: KeyboardBridge):
        bridge.setDictationApiKey("  sk-abc \n def  ")
        assert bridge._dictation_config.api_key == "sk-abcdef"

    def test_an_unknown_model_is_refused(self, bridge: KeyboardBridge):
        before = bridge._dictation_config.model
        bridge.setDictationModel("gpt-nonsense")
        assert bridge._dictation_config.model == before

    def test_disabling_takes_a_live_run_with_it(self, bridge: KeyboardBridge, monkeypatch):
        """The failure ``setSnippetDetection(False)`` learned.

        Clearing only the Python side leaves a control on screen that
        silently does nothing.
        """
        monkeypatch.setattr("src.dictation.controller.DeepgramStream", _FakeStream)
        controller = bridge._dictation_controller()
        bridge._dictation_config.enabled = True
        bridge._dictation_config.api_key = "k" * 40
        monkeypatch.setattr(controller._capture, "start", lambda device="": True)
        monkeypatch.setattr(controller._capture, "stop", lambda: None)
        controller.start()
        bridge.setDictationEnabled(False)
        assert controller.state is DictationState.IDLE

    def test_shutdown_releases_the_microphone(self, bridge: KeyboardBridge, monkeypatch):
        monkeypatch.setattr("src.dictation.controller.DeepgramStream", _FakeStream)
        controller = bridge._dictation_controller()
        bridge._dictation_config.enabled = True
        bridge._dictation_config.api_key = "k" * 40
        stopped: List[bool] = []
        monkeypatch.setattr(controller._capture, "start", lambda device="": True)
        monkeypatch.setattr(controller._capture, "stop", lambda: stopped.append(True))
        controller.start()
        bridge.shutdown()
        assert stopped, "an open capture device keeps the OS mic indicator lit"
        assert controller.state is DictationState.IDLE

    def test_the_controller_is_not_built_until_it_is_needed(self, bridge: KeyboardBridge):
        """~1300 bridges get constructed by this suite alone."""
        assert bridge._dictation is None


class TestThePeakDetector:
    def test_silence_reads_as_silence(self):
        from src.dictation.audio import SILENCE_PEAK, _peak

        assert _peak(b"\x00\x00" * 400) < SILENCE_PEAK

    def test_a_loud_buffer_reads_loud(self):
        from src.dictation.audio import SILENCE_PEAK, _peak

        assert _peak(b"\x00\x40" * 400) > SILENCE_PEAK

    def test_an_empty_buffer_is_zero_rather_than_an_exception(self):
        from src.dictation.audio import _peak

        assert _peak(b"") == 0.0

    def test_negative_samples_count(self):
        """Speech is symmetric about zero; a signed peak would read as 0."""
        from src.dictation.audio import SILENCE_PEAK, _peak

        assert _peak(b"\x00\xc0" * 400) > SILENCE_PEAK
