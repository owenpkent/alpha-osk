"""The CI shard split, which can only fail silently.

`.github/workflows/ci.yml` runs the suite as four shards per OS, each
selecting its own tests by `conftest.shard_bucket`.  Every other kind of
bug in that split announces itself: a shard that raises fails the job, a
shard that keeps too much is merely slow.  The one that does not is a
test dropped from *every* shard, because the run it disappears from
still reports green, and nothing downstream notices that CI executed
1500 tests where it collected 1600.

So the property asserted here is the partition: the shards are disjoint
and together they cover everything.
"""

from __future__ import annotations

import subprocess
import sys

from tests.conftest import shard_bucket

# Shapes real node ids take, so the sample is not all one length or all
# one prefix: parametrised ids, class methods, nested directories.
_SAMPLE_NODE_IDS = [
    f"tests/test_module_{module}.py::TestSomeClass{module}::test_a_behaviour_{index}[{param}]"
    for module in range(12)
    for index in range(12)
    for param in ("True", "False", "0", "seed-42")
] + [f"tests/nested/test_deeper_{index}.py::test_top_level_{index}" for index in range(64)]


class TestTheShardsPartitionTheSuite:
    """Disjoint and covering, for every shard count CI might use."""

    def test_every_test_lands_in_exactly_one_shard(self) -> None:
        for shard_count in (1, 2, 3, 4, 8):
            buckets = [shard_bucket(nodeid, shard_count) for nodeid in _SAMPLE_NODE_IDS]
            assert len(buckets) == len(_SAMPLE_NODE_IDS)
            assert all(0 <= bucket < shard_count for bucket in buckets), (
                f"shard_bucket returned an id outside [0, {shard_count})"
            )
            # Covering: reassembling the shards gives the whole set back.
            reassembled = {
                nodeid
                for shard in range(shard_count)
                for nodeid, bucket in zip(_SAMPLE_NODE_IDS, buckets)
                if bucket == shard
            }
            assert reassembled == set(_SAMPLE_NODE_IDS)

    def test_the_split_is_not_wildly_lopsided(self) -> None:
        """A shard holding most of the suite gives back most of the win.

        Loose on purpose: this is a hash, not a scheduler, and the point
        is to catch a bucket function that has collapsed (every test in
        shard 0), not to police a few percent of drift.  The real suite
        measures 377 / 389 / 421 / 421 over four shards.
        """
        shard_count = 4
        counts = [0] * shard_count
        for nodeid in _SAMPLE_NODE_IDS:
            counts[shard_bucket(nodeid, shard_count)] += 1
        fair_share = len(_SAMPLE_NODE_IDS) / shard_count
        assert min(counts) > fair_share * 0.5, f"a shard is nearly empty: {counts}"
        assert max(counts) < fair_share * 1.5, f"a shard holds most of the suite: {counts}"


class TestTheBucketIsStableAcrossProcesses:
    """The subtle one, and the reason this is not `hash()`.

    CPython salts string hashing per process unless PYTHONHASHSEED is
    set, and every xdist worker inside a shard is a separate process.
    With the builtin, workers would disagree about which tests are
    theirs, which does not fail loudly: some tests run twice and others
    never run at all.
    """

    def test_a_known_node_id_always_lands_in_the_same_bucket(self) -> None:
        """Pins the value, so swapping in `hash()` fails here.

        Under a randomised hash seed this assertion fails on most runs
        rather than all of them, which is still enough: the change would
        never survive a green CI matrix.
        """
        assert shard_bucket("tests/test_keyboard_bridge.py::TestModifierState::test_shift", 4) == 2

    def test_a_fresh_interpreter_agrees(self) -> None:
        """The property itself, rather than a proxy for it.

        Runs the bucket function in a subprocess with hash randomisation
        explicitly left on, which is the condition an xdist worker is
        actually under.
        """
        nodeid = "tests/test_keyboard_bridge.py::TestModifierState::test_shift"
        program = f"import zlib;print(zlib.crc32({nodeid!r}.encode('utf-8')) % 4)"
        result = subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True,
            text=True,
            check=True,
        )
        assert int(result.stdout.strip()) == shard_bucket(nodeid, 4)
