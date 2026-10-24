"""On-disk settings for dictation, including the provider API key.

Stored as ``dictation.json`` in the config dir, written atomically
(tempfile-then-rename) on every mutation the same way ``snippets.json``
is, so there is no on-quit save path to wire up and a crash mid-write
cannot truncate the file.

Two things about this file are load-bearing and are not house style:

**It is deliberately absent from the Data Backup archive.**  It holds an
API key, and the archive exists to be carried between machines and handed
around; ``telemetry.json`` is excluded for the same class of reason.  Do
not add it to ``_MODEL_FILES`` in :mod:`src.data_export`.
``tests/test_dictation.py::TestTheKeyNeverLeavesTheMachine`` asserts the
absence, so re-adding it fails loudly rather than quietly shipping a
credential inside a user's backup.

**The key is wrapped with DPAPI on Windows** (``CryptProtectData``, via
ctypes, so no new dependency), which ties the ciphertext to the user
account: a copied ``dictation.json`` is inert on another machine or under
another login.  This is not a claim that the key is safe from code
already running as the user, because nothing on a desktop is and a
keyring would be no better there.  What it buys is that the key is not
sitting in plaintext in a file that gets synced, screen-shared, or
attached to a bug report.  On Linux and macOS there is no equivalent that
avoids a new dependency, so the key is stored in plaintext with the file
mode narrowed to 0600, and ``key_protected`` records which form is on
disk so a file written on either platform still loads on the other.
"""

from __future__ import annotations

import base64
import json
import logging
import sys
from dataclasses import dataclass, field
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any

from ..atomic_write import atomic_write_json

_logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

#: Whole-file cap.  Nothing here is user prose, so this is generous by
#: orders of magnitude; it exists so a corrupt or hostile file is rejected
#: by ``stat()`` before it is ever opened, the same before-you-open-it
#: pattern the n-gram, analytics and snippet loaders use.
MAX_FILE_BYTES = 64 * 1024

#: Deepgram keys are 40 hex characters today.  The cap is loose enough for
#: any provider's format and tight enough that a pasted essay is rejected
#: rather than stored.
MAX_KEY_LEN = 512

#: Newline-separated terms boosted in recognition (Deepgram ``keyterm``).
MAX_KEYTERMS = 50
MAX_KEYTERM_LEN = 100

#: Models offered in the settings picker.  ``nova-3`` is the default for
#: the reason MacroVox picked it: it is the current general model, and the
#: only one whose custom-vocabulary parameter is ``keyterm``.
MODELS: tuple[tuple[str, str], ...] = (
    ("nova-3", "Nova 3, most accurate, best for dictation"),
    ("nova-2", "Nova 2, previous generation"),
)

LANGUAGES: tuple[tuple[str, str], ...] = (
    ("en", "English"),
    ("en-GB", "English (UK)"),
    ("es", "Spanish"),
    ("fr", "French"),
    ("de", "German"),
    ("it", "Italian"),
    ("pt", "Portuguese"),
    ("nl", "Dutch"),
    ("hi", "Hindi"),
    ("ja", "Japanese"),
    ("multi", "Multilingual"),
)

#: How the AI cleanup pass writes numbers.  "smart" lets the model decide
#: from context ("two dogs" but "12 items"), as MacroVox does.
NUMBER_FORMATS: tuple[tuple[str, str], ...] = (
    ("smart", "Smart"),
    ("digits", "Digits"),
    ("words", "Words"),
)

#: The user's own description of their writing voice, sent with a "Write
#: with AI" request.  A paragraph or two; the cap keeps a pasted document
#: out of every request (and out of the bill).
MAX_STYLE_LEN = 2000

#: Wall-clock ceiling on one dictation run, in seconds.  Not a silence
#: detector: it is the backstop for a microphone left live by a click that
#: did not register as a stop, which on a metered API is the failure that
#: costs money rather than merely annoying.
DEFAULT_MAX_SECONDS = 120
MIN_MAX_SECONDS = 15
MAX_MAX_SECONDS = 600

