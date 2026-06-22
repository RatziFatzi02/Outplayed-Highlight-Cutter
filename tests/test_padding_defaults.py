from outplayed_highlight_cutter.models import Event
from outplayed_highlight_cutter.padding_defaults import (
    TypePadding,
    apply_padding_defaults,
    collect_type_paddings,
    decode_padding_defaults,
    encode_padding_defaults,
)


def test_padding_defaults_round_trip_and_apply_to_future_events() -> None:
    defaults = {"kill": TypePadding(7000, 3000), "victory": TypePadding(12000, 5000)}
    restored = decode_padding_defaults(encode_padding_defaults(defaults))
    events = [Event("kill", 1000, 1000), Event("assist", 2000, 2000)]
    apply_padding_defaults(events, restored)
    assert (events[0].before_ms, events[0].after_ms) == (7000, 3000)
    assert (events[1].before_ms, events[1].after_ms) == (10000, 5000)


def test_collect_type_paddings_uses_first_visible_value_per_type() -> None:
    events = [
        Event("kill", 1000, 1000, before_ms=7000, after_ms=3000),
        Event("kill", 2000, 2000, before_ms=9000, after_ms=4000),
        Event("victory", 3000, 3000, before_ms=15000, after_ms=6000),
    ]
    result = collect_type_paddings(events)
    assert result["kill"] == TypePadding(7000, 3000)
    assert result["victory"] == TypePadding(15000, 6000)
