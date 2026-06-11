from __future__ import annotations

import os
import contextlib
import io
import string
import shutil
import sys
import tempfile
import types
from pathlib import Path
from typing import Any, Iterable

from .models import Event, MediaRecord, deduplicate_events, normalize_path, resolve_event_time


APP_ID = "cghphpbjeabdkomiphingnegihoigeggcfphdofo"


def default_database_path() -> Path:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    return (
        local_app_data
        / "Overwolf/CefBrowserCache/Default/IndexedDB"
        / f"overwolf-extension_{APP_ID}_0.indexeddb.leveldb"
    )


def snapshot_database(source: Path, destination: Path) -> Path:
    if not source.is_dir():
        raise FileNotFoundError(f"Outplayed database not found: {source}")
    destination.mkdir(parents=True, exist_ok=True)
    copied = 0
    for entry in source.iterdir():
        if not entry.is_file() or entry.name == "LOCK":
            continue
        target = destination / entry.name
        try:
            with entry.open("rb", buffering=0) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
            copied += 1
        except OSError:
            if entry.suffix.lower() in {".ldb", ".log"}:
                raise
    if not copied:
        raise RuntimeError("The Outplayed database snapshot is empty.")
    return destination


def _install_zstd_compatibility_shim() -> None:
    if "zstd" in sys.modules:
        return
    try:
        import zstd  # type: ignore  # noqa: F401
    except ImportError:
        import zstandard

        sys.modules["zstd"] = types.SimpleNamespace(decompress=zstandard.decompress)


def _js_values(value: Any) -> list[Any]:
    values = getattr(value, "values", value)
    return values if isinstance(values, list) else []


def _iter_media_records(records: Iterable[Any]) -> Iterable[MediaRecord]:
    for record in records:
        value_wrapper = getattr(record, "value", None)
        payload = getattr(value_wrapper, "value", None)
        if not isinstance(payload, dict):
            continue
        medias = _js_values(payload.get("medias"))
        for media in medias:
            if not isinstance(media, dict) or not media.get("path"):
                continue
            media_start_ms = float(media.get("startTime", 0.0)) * 1000.0
            media_end_ms = float(media.get("endTime", 0.0)) * 1000.0
            duration_ms = max(0.0, media_end_ms - media_start_ms)
            events: list[Event] = []
            for raw_event in _js_values(media.get("events")):
                if not isinstance(raw_event, dict) or "type" not in raw_event or "time" not in raw_event:
                    continue
                source_time = float(raw_event["time"])
                local_time, resolved = resolve_event_time(source_time, media_start_ms, duration_ms)
                timing = raw_event.get("timing") if isinstance(raw_event.get("timing"), dict) else {}
                events.append(
                    Event(
                        type=str(raw_event["type"]),
                        source_time_ms=source_time,
                        local_time_ms=local_time,
                        before_ms=int(timing.get("past", 10_000)),
                        after_ms=int(timing.get("future", 5_000)),
                        resolved=resolved,
                        selected=resolved,
                        data=raw_event.get("data"),
                    )
                )
            yield MediaRecord(
                path=Path(str(media["path"])),
                media_start_ms=media_start_ms,
                media_end_ms=media_end_ms,
                duration_ms=duration_ms,
                events=deduplicate_events(events),
                session_id=str(payload.get("sessionId")) if payload.get("sessionId") else None,
                game_id=int(payload["gameId"]) if payload.get("gameId") is not None else None,
                sequence_number=int(getattr(record, "sequence_number", 0) or 0),
            )


class OutplayedDatabase:
    def __init__(self, database_path: Path | None = None):
        self.database_path = database_path or default_database_path()

    def find_media(self, video_path: Path) -> MediaRecord:
        _install_zstd_compatibility_shim()
        from dfindexeddb.indexeddb.chromium import record as chromium_record

        target = normalize_path(video_path)
        matches: list[MediaRecord] = []
        with tempfile.TemporaryDirectory(prefix="outplayed-db-") as temp_dir:
            # dfindexeddb treats folders ending in .leveldb as blob-backed stores
            # and requires a sibling .blob directory. Outplayed records do not need it.
            snapshot = snapshot_database(self.database_path, Path(temp_dir) / "snapshot")
            reader = chromium_record.FolderReader(snapshot)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                parsed = reader.GetRecords(
                    use_manifest=False,
                    use_sequence_number=False,
                    include_raw_data=False,
                    load_blobs=False,
                )
                for media in _iter_media_records(parsed):
                    if normalize_path(media.path) == target:
                        matches.append(media)

        if not matches:
            raise LookupError(f"No Outplayed event record found for: {video_path}")
        # Outplayed can append a newer metadata copy without the original event
        # array. Prefer the richest matching record, then its latest sequence.
        return max(matches, key=lambda item: (len(item.events), item.sequence_number))

    def find_recording_directory(self) -> Path | None:
        """Find the active Outplayed media root from IndexedDB paths."""
        _install_zstd_compatibility_shim()
        from dfindexeddb.indexeddb.chromium import record as chromium_record

        paths: list[Path] = []
        if self.database_path.is_dir():
            with tempfile.TemporaryDirectory(prefix="outplayed-db-") as temp_dir:
                snapshot = snapshot_database(self.database_path, Path(temp_dir) / "snapshot")
                reader = chromium_record.FolderReader(snapshot)
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    parsed = reader.GetRecords(
                        use_manifest=False,
                        use_sequence_number=False,
                        include_raw_data=False,
                        load_blobs=False,
                    )
                    paths = [media.path for media in _iter_media_records(parsed)]

        # The nearest .owclient.ini parent is Outplayed's configured media root.
        candidates: list[Path] = []
        for media_path in paths:
            for parent in media_path.parents:
                if (parent / ".owclient.ini").is_file():
                    candidates.append(parent)
                    break
        if candidates:
            counts: dict[str, tuple[Path, int]] = {}
            for candidate in candidates:
                key = normalize_path(candidate)
                path, count = counts.get(key, (candidate, 0))
                counts[key] = (path, count + 1)
            return max(counts.values(), key=lambda item: item[1])[0]

        existing_parents = [path.parent for path in paths if path.parent.is_dir()]
        if existing_parents:
            try:
                common = Path(os.path.commonpath([str(path) for path in existing_parents]))
                if common.is_dir():
                    return common
            except ValueError:
                pass

        return find_standard_recording_directory()


def find_standard_recording_directory() -> Path | None:
    """Return a conventional Outplayed directory when metadata is unavailable."""
    candidates = [
        Path.home() / "Videos" / "Outplayed" / "Outplayed",
        Path.home() / "Videos" / "Outplayed",
    ]
    for letter in string.ascii_uppercase:
        drive = Path(f"{letter}:\\")
        candidates.extend(
            [
                drive / "Videos" / "Outplayed" / "Outplayed",
                drive / "Videos" / "Outplayed",
            ]
        )
    return next((candidate for candidate in candidates if candidate.is_dir()), None)
