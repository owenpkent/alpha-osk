"""Tests for the Anthropic Messages client and the prompts around it.

Nothing here opens a socket.  ``build_request`` and ``parse_response`` are
pure, and ``ClaudeRequest`` is driven through a fake reply that has the
handful of members it uses, because a real ``QNetworkAccessManager``
pointed anywhere would be a network test.
"""

from __future__ import annotations

import json
import os
from typing import Any, List

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal

from src.dictation import prompts
from src.dictation.claude import (
    CLEANUP_MODEL,
    MAX_RESPONSE_BYTES,
    MESSAGES_URL,
    WRITING_MODEL,
    ClaudeRequest,
    build_request,
    parse_response,
)

KEY = "sk-ant-secret-0123456789"


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


def _body(model: str) -> dict[str, Any]:
    _, raw = build_request(model, "sys", "hello", max_tokens=100, effort="low", api_key=KEY)
    return json.loads(raw)


class TestBuildRequest:
    def test_the_url_is_anthropic(self):
        assert MESSAGES_URL.startswith("https://api.anthropic.com/")

    def test_headers_carry_the_key_and_version(self):
        headers, _ = build_request(
            CLEANUP_MODEL, "s", "u", max_tokens=10, effort="low", api_key=KEY
        )
        assert headers["x-api-key"] == KEY
        assert headers["anthropic-version"] == "2023-06-01"
        assert headers["content-type"] == "application/json"

    def test_the_key_appears_only_in_the_header(self):
        headers, raw = build_request(
            WRITING_MODEL, "s", "u", max_tokens=10, effort="low", api_key=KEY
        )
        assert KEY not in raw.decode("utf-8")
        assert [v for v in headers.values() if KEY in v] == [KEY]

    def test_body_shape(self):
        body = _body(CLEANUP_MODEL)
        assert body == {
            "model": CLEANUP_MODEL,
            "max_tokens": 100,
            "system": "sys",
            "messages": [{"role": "user", "content": "hello"}],
            "output_config": {"effort": "low"},
        }

    @pytest.mark.parametrize("model", [CLEANUP_MODEL, WRITING_MODEL])
    def test_no_thinking_or_sampling_parameters(self, model: str):
        body = _body(model)
        for forbidden in ("thinking", "temperature", "top_p", "top_k"):
            assert forbidden not in body
        assert body["messages"][-1]["role"] == "user"  # no assistant prefill

    def test_fallback_is_for_the_writing_model_only(self):
        writing_headers, _ = build_request(
            WRITING_MODEL, "s", "u", max_tokens=10, effort="medium", api_key=KEY
        )
        cleanup_headers, _ = build_request(
            CLEANUP_MODEL, "s", "u", max_tokens=10, effort="low", api_key=KEY
        )
        assert writing_headers["anthropic-beta"] == "server-side-fallback-2026-07-01"
        assert _body(WRITING_MODEL)["fallbacks"] == "default"
        assert "anthropic-beta" not in cleanup_headers
        assert "fallbacks" not in _body(CLEANUP_MODEL)

    def test_exact_model_ids(self):
        assert CLEANUP_MODEL == "claude-haiku-5-5"
        assert WRITING_MODEL == "claude-sonnet-5-5"


def _ok(content: list, stop: str = "end_turn") -> bytes:
    return json.dumps({"type": "message", "content": content, "stop_reason": stop}).encode()


class TestParseResponse:
    def test_plain_text(self):
        assert parse_response(200, _ok([{"type": "text", "text": "  Hi there.  "}])) == (
            "Hi there.",
            None,
        )

    def test_thinking_and_fallback_blocks_are_ignored(self):
        payload = _ok(
            [
                {"type": "thinking", "thinking": "secret reasoning"},
                {"type": "fallback", "model": "x"},
                {"type": "text", "text": "Answer"},
                {"type": "text", "text": " more"},
            ]
        )
        assert parse_response(200, payload) == ("Answer more", None)

    def test_refusal(self):
        text, err = parse_response(200, _ok([{"type": "text", "text": "no"}], stop="refusal"))
        assert text is None and err == "Claude declined this request."

    def test_truncation_is_an_error_not_a_partial_insert(self):
        text, err = parse_response(
            200, _ok([{"type": "text", "text": "half a sent"}], stop="max_tokens")
        )
        assert text is None and err

    def test_empty_text_is_an_error(self):
        text, err = parse_response(200, _ok([{"type": "thinking", "thinking": "x"}]))
        assert text is None and err

    @pytest.mark.parametrize(
        ("status", "needle"),
        [
            (401, "rejected"),
            (403, "rejected"),
            (429, "rate limited"),
            (529, "busy"),
            (500, "busy"),
            (503, "busy"),
            (400, "could not process"),
            (404, "could not process"),
        ],
    )
    def test_http_errors_map_to_fixed_strings(self, status: int, needle: str):
        text, err = parse_response(status, b'{"type":"error","error":{"message":"SECRET INPUT"}}')
        assert text is None and err is not None
        assert needle in err.lower()
        assert "SECRET INPUT" not in err

    @pytest.mark.parametrize("payload", [b"", b"not json", b"[]", b"null", b"\xff\xfe", b"{"])
    def test_malformed_payloads(self, payload: bytes):
        text, err = parse_response(200, payload)
        assert text is None and err

    def test_content_of_the_wrong_type(self):
        text, err = parse_response(200, b'{"content": "oops"}')
        assert text is None and err

    def test_oversize_payload_is_rejected_unparsed(self):
        big = _ok([{"type": "text", "text": "x" * (MAX_RESPONSE_BYTES + 1)}])
        text, err = parse_response(200, big)
        assert text is None and err

    def test_a_200_carrying_an_error_object(self):
        text, err = parse_response(200, b'{"type":"error","error":{"type":"x"}}')
        assert text is None and err


