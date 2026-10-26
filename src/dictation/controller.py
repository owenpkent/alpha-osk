"""The dictation state machine the keyboard bridge talks to.

Owns a :class:`~src.dictation.audio.MicrophoneCapture` and a
:class:`~src.dictation.providers.TranscriptionStream`, and turns the two
into four signals the bridge can wire to QML without knowing that either
exists.

The interaction model is a **toggle**, not push-to-talk, and that is the
same decision the swipe-typing removal was about: a sustained, precise
gesture is the one thing a mouse-driven user with imprecise motor control
cannot reliably make, so "hold the button while you speak" is exactly the
wrong shape for this app.  Click to start, click to stop, with two
automatic stops behind it so a click that does not register cannot leave
the microphone live (and, on a metered API, billing) indefinitely:

* a **silence timeout**, the one the user actually notices, which ends a
  run a few seconds after they stop speaking;
* a **wall-clock ceiling**, which is the backstop for the silence
  detector itself failing, e.g. a noisy room where nothing ever reads as
  silence.

Both are configurable and the silence one is disableable, because "a few
seconds" is a guess about someone else's speech rhythm.

State is a small enum rather than a pile of booleans because the UI has
to render five visibly different things (idle, connecting, listening,
finishing, polishing) and a boolean pair would let it render a sixth that
does not exist.

**Two optional passes run a transcript through Claude** (AI cleanup, and
"Write with AI", see :mod:`src.dictation.claude`).  Both are built around
one rule: *speech is never lost because of the LLM.*  A cleanup that fails
inserts the raw words and says so; a written draft that fails keeps the
spoken request in history.  A cancel (privacy mode) is the one exception
and is the point of it: it aborts the request and delivers nothing.
"""

from __future__ import annotations

import enum
import logging
import time
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from . import prompts
from .audio import SILENCE_PEAK, MicrophoneCapture
from .claude import CLEANUP_MODEL, WRITING_MODEL, ClaudeClient, ClaudeRequest
from .config import DictationConfig
from .history import DictationHistory
from .providers import DeepgramStream, TranscriptionStream

_logger = logging.getLogger(__name__)


class DictationState(enum.Enum):
    """What the mic button and the suggestion bar should be showing."""

    IDLE = "idle"
    #: Socket opening.  Audio is already being captured and buffered by
    #: the provider's own send guard, so speech during the handshake is
    #: not lost, but nothing has come back yet.
    CONNECTING = "connecting"
    LISTENING = "listening"
    #: The user stopped; the provider is flushing the tail of the last
    #: sentence.  Short, but visible, and it must not look like idle or
    #: the user will click again and start a second run.
    FINISHING = "finishing"
    #: The transcript is with Claude (cleanup or writing).  Still
    #: "active", so the bar keeps showing the words, and a stray second
    #: tap on the button does nothing rather than throwing them away.
    POLISHING = "polishing"


#: 16 kHz mono s16 is 32,000 bytes a second; bounds the history audio tee.
#: A failed run keeps its audio in history only past this much: under a
#: second is a click, not a sentence, and would litter the list.
_PCM_BYTES_PER_SECOND = 32_000
_MIN_KEPT_PCM_BYTES = _PCM_BYTES_PER_SECOND

_CLEANUP_TIMEOUT_MS = 20_000
_WRITING_TIMEOUT_MS = 60_000
_WRITING_MAX_TOKENS = 8_000
_CLEANUP_MAX_TOKENS = 8_000
_CLEANUP_TOKEN_FLOOR = 2_048


def _reason(message: str) -> str:
    """A friendly sentence as a parenthetical: no trailing full stop."""
    return message.strip().rstrip(".")


