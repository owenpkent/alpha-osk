"""The submission bundle, and the summary shown before it is sent.

Protocol section 12: nothing is transmitted until the participant has seen
their own numbers and pressed submit, and the bundle is a single readable JSON
file they can open first.  That constrains the format more than it looks.  It
has to be small enough to scroll through, it has to contain nothing the
participant would be surprised to find in it, and the summary they are shown
has to be computed from the bundle rather than alongside it, or the review is
of something other than what gets sent.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import metrics
from .config import StudyState
from .metrics import Trial
from .session import Session

_logger = logging.getLogger("Study.Export")

BUNDLE_VERSION = 1


def trial_to_dict(trial: Trial) -> dict[str, Any]:
    return {
        "condition": trial.condition,
        "phrase_index": trial.phrase_index,
        "presented": trial.presented,
        "transcribed": trial.transcribed,
        "events": [asdict(e) for e in trial.events],
    }


def trial_from_dict(data: dict[str, Any], participant: str) -> Trial:
    events = tuple(
        metrics.Event(
            t_ms=int(e.get("t_ms", 0)),
            kind=str(e.get("kind", "char")),
            text=str(e.get("text", "")),
            pill_rank=e.get("pill_rank"),
            offered=tuple(e.get("offered", ())),
        )
        for e in data.get("events", ())
        if isinstance(e, dict) and e.get("kind") in metrics.EVENT_KINDS
    )
    return Trial(
        presented=str(data.get("presented", "")),
        transcribed=str(data.get("transcribed", "")),
        events=events,
        condition=str(data.get("condition", "")),
        participant=participant,
        phrase_index=int(data.get("phrase_index", -1)),
    )


def per_condition_summary(session: Session) -> dict[str, dict[str, float]]:
    """Aggregate measures per condition, practice excluded."""
    out: dict[str, dict[str, float]] = {}
    for condition in session.order:
        trials = session.scored_trials(condition.id)
        if trials:
            out[condition.id] = metrics.summarise(trials)
    return out


def review_summary(session: Session, *, benchmark_savings: float = 49.1) -> dict[str, Any]:
    """The numbers the participant is shown before deciding to submit.

    Deliberately the participant's own result rather than a thank-you screen.
    Someone who has just spent 25 minutes on a typing task has earned the
    answer, and showing it is also the only honest way to ask for consent to
    send: they can see exactly what the data says about them before it goes.

    ``benchmark_savings`` is the offline figure this study exists to check
    against, so the shortfall is computed here rather than left for the
    analysis, and the participant sees the same comparison the paper will.
    """
    summaries = per_condition_summary(session)
    on = summaries.get("alpha_on", {})
    off = summaries.get("alpha_off", {})

    realised = on.get("realised_savings", 0.0)
    review: dict[str, Any] = {
        "conditions": summaries,
        "realised_savings": realised,
        "benchmark_savings": benchmark_savings,
        "savings_shortfall": benchmark_savings - realised,
    }
    if on and off:
        review["entry_rate_difference_wpm"] = on.get("entry_rate_wpm", 0.0) - off.get(
            "entry_rate_wpm", 0.0
        )
    return review


def build_bundle(
    session: Session,
    state: StudyState,
    *,
    app_version: str = "",
    os_name: str = "",
) -> dict[str, Any]:
    """Everything that would be submitted, and nothing else.

    The identity fields are the participant code, the app version and the OS
    name.  There is no hostname, no username, no locale, no screen resolution
    and no timestamp finer than the consent date: each of those is individually
    harmless and collectively a fingerprint, and none of them is needed to
    answer any question in the protocol.
    """
    return {
        "bundle_version": BUNDLE_VERSION,
        "participant": state.participant,
        "design_id": state.design_id,
        "consent_version": state.consent_version,
        "app_version": app_version,
        "os": os_name,
        "order": [c.id for c in session.order],
        "background": dict(state.background),
        "workload": {k: dict(v) for k, v in session.workload.items()},
        "summary": per_condition_summary(session),
        "trials": [trial_to_dict(t) for t in session.trials],
    }


def write_bundle(path: Path, bundle: dict[str, Any]) -> bool:
    """Write the bundle where the participant can read it before sending.

    Indented rather than compact, because the entire point is that a
    non-technical person can open it and see that it holds phrases they were
    given and numbers about how they typed them.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(bundle, indent=2, sort_keys=True), encoding="utf-8")
        return True
    except (OSError, TypeError, ValueError) as e:
        _logger.warning("could not write study bundle: %s", e)
        return False
