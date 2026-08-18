"""The speech-to-text provider seam, and the Deepgram implementation.

There is one provider today.  It still sits behind a named contract
(:class:`TranscriptionStream`) because the interesting part of this
feature, the state machine in :mod:`~src.dictation.controller` and the
insert path in the bridge, has nothing to do with Deepgram, and a second
provider should not require touching either.  The contract is four
signals and three methods; MacroVox arrived at the same
``{transcript, is_final}`` shape from the other direction, in
``docs/PLAN_ELEVENLABS_STT.md``.

Transport is ``QWebSocket`` on the Qt event loop.  No thread, no asyncio
loop, no queue between the audio callback and the socket: a slice of PCM
arrives on the UI thread from :class:`~src.dictation.audio.MicrophoneCapture`
and is written straight out.

**There is deliberately no automatic reconnect.**  A dropped socket
mid-utterance means the audio spoken during the gap is gone, and a
reconnect that hides that produces a transcript with an invisible hole in
it: words the user said, believes they said, and cannot see are missing.
For dictation that is worse than a visible failure, so a drop ends the
run and says so.  (MacroVox has no reconnect either, though by omission
rather than by argument.)
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import quote

from PySide6.QtCore import QObject, Signal

from .audio import CHANNELS, SAMPLE_RATE

_logger = logging.getLogger(__name__)

DEEPGRAM_HOST = "wss://api.deepgram.com/v1/listen"

#: How long to keep the socket open after asking the provider to finish,
#: waiting for the flushed final transcript.  Deepgram answers a
#: ``CloseStream`` in well under a second; this is the ceiling before the
#: socket is dropped anyway so a wedged server cannot pin the UI in
#: "finishing".
FLUSH_GRACE_MS = 2500


class TranscriptionStream(QObject):
    """What the controller requires of a provider.

    Subclasses emit :attr:`interim` for revisable text, :attr:`final` for
    text the provider will not revise, :attr:`opened` once audio may be
    sent, :attr:`closed` when the stream is finished, and :attr:`failed`
    with a sentence fit to show a user.
    """

    interim = Signal(str)
    final = Signal(str)
    opened = Signal()
    closed = Signal()
    failed = Signal(str)

    def start(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def send_audio(self, pcm: bytes) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def finish(self) -> None:  # pragma: no cover - abstract
        """Ask for any buffered audio to be transcribed, then close."""
        raise NotImplementedError

    def abort(self) -> None:  # pragma: no cover - abstract
        """Drop the connection now, discarding anything in flight."""
        raise NotImplementedError


class DeepgramStream(TranscriptionStream):
    """Streaming Deepgram over a websocket.

    Parameters mirror the set MacroVox has in production
    (``model``/``punctuate``/``smart_format``/``interim_results``, with
    ``keyterm`` for custom vocabulary, which is what nova-3 takes in place
    of the legacy ``keywords``).  ``encoding``, ``sample_rate`` and
    ``channels`` are constants here rather than device properties, because
    :mod:`~src.dictation.audio` fixes the capture format.
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "nova-3",
        language: str = "en",
        keyterms: list[str] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._api_key = api_key
        self._model = model
        self._language = language
        self._keyterms = keyterms or []
        self._socket: Any = None
        self._flush_timer: Any = None
        self._finishing = False
        self._reported = False

    # --- URL -----------------------------------------------------------

    def _url(self) -> str:
        params = [
            f"model={quote(self._model)}",
            f"language={quote(self._language)}",
            "punctuate=true",
            "smart_format=true",
            "interim_results=true",
            "encoding=linear16",
            f"sample_rate={SAMPLE_RATE}",
            f"channels={CHANNELS}",
        ]
        # nova-3 rejects the legacy `keywords` parameter; `keyterm` is its
        # replacement, one occurrence per term.
        params.extend(f"keyterm={quote(term)}" for term in self._keyterms)
        return f"{DEEPGRAM_HOST}?{'&'.join(params)}"

    # --- lifecycle -----------------------------------------------------

    def start(self) -> None:
        try:
            from PySide6.QtCore import QUrl
            from PySide6.QtNetwork import QNetworkRequest
            from PySide6.QtWebSockets import QWebSocket
        except ImportError:
            self._fail("This build has no network support for dictation.")
            return

        if not self._api_key:
            self._fail("Add your Deepgram API key in Settings to use dictation.")
            return

        request = QNetworkRequest(QUrl(self._url()))
        # Deepgram authenticates the upgrade request itself, so the key
        # rides in a header rather than the query string, where it would
        # end up in any proxy or server access log along the way.
        request.setRawHeader(b"Authorization", f"Token {self._api_key}".encode())

        socket = QWebSocket()
        socket.setParent(self)
        socket.connected.connect(self._on_connected)
        socket.disconnected.connect(self._on_disconnected)
        socket.textMessageReceived.connect(self._on_text)
        socket.errorOccurred.connect(self._on_error)
        self._socket = socket
        socket.open(request)

    def send_audio(self, pcm: bytes) -> None:
        if self._socket is None or self._finishing:
            return
        try:
            if self._socket.state() != self._socket.state().__class__.ConnectedState:
                return
            self._socket.sendBinaryMessage(pcm)
        except RuntimeError:  # pragma: no cover - socket torn down mid-send
            pass

    def finish(self) -> None:
        """Flush: tell Deepgram to transcribe what it has, then close.

        The socket stays open afterwards on purpose.  The final fragment
        for the last thing said arrives *after* this message, so closing
        here would throw away the tail of the user's sentence, which is
        the part they are most likely to be watching for.
        """
        if self._socket is None or self._finishing:
            return
        self._finishing = True
        try:
            self._socket.sendTextMessage(json.dumps({"type": "CloseStream"}))
        except RuntimeError:  # pragma: no cover
            self.abort()
            return
        from PySide6.QtCore import QTimer

        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(FLUSH_GRACE_MS)
        self._flush_timer.timeout.connect(self.abort)
        self._flush_timer.start()

    def abort(self) -> None:
        timer, self._flush_timer = self._flush_timer, None
        if timer is not None:
            try:
                timer.stop()
            except RuntimeError:
                pass
        socket, self._socket = self._socket, None
        if socket is not None:
            for signal, slot in (
                (socket.connected, self._on_connected),
                (socket.disconnected, self._on_disconnected),
                (socket.textMessageReceived, self._on_text),
                (socket.errorOccurred, self._on_error),
            ):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
            try:
                socket.abort()
            except RuntimeError:
                pass
            try:
                socket.deleteLater()
            except RuntimeError:
                pass
        if not self._reported:
            self._reported = True
            self.closed.emit()

    # --- socket handlers -----------------------------------------------

    def _on_connected(self) -> None:
        self.opened.emit()

    def _on_disconnected(self) -> None:
        # Expected once `finish()` has been acknowledged; unexpected
        # otherwise, but by this point any audio in the gap is already
        # lost, so it is reported as the end of the run either way and
        # the controller decides whether to call it an error.
        self.abort()

    def _on_text(self, message: str) -> None:
        try:
            payload = json.loads(message)
        except (ValueError, TypeError):
            return
        if not isinstance(payload, dict):
            return
        if payload.get("type") == "Metadata":
            return
        channel = payload.get("channel")
        if not isinstance(channel, dict):
            return
        alternatives = channel.get("alternatives")
        if not isinstance(alternatives, list) or not alternatives:
            return
        first = alternatives[0]
        if not isinstance(first, dict):
            return
        text = first.get("transcript")
        if not isinstance(text, str) or not text.strip():
            return
        if payload.get("is_final"):
            self.final.emit(text)
        else:
            self.interim.emit(text)

    def _on_error(self, _error: Any) -> None:
        message = ""
        if self._socket is not None:
            try:
                message = self._socket.errorString() or ""
            except RuntimeError:
                message = ""
        self._fail(_friendly_error(message))

    def _fail(self, message: str) -> None:
        if self._reported:
            return
        self._reported = True
        # The raw string can name the host and the failure but never the
        # key: the header is not part of errorString(), and nothing here
        # interpolates self._api_key.
        _logger.warning("Dictation transport error: %s", message)
        self.failed.emit(message)
        self.abort()


def _friendly_error(raw: str) -> str:
    """Turn a Qt socket error string into a sentence with a next step.

    Kept deliberately coarse.  The user cannot act on "QAbstractSocket::
    RemoteHostClosedError"; they can act on "check your key" and on "check
    your connection", and those two cover essentially every real failure.
    """
    lowered = raw.lower()
    if "401" in lowered or "403" in lowered or "unauthor" in lowered or "forbidden" in lowered:
        return "Dictation was refused: check your API key in Settings."
    if "host" in lowered or "resolve" in lowered or "network" in lowered or "unreach" in lowered:
        return "Could not reach the dictation service: check your connection."
    if "timed out" in lowered or "timeout" in lowered:
        return "The dictation service did not respond in time."
    if not raw:
        return "Dictation stopped: the connection was lost."
    return f"Dictation stopped: {raw}"
