"""The learned click-offset table behind the beam's click positions."""

from __future__ import annotations

import math

import pytest

from src.prediction.pointer_model import MAX_SLOTS, PRIOR, PointerModel, slot_id


class TestEstimates:
    def test_an_unseen_slot_borrows_the_global_bias(self):
        m = PointerModel()
        for _ in range(20):
            m.observe("a", 0.3, -0.1)
        assert m.bias("never-pressed") == pytest.approx((0.3, -0.1))

    def test_a_slot_is_shrunk_toward_the_global_mean_by_the_prior(self):
        m = PointerModel()
        for _ in range(100):
            m.observe("a", 0.4, 0.0)  # sets the global mean at 0.4 for x
        for _ in range(int(PRIOR)):
            m.observe("b", 0.0, 0.0)  # PRIOR presses at 0 on b
        # global mean now 40/110; b's own mean 0, weighed equally with PRIOR
        global_x = 40.0 / 110.0
        assert m.bias("b")[0] == pytest.approx(global_x * PRIOR / (PRIOR + PRIOR))

    def test_correct_removes_the_bias(self):
        m = PointerModel()
        for _ in range(50):
            m.observe("h", 0.3, 0.2)
        dx, dy = m.correct("h", 0.3, 0.2)
        assert (dx, dy) == pytest.approx((0.0, 0.0), abs=1e-9)

    def test_empty_model_is_the_identity(self):
        m = PointerModel()
        assert m.bias("h") == (0.0, 0.0)
        assert m.correct("h", -0.2, 0.4) == (-0.2, 0.4)

    def test_offsets_are_clamped_and_junk_is_ignored(self):
        m = PointerModel()
        m.observe("h", 5.0, -5.0)
        # one press: the slot mean and the global mean coincide at the clamp
        assert m.bias("h") == pytest.approx((1.0, -1.0))
        m.observe("h", math.nan, 0.0)
        m.observe("", 0.1, 0.1)
        assert len(m) == 1


class TestPersistence:
    def test_round_trip(self):
        m = PointerModel()
        for _ in range(7):
            m.observe(slot_id((1, 5.25)), 0.25, -0.05)
        n = PointerModel()
        n.from_dict(m.to_dict())
        assert n.bias("1,5.25") == pytest.approx(m.bias("1,5.25"))
        assert len(n) == 7

    def test_bad_rows_are_skipped_one_at_a_time(self):
        n = PointerModel()
        n.from_dict(
            {
                "ok": [3, 0.3, 0.0],
                "short": [3, 0.3],
                "text": ["a", "b", "c"],
                "nan": [3, float("nan"), 0],
                "zero": [0, 0.0, 0.0],
                "flag": [True, 0.0, 0.0],
                "outside": [2, 9.0, 0.0],
                7: [3, 0.1, 0.1],
            }
        )
        assert set(n.to_dict()) == {"ok"}
        assert len(n) == 3

    def test_not_a_mapping_is_an_empty_table(self):
        n = PointerModel()
        n.observe("h", 0.1, 0.1)
        n.from_dict(["not", "a", "mapping"])
        assert len(n) == 0

    def test_slot_cap(self):
        n = PointerModel()
        n.from_dict({f"s{i}": [1, 0.0, 0.0] for i in range(MAX_SLOTS + 50)})
        assert len(n.to_dict()) == MAX_SLOTS
        n.observe("one-more", 0.1, 0.1)
        assert len(n.to_dict()) == MAX_SLOTS

    def test_slot_id_is_stable_and_compact(self):
        assert slot_id((1.0, 5.25)) == "1,5.25"
        assert slot_id((-1.0, 0.0)) == "-1,0"
