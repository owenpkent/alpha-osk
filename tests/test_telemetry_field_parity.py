"""The telemetry payload is exactly ten fields, in every place that names them.

Changing the payload needs the owner's sign-off (see CLAUDE.md, *Opt-in
Telemetry*). The per-value assertions in test_telemetry.py would not notice an
added field, so this module pins the exact key set against every place that
enumerates it. Each parser is paired with a check that it really finds fields.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Callable

import pytest

from src.telemetry import SUBMIT_INTERVAL_SECONDS, TelemetryClient

REPO_ROOT = Path(__file__).resolve().parent.parent

CANONICAL = frozenset(
    {
        "anon_id",
        "app_version",
        "os",
        "keystrokes",
        "words",
        "predictions",
        "keystrokes_saved",
        "minutes",
        "sessions",
        "prediction_offers",
    }
)

SIGN_OFF = (
    "Changing the telemetry payload needs the owner's explicit sign-off, and "
    "every place that lists it must be updated together: src/telemetry.py, "
    "backend/cf-worker/src/worker.ts, docs/PRIVACY.md, "
    "docs/architecture/TELEMETRY.md, the installer sample in build/windows/build.py, "
    "and the CANONICAL set in this file."
)


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def _assert_exact(found: set[str], where: str) -> None:
    assert found, f"{where}: parser found no fields at all"
    assert found == CANONICAL, (
        f"{where} disagrees with the canonical payload. "
        f"extra={sorted(found - CANONICAL)} missing={sorted(CANONICAL - found)}. {SIGN_OFF}"
    )


def _client_payload_keys() -> set[str]:
    sent: list[bytes] = []
    clock = [1_000_000.0]

    def submit(url: str, body: bytes) -> int:
        sent.append(body)
        return 204

    stats = {
        "alltimeKeystrokes": 1,
        "alltimeWords": 1,
        "alltimePredictionHits": 1,
        "alltimeKeystrokesSaved": 1,
        "alltimeMinutes": 1.0,
        "alltimeSessions": 1,
        "alltimePredictionOffers": 1,
    }
    with tempfile.TemporaryDirectory() as d:
        client = TelemetryClient(
            state_path=Path(d) / "telemetry.json",
            endpoint="https://test.example/",
            analytics_provider=lambda: stats,
            app_version="1.0.16",
            os_name="windows",
            now=lambda: clock[0],
            submit_fn=submit,
        )
        client.enable()
        clock[0] += SUBMIT_INTERVAL_SECONDS + 1
        client.maybe_submit()
    assert sent, "the client never submitted"
    return set(json.loads(sent[0]))


def _worker_interface_fields(text: str) -> set[str]:
    body = text.split("interface SubmitPayload", 1)[1].split("}", 1)[0]
    return set(re.findall(r"^\s*(\w+)\s*:", body, re.M))


def _worker_validated_fields(text: str) -> set[str]:
    body = text.split("function validatePayload", 1)[1]
    body = body.split("return b as unknown as SubmitPayload", 1)[0]
    return set(re.findall(r"\bb\.(\w+)\b", body))


def _privacy_fields(text: str) -> set[str]:
    section = text.split("### What's in the report", 1)[1].split("###", 1)[0]
    return set(re.findall(r"^\|\s*`(\w+)`\s*\|", section, re.M))


def _architecture_fields(text: str) -> set[str]:
    section = text.split("## What goes over the wire", 1)[1].split("```jsonc", 1)[1]
    section = section.split("```", 1)[0]
    return set(re.findall(r'^\s*"(\w+)"\s*:', section, re.M))


def _installer_sample_fields(text: str) -> set[str]:
    lines = [ln for ln in text.splitlines() if "NSD_CreateLabel" in ln and "anon_id" in ln]
    assert len(lines) == 1, "expected exactly one installer sample label"
    sample = lines[0].split('"', 1)[1].rsplit('"', 1)[0]
    sample = re.sub(r"\$\\+[rn]", " ", sample)
    sample = sample.replace("${APP_VERSION}", " ")
    return {t for t in re.findall(r"\b[a-z][a-z_]*\b", sample) if t != "windows"}


class TestCanonicalSet:
    def test_it_is_ten_fields(self) -> None:
        assert len(CANONICAL) == 10


class TestParity:
    def test_client_payload(self) -> None:
        _assert_exact(_client_payload_keys(), "TelemetryClient payload")

    def test_worker_interface(self) -> None:
        _assert_exact(
            _worker_interface_fields(_read("backend/cf-worker/src/worker.ts")),
            "worker.ts SubmitPayload",
        )

    def test_worker_validator(self) -> None:
        _assert_exact(
            _worker_validated_fields(_read("backend/cf-worker/src/worker.ts")),
            "worker.ts validatePayload (includes anon_id, validated first)",
        )

    def test_privacy_doc(self) -> None:
        _assert_exact(_privacy_fields(_read("docs/PRIVACY.md")), "docs/PRIVACY.md")

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "docs/architecture/TELEMETRY.md's wire example also lists `ts`, which the "
            "doc itself says is set server-side and which the client never sends. "
            "Doc and payload disagree today; reported, not fixed here."
        ),
    )
    def test_architecture_doc(self) -> None:
        _assert_exact(
            _architecture_fields(_read("docs/architecture/TELEMETRY.md")),
            "docs/architecture/TELEMETRY.md",
        )

    def test_installer_sample(self) -> None:
        # Complements tests/test_windows_installer.py::TestThePageShowsTheMessageItSends.
        _assert_exact(
            _installer_sample_fields(_read("build/windows/build.py")),
            "installer participation page sample",
        )


class TestTheParsersActuallyParse:
    """A parser that returns nothing, or the canonical set from nowhere, must fail."""

    @pytest.mark.parametrize(
        ("parser", "path"),
        [
            (_worker_interface_fields, "backend/cf-worker/src/worker.ts"),
            (_worker_validated_fields, "backend/cf-worker/src/worker.ts"),
            (_privacy_fields, "docs/PRIVACY.md"),
            (_architecture_fields, "docs/architecture/TELEMETRY.md"),
            (_installer_sample_fields, "build/windows/build.py"),
        ],
    )
    def test_non_empty_and_covers_the_canonical_fields(
        self, parser: Callable[[str], set[str]], path: str
    ) -> None:
        found = parser(_read(path))
        assert found
        assert CANONICAL <= found

    def test_an_added_field_is_detected(self) -> None:
        worker = _read("backend/cf-worker/src/worker.ts")
        iface = worker.replace(
            "    prediction_offers: number;",
            "    prediction_offers: number;\n    word_freq: number;",
            1,
        )
        assert "word_freq" in _worker_interface_fields(iface)
        with pytest.raises(AssertionError, match="sign-off"):
            _assert_exact(_worker_interface_fields(iface), "mutated worker")

        validated = worker.replace(
            "    return b as unknown as SubmitPayload",
            '    if (!b.word_freq) return "x";\n    return b as unknown as SubmitPayload',
            1,
        )
        assert "word_freq" in _worker_validated_fields(validated)

        doc = _read("docs/PRIVACY.md").replace(
            "| `sessions` |", "| `word_freq` | `1` | x |\n| `sessions` |", 1
        )
        assert "word_freq" in _privacy_fields(doc)

        arch = _read("docs/architecture/TELEMETRY.md").replace(
            '"sessions":', '"word_freq": 1,\n  "sessions":', 1
        )
        assert "word_freq" in _architecture_fields(arch)

    def test_a_missing_field_is_detected(self) -> None:
        with pytest.raises(AssertionError, match="missing"):
            _assert_exact(set(CANONICAL - {"words"}), "mutated")

    def test_an_empty_set_is_rejected(self) -> None:
        with pytest.raises(AssertionError, match="no fields"):
            _assert_exact(set(), "empty")