#: Silence, in seconds, after which a run stops itself.  0 disables it.
DEFAULT_SILENCE_SECONDS = 4
MAX_SILENCE_SECONDS = 30


def _config_path() -> Path:
    from ..platform import get_config_dir

    return get_config_dir() / "dictation.json"


# --- Windows DPAPI key wrapping ---------------------------------------
#
# ctypes rather than pywin32: the project ships no Windows-only Python
# packages at all (see requirements.txt), and the two calls needed here
# are a dozen lines each.


def _dpapi_available() -> bool:
    return sys.platform == "win32"


def _protect(secret: str) -> str | None:
    """DPAPI-wrap *secret*, returning base64.  ``None`` if unavailable."""
    # Written as a literal `sys.platform` comparison rather than a call to
    # `_dpapi_available()` so mypy prunes the body on the `--platform
    # linux` pass, where typeshed does not define `ctypes.WinDLL` at all.
    # Both passes are required and neither substitutes for the other; see
    # the mypy note in CLAUDE.md.
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _Blob(ctypes.Structure):
            _fields_ = [
                ("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char)),
            ]

        raw = secret.encode("utf-8")
        buf = ctypes.create_string_buffer(raw, len(raw))
        blob_in = _Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_out = _Blob()
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        ok = crypt32.CryptProtectData(
            ctypes.byref(blob_in),
            "alpha-osk dictation",
            None,
            None,
            None,
            0,
            ctypes.byref(blob_out),
        )
        if not ok:
            return None
        try:
            out = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.WinDLL("kernel32", use_last_error=True).LocalFree(blob_out.pbData)
        return base64.b64encode(out).decode("ascii")
    except Exception:  # pragma: no cover - any ctypes / OS failure
        _logger.debug("DPAPI protect unavailable, storing key unwrapped", exc_info=True)
        return None


def _unprotect(wrapped: str) -> str | None:
    """Reverse :func:`_protect`.  ``None`` if it cannot be unwrapped."""
    if sys.platform != "win32":  # see the note in _protect
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _Blob(ctypes.Structure):
            _fields_ = [
                ("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char)),
            ]

        raw = base64.b64decode(wrapped.encode("ascii"), validate=True)
        buf = ctypes.create_string_buffer(raw, len(raw))
        blob_in = _Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_out = _Blob()
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        )
        if not ok:
            return None
        try:
            out = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.WinDLL("kernel32", use_last_error=True).LocalFree(blob_out.pbData)
        return out.decode("utf-8")
    except Exception:  # pragma: no cover - defensive
        _logger.debug("DPAPI unprotect failed", exc_info=True)
        return None


def _clip_int(value: Any, low: int, high: int, fallback: int) -> int:
    """Coerce *value* to an int inside [low, high].

    ``bool`` is rejected explicitly because it is an ``int`` subclass, the
    same trap :mod:`src.analytics` documents on its own scalar loader.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError):
        return fallback
    return max(low, min(high, n))


def _clean_style(raw: Any) -> str:
    """A style profile with control characters removed, newlines kept."""
    if not isinstance(raw, str):
        return ""
    kept = "".join(ch for ch in raw if ch == chr(10) or ch.isprintable())
    return kept.strip()[:MAX_STYLE_LEN]


def clean_pasted_key(raw: str) -> str:
    """A pasted key with its whitespace removed and a double paste undone.

    A key pasted twice into the field arrives as one string of twice the
    length; the provider refuses it and the user sees only "check your
    key".  No real key is two identical halves, so collapsing one is safe.
    """
    key = "".join(raw.split())[:MAX_KEY_LEN]
    half = len(key) // 2
    if half and key[:half] == key[half:]:
        key = key[:half]
    return key


def _mask(secret: str) -> str:
    if not secret:
        return ""
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}{'*' * 8}{secret[-4:]}"


def _load_secret(data: dict[str, Any], name: str, flag: str) -> str:
    """Read one possibly-DPAPI-wrapped secret field out of the parsed file."""
    stored = data.get(name, "")
    if not (isinstance(stored, str) and 0 < len(stored) <= MAX_KEY_LEN * 4):
        return ""
    if data.get(flag):
        # Written on another machine or under another login is not an
        # error worth shouting about; the user re-enters it.
        return _unprotect(stored) or ""
    return stored[:MAX_KEY_LEN]


def _store_secret(payload: dict[str, Any], name: str, flag: str, secret: str) -> None:
    if not secret:
        return
    wrapped = _protect(secret)
    if wrapped is not None:
        payload[name] = wrapped
        payload[flag] = True
    else:
        payload[name] = secret[:MAX_KEY_LEN]
        payload[flag] = False


def _clean_keyterms(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        term = " ".join(item.split())[:MAX_KEYTERM_LEN].strip()
        if term and term not in out:
            out.append(term)
        if len(out) >= MAX_KEYTERMS:
            break
    return out


@dataclass
class DictationConfig:
    """Everything dictation persists, plus load and save.

    ``api_key`` is held in the clear *in memory*, because it has to be to
    build the websocket's Authorization header, and is wrapped on the way
    to disk.  Nothing here is ever logged: :meth:`save` and :meth:`load`
    log counts and booleans only.
    """

    enabled: bool = False
    api_key: str = ""
    model: str = "nova-3"
    language: str = "en"
    device: str = ""  # empty means the system default input
    max_seconds: int = DEFAULT_MAX_SECONDS
    silence_seconds: int = DEFAULT_SILENCE_SECONDS
    keyterms: list[str] = field(default_factory=list)
    #: Type each finalised phrase into the focused app as it arrives.
    #: When off, the transcript accumulates in the suggestion bar and is
    #: inserted in one go when the user stops.
    stream_inserts: bool = True
    #: Anthropic key for AI cleanup and Write with AI.  Same handling as
    #: ``api_key`` (clear in memory, DPAPI on disk, never logged), and it
    #: is a separate credential for a separate third party, so it has its
    #: own wrapped-or-not flag on disk.
    anthropic_key: str = ""
    #: Run the transcript through Claude before it is typed.  Needs the key.
    cleanup_enabled: bool = False
    number_format: str = "smart"
    #: Offer the Write with AI button.  Needs both keys.
    writing_enabled: bool = False
    #: The user's own description of their voice, for Write with AI.
    writing_style: str = ""
    #: Keep the last few runs (text and audio) so a lost sentence can be
    #: copied back out.  On by default: the point is recovery.
    history_enabled: bool = True

    # Hand-written rather than dataclass-generated.  A generated repr puts
    # the key into every log line, f-string and traceback that formats this
    # object, and the diagnostic log is the file users attach to bug
    # reports.  The docstring above says nothing here is ever logged, which
    # is true of today's call sites and is exactly the kind of promise the
    # next one breaks in silence; this makes it true by construction rather
    # than by everyone remembering.  It walks the fields rather than naming
    # them so a field added later is carried without a second edit.
    def __repr__(self) -> str:
        parts = []
        for f in dataclass_fields(self):
            if f.name in ("api_key", "anthropic_key"):
                parts.append(f"{f.name}={'<set>' if getattr(self, f.name) else '<unset>'}")
            elif f.name == "writing_style":
                # Prose about the user's voice; length only, like a transcript.
                parts.append(f"writing_style=<{len(self.writing_style)} chars>")
            else:
                parts.append(f"{f.name}={getattr(self, f.name)!r}")
        return f"{type(self).__name__}({', '.join(parts)})"

    @property
    def has_key(self) -> bool:
        return bool(self.api_key)

    def masked_key(self) -> str:
        """A key preview safe to render in the settings panel."""
        return _mask(self.api_key)

    @property
    def has_anthropic_key(self) -> bool:
        return bool(self.anthropic_key)

    def masked_anthropic_key(self) -> str:
        """The Anthropic key preview safe to render in the settings panel."""
        return _mask(self.anthropic_key)

    @property
    def cleanup_ready(self) -> bool:
        """Whether a dictation run should be polished by Claude."""
        return self.cleanup_enabled and self.has_anthropic_key

    # --- persistence ---------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> DictationConfig:
        """Read the config, falling back to defaults on any problem.

        A missing, oversized, malformed or wrongly-typed file yields a
        default config rather than an exception: dictation being
        unconfigured is a normal state, and the keyboard must start.
        """
        target = path or _config_path()
        cfg = cls()
        try:
            if not target.exists():
                return cfg
            if target.stat().st_size > MAX_FILE_BYTES:
                _logger.warning("dictation.json exceeds the size cap, ignoring it")
                return cfg
            with open(target, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                return cfg
        except (OSError, json.JSONDecodeError, ValueError, RecursionError):
            _logger.warning("dictation.json could not be read, using defaults", exc_info=True)
            return cfg

        cfg.enabled = bool(data.get("enabled", False))

        cfg.api_key = _load_secret(data, "api_key", "key_protected")
        if not cfg.api_key and data.get("api_key") and data.get("key_protected"):
            _logger.info("Stored dictation key could not be unwrapped on this account")
        cfg.anthropic_key = _load_secret(data, "anthropic_key", "anthropic_key_protected")

        model = data.get("model")
        cfg.model = (
            model if isinstance(model, str) and model in {m for m, _ in MODELS} else cls.model
        )
        language = data.get("language")
        cfg.language = (
            language
            if isinstance(language, str) and language in {c for c, _ in LANGUAGES}
            else cls.language
        )
        device = data.get("device")
        cfg.device = device[:256] if isinstance(device, str) else ""
        cfg.max_seconds = _clip_int(
            data.get("max_seconds"), MIN_MAX_SECONDS, MAX_MAX_SECONDS, DEFAULT_MAX_SECONDS
        )
        cfg.silence_seconds = _clip_int(
            data.get("silence_seconds"), 0, MAX_SILENCE_SECONDS, DEFAULT_SILENCE_SECONDS
        )
        cfg.keyterms = _clean_keyterms(data.get("keyterms"))
        cfg.stream_inserts = bool(data.get("stream_inserts", True))
        cfg.cleanup_enabled = bool(data.get("cleanup_enabled", False))
        number_format = data.get("number_format")
        cfg.number_format = (
            number_format
            if isinstance(number_format, str) and number_format in {n for n, _ in NUMBER_FORMATS}
            else cls.number_format
        )
        cfg.writing_enabled = bool(data.get("writing_enabled", False))
        cfg.writing_style = _clean_style(data.get("writing_style"))
        cfg.history_enabled = bool(data.get("history_enabled", True))
        return cfg

    def save(self, path: Path | None = None) -> bool:
        """Write atomically.  Returns False on failure rather than raising."""
        target = path or _config_path()
        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "enabled": self.enabled,
            "model": self.model,
            "language": self.language,
            "device": self.device,
            "max_seconds": self.max_seconds,
            "silence_seconds": self.silence_seconds,
            "keyterms": self.keyterms[:MAX_KEYTERMS],
            "stream_inserts": self.stream_inserts,
        }
        payload.update(
            {
                "cleanup_enabled": self.cleanup_enabled,
                "number_format": self.number_format,
                "writing_enabled": self.writing_enabled,
                "writing_style": self.writing_style[:MAX_STYLE_LEN],
                "history_enabled": self.history_enabled,
            }
        )
        _store_secret(payload, "api_key", "key_protected", self.api_key)
        _store_secret(payload, "anthropic_key", "anthropic_key_protected", self.anthropic_key)

        try:
            # mode=0o600 narrows the file before the rename, so it is never
            # world-readable even for the instant between the two.  A no-op
            # on Windows, where DPAPI is doing this job.
            atomic_write_json(target, payload, indent=2, mode=0o600)
        except OSError:
            _logger.warning("Could not save dictation settings", exc_info=True)
            return False
        return True
