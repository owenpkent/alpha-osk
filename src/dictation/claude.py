"""A minimal Anthropic Messages client built on Qt's own networking.

Two features use it: the AI cleanup pass over a dictated transcript, and
"Write with AI".  Both send the user's words to a third party with the
user's own key, which is why the rules here are stricter than the size of
the file suggests:

* **No new dependency, no thread.**  ``QNetworkAccessManager`` runs on the
  event loop like the Deepgram socket does, so there is no worker to
  cancel and no cross-thread signal to race a privacy-mode cancel.  The
  ``anthropic`` SDK would add a package, an HTTP stack and a thread pool
  for one POST.
* **The key travels in a header and nowhere else.**  Never in the URL
  (URLs are logged by proxies and Qt), never in the body, never in a log
  line, and never echoed back in an error.
* **Nothing about the request or the response is logged.**  Status codes
  and lengths only, the same rule the rest of the dictation path follows:
  the diagnostic log is what users attach to bug reports.
* **The server's error text is never shown.**  An API error message can
  quote the input, so each class of failure maps to one short fixed
  sentence that names a next step.

The request and response handling is split into two pure functions
(:func:`build_request`, :func:`parse_response`) so the contract is testable
with no Qt at all; :class:`ClaudeRequest` and :class:`ClaudeClient` are the
thin shell over ``QtNetwork`` around them.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QTimer, QUrl, Signal

_logger = logging.getLogger(__name__)

MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

#: Exact model ids, no date suffix.  Cleanup is a short, mechanical edit
#: so it takes the small fast model; writing is composition, so it takes
#: the larger one.
CLEANUP_MODEL = "claude-haiku-5-5"
WRITING_MODEL = "claude-sonnet-5-5"

#: Server-side fallback when the writing model declines a request, so a
#: spurious refusal is retried on another model by Anthropic rather than
#: surfacing to a user who cannot easily rephrase.  Only the writing model
#: supports it; the cleanup model has no fallback target.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

#: A reply larger than this is rejected without being parsed.  Real
#: replies are a few kilobytes; the cap exists so a misbehaving proxy
#: cannot make us hold and decode an arbitrarily large body.
MAX_RESPONSE_BYTES = 1024 * 1024


def build_request(
    model: str,
    system: str,
    user_text: str,
    *,
    max_tokens: int,
    effort: str,
    api_key: str,
) -> tuple[dict[str, str], bytes]:
    """The headers and JSON body for one Messages call.

    ``thinking`` and sampling parameters are deliberately absent: these
    models reject them with a 400.  There is no assistant prefill for the
    same reason.  ``effort`` rides in ``output_config``.
    """
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": int(max_tokens),
        "system": system,
        "messages": [{"role": "user", "content": user_text}],
        "output_config": {"effort": effort},
    }
    if model == WRITING_MODEL:
        headers["anthropic-beta"] = _FALLBACK_BETA
        body["fallbacks"] = "default"
    return headers, json.dumps(body, ensure_ascii=False).encode("utf-8")


def _error_for_status(status: int) -> str:
    if status in (401, 403):
        return "Anthropic key was rejected. Check it in Settings."
    if status == 429:
        return "Claude is rate limited, try again in a moment."
    if status == 529 or status >= 500:
        return "Claude is busy, try again."
    return "Claude could not process this request."


def parse_response(status: int, payload: bytes) -> tuple[str | None, str | None]:
    """Turn an HTTP reply into ``(text, friendly_error)``; exactly one is set."""
    if len(payload) > MAX_RESPONSE_BYTES:
        return None, "Claude sent back too much data."
    if not 200 <= status < 300:
        return None, _error_for_status(status)
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        return None, "Claude sent back something unreadable."
    if not isinstance(data, dict):
        return None, "Claude sent back something unreadable."
    if data.get("type") == "error":
        # A 200 carrying an error object; treat it like the failure it is.
        return None, "Claude could not process this request."

    stop = data.get("stop_reason")
    if stop == "refusal":
        return None, "Claude declined this request."
    if stop == "max_tokens":
        return None, "Claude's reply was cut off before it finished."

    content = data.get("content")
    if not isinstance(content, list):
        return None, "Claude sent back something unreadable."
    parts: list[str] = []
    for block in content:
        # `thinking`, `fallback` and any block type added later are not
        # text the user asked for; only `text` blocks are the reply.
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    result = "".join(parts).strip()
    if not result:
        return None, "Claude sent back an empty reply."
    return result, None


class ClaudeRequest(QObject):
    """One in-flight call.  Emits exactly one of ``succeeded`` / ``failed``.

    Wraps a ``QNetworkReply`` (or anything with its small surface, which is
    how the tests drive it without a socket).  After :meth:`abort` nothing
    is ever emitted: privacy mode aborts a request precisely so that no
    text arrives afterwards.
    """

    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, reply: Any, timeout_ms: int, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._reply = reply
        self._done = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)
        self._timer.start(max(1, int(timeout_ms)))
        reply.finished.connect(self._on_finished)

    @property
    def done(self) -> bool:
        return self._done

    def abort(self) -> None:
        """Cancel the call and guarantee no signal fires afterwards."""
        if self._done:
            return
        self._done = True
        self._release(abort_reply=True)

    # --- internals ------------------------------------------------------

    def _release(self, *, abort_reply: bool) -> None:
        self._timer.stop()
        reply, self._reply = self._reply, None
        if reply is None:
            return
        try:
            reply.finished.disconnect(self._on_finished)
        except (RuntimeError, TypeError):
            pass
        if abort_reply:
            try:
                reply.abort()
            except RuntimeError:  # pragma: no cover - already deleted
                pass
        reply.deleteLater()

    def _on_timeout(self) -> None:
        if self._done:
            return
        self._done = True
        self._release(abort_reply=True)
        _logger.info("Claude request timed out")
        self.failed.emit("Claude took too long to answer.")

    def _on_finished(self) -> None:
        if self._done or self._reply is None:
            return
        self._done = True
        reply = self._reply
        status = _http_status(reply)
        try:
            body = bytes(reply.readAll())
        except RuntimeError:  # pragma: no cover - reply already deleted
            body = b""
        self._release(abort_reply=False)

        if status <= 0:
            # No HTTP exchange happened at all: DNS, TLS, offline.
            _logger.info("Claude request failed before any HTTP reply")
            self.failed.emit("No connection to Claude.")
            return
        text, error = parse_response(status, body)
        _logger.info("Claude replied: status %d, %d bytes", status, len(body))
        if text is not None:
            self.succeeded.emit(text)
        else:
            self.failed.emit(error or "Claude could not process this request.")


def _http_status(reply: Any) -> int:
    from PySide6.QtNetwork import QNetworkRequest

    try:
        value = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
    except RuntimeError:  # pragma: no cover - reply already deleted
        return 0
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


class ClaudeClient(QObject):
    """Owns the network manager and starts :class:`ClaudeRequest` objects.

    The manager is built on first use so a session that never touches AI
    features pays nothing.  Callers take the client as a constructor
    argument so tests can substitute a fake with the same ``send``.
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._nam: Any = None

    def send(
        self,
        model: str,
        system: str,
        user_text: str,
        *,
        api_key: str,
        max_tokens: int,
        effort: str,
        timeout_ms: int,
    ) -> ClaudeRequest:
        from PySide6.QtCore import QByteArray
        from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest

        # The key must only ever be sent to Anthropic.  The URL is a
        # constant today; this makes a future edit that makes it
        # configurable fail loudly instead of leaking the key elsewhere.
        assert urlsplit(MESSAGES_URL).hostname == "api.anthropic.com"

        if self._nam is None:
            self._nam = QNetworkAccessManager(self)
        headers, body = build_request(
            model, system, user_text, max_tokens=max_tokens, effort=effort, api_key=api_key
        )
        request = QNetworkRequest(QUrl(MESSAGES_URL))
        for name, value in headers.items():
            request.setRawHeader(
                QByteArray(name.encode("ascii")), QByteArray(value.encode("utf-8"))
            )
        reply = self._nam.post(request, QByteArray(body))
        _logger.info("Claude request sent: model %s, %d bytes", model, len(body))
        return ClaudeRequest(reply, timeout_ms, self)
