"""Tests for the dictation history store.

The interesting cases are all hostile: the index is a file anything may
have damaged, and a WAV path is derived from an id, so an id that is not
sixteen hex characters must never reach the filesystem.
"""

from __future__ import annotations

import json
import wave
from pathlib import Path

import pytest

from src.dictation import history as hist
from src.dictation.history import DictationHistory

PCM = b"\x01\x00" * 1600  # 0.1 s


@pytest.fixture
def store(tmp_path: Path) -> DictationHistory:
    return DictationHistory(tmp_path / "h")


def _add(store: DictationHistory, n: int = 1, pcm: bytes | None = PCM) -> list[str]:
    return [store.add("dictate", f"raw {i}", f"result {i}", True, 1.5, pcm) for i in range(n)]


class TestAddAndList:
    def test_round_trip_newest_first(self, store: DictationHistory):
        a, b = _add(store, 2)
        entries = store.entries()
        assert [e["id"] for e in entries] == [b, a]
        first = entries[0]
        assert first["purpose"] == "dictate" and first["raw"] == "raw 1"
        assert first["result"] == "result 1" and first["cleaned"] is True
        assert first["hasAudio"] is True and first["seconds"] == 1.5
        assert first["created"].endswith("+00:00")

    def test_entries_expose_no_paths(self, store: DictationHistory, tmp_path: Path):
        _add(store)
        assert str(tmp_path) not in json.dumps(store.entries())

    def test_it_survives_a_reload(self, store: DictationHistory, tmp_path: Path):
        (entry_id,) = _add(store)
        again = DictationHistory(tmp_path / "h")
        assert [e["id"] for e in again.entries()] == [entry_id]

    def test_no_audio_means_no_wav(self, store: DictationHistory):
        (entry_id,) = _add(store, pcm=None)
        assert store.entries()[0]["hasAudio"] is False
        assert store.audio_path(entry_id) is None

    def test_a_bad_purpose_is_refused(self, store: DictationHistory):
        assert store.add("nonsense", "a", "b", False, 1.0, None) == ""
        assert store.entries() == []

    def test_text_is_capped(self, store: DictationHistory):
        entry_id = store.add("write", "r" * 50_000, "x" * 50_000, True, 1.0, None)
        entry = store.get(entry_id)
        assert entry is not None
        assert len(entry["raw"]) == hist.MAX_TEXT_CHARS
        assert len(entry["result"]) == hist.MAX_TEXT_CHARS


class TestTheWav:
    def test_header_is_16k_mono_16_bit(self, store: DictationHistory):
        (entry_id,) = _add(store)
        path = store.audio_path(entry_id)
        assert path is not None
        with wave.open(str(path), "rb") as wav:
            assert wav.getframerate() == 16000
            assert wav.getnchannels() == 1
            assert wav.getsampwidth() == 2
            assert wav.getnframes() == len(PCM) // 2

    def test_an_odd_byte_count_does_not_break_the_file(self, store: DictationHistory):
        entry_id = store.add("dictate", "r", "r", False, 1.0, PCM + b"\x07")
        assert store.audio_path(entry_id) is not None

    def test_no_temp_files_are_left_behind(self, store: DictationHistory, tmp_path: Path):
        _add(store, 3)
        assert not list((tmp_path / "h").glob("*.tmp"))


