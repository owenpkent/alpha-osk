"""Voice dictation for Alpha-OSK.

Streams microphone audio to a speech-to-text provider and types the
result into whatever app holds OS focus, using the same verbatim-insert
path a snippet or a prediction pill uses.

The whole feature is built on Qt rather than on new third-party
packages, and that is a deliberate constraint rather than a coincidence:

* ``QAudioSource`` (QtMultimedia) captures the microphone and *resamples
  for us*, so the provider always sees 16 kHz mono signed-16-bit PCM no
  matter what the device natively runs at.  The alternative
  (``sounddevice``) drags PortAudio in as a binary dependency, which the
  PyInstaller + NSIS pipeline would then have to carry and sign.
* ``QWebSocket`` (QtWebSockets) speaks to the provider on the Qt event
  loop, so there is no worker thread, no asyncio loop, and no
  cross-thread queue between the audio callback and the socket.  Both Qt
  modules already ship inside the PySide6 wheel the build bundles, so
  this costs nothing at package time.

Layout:

* :mod:`~src.dictation.config` - on-disk settings + API-key storage.
* :mod:`~src.dictation.audio` - microphone capture.
* :mod:`~src.dictation.providers` - the provider seam (Deepgram today).
* :mod:`~src.dictation.controller` - the state machine the bridge talks to.
"""

from .config import DictationConfig
from .controller import DictationController, DictationState

__all__ = ["DictationConfig", "DictationController", "DictationState"]