class DictationController(QObject):
    """Runs one dictation session at a time.

    Nothing in here logs transcript content.  That is the same rule the
    rest of the keystroke path follows, and for the same reason: the
    diagnostic log is what users attach to bug reports, and a dictated
    sentence is exactly as private as a typed one.  Lengths and states
    only.
    """

    stateChanged = Signal(str)
    #: The revisable text for the phrase currently being spoken.  Empty
    #: whenever there is nothing in flight.
    interimChanged = Signal(str)
    #: A phrase the provider will not revise.  The bridge types this.
    finalReady = Signal(str)
    #: The whole text of a finished *dictate* run, once it has been handed to
    #: :attr:`finalReady` (or was typed phrase by phrase).  Emitted exactly
    #: once per such run: never for Write with AI, never for a cancel.  The
    #: bridge copies it and shows it in the bar.
    runFinished = Signal(str)
    #: Mic level, 0.0 to 1.0, for the button's live indicator.
    levelChanged = Signal(float)
    #: A user-facing sentence.  Always paired with a return to IDLE.
    errorRaised = Signal(str)
    #: Text written by "Write with AI".  Never typed automatically: the
    #: user reads it first and chooses Insert, Copy or Discard.
    draftReady = Signal(str)
    #: The history store changed (a run was recorded, or a reprocess
    #: updated an entry).
    historyChanged = Signal()

    def __init__(
        self,
        config: DictationConfig,
        parent: QObject | None = None,
        *,
        claude_client: Any = None,
        history: DictationHistory | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        # Both collaborators are injectable so tests never touch the
        # network or the real config dir, and both are built on first use
        # so a session that never uses an AI feature pays nothing.
        self._claude_client = claude_client
        self._history = history
        self._purpose = "dictate"
        #: Every final of the current run, joined with single spaces.
        #: Kept even when finals are typed as they arrive, because it is
        #: what history records.
        self._run_text = ""
        #: Whether finals are held back and delivered once at the end.
        self._accumulate = False
        #: Whether the finished transcript goes to Claude (cleanup, or a
        #: write).  Decided with `_accumulate`, read with it, never the
        #: live config.
        self._polish = False
        #: Whether a dictate run's words are typed (`finalReady`) at all.
        #: Decided at `start` like `_accumulate`, which it forces on.
        self._type_into_app = True
        self._record_history = False
        self._pcm = bytearray()
        self._pcm_cap = 0
        self._run_started = 0.0
        self._request: ClaudeRequest | None = None
        self._reprocess_request: ClaudeRequest | None = None
        self._state = DictationState.IDLE
        self._interim = ""
        self._capture = MicrophoneCapture(self)
        self._capture.chunk.connect(self._on_chunk)
        self._capture.level.connect(self._on_level)
        self._capture.failed.connect(self._on_capture_failed)
        self._stream: TranscriptionStream | None = None
        #: Set for a stream that produced at least one transcript, so a
        #: socket close after a successful run is not reported as a
        #: failure the way a close during the handshake is.
        self._got_transcript = False

        self._silence_timer = QTimer(self)
        self._silence_timer.setSingleShot(True)
        self._silence_timer.timeout.connect(self._on_silence)

        self._max_timer = QTimer(self)
        self._max_timer.setSingleShot(True)
        self._max_timer.timeout.connect(self._on_max_duration)

    # --- read-only state ------------------------------------------------

    @property
    def state(self) -> DictationState:
        return self._state

    @property
    def purpose(self) -> str:
        """``"write"`` while a Write with AI run is under way, else ``"dictate"``."""
        return self._purpose

    @property
    def active(self) -> bool:
        """Whether a run is under way, including connecting and finishing.

        The suggestion bar renders the transcript for the whole of this,
        so "active" has to include the two transitional states or the
        pills would flash back for the moment the tail is flushing.
        """
        return self._state is not DictationState.IDLE

    @property
    def interim(self) -> str:
        return self._interim

    def is_configured(self) -> bool:
        """Whether starting could plausibly succeed."""
        from . import audio

        return self._config.enabled and self._config.has_key and audio.available()

    # --- control --------------------------------------------------------

    def toggle(self, purpose: str = "dictate") -> None:
        if self._state is DictationState.IDLE:
            self.start(purpose)
        elif self._state is DictationState.POLISHING:
            # The words are already with Claude; a stop has nothing left
            # to stop, and treating the tap as a cancel would discard them
            # on a stray click.  `cancel()` is the deliberate way out.
            return
        else:
            self.stop()

    def start(self, purpose: str = "dictate") -> None:
        """Begin a run.  A no-op if one is already under way.

        Failures here are reported through :attr:`errorRaised` and leave
        the controller idle; nothing raises, because the caller is a QML
        button click on an accessibility tool.
        """
        if self._state is not DictationState.IDLE:
            return
        if not self._config.enabled:
            self.errorRaised.emit("Turn on dictation in Settings first.")
            return
        if not self._config.has_key:
            self.errorRaised.emit("Add your Deepgram API key in Settings to use dictation.")
            return
        if purpose == "write" and not self._config.has_anthropic_key:
            self.errorRaised.emit("Add your Anthropic API key in Settings to write with AI.")
            return

        self._purpose = "write" if purpose == "write" else "dictate"
        # Both decided once, here, so a settings change mid-run cannot flip
        # a run between modes after some of it has been typed.  A run that
        # polishes is always held back, and `_finish_run` reads these two
        # rather than the config: it used to re-read `cleanup_ready`, so
        # cleanup switched on after the first final had been typed sent the
        # whole run to Claude and typed the result on top of the phrases
        # already on screen.
        self._polish = self._purpose == "write" or self._config.cleanup_ready
        self._type_into_app = self._config.type_into_app
        self._accumulate = (
            self._polish or not self._config.stream_inserts or not self._type_into_app
        )
        self._record_history = self._config.history_enabled
        self._run_text = ""
        self._pcm = bytearray()
        self._pcm_cap = max(1, self._config.max_seconds) * _PCM_BYTES_PER_SECOND
        self._run_started = time.monotonic()
        self._got_transcript = False
        self._set_interim("")
        self._set_state(DictationState.CONNECTING)

        stream = DeepgramStream(
            self._config.api_key,
            model=self._config.model,
            language=self._config.language,
            keyterms=list(self._config.keyterms),
            parent=self,
        )
        self._connect_stream(stream)
        self._stream = stream
        stream.start()

        if not self._capture.start(self._config.device):
            # `MicrophoneCapture.start` has already emitted its own
            # message through `failed`, which lands in
            # `_on_capture_failed` and tears the run down.
            return

        self._max_timer.setInterval(max(1, self._config.max_seconds) * 1000)
        self._max_timer.start()
        self._restart_silence_timer()

    def stop(self) -> None:
        """End the run, keeping whatever the provider still owes us.

        The microphone closes immediately (the user is done speaking and
        an open mic light is alarming), but the socket stays up until the
        provider flushes the final fragment for the last thing said.
        """
        if self._state is DictationState.IDLE:
            return
        self._silence_timer.stop()
        self._max_timer.stop()
        self._capture.stop()
        self._set_state(DictationState.FINISHING)
        if self._stream is not None:
            self._stream.finish()
        else:
            self._finish_run()

    def cancel(self) -> None:
        """End the run and throw away anything in flight.

        This is what privacy mode calls.  It differs from :meth:`stop` in
        that it does not wait for the provider to flush: a run cancelled
        because the caret landed on a password field must not deliver one
        more sentence afterwards.  It also aborts any request in flight to
        Claude and drops its text: nothing is delivered afterwards.
        """
        self._abort_requests()
        if self._state is DictationState.IDLE:
            return
        self._silence_timer.stop()
        self._max_timer.stop()
        self._capture.stop()
        stream = self._drop_stream()
        if stream is not None:
            stream.abort()
        self._clear_run()
        self._set_interim("")
        self._set_state(DictationState.IDLE)

    def shutdown(self) -> None:
        """Release the microphone and the socket at app exit."""
        self.cancel()

    # --- stream wiring ----------------------------------------------------

    def _connect_stream(self, stream: TranscriptionStream) -> None:
        for signal, slot in self._stream_links(stream):
            signal.connect(slot)

    def _drop_stream(self) -> TranscriptionStream | None:
        """Detach from the current stream and hand it back to be aborted.

        **Disconnecting is the load-bearing half, not clearing the
        attribute.**  Setting ``self._stream = None`` only stops us
        *sending* to the provider; it does nothing about the provider
        still *telling us* things, and a websocket can have a
        ``textMessageReceived`` already sitting in the Qt event queue at
        the moment we tear it down.  Without the disconnect, a run
        cancelled because the caret landed on a password field would go
        on to deliver one more transcribed phrase, and type it into
        whatever field the user had moved to.  That is precisely the
        thing :meth:`cancel` exists to prevent, so it cannot be left to
        the provider being well-behaved about its own teardown.
        """
        stream, self._stream = self._stream, None
        if stream is None:
            return None
        for signal, slot in self._stream_links(stream):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        return stream

    def _stream_links(self, stream: TranscriptionStream) -> tuple:
        """The signal/slot pairs, named once so the two halves cannot drift."""
        return (
            (stream.opened, self._on_opened),
            (stream.interim, self._on_interim),
            (stream.final, self._on_final),
            (stream.closed, self._on_stream_closed),
            (stream.failed, self._on_stream_failed),
        )

    # --- capture handlers -----------------------------------------------

    def _on_chunk(self, pcm: bytes) -> None:
        if self._stream is not None:
            self._stream.send_audio(pcm)
        if self._record_history and len(self._pcm) < self._pcm_cap:
            self._pcm += pcm[: self._pcm_cap - len(self._pcm)]

    def _on_level(self, level: float) -> None:
        self.levelChanged.emit(level)
        if level >= SILENCE_PEAK:
            self._restart_silence_timer()

    def _on_capture_failed(self, message: str) -> None:
        self.errorRaised.emit(message)
        if self._run_text.strip():
            # The microphone went away, not the user's words.  A held-back
            # run exists nowhere but here, and a streamed one still owes
            # history its record, so this is a stop rather than a cancel:
            # the socket is alive, the provider flushes the tail of the
            # last phrase, and the run ends through the same path a stop
            # does.  Only a run with nothing said yet is torn down.
            self.stop()
            return
        self.cancel()

    # --- stream handlers ------------------------------------------------

    def _on_opened(self) -> None:
        if self._state is DictationState.CONNECTING:
            self._set_state(DictationState.LISTENING)

    def _on_interim(self, text: str) -> None:
        self._got_transcript = True
        self._restart_silence_timer()
        # A held-back run publishes no interim (the owner's call,
        # 2026-10-24: it read as "it is typing" when it was not).  The bar
        # renders no interim for any run since 2026-10-26, so this signal
        # has no consumer outside the tests; the finished text arrives
        # through `runFinished` instead.
        if self._accumulate:
            return
        self._set_interim(text)

    def _on_final(self, text: str) -> None:
        self._got_transcript = True
        self._restart_silence_timer()
        phrase = text.strip()
        if phrase:
            self._run_text = self._joined(phrase)
        if self._accumulate:
            return
        # The interim for this phrase is now superseded.  Clearing it
        # before emitting the final keeps the bar from briefly showing the
        # same words twice, once as pending text and once as typed.
        self._set_interim("")
        self.finalReady.emit(text)

    def _joined(self, tail: str) -> str:
        return f"{self._run_text} {tail.strip()}".strip()

    def _on_stream_failed(self, message: str) -> None:
        if self._run_text.strip():
            # Something was heard.  A dropped socket is not a privacy
            # cancel, so the run ends through the same path a stop takes:
            # held-back finals are delivered, streamed ones were typed as
            # they arrived, and either way history gets its record (with
            # the audio).  Cancelling here used to drop a streamed run's
            # history entry along with the socket.
            self.errorRaised.emit(message)
            self._finish_run()
            return
        kept = self._keep_failed_audio()
        if kept:
            self.errorRaised.emit(f"{message} The audio is saved in History.")
        elif self._state is not DictationState.FINISHING:
            # A flush timeout after a run in which nothing was ever heard,
            # and with no audio worth keeping, is not news to anyone.
            self.errorRaised.emit(message)
        self.cancel()

    def _on_stream_closed(self) -> None:
        if self._state is DictationState.IDLE:
            return
        if self._state is not DictationState.FINISHING and not self._got_transcript:
            # Closed during the handshake without ever transcribing:
            # almost always a rejected key, and reporting nothing here
            # would leave the button spinning with no explanation.
            message = "Dictation could not start: check your API key in Settings."
            if self._keep_failed_audio():
                message = f"{message} The audio is saved in History."
            self.errorRaised.emit(message)
        self._finish_run()

    def _keep_failed_audio(self) -> bool:
        """Keep a failed run's audio as an audio-only history entry.

        The words were spoken and nobody transcribed them: a rejected key,
        a connection that never opened, a flush that never came back.  The
        audio tee has them, so history gets an entry with no text, where
        Play lets the user hear what they said and type it.  Discarding
        the audio with the error (what MacroVox used to do on a failed
        upload) is the one outcome that cannot be recovered from.
        Returns whether an entry was kept.
        """
        if not self._record_history or len(self._pcm) < _MIN_KEPT_PCM_BYTES:
            return False
        return self._record(self._purpose, "", "", cleaned=False)

    # --- timers ----------------------------------------------------------

    def _restart_silence_timer(self) -> None:
        seconds = self._config.silence_seconds
        if seconds <= 0:
            self._silence_timer.stop()
            return
        if self._state in (DictationState.CONNECTING, DictationState.LISTENING):
            self._silence_timer.start(seconds * 1000)

    def _on_silence(self) -> None:
        if self._state in (DictationState.CONNECTING, DictationState.LISTENING):
            self.stop()

    def _on_max_duration(self) -> None:
        if self.active:
            _logger.info("Dictation hit its %s second ceiling", self._config.max_seconds)
            self.stop()

    # --- transitions ------------------------------------------------------

    def _finish_run(self) -> None:
        if self._state is DictationState.POLISHING:
            return
        self._silence_timer.stop()
        self._max_timer.stop()
        self._capture.stop()
        stream = self._drop_stream()
        if stream is not None:
            stream.abort()
        text = self._run_text.strip()
        if not text:
            self._clear_run()
            self._set_interim("")
            self._set_state(DictationState.IDLE)
            return
        if self._polish:
            self._begin_polish(text)
            return
        # No Claude involved.  Streamed finals were typed as they arrived;
        # a held-back run is delivered whole, raw, now.
        self._set_interim("")
        self._set_state(DictationState.IDLE)
        # Typing and the bar come first: the record is a disk write that
        # grows with the history, and the words must not wait on it.
        if self._accumulate:
            self._deliver(text)
        self._emit_run_finished(text)
        self._record("dictate", text, text, cleaned=False)
        self._clear_run()

    # --- Claude passes ----------------------------------------------------

    def _client(self) -> Any:
        if self._claude_client is None:
            self._claude_client = ClaudeClient(self)
        return self._claude_client

    def _history_store(self) -> DictationHistory:
        if self._history is None:
            self._history = DictationHistory()
        return self._history

    def _send_cleanup(self, raw: str) -> ClaudeRequest:
        # Thinking is on by default on the cleanup model and its tokens
        # count against max_tokens, so the floor leaves room for it on top
        # of the rewritten text (about one token per three characters).
        max_tokens = min(_CLEANUP_MAX_TOKENS, _CLEANUP_TOKEN_FLOOR + len(raw) // 2)
        request: ClaudeRequest = self._client().send(
            CLEANUP_MODEL,
            prompts.cleanup_system_prompt(
                self._config.language, self._config.number_format, self._config.speech_notes
            ),
            prompts.cleanup_user_message(raw),
            api_key=self._config.anthropic_key,
            max_tokens=max_tokens,
            effort="low",
            timeout_ms=_CLEANUP_TIMEOUT_MS,
        )
        return request

    def _begin_polish(self, text: str) -> None:
        """Hand the finished transcript to Claude and wait for the answer.

        The bar shows nothing of the text while Claude has it, for the same
        reason a held-back run previews nothing while listening: the raw
        words on screen read as "this is being typed", and what gets typed
        is Claude's version.  The banner's own "Polishing..." / "Writing..."
        label stands in (review of #212, 2026-10-24).
        """
        self._set_interim("")
        self._set_state(DictationState.POLISHING)
        request: ClaudeRequest
        if self._purpose == "write":
            request = self._client().send(
                WRITING_MODEL,
                prompts.writing_system_prompt(
                    self._config.writing_style, self._config.speech_notes
                ),
                prompts.writing_user_message(text),
                api_key=self._config.anthropic_key,
                max_tokens=_WRITING_MAX_TOKENS,
                effort="medium",
                timeout_ms=_WRITING_TIMEOUT_MS,
            )
        else:
            request = self._send_cleanup(text)
        self._request = request
        request.succeeded.connect(lambda result: self._on_polished(request, text, result))
        request.failed.connect(lambda reason: self._on_polish_failed(request, text, reason))

    def _deliver(self, text: str) -> None:
        """Type a held-back dictate run, unless typing into the app is off."""
        if self._type_into_app:
            self.finalReady.emit(text)

    def _emit_run_finished(self, text: str) -> None:
        """Announce the delivered text of a dictate run (never a write run)."""
        if self._purpose == "dictate" and text.strip():
            self.runFinished.emit(text)

    def _take_request(self, request: ClaudeRequest) -> bool:
        """Whether *request* is still the live one (a cancel clears it)."""
        if self._request is not request:
            return False
        self._request = None
        return True

    def _on_polished(self, request: ClaudeRequest, raw: str, result: str) -> None:
        if not self._take_request(request):
            return
        purpose = self._purpose
        self._set_interim("")
        self._set_state(DictationState.IDLE)
        if purpose == "write":
            self._record(purpose, raw, result, cleaned=True)
            self._clear_run()
            self.draftReady.emit(result)
        else:
            # Insert first, record after (see `_finish_run`).
            self._deliver(result)
            self._emit_run_finished(result)
            self._record(purpose, raw, result, cleaned=True)
            self._clear_run()

    def _on_polish_failed(self, request: ClaudeRequest, raw: str, reason: str) -> None:
        if not self._take_request(request):
            return
        purpose = self._purpose
        self._set_interim("")
        self._set_state(DictationState.IDLE)
        # Recorded even on failure: for a write the spoken request is the
        # only copy, and for a dictation it is the raw words.
        if purpose != "write":
            self._deliver(raw)
            self._emit_run_finished(raw)
            self._record(purpose, raw, raw, cleaned=False)
            self._clear_run()
            done = "Inserted as spoken." if self._type_into_app else "Kept as spoken."
            self.errorRaised.emit(f"AI cleanup failed ({_reason(reason)}). {done}")
            return
        kept = self._record(purpose, raw, raw, cleaned=False)
        self._clear_run()
        if purpose == "write":
            if kept:
                self.errorRaised.emit(
                    f"Write with AI failed ({_reason(reason)}). Your request is saved in History."
                )
            else:
                # History is off, or the store refused, so the spoken
                # instruction has no durable copy and promising one would
                # be a lie.  The draft preview is the one surface that can
                # hold it without writing to disk against the user's
                # setting: shown there it can be read back or copied, and
                # the message says that instead.
                self.draftReady.emit(raw)
                self.errorRaised.emit(
                    f"Write with AI failed ({_reason(reason)}). "
                    "Your spoken request is shown so you can copy it."
                )

    def reprocess(self, entry_id: str) -> bool:
        """Re-run cleanup over a history entry's raw text.

        Independent of a live run, but refused while one is active (two
        passes would race for the one status line).  Returns whether a
        request was started; the outcome arrives as :attr:`historyChanged`
        or :attr:`errorRaised`.
        """
        if self.active or self._reprocess_request is not None:
            return False
        # Gated on the cleanup toggle, not merely a stored key.  This sends
        # stored text to Anthropic, and the privacy page promises that with
        # both AI features off Anthropic is never contacted; a key left in
        # Settings is not consent to that.
        if not self._config.cleanup_ready:
            if not self._config.has_anthropic_key:
                self.errorRaised.emit("Add your Anthropic API key in Settings to clean up with AI.")
            else:
                self.errorRaised.emit("Turn on AI cleanup in Settings to reprocess a run.")
            return False
        entry = self._history_store().get(entry_id)
        if entry is None or entry["purpose"] != "dictate" or not entry["raw"].strip():
            return False
        request = self._send_cleanup(entry["raw"])
        self._reprocess_request = request

        def _done(result: str) -> None:
            if self._reprocess_request is not request:
                return
            self._reprocess_request = None
            if self._history_store().update_result(entry_id, result, cleaned=True):
                self.historyChanged.emit()

        def _failed(reason: str) -> None:
            if self._reprocess_request is not request:
                return
            self._reprocess_request = None
            self.errorRaised.emit(f"AI cleanup failed ({_reason(reason)}).")

        request.succeeded.connect(_done)
        request.failed.connect(_failed)
        return True

    def _abort_requests(self) -> None:
        request, self._request = self._request, None
        reprocess, self._reprocess_request = self._reprocess_request, None
        for pending in (request, reprocess):
            if pending is not None:
                pending.abort()

    def _record(self, purpose: str, raw: str, result: str, *, cleaned: bool) -> bool:
        """Keep the run in history.  False when nothing was kept, whether
        because the setting is off or because the store refused."""
        if not self._record_history:
            return False
        seconds = max(0.0, time.monotonic() - self._run_started)
        store = self._history_store()
        store.limit_bytes = self._config.history_limit_mb << 20
        entry_id = store.add(purpose, raw, result, cleaned, seconds, bytes(self._pcm) or None)
        if entry_id:
            self.historyChanged.emit()
        return bool(entry_id)

    def _clear_run(self) -> None:
        self._run_text = ""
        self._pcm = bytearray()

    def _set_state(self, state: DictationState) -> None:
        if state is self._state:
            return
        self._state = state
        self.stateChanged.emit(state.value)

    def _set_interim(self, text: str) -> None:
        if text == self._interim:
            return
        self._interim = text
        self.interimChanged.emit(text)