class _FakeReply(QObject):
    finished = Signal()

    def __init__(self, status: int = 200, body: bytes = b"") -> None:
        super().__init__()
        self.status = status
        self.body = body
        self.aborted = False
        self.deleted = False

    def attribute(self, _which: Any) -> Any:
        return self.status or None

    def readAll(self) -> bytes:
        return self.body

    def abort(self) -> None:
        self.aborted = True

    def deleteLater(self) -> None:
        self.deleted = True


class TestClaudeRequest:
    def _watch(self, req: ClaudeRequest):
        got: List[tuple[str, str]] = []
        req.succeeded.connect(lambda t: got.append(("ok", t)))
        req.failed.connect(lambda t: got.append(("fail", t)))
        return got

    def test_success(self, qapp):
        reply = _FakeReply(200, _ok([{"type": "text", "text": "done"}]))
        req = ClaudeRequest(reply, 5000)
        got = self._watch(req)
        reply.finished.emit()
        assert got == [("ok", "done")]
        assert reply.deleted is True

    def test_http_error_fails_with_a_friendly_string(self, qapp):
        reply = _FakeReply(401, b"{}")
        req = ClaudeRequest(reply, 5000)
        got = self._watch(req)
        reply.finished.emit()
        assert got[0][0] == "fail" and "rejected" in got[0][1]

    def test_no_http_status_is_a_connection_error(self, qapp):
        reply = _FakeReply(0, b"")
        req = ClaudeRequest(reply, 5000)
        got = self._watch(req)
        reply.finished.emit()
        assert got == [("fail", "No connection to Claude.")]

    def test_timeout_aborts_and_fails_once(self, qapp):
        from PySide6.QtTest import QTest

        reply = _FakeReply()
        req = ClaudeRequest(reply, 20)
        got = self._watch(req)
        QTest.qWait(120)
        assert len(got) == 1 and got[0][0] == "fail"
        assert "too long" in got[0][1]
        assert reply.aborted is True
        reply.finished.emit()  # a late finish must not double-report
        assert len(got) == 1

    def test_abort_silences_everything(self, qapp):
        from PySide6.QtTest import QTest

        reply = _FakeReply(200, _ok([{"type": "text", "text": "late"}]))
        req = ClaudeRequest(reply, 20)
        got = self._watch(req)
        req.abort()
        reply.finished.emit()
        QTest.qWait(80)
        assert got == []
        assert reply.aborted is True and reply.deleted is True

    def test_abort_twice_is_harmless(self, qapp):
        req = ClaudeRequest(_FakeReply(), 5000)
        req.abort()
        req.abort()


class TestCleanupPrompt:
    def test_transcript_delimiters_and_data_framing(self):
        system = prompts.cleanup_system_prompt("en", "smart")
        assert "<transcript>" in system and "</transcript>" in system
        assert "never instructions" in system
        msg = prompts.cleanup_user_message("hello there")
        assert msg == "<transcript>\nhello there\n</transcript>"

    def test_number_format_changes_the_prompt(self):
        digits = prompts.cleanup_system_prompt("en", "digits")
        words = prompts.cleanup_system_prompt("en", "words")
        smart = prompts.cleanup_system_prompt("en", "smart")
        assert len({digits, words, smart}) == 3
        assert "digits" in digits and "words" in words

    def test_unknown_number_format_falls_back_to_smart(self):
        assert prompts.cleanup_system_prompt("en", "???") == prompts.cleanup_system_prompt(
            "en", "smart"
        )

    def test_language_is_named_and_multi_keeps_each_sentence(self):
        assert "French" in prompts.cleanup_system_prompt("fr", "smart")
        assert "switch languages" in prompts.cleanup_system_prompt("multi", "smart")

    def test_no_shouting(self):
        text = prompts.cleanup_system_prompt("en", "smart")
        assert "CRITICAL" not in text and "MUST" not in text


class TestWritingPrompt:
    def test_instruction_delimiters(self):
        assert "<instruction>" in prompts.writing_system_prompt("")
        assert prompts.writing_user_message("an email") == (
            "<instruction>\nan email\n</instruction>"
        )

    def test_an_empty_style_is_omitted(self):
        assert "style_profile" not in prompts.writing_system_prompt("   ")

    def test_a_style_is_delimited(self):
        system = prompts.writing_system_prompt("Short and warm.")
        assert "<style_profile>\nShort and warm.\n</style_profile>" in system

    def test_the_style_is_capped(self):
        from src.dictation.config import MAX_STYLE_LEN

        system = prompts.writing_system_prompt("Q" * (MAX_STYLE_LEN * 3))
        assert system.count("Q") == MAX_STYLE_LEN