class TestPruning:
    def test_past_the_entry_ceiling_the_oldest_go_with_their_audio(
        self, store: DictationHistory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(hist, "MAX_ENTRIES", 5)
        ids = _add(store, 8)
        entries = store.entries()
        assert len(entries) == 5
        assert [e["id"] for e in entries] == list(reversed(ids[3:]))
        for old in ids[:3]:
            assert not (tmp_path / "h" / f"{old}.wav").exists()

    def test_past_the_byte_limit_the_oldest_go_first(self, tmp_path: Path):
        probe = DictationHistory(tmp_path / "probe")
        _add(probe)
        one = probe.usage_bytes()
        store = DictationHistory(tmp_path / "h", limit_bytes=3 * one + one // 2)
        ids = _add(store, 6)
        kept = [e["id"] for e in store.entries()]
        assert kept[0] == ids[-1]
        assert 1 < len(kept) < 6 and set(kept) <= set(ids)
        assert kept == list(reversed(ids[-len(kept) :]))
        assert store.usage_bytes() <= store.limit_bytes

    def test_the_newest_entry_survives_even_alone_over_the_limit(self, tmp_path: Path):
        store = DictationHistory(tmp_path / "h", limit_bytes=10)
        (entry_id,) = _add(store)
        assert [e["id"] for e in store.entries()] == [entry_id]

    def test_lowering_the_limit_prunes_at_once(self, tmp_path: Path):
        store = DictationHistory(tmp_path / "h", limit_bytes=10**9)
        ids = _add(store, 6)
        assert len(store.entries()) == 6
        store.set_limit_bytes(store.usage_bytes() // 2)
        kept = [e["id"] for e in DictationHistory(tmp_path / "h").entries()]
        assert 1 <= len(kept) < 6 and kept[0] == ids[-1]
        for gone in ids[: 6 - len(kept)]:
            assert not (tmp_path / "h" / f"{gone}.wav").exists()

    def test_a_limit_of_zero_means_no_byte_pruning(self, tmp_path: Path):
        store = DictationHistory(tmp_path / "h", limit_bytes=0)
        ids = _add(store, 12)
        assert [e["id"] for e in store.entries()] == list(reversed(ids))
        store.set_limit_bytes(0)
        assert len(store.entries()) == 12

    def test_usage_counts_recordings_and_the_index(self, store: DictationHistory, tmp_path: Path):
        _add(store, 2)
        wavs = sum(f.stat().st_size for f in (tmp_path / "h").glob("*.wav"))
        index = (tmp_path / "h" / "index.json").stat().st_size
        # The index is measured by re-encoding, which can differ by a few bytes.
        assert abs(store.usage_bytes() - (wavs + index)) <= 64

    def test_the_default_limit_is_two_hundred_megabytes(self, store: DictationHistory):
        assert store.limit_bytes == 200 * 1024 * 1024

    def test_the_saved_index_always_fits_the_loader(
        self, store: DictationHistory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # Non-ASCII text is the trap: the writer escapes it, so a character
        # costs six bytes on disk.  Ten entries at the text cap stayed
        # under the audio budget but encoded past the index cap, and the
        # next launch read back nothing and swept every WAV as an orphan.
        monkeypatch.setattr(hist, "MAX_INDEX_BYTES", 500_000)
        text = "\u3042" * hist.MAX_TEXT_CHARS
        ids = [store.add("dictate", text, text, True, 1.0, PCM) for _ in range(10)]
        assert (tmp_path / "h" / "index.json").stat().st_size <= hist.MAX_INDEX_BYTES
        kept = [e["id"] for e in store.entries()]
        assert kept and kept[0] == ids[-1] and len(kept) < 10
        again = DictationHistory(tmp_path / "h")
        assert [e["id"] for e in again.entries()] == kept
        for entry_id in kept:
            assert (tmp_path / "h" / f"{entry_id}.wav").exists()


class TestEditing:
    def test_update_result(self, store: DictationHistory):
        (entry_id,) = _add(store)
        assert store.update_result(entry_id, "better", cleaned=True) is True
        entry = store.get(entry_id)
        assert entry is not None and entry["result"] == "better"
        assert store.update_result("0" * 16, "x") is False

    def test_delete_removes_the_audio(self, store: DictationHistory, tmp_path: Path):
        a, b = _add(store, 2)
        assert store.delete(a) is True
        assert [e["id"] for e in store.entries()] == [b]
        assert not (tmp_path / "h" / f"{a}.wav").exists()
        assert store.delete(a) is False

    def test_clear(self, store: DictationHistory, tmp_path: Path):
        _add(store, 3)
        store.clear()
        assert store.entries() == []
        assert not list((tmp_path / "h").glob("*.wav"))
        assert DictationHistory(tmp_path / "h").entries() == []


def _index(directory: Path, payload) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    text = payload if isinstance(payload, str) else json.dumps(payload)
    (directory / "index.json").write_text(text, encoding="utf-8")


def _good(entry_id: str = "a" * 16) -> dict:
    return {
        "id": entry_id,
        "created": "2026-01-01T00:00:00+00:00",
        "purpose": "dictate",
        "raw": "r",
        "result": "x",
        "cleaned": False,
        "seconds": 2,
    }


class TestAHostileIndex:
    @pytest.mark.parametrize(
        "bad_id",
        ["../x", "..\\x", "C:/evil", "/etc/passwd", "a" * 15, "A" * 16, "g" * 16, "a" * 17, "", 7],
    )
    def test_bad_ids_are_dropped(self, tmp_path: Path, bad_id):
        _index(tmp_path / "h", {"entries": [{**_good(), "id": bad_id}, _good("b" * 16)]})
        assert [e["id"] for e in DictationHistory(tmp_path / "h").entries()] == ["b" * 16]

    def test_a_stored_path_is_never_followed(self, tmp_path: Path):
        secret = tmp_path / "secret.wav"
        secret.write_bytes(b"RIFF")
        entry = {**_good(), "path": str(secret), "wav": str(secret), "audio": str(secret)}
        _index(tmp_path / "h", {"entries": [entry]})
        store = DictationHistory(tmp_path / "h")
        assert store.audio_path("a" * 16) is None
        assert str(secret) not in json.dumps(store.entries())
        store.delete("a" * 16)
        store.clear()
        assert secret.exists()

    @pytest.mark.parametrize(
        "payload",
        ["", "not json", "[]", "null", '"x"', "7", '{"entries": 5}', '{"entries": {"a": 1}}'],
    )
    def test_garbage_loads_as_empty(self, tmp_path: Path, payload: str):
        _index(tmp_path / "h", payload)
        store = DictationHistory(tmp_path / "h")
        assert store.entries() == []
        assert store.add("dictate", "r", "x", False, 1.0, None) != ""

    def test_wrong_field_types_drop_the_entry(self, tmp_path: Path):
        bad = [
            {**_good("1" * 16), "raw": 5},
            {**_good("2" * 16), "result": None},
            {**_good("3" * 16), "purpose": "other"},
            "string",
            None,
            7,
        ]
        _index(tmp_path / "h", {"entries": [*bad, _good("4" * 16)]})
        assert [e["id"] for e in DictationHistory(tmp_path / "h").entries()] == ["4" * 16]

    def test_scalars_are_sanitised(self, tmp_path: Path):
        entry = {**_good(), "seconds": 1e308, "cleaned": "yes", "created": 5}
        _index(tmp_path / "h", {"entries": [entry]})
        (loaded,) = DictationHistory(tmp_path / "h").entries()
        assert loaded["seconds"] == hist.MAX_SECONDS
        assert loaded["cleaned"] is False and loaded["created"] == ""

    def test_duplicates_and_overflow(self, tmp_path: Path):
        many = [_good(f"{i:016x}") for i in range(hist.MAX_ENTRIES + 10)]
        _index(tmp_path / "h", {"entries": [_good(), _good(), *many]})
        ids = [e["id"] for e in DictationHistory(tmp_path / "h").entries()]
        assert len(ids) == hist.MAX_ENTRIES and len(set(ids)) == len(ids)

    def test_an_oversize_index_is_ignored_unread(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        _index(tmp_path / "h", {"entries": [_good()]})
        monkeypatch.setattr(hist, "MAX_INDEX_BYTES", 10)
        assert DictationHistory(tmp_path / "h").entries() == []

    @pytest.mark.parametrize("damage", ["oversize", "garbage"])
    def test_a_rejected_index_keeps_every_recording(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str
    ):
        directory = tmp_path / "h"
        if damage == "oversize":
            _index(directory, {"entries": [_good()]})
            monkeypatch.setattr(hist, "MAX_INDEX_BYTES", 10)
        else:
            _index(directory, "not json")
        wav = directory / f"{'a' * 16}.wav"
        wav.write_bytes(b"x")
        assert DictationHistory(directory).entries() == []
        assert wav.exists()


class TestOrphans:
    def test_only_valid_names_are_swept(self, tmp_path: Path):
        directory = tmp_path / "h"
        _index(directory, {"entries": [_good()]})
        owned = directory / f"{'a' * 16}.wav"
        orphan = directory / f"{'b' * 16}.wav"
        odd_names = [
            directory / "notes.wav",
            directory / f"{'D' * 16}.wav",
            directory / f"{'c' * 16}.txt",
            directory / "short.wav",
        ]
        for f in [owned, orphan, *odd_names]:
            f.write_bytes(b"x")
        DictationHistory(directory).entries()
        assert owned.exists() and not orphan.exists()
        assert all(f.exists() for f in odd_names)


class TestIOFailuresNeverRaise:
    def test_an_unwritable_directory(self, tmp_path: Path):
        blocker = tmp_path / "file"
        blocker.write_text("not a directory")
        store = DictationHistory(blocker / "sub")
        assert store.add("dictate", "r", "x", False, 1.0, PCM) == ""
        assert store.entries() is not None


class TestNotInTheBackupArchive:
    def test_data_export_never_names_it(self):
        from src import data_export

        names = " ".join(
            [*map(str, data_export._MODEL_FILES.values()), *map(str, data_export._MODEL_FILES)]
        )
        assert "dictation_history" not in names
        source = Path(data_export.__file__).read_text(encoding="utf-8")
        assert "dictation_history" not in source

    def test_a_backup_made_with_history_present_excludes_it(self, tmp_path: Path):
        import zipfile

        from src import data_export
        from src.platform import get_config_dir

        store = DictationHistory()  # the (faked) real location
        store.add("dictate", "private words", "private words", False, 1.0, PCM)
        assert (get_config_dir() / "dictation_history").is_dir()

        archive = tmp_path / "backup.zip"
        data_export.export_user_data(get_config_dir(), archive)
        with zipfile.ZipFile(archive) as zf:
            assert not [n for n in zf.namelist() if "dictation_history" in n]
            assert b"private words" not in b"".join(zf.read(n) for n in zf.namelist())


class TestTheEntryCeiling:
    def test_a_full_ceiling_sized_index_loads(self, tmp_path: Path):
        # Roughly a thousand characters each way: well past a typical run.
        text = "word " * 200
        entries = [{**_good(f"{i:016x}"), "raw": text, "result": text} for i in range(5000)]
        _index(tmp_path / "h", {"entries": entries})
        size = (tmp_path / "h" / "index.json").stat().st_size
        assert hist.MAX_ENTRIES == 5000 and size <= hist.MAX_INDEX_BYTES
        assert len(DictationHistory(tmp_path / "h").entries()) == 5000

    def test_with_no_limit_the_ceiling_drops_the_oldest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(hist, "MAX_ENTRIES", 4)
        store = DictationHistory(tmp_path / "h", limit_bytes=0)
        ids = _add(store, 7)
        assert [e["id"] for e in store.entries()] == list(reversed(ids[3:]))


class TestPagesAndRecordedAudioSize:
    def test_a_page_is_the_newest_and_count_is_the_whole(self, store: DictationHistory):
        ids = _add(store, 7)
        assert [e["id"] for e in store.entries(3)] == ids[::-1][:3]
        assert store.count() == 7
        assert store.entries(0) == []
        assert len(store.entries(100)) == 7 and len(store.entries()) == 7

    def test_audio_size_is_recorded_and_survives_a_reload(
        self, store: DictationHistory, tmp_path: Path
    ):
        (with_audio,) = _add(store)
        (without,) = _add(store, pcm=None)
        wav_size = (tmp_path / "h" / f"{with_audio}.wav").stat().st_size
        again = DictationHistory(tmp_path / "h")
        sizes = {e["id"]: e["audio_bytes"] for e in again.entries()}
        assert sizes == {with_audio: wav_size, without: 0}

    def test_pruning_never_stats_a_recording(
        self, store: DictationHistory, monkeypatch: pytest.MonkeyPatch
    ):
        _add(store, 5)
        store.limit_bytes = store.usage_bytes() // 2
        calls = []
        real = DictationHistory._measure_audio
        monkeypatch.setattr(
            DictationHistory, "_measure_audio", lambda s, i: calls.append(i) or real(s, i)
        )
        store._prune()
        assert calls == []
        assert store.usage_bytes() <= store.limit_bytes

    def test_a_legacy_entry_without_a_size_is_measured_once(self, tmp_path: Path):
        directory = tmp_path / "h"
        directory.mkdir()
        entry_id = "0123456789abcdef"
        (directory / f"{entry_id}.wav").write_bytes(b"x" * 100)
        legacy = {
            "id": entry_id,
            "created": "",
            "purpose": "dictate",
            "raw": "a",
            "result": "b",
            "cleaned": False,
            "seconds": 1.0,
        }
        (directory / "index.json").write_text(
            json.dumps({"schema_version": 1, "entries": [legacy]}), encoding="utf-8"
        )
        assert DictationHistory(directory).entries()[0]["audio_bytes"] == 100

    @pytest.mark.parametrize("bad", [-5, 1e30, "9", None, True, [1], {"a": 1}, 2**80])
    def test_a_hostile_size_is_clamped_or_ignored(self, tmp_path: Path, bad):
        directory = tmp_path / "h"
        directory.mkdir()
        entry = {
            "id": "0123456789abcdef",
            "created": "",
            "purpose": "dictate",
            "raw": "a",
            "result": "b",
            "cleaned": False,
            "seconds": 1.0,
            "audio_bytes": bad,
        }
        (directory / "index.json").write_text(
            json.dumps({"schema_version": 1, "entries": [entry]}), encoding="utf-8"
        )
        value = DictationHistory(directory).entries()[0]["audio_bytes"]
        assert isinstance(value, int) and not isinstance(value, bool)
        assert 0 <= value <= hist._MAX_AUDIO_BYTES

    def test_the_index_is_compact_and_the_size_accounting_matches_it(
        self, store: DictationHistory, tmp_path: Path
    ):
        _add(store, 4)
        text = (tmp_path / "h" / "index.json").read_text(encoding="utf-8")
        assert "\n" not in text
        assert len(text.encode("utf-8")) == store._encoded_bytes()
