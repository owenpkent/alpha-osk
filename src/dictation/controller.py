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
to render four visibly different things (idle, connecting, listening,
finishing) and a boolean pair would let it render a fifth that does not
exist.
"""

from __future__ import annotations

import enum
import logging

from PySide6.QtCore import QObject, QTimer, Signal

from .audio import SILENCE_PEAK, MicrophoneCapture
from .config import DictationConfig
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
    #: Mic level, 0.0 to 1.0, for the button's live indicator.
    levelChanged = Signal(float)
    #: A user-facing sentence.  Always paired with a return to IDLE.
    errorRaised = Signal(str)

    def __init__(self, config: DictationConfig, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config = config
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

    def toggle(self) -> None:
        if self._state is DictationState.IDLE:
            self.start()
        else:
            self.stop()

    def start(self) -> None:
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
        more sentence afterwards.
        """
        if self._state is DictationState.IDLE:
            return
        self._silence_timer.stop()
        self._max_timer.stop()
        self._capture.stop()
        stream = self._drop_stream()
        if stream is not None:
            stream.abort()
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

    def _on_level(self, level: float) -> None:
        self.levelChanged.emit(level)
        if level >= SILENCE_PEAK:
            self._restart_silence_timer()

    def _on_capture_failed(self, message: str) -> None:
        self.errorRaised.emit(message)
        self.cancel()

    # --- stream handlers ------------------------------------------------

    def _on_opened(self) -> None:
        if self._state is DictationState.CONNECTING:
            self._set_state(DictationState.LISTENING)

    def _on_interim(self, text: str) -> None:
        self._got_transcript = True
        self._restart_silence_timer()
        self._set_interim(text)

    def _on_final(self, text: str) -> None:
        self._got_transcript = True
        self._restart_silence_timer()
        # The interim for this phrase is now superseded.  Clearing it
        # before emitting the final keeps the bar from briefly showing the
        # same words twice, once as pending text and once as typed.
        self._set_interim("")
        self.finalReady.emit(text)

    def _on_stream_failed(self, message: str) -> None:
        self.errorRaised.emit(message)
        self.cancel()

    def _on_stream_closed(self) -> None:
        if self._state is DictationState.IDLE:
            return
        if self._state is not DictationState.FINISHING and not self._got_transcript:
            # Closed during the handshake without ever transcribing:
            # almost always a rejected key, and reporting nothing here
            # would leave the button spinning with no explanation.
            self.errorRaised.emit("Dictation could not start: check your API key in Settings.")
        self._finish_run()

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
        self._silence_timer.stop()
        self._max_timer.stop()
        self._capture.stop()
        stream = self._drop_stream()
        if stream is not None:
            stream.abort()
        self._set_interim("")
        self._set_state(DictationState.IDLE)

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
