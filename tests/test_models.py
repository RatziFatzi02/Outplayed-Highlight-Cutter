from outplayed_highlight_cutter.models import (
    Event,
    build_cut_ranges,
    deduplicate_events,
    resolve_event_time,
)


def test_resolve_full_match_time() -> None:
    local, resolved = resolve_event_time(79_110, 0, 1_574_983)
    assert resolved
    assert local == 79_110


def test_resolve_segmented_media_time() -> None:
    local, resolved = resolve_event_time(310_000, 300_000, 30_000)
    assert resolved
    assert local == 10_000


def test_unresolved_time_outside_media() -> None:
    local, resolved = resolve_event_time(500_000, 100_000, 30_000)
    assert not resolved
    assert local is None


def test_deduplicates_same_type_and_time() -> None:
    events = [
        Event("kill", 1000, 1000),
        Event("kill", 1001, 1001),
        Event("assist", 1001, 1001),
    ]
    result = deduplicate_events(events)
    assert [(item.type, item.local_time_ms) for item in result] == [
        ("kill", 1000),
        ("assist", 1001),
    ]


def test_merges_overlapping_ranges_and_clamps_bounds() -> None:
    events = [
        Event("kill", 1000, 1000, before_ms=2000, after_ms=3000),
        Event("assist", 3500, 3500, before_ms=1000, after_ms=1000),
        Event("kill", 9500, 9500, before_ms=1000, after_ms=2000),
    ]
    result = build_cut_ranges(events, duration_seconds=10)
    assert len(result) == 2
    assert (result[0].start_seconds, result[0].end_seconds) == (0, 4.5)
    assert (result[1].start_seconds, result[1].end_seconds) == (8.5, 10)

