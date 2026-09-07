"""Tests for src/study/config.py and src/study/export.py.

See docs/research/STUDY_PROTOCOL.md section 12: no name, email, address or
machine identifier is ever collected, the participant code is the only
handle that exists, and nothing is transmitted until the participant has
reviewed the bundle and pressed submit. Every fixture below passes
``config_dir`` explicitly (see tests/conftest.py::_stay_off_the_real_config_dir)
so the suite never touches a developer's real study state.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.study import export, metrics
from src.study.config import STATE_FILENAME, StudyState, StudyStore, new_participant_code
from src.study.metrics import Event, Trial
from src.study.session import (
    ALPHA_OFF,
    ALPHA_ON,
    DESIGN_A,
    STEP_TRIAL,
    STEP_WORKLOAD,
    Design,
    Session,
    build_plan,
)


def _consented_payload() -> dict[str, Any]:
    return {
        "participant": "P-aaaaaaaa",
        "participant_index": 2,
        "design_id": "A",
        "consent_version": "v1",
        "consented_at": 1000.0,
        "cursor": 5,
        "submitted_at": 0.0,
        "background": {"pointing_device": "trackball"},
    }


class TestNewParticipantCode:
    def test_many_calls_give_distinct_codes(self) -> None:
        codes = {new_participant_code() for _ in range(200)}
        assert len(codes) == 200

    def test_two_stores_in_different_directories_mint_different_codes(self, tmp_path: Path) -> None:
        """Not derived from the machine: if the code were a hash of a
        hostname or similar, two stores on two directories (standing in
        for two machines) would collide, and a colliding participant code
        would let one person's trial log be attributed to another's.
        """
        store_a = StudyStore(config_dir=tmp_path / "a")
        store_b = StudyStore(config_dir=tmp_path / "b")
        code_a = store_a.record_consent(consent_version="v1", design_id="A", participant_index=0)
        code_b = store_b.record_consent(consent_version="v1", design_id="A", participant_index=0)
        assert code_a != code_b


class TestRecordConsentIsIdempotent:
    def test_a_second_call_keeps_the_first_code_timestamp_and_index(self, tmp_path: Path) -> None:
        """The near-miss: a second call must not reassign the condition
        order underneath a session already in progress, which is exactly
        what would happen if design_id or participant_index moved on a
        re-consent.
        """
        store = StudyStore(config_dir=tmp_path)
        code_1 = store.record_consent(consent_version="v1", design_id="A", participant_index=0)
        consented_at_1 = store.state.consented_at
        index_1 = store.state.participant_index

        code_2 = store.record_consent(consent_version="v2", design_id="B", participant_index=99)

        assert code_2 == code_1
        assert store.state.consented_at == consented_at_1
        assert store.state.participant_index == index_1
        assert store.state.design_id == "A"
        assert store.state.consent_version == "v1"


class TestStateRoundTrips:
    def test_saved_state_reloads_with_every_field_intact(self, tmp_path: Path) -> None:
        store = StudyStore(config_dir=tmp_path)
        store.record_consent(consent_version="v1", design_id="A", participant_index=3)
        store.set_cursor(7)
        store.mark_submitted()
        store.state.background = {"pointing_device": "trackball"}
        store.save()

        reloaded = StudyStore(config_dir=tmp_path)

        assert reloaded.state.participant == store.state.participant
        assert reloaded.state.participant_index == 3
        assert reloaded.state.design_id == "A"
        assert reloaded.state.consent_version == "v1"
        assert reloaded.state.consented_at == store.state.consented_at
        assert reloaded.state.cursor == 7
        assert reloaded.state.submitted_at == store.state.submitted_at
        assert reloaded.state.background == {"pointing_device": "trackball"}


class TestStudyStateFromDictTolerance:
    """A study state file nothing has validated is read on every launch,
    so every field must fail toward its own default rather than toward an
    exception that stops the study from starting at all.
    """

    def test_a_value_that_is_not_a_dict_at_all_returns_every_default(self) -> None:
        for bad in ("not a dict", None, [1, 2, 3], 42):
            assert StudyState.from_dict(bad) == StudyState()

    def test_a_non_string_participant_falls_back_without_losing_the_rest(self) -> None:
        data = _consented_payload()
        data["participant"] = 12345
        state = StudyState.from_dict(data)
        assert state.participant_index == 2
        assert state.cursor == 5
        assert state.background == {"pointing_device": "trackball"}
        assert state.has_consented is False

    def test_a_non_int_participant_index_falls_back_to_zero(self) -> None:
        data = _consented_payload()
        data["participant_index"] = "two"
        state = StudyState.from_dict(data)
        assert state.participant_index == 0
        assert state.participant == "P-aaaaaaaa"

    def test_a_bool_participant_index_is_rejected_like_any_other_bad_type(self) -> None:
        """bool is an int subclass in Python, so a naive
        ``isinstance(value, int)`` check would silently accept True/False
        as 1/0. The loader has to reject it explicitly.
        """
        data = _consented_payload()
        data["participant_index"] = True
        state = StudyState.from_dict(data)
        assert state.participant_index == 0

    def test_a_negative_cursor_falls_back_to_zero(self) -> None:
        data = _consented_payload()
        data["cursor"] = -3
        state = StudyState.from_dict(data)
        assert state.cursor == 0
        assert state.participant == "P-aaaaaaaa"

    def test_a_non_int_cursor_falls_back_to_zero(self) -> None:
        data = _consented_payload()
        data["cursor"] = "five"
        state = StudyState.from_dict(data)
        assert state.cursor == 0

    def test_a_non_string_design_id_falls_back_to_empty(self) -> None:
        data = _consented_payload()
        data["design_id"] = 123
        state = StudyState.from_dict(data)
        assert state.design_id == ""
        assert state.participant == "P-aaaaaaaa"

    def test_a_non_string_consent_version_falls_back_to_empty(self) -> None:
        data = _consented_payload()
        data["consent_version"] = 123
        state = StudyState.from_dict(data)
        assert state.consent_version == ""
        assert state.participant == "P-aaaaaaaa"

    def test_a_non_numeric_consented_at_falls_back_and_reads_as_unconsented(self) -> None:
        data = _consented_payload()
        data["consented_at"] = "yesterday"
        state = StudyState.from_dict(data)
        assert state.consented_at == 0.0
        assert state.has_consented is False
        assert state.cursor == 5

    def test_nan_consented_at_falls_back_and_reads_as_unconsented(self) -> None:
        data = _consented_payload()
        data["consented_at"] = float("nan")
        state = StudyState.from_dict(data)
        assert state.consented_at == 0.0
        assert state.has_consented is False

    def test_infinite_consented_at_falls_back_and_reads_as_unconsented(self) -> None:
        data = _consented_payload()
        data["consented_at"] = float("inf")
        state = StudyState.from_dict(data)
        assert state.consented_at == 0.0
        assert state.has_consented is False

    def test_a_non_numeric_submitted_at_falls_back_to_zero(self) -> None:
        data = _consented_payload()
        data["submitted_at"] = "never"
        state = StudyState.from_dict(data)
        assert state.submitted_at == 0.0
        assert state.participant == "P-aaaaaaaa"

    def test_nan_submitted_at_falls_back_to_zero(self) -> None:
        data = _consented_payload()
        data["submitted_at"] = float("nan")
        state = StudyState.from_dict(data)
        assert state.submitted_at == 0.0

    def test_a_non_dict_background_falls_back_to_empty(self) -> None:
        data = _consented_payload()
        data["background"] = "oops"
        state = StudyState.from_dict(data)
        assert state.background == {}
        assert state.participant == "P-aaaaaaaa"


class TestFromDictNeverFailsOpenOnConsent:
    """Section 12: no participant is enrolled and no data is collected
    without consent, so a state file whose consent half did not survive
    (a participant code with no timestamp, or the reverse) must never be
    read as consented. Failing open on consent is the one failure this
    file is not allowed to have.
    """

    def test_a_participant_code_with_no_timestamp_is_not_consented(self) -> None:
        state = StudyState.from_dict({"participant": "P-deadbeef", "consented_at": 0.0})
        assert state.has_consented is False
        assert state.participant == ""

    def test_a_timestamp_with_no_participant_code_is_not_consented(self) -> None:
        state = StudyState.from_dict({"participant": "", "consented_at": 1234.0})
        assert state.has_consented is False
        assert state.consented_at == 0.0

    def test_a_complete_consent_record_is_read_as_consented(self) -> None:
        state = StudyState.from_dict({"participant": "P-deadbeef", "consented_at": 1234.0})
        assert state.has_consented is True


class TestLoadStateCaps:
    def test_a_state_file_over_the_size_cap_is_ignored(self, tmp_path: Path) -> None:
        path = tmp_path / STATE_FILENAME
        oversized = json.dumps(
            {"participant": "P-x", "consented_at": 1.0, "padding": "x" * (300 * 1024)}
        )
        path.write_text(oversized, encoding="utf-8")

        store = StudyStore(config_dir=tmp_path)

        assert store.state == StudyState()

    def test_a_corrupt_state_file_starts_fresh_rather_than_raising(self, tmp_path: Path) -> None:
        path = tmp_path / STATE_FILENAME
        path.write_text("{not valid json", encoding="utf-8")

        store = StudyStore(config_dir=tmp_path)  # must not raise

        assert store.state == StudyState()


class TestTrialLogRoundTrips:
    def test_appended_trials_read_back_in_order(self, tmp_path: Path) -> None:
        store = StudyStore(config_dir=tmp_path)
        assert store.append_trial({"condition": "alpha_on", "phrase_index": 0}) is True
        assert store.append_trial({"condition": "alpha_off", "phrase_index": 1}) is True

        trials = store.read_trials()

        assert trials == [
            {"condition": "alpha_on", "phrase_index": 0},
            {"condition": "alpha_off", "phrase_index": 1},
        ]

    def test_a_corrupt_line_is_skipped_and_the_good_lines_either_side_survive(
        self, tmp_path: Path
    ) -> None:
        """One bad line in an append-only log must not cost every trial
        around it: the whole point of appending synchronously is that a
        session that dies mid-block loses only the trial in flight, not
        the ones before it.
        """
        store = StudyStore(config_dir=tmp_path)
        store.append_trial({"condition": "alpha_on", "phrase_index": 0})
        with store.trials_path.open("a", encoding="utf-8") as fh:
            fh.write("{not valid json\n")
        store.append_trial({"condition": "alpha_on", "phrase_index": 1})

        trials = store.read_trials()

        assert trials == [
            {"condition": "alpha_on", "phrase_index": 0},
            {"condition": "alpha_on", "phrase_index": 1},
        ]


class TestWithdraw:
    def test_withdraw_removes_both_files_and_resets_in_memory_state(self, tmp_path: Path) -> None:
        store = StudyStore(config_dir=tmp_path)
        store.record_consent(consent_version="v1", design_id="A", participant_index=0)
        store.append_trial({"condition": "alpha_on", "phrase_index": 0})
        assert store.state_path.exists()
        assert store.trials_path.exists()

        store.withdraw()

        assert not store.state_path.exists()
        assert not store.trials_path.exists()
        assert store.state == StudyState()

    def test_withdrawing_twice_does_not_raise(self, tmp_path: Path) -> None:
        """A worried participant clicking withdraw a second time must not
        crash the app on top of everything else.
        """
        store = StudyStore(config_dir=tmp_path)
        store.record_consent(consent_version="v1", design_id="A", participant_index=0)

        store.withdraw()
        store.withdraw()  # must not raise


def _design_a_session() -> Session:
    order = DESIGN_A.conditions
    phrases = {c.id: [f"{c.id}-{i}" for i in range(15)] for c in order}
    return Session.create(DESIGN_A, participant="P-test", participant_index=0, phrases=phrases)


class TestBuildBundleExcludesFingerprintingFields:
    def test_the_bundle_carries_no_hostname_username_locale_or_screen_resolution(self) -> None:
        """Section 12: the identity fields are the participant code, the
        app version and the OS name, and nothing else. A hostname,
        username, locale or screen resolution is each individually
        harmless and collectively a fingerprint, which is exactly what a
        random participant code exists to avoid re-introducing.
        """
        session = _design_a_session()
        state = StudyState(participant="P-test", design_id="A", consented_at=1.0)

        bundle = export.build_bundle(session, state, app_version="1.3.0", os_name="windows")

        deny_list = {
            "hostname",
            "host_name",
            "username",
            "user_name",
            "user",
            "locale",
            "language",
            "screen_resolution",
            "screen_width",
            "screen_height",
            "resolution",
            "ip",
            "ip_address",
            "mac_address",
            "email",
            "name",
        }
        assert set(bundle.keys()).isdisjoint(deny_list)
        assert bundle["participant"] == "P-test"


def _trial_with_gap(condition: str, phrase_index: int, gap_ms: int) -> Trial:
    events = (
        Event(t_ms=0, kind="char", text="h"),
        Event(t_ms=gap_ms, kind="char", text="i"),
    )
    return Trial(
        presented="hi",
        transcribed="hi",
        events=events,
        condition=condition,
        participant="P-test",
        phrase_index=phrase_index,
    )


_PLACEHOLDER_TLX = {
    "mental": 5,
    "physical": 5,
    "temporal": 5,
    "performance": 5,
    "effort": 5,
    "frustration": 5,
}


def _drive_to_completion(session: Session) -> None:
    """Advance *session* through its whole plan.

    Every STEP_TRIAL gets a gap-timed trial (faster for alpha_on than for
    alpha_off, so the two conditions are actually distinguishable),
    STEP_WORKLOAD gets a placeholder TLX response, and STEP_BREAK is just
    advanced past. review_summary only reads scored trials and workload,
    so the exact TLX numbers here do not matter.
    """
    while not session.is_complete:
        step = session.current
        assert step is not None
        if step.kind == STEP_TRIAL:
            gap = 500 if step.condition.id == "alpha_on" else 1000
            session.record(_trial_with_gap(step.condition.id, step.phrase_index, gap))
        elif step.kind == STEP_WORKLOAD:
            session.record_workload(step.condition.id, _PLACEHOLDER_TLX)
        else:
            session.advance()


def _mini_review_session() -> Session:
    design = Design(
        id="mini",
        label="mini",
        conditions=(ALPHA_ON, ALPHA_OFF),
        scored_phrases=1,
        practice_phrases=0,
    )
    order = design.conditions
    phrases = {c.id: [f"{c.id}-phrase"] for c in order}
    plan = build_plan(design, order, phrases)
    session = Session(design=design, order=order, plan=plan, participant="P-test")
    _drive_to_completion(session)
    return session


class TestReviewSummary:
    def test_savings_shortfall_is_benchmark_minus_realised(self) -> None:
        session = _mini_review_session()
        review = export.review_summary(session, benchmark_savings=49.1)

        on_summary = metrics.summarise(session.scored_trials("alpha_on"))
        assert review["realised_savings"] == pytest.approx(on_summary["realised_savings"])
        assert review["savings_shortfall"] == pytest.approx(49.1 - on_summary["realised_savings"])

    def test_entry_rate_difference_is_present_when_both_conditions_have_scored_trials(
        self,
    ) -> None:
        session = _mini_review_session()
        review = export.review_summary(session)

        on_summary = metrics.summarise(session.scored_trials("alpha_on"))
        off_summary = metrics.summarise(session.scored_trials("alpha_off"))
        assert review["entry_rate_difference_wpm"] == pytest.approx(
            on_summary["entry_rate_wpm"] - off_summary["entry_rate_wpm"]
        )

    def test_entry_rate_difference_is_absent_when_one_condition_has_no_scored_trials(
        self,
    ) -> None:
        """The near-miss: computing a difference against an empty,
        all-zero summary would silently report a fake number rather than
        admitting there is nothing yet to compare.
        """
        design = Design(
            id="mini-on-only",
            label="mini",
            conditions=(ALPHA_ON,),
            scored_phrases=1,
            practice_phrases=0,
        )
        order = design.conditions
        phrases = {c.id: [f"{c.id}-phrase"] for c in order}
        plan = build_plan(design, order, phrases)
        session = Session(design=design, order=order, plan=plan, participant="P-test")
        _drive_to_completion(session)

        review = export.review_summary(session)

        assert "entry_rate_difference_wpm" not in review


class TestTrialDictRoundTrip:
    def test_round_trips_a_trial_including_its_events(self) -> None:
        events = (
            Event(t_ms=0, kind="char", text="h"),
            Event(t_ms=50, kind="offer", offered=("hi", "help")),
            Event(t_ms=120, kind="pill", text="i", pill_rank=2),
        )
        trial = Trial(
            presented="hi",
            transcribed="hi",
            events=events,
            condition="alpha_on",
            participant="P-test",
            phrase_index=3,
        )

        data = export.trial_to_dict(trial)
        rebuilt = export.trial_from_dict(data, participant="P-test")

        assert rebuilt == trial

    def test_an_event_with_an_unknown_kind_is_dropped_on_the_way_back_in(self) -> None:
        """A hand-edited or future-version archive could carry an event
        kind this build does not know. Keeping it would let a bogus kind
        reach every function in metrics.py that switches on event.kind.
        """
        data = {
            "condition": "alpha_on",
            "phrase_index": 0,
            "presented": "hi",
            "transcribed": "hi",
            "events": [
                {"t_ms": 0, "kind": "char", "text": "h"},
                {"t_ms": 10, "kind": "sorcery", "text": "poof"},
                {"t_ms": 20, "kind": "char", "text": "i"},
            ],
        }

        rebuilt = export.trial_from_dict(data, participant="P-test")

        assert [e.kind for e in rebuilt.events] == ["char", "char"]
