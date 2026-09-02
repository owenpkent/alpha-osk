"""Microphone capture for dictation, via ``QAudioSource``.

Qt was chosen over ``sounddevice`` for two reasons, and the second is the
one that matters at runtime rather than at build time:

1. ``sounddevice`` needs PortAudio, a native binary the PyInstaller + NSIS
   pipeline would then have to carry and sign.  ``QtMultimedia`` is
   already inside the PySide6 wheel the build bundles.
2. **Qt resamples for us.**  ``QAudioSource`` takes the format you ask
   for, not the device's native one, so the provider always sees 16 kHz
   mono signed-16-bit little-endian PCM regardless of whether the mic is
   a 48 kHz stereo interface or a 44.1 kHz headset.  MacroVox sends the
   device's native rate and tells Deepgram what it was; that works, but
   it means every recogniser parameter is a variable and a device
   reporting something exotic is an untested path.  Fixing the format at
   the capture boundary makes the wire format a constant.

The chosen format is what the recogniser wants and not a compromise:
speech models are trained at 16 kHz, so sending 48 kHz costs three times
the bandwidth for no accuracy, and mono is what a single speaker is.

``QAudioSource`` runs its own thread internally and hands buffers to the
QIODevice returned by ``start()``; the ``readyRead`` signal is delivered
on the thread that owns this object, i.e. the Qt UI thread.  So there is
no worker thread here and no cross-thread queue: a chunk arrives on the
UI thread and is written straight to the websocket, which also lives on
the UI thread.  At 16 kHz mono the whole stream is 32 kB/s, and the work
per chunk is a memcpy into a socket buffer, so the keystroke path is not
at risk.
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QObject, Signal

_logger = logging.getLogger(__name__)

#: What the recogniser is told to expect.  Changing any of these means
#: changing the provider query string in lockstep.
SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_BYTES = 2  # signed 16-bit

#: Audio is pulled in slices of roughly this length.  100 ms is a
#: compromise the streaming path is not sensitive to (the recogniser does
#: its own buffering) but the *level meter* is: a longer slice makes the
#: mic indicator visibly laggy, and that indicator is the only feedback
#: that the microphone is actually live.
CHUNK_MS = 100
CHUNK_BYTES = SAMPLE_RATE * CHANNELS * SAMPLE_BYTES * CHUNK_MS // 1000

#: Peak amplitude below which a slice counts as silence, as a fraction of
#: full scale.  Deliberately low: this drives the silence auto-stop, and
#: stopping on a pause mid-sentence is far worse than running a few
#: seconds long.  Room tone on a desktop mic sits well under this.
SILENCE_PEAK = 0.015


def available() -> bool:
    """Whether audio capture can work at all in this process.

    False when QtMultimedia is missing from the build, which is a
    build-configuration problem rather than a user-facing one, but must
    degrade to a disabled button rather than a crash.
    """
    try:
        from PySide6.QtMultimedia import QAudioSource  # noqa: F401
    except ImportError:
        return False
    return True


def input_device_names() -> list[str]:
    """Descriptions of every audio input, for the settings picker.

    The empty string, meaning "system default", is *not* included: the
    picker adds it as its own first entry so the label can read "System
    default" rather than repeating whatever that device is called today.
    """
    try:
        from PySide6.QtMultimedia import QMediaDevices
    except ImportError:
        return []
    try:
        return [d.description() for d in QMediaDevices.audioInputs()]
    except Exception:  # pragma: no cover - driver enumeration can throw
        _logger.warning("Could not enumerate audio inputs", exc_info=True)
        return []


def _peak(pcm: bytes) -> float:
    """Peak amplitude of a signed-16-bit LE buffer, 0.0 to 1.0.

    Hand-rolled rather than via numpy, which the build explicitly excludes
    (see the PyInstaller specs).  ``int.from_bytes`` over a memoryview in
    a stride loop is fast enough: one 100 ms slice is 1600 samples, and
    this runs ten times a second.
    """
    if not pcm:
        return 0.0
    peak = 0
    # Sample every 4th frame.  A peak detector does not need every sample
    # to notice speech, and this quarters the per-chunk cost on the UI
    # thread for a meter and a silence threshold, neither of which is
    # measuring anything a missed sample would change.
    step = SAMPLE_BYTES * 4
    view = memoryview(pcm)
    for i in range(0, len(pcm) - SAMPLE_BYTES + 1, step):
        value = int.from_bytes(view[i : i + SAMPLE_BYTES], "little", signed=True)
        if value < 0:
            value = -value
        if value > peak:
            peak = value
    return min(1.0, peak / 32768.0)


class MicrophoneCapture(QObject):
    """Pulls PCM off the selected input and emits it in slices.

    Emits :attr:`chunk` for the transport and :attr:`level` for the UI.
    Both fire on the Qt thread that owns this object.
    """

    #: One slice of 16 kHz mono signed-16-bit LE PCM.
    chunk = Signal(bytes)
    #: Peak amplitude of the latest slice, 0.0 to 1.0, for the mic meter.
    level = Signal(float)
    #: Capture could not start or died mid-run.  Carries a user-facing
    #: sentence, never a driver string.
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._source: Any = None
        self._io: Any = None
        self._buffer = bytearray()

    @property
    def active(self) -> bool:
        return self._source is not None

    def start(self, device_description: str = "") -> bool:
        """Open *device_description* (empty means system default).

        Returns False and emits :attr:`failed` rather than raising: a
        missing or busy microphone is an ordinary condition on a desktop,
        and it must leave the keyboard working.
        """
        if self._source is not None:
            return True
        try:
            from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices
        except ImportError:
            self.failed.emit("This build has no audio support.")
            return False

        try:
            device = None
            if device_description:
                for candidate in QMediaDevices.audioInputs():
                    if candidate.description() == device_description:
                        device = candidate
                        break
                if device is None:
                    # The saved device was unplugged.  Falling back to the
                    # default beats refusing to listen, and the settings
                    # panel still shows the stale name so the user can see
                    # what happened.
                    _logger.info("Saved dictation input not present, using the default")
            if device is None:
                device = QMediaDevices.defaultAudioInput()
            if device is None or device.isNull():
                self.failed.emit("No microphone was found.")
                return False

            fmt = QAudioFormat()
            fmt.setSampleRate(SAMPLE_RATE)
            fmt.setChannelCount(CHANNELS)
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            if not device.isFormatSupported(fmt):
                # QAudioSource resamples, so this is informational rather
                # than fatal; a device that truly cannot cope will fail on
                # start() below and be reported there.
                _logger.info("Input does not natively support 16 kHz mono, Qt will convert")

            source = QAudioSource(device, fmt, self)
            io = source.start()
            if io is None:
                self.failed.emit("The microphone could not be opened.")
                return False
            io.readyRead.connect(self._on_ready_read)
            self._source = source
            self._io = io
            self._buffer.clear()
        except Exception:
            _logger.warning("Microphone capture failed to start", exc_info=True)
            self.failed.emit("The microphone could not be opened.")
            self.stop()
            return False
        return True

    def stop(self) -> None:
        """Close the device.  Safe to call when not running, and twice."""
        io, self._io = self._io, None
        source, self._source = self._source, None
        if io is not None:
            try:
                io.readyRead.disconnect(self._on_ready_read)
            except (RuntimeError, TypeError):
                pass
        if source is not None:
            try:
                source.stop()
            except RuntimeError:
                pass
            try:
                source.deleteLater()
            except RuntimeError:
                pass
        self._buffer.clear()

    def _on_ready_read(self) -> None:
        if self._io is None:
            return
        try:
            data = self._io.readAll().data()
        except RuntimeError:
            # The device vanished (unplugged) and Qt tore the QIODevice
            # down under us.  Stopping is the whole recovery: the
            # controller notices the capture is gone and ends the run.
            self.stop()
            self.failed.emit("The microphone was disconnected.")
            return
        if not data:
            return
        self._buffer.extend(data)
        # Emit in fixed slices so the transport sees a steady cadence
        # whatever the driver's own buffer size is.
        while len(self._buffer) >= CHUNK_BYTES:
            slice_ = bytes(self._buffer[:CHUNK_BYTES])
            del self._buffer[:CHUNK_BYTES]
            self.level.emit(_peak(slice_))
            self.chunk.emit(slice_)
