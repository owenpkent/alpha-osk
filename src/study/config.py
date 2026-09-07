"""Consent record, participant code, and resumable progress.

Two files in the config directory, and the split is on write frequency rather
than on subject matter.  ``study.json`` is small state rewritten whole on every
change.  ``study_trials.jsonl`` is append-only, one line per finished trial, so
a session that dies mid-block loses the trial in flight and nothing before it.

Nothing here carries a name, an email, a machine identifier or anything the
participant typed outside a presented phrase (protocol section 12).  The
participant code is random and is the only handle that exists, which is what
makes deletion on request possible without ever having known who they are.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..atomic_write import atomic_write_json

_logger = logging.getLogger("Study.Config")

STATE_FILENAME = "study.json"
TRIALS_FILENAME = "study_trials.jsonl"

SCHEMA_VERSION = 1

# Load caps, mirroring every other store here.  The trial log is the one that
# can genuinely grow: a full session is a few hundred kilobytes of events, and
# anything past this is a bug or a file that is not ours.
_MAX_STATE_BYTES = 256 * 1_024
_MAX_TRIALS_BYTES = 32 * 1_024 * 1_024


def _config_dir() -> Path:
    """Resolve the config directory at call time, never at import time.

    Deliberately not the module-scope ``from ..platform import get_config_dir``
    that ``src/snippets.py`` and ``src/key_actions.py`` use.  Those each had to
    be added to ``tests/conftest.py::_stay_off_the_real_config_dir`` by name,
    and the snippet store shipped before that happened and spent a while
    overwriting the developer's own saved email and phone number during test
    runs.  Resolving through ``src.platform`` at call time means the existing
    patch covers this module for free and cannot be forgotten.
    """
    from ..platform import get_config_dir

    return get_config_dir()


def new_participant_code() -> str:
    """A random, opaque participant handle.

    Not derived from anything on the machine.  A hash of a hostname or a MAC
    address would be stable and would also be a pseudonym that can be
    recomputed by anyone holding the original, which is the property a
    participant code must not have.
    """
    return "P-" + secrets.token_hex(4)


@dataclass
class StudyState:
    """The whole of the resumable, persisted study state."""

    participant: str = ""
    participant_index: int = 0
    design_id: str = ""
    consent_version: str = ""
    consented_at: float = 0.0
    cursor: int = 0
    submitted_at: float = 0.0
    background: dict[str, Any] = field(default_factory=dict)

    @property
    def has_consented(self) -> bool:
        return bool(self.participant and self.consented_at > 0.0)

    @property
    def has_submitted(self) -> bool:
        return self.submitted_at > 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": SCHEMA_VERSION,
            "participant": self.participant,
            "participant_index": self.participant_index,
            "design_id": self.design_id,
            "consent_version": self.consent_version,
            "consented_at": self.consented_at,
            "cursor": self.cursor,
            "submitted_at": self.submitted_at,
            "background": self.background,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "StudyState":
        """Rebuild from disk, dropping anything malformed field by field.

        A study that cannot start because its own state file has one bad field
        is worse than one that starts having forgotten a detail, so every field
        falls back to its default individually.  The exception is consent: a
        record whose participant code or timestamp did not survive reads as no
        consent at all, and the participant is asked again.  Failing open on
        consent is the one failure this file is not allowed to have.
        """
        if not isinstance(data, dict):
            return cls()
        state = cls()
        participant = data.get("participant")
        if isinstance(participant, str) and participant:
            state.participant = participant
        for name in ("participant_index", "cursor"):
            value = data.get(name)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                setattr(state, name, value)
        for name in ("design_id", "consent_version"):
            value = data.get(name)
            if isinstance(value, str):
                setattr(state, name, value)
        for name in ("consented_at", "submitted_at"):
            value = data.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if value >= 0 and value == value and value != float("inf"):
                    setattr(state, name, float(value))
        background = data.get("background")
        if isinstance(background, dict):
            state.background = {str(k): v for k, v in background.items()}
        if not state.participant or state.consented_at <= 0.0:
            state.participant = state.participant if state.consented_at > 0 else ""
            state.consented_at = 0.0
        return state


class StudyStore:
    """Owns ``study.json`` and ``study_trials.jsonl``."""

    def __init__(self, config_dir: Path | None = None) -> None:
        self._dir = config_dir or _config_dir()
        self.state = self._load_state()

    @property
    def state_path(self) -> Path:
        return self._dir / STATE_FILENAME

    @property
    def trials_path(self) -> Path:
        return self._dir / TRIALS_FILENAME

    # --- state ---

    def _load_state(self) -> StudyState:
        path = self.state_path
        try:
            if not path.exists():
                return StudyState()
            if path.stat().st_size > _MAX_STATE_BYTES:
                _logger.warning("study state over cap, ignoring")
                return StudyState()
            return StudyState.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
            _logger.warning("study state unreadable, starting fresh: %s", e)
            return StudyState()

    def save(self) -> bool:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self.state_path, self.state.to_dict())
            return True
        except OSError as e:
            _logger.warning("could not save study state: %s", e)
            return False

    def record_consent(
        self, *, consent_version: str, design_id: str, participant_index: int
    ) -> str:
        """Mint a participant and record that consent was given.

        Idempotent: consenting twice keeps the original code and timestamp, so
        a participant who reopens the form does not become a second
        participant, and does not get their condition order reassigned
        underneath a session already in progress.
        """
        if self.state.has_consented:
            return self.state.participant
        self.state.participant = new_participant_code()
        self.state.participant_index = participant_index
        self.state.design_id = design_id
        self.state.consent_version = consent_version
        self.state.consented_at = time.time()
        self.save()
        return self.state.participant

    def set_cursor(self, cursor: int) -> None:
        self.state.cursor = max(0, cursor)
        self.save()

    def mark_submitted(self) -> None:
        self.state.submitted_at = time.time()
        self.save()

    # --- trials ---

    def append_trial(self, record: dict[str, Any]) -> bool:
        """Append one finished trial as a JSON line.

        Append rather than rewrite, and synchronously, because the cost of
        losing a session is a participant asked to do it again, which in this
        population is a cost that may simply not be payable.
        """
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            with self.trials_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, separators=(",", ":")) + "\n")
            return True
        except (OSError, TypeError, ValueError) as e:
            _logger.warning("could not append trial: %s", e)
            return False

    def read_trials(self) -> list[dict[str, Any]]:
        """Every recorded trial, skipping any line that will not parse."""
        path = self.trials_path
        try:
            if not path.exists():
                return []
            if path.stat().st_size > _MAX_TRIALS_BYTES:
                _logger.warning("trial log over cap, refusing to read")
                return []
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            _logger.warning("trial log unreadable: %s", e)
            return []

        out: list[dict[str, Any]] = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                out.append(parsed)
        return out

    # --- withdrawal ---

    def withdraw(self) -> None:
        """Discard everything this study has stored, locally and immediately.

        Withdrawal has to be a real deletion rather than a flag, because the
        consent form promises exactly that, and because a flag leaves the data
        on the participant's own disk after they asked for it to be gone.
        Missing files are not an error: withdrawing twice is a thing a worried
        person does.
        """
        for path in (self.trials_path, self.state_path):
            try:
                path.unlink(missing_ok=True)
            except OSError as e:
                _logger.warning("could not remove %s: %s", path.name, e)
        self.state = StudyState()
