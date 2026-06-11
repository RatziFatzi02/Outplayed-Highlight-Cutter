from pathlib import Path
from types import SimpleNamespace

from outplayed_highlight_cutter.outplayed_db import (
    OutplayedDatabase,
    _iter_media_records,
    snapshot_database,
)


class JSArray:
    def __init__(self, values):
        self.values = values


def test_extracts_media_and_events() -> None:
    payload = {
        "sessionId": "session",
        "gameId": 123,
        "medias": JSArray(
            [
                {
                    "path": r"D:\Videos\clip.mp4",
                    "startTime": 100.0,
                    "endTime": 130.0,
                    "events": JSArray(
                        [
                            {"type": "kill", "time": 105_000, "timing": {"past": 2000, "future": 1000}},
                            {"type": "kill", "time": 105_001, "timing": {"past": 2000, "future": 1000}},
                        ]
                    ),
                }
            ]
        ),
    }
    record = SimpleNamespace(value=SimpleNamespace(value=payload), sequence_number=7)
    media = list(_iter_media_records([record]))[0]
    assert media.path == Path(r"D:\Videos\clip.mp4")
    assert media.duration_ms == 30_000
    assert media.events[0].local_time_ms == 5_000
    assert len(media.events) == 1


def test_snapshot_excludes_lock_file(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "000001.log").write_bytes(b"data")
    (source / "CURRENT").write_text("MANIFEST-000001", encoding="ascii")
    (source / "LOCK").write_bytes(b"")
    destination = tmp_path / "snapshot"
    snapshot_database(source, destination)
    assert (destination / "000001.log").read_bytes() == b"data"
    assert not (destination / "LOCK").exists()


def test_recording_directory_falls_back_when_database_missing(monkeypatch, tmp_path: Path) -> None:
    expected = tmp_path / "Videos" / "Outplayed"
    expected.mkdir(parents=True)
    monkeypatch.setattr(
        "outplayed_highlight_cutter.outplayed_db.find_standard_recording_directory",
        lambda: expected,
    )
    database = OutplayedDatabase(tmp_path / "missing.leveldb")
    assert database.find_recording_directory() == expected
