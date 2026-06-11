from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Event:
    type: str
    source_time_ms: float
    local_time_ms: float | None
    before_ms: int = 10_000
    after_ms: int = 5_000
    selected: bool = True
    resolved: bool = True
    data: Any = None

    @property
    def local_seconds(self) -> float | None:
        return None if self.local_time_ms is None else self.local_time_ms / 1000.0


@dataclass
class MediaRecord:
    path: Path
    media_start_ms: float
    media_end_ms: float
    duration_ms: float
    events: list[Event] = field(default_factory=list)
    session_id: str | None = None
    game_id: int | None = None
    sequence_number: int = 0


@dataclass
class CutRange:
    start_seconds: float
    end_seconds: float
    events: list[Event] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_seconds - self.start_seconds)


def normalize_path(path: str | Path) -> str:
    return str(Path(path)).replace("/", "\\").casefold()


def resolve_event_time(
    source_time_ms: float,
    media_start_ms: float,
    duration_ms: float,
) -> tuple[float | None, bool]:
    """Resolve Outplayed session-relative markers to media-local time."""
    candidates: list[tuple[str, float]] = [("raw", source_time_ms)]
    if media_start_ms:
        candidates.insert(0, ("offset", source_time_ms - media_start_ms))

    valid = [(kind, value) for kind, value in candidates if -250 <= value <= duration_ms + 250]
    if not valid:
        return None, False

    # Segmented media uses session-relative event times. Full-match media starts at zero.
    preferred = valid[0][1]
    return min(max(preferred, 0.0), duration_ms), True


def deduplicate_events(events: list[Event], tolerance_ms: float = 2.0) -> list[Event]:
    result: list[Event] = []
    for event in sorted(events, key=lambda item: (item.local_time_ms or -1, item.type)):
        duplicate = any(
            existing.type == event.type
            and existing.local_time_ms is not None
            and event.local_time_ms is not None
            and abs(existing.local_time_ms - event.local_time_ms) <= tolerance_ms
            for existing in result
        )
        if not duplicate:
            result.append(event)
    return result


def build_cut_ranges(
    events: list[Event],
    duration_seconds: float,
    merge_gap_seconds: float = 0.05,
) -> list[CutRange]:
    ranges: list[CutRange] = []
    for event in events:
        if not event.selected or not event.resolved or event.local_seconds is None:
            continue
        start = max(0.0, event.local_seconds - event.before_ms / 1000.0)
        end = min(duration_seconds, event.local_seconds + event.after_ms / 1000.0)
        if end > start:
            ranges.append(CutRange(start, end, [event]))

    ranges.sort(key=lambda item: item.start_seconds)
    merged: list[CutRange] = []
    for current in ranges:
        if merged and current.start_seconds <= merged[-1].end_seconds + merge_gap_seconds:
            merged[-1].end_seconds = max(merged[-1].end_seconds, current.end_seconds)
            merged[-1].events.extend(current.events)
        else:
            merged.append(current)
    return merged

