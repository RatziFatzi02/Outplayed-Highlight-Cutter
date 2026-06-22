from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

from .models import Event


@dataclass(frozen=True)
class TypePadding:
    before_ms: int
    after_ms: int


def encode_padding_defaults(defaults: dict[str, TypePadding]) -> str:
    return json.dumps(
        {
            event_type: {"before_ms": value.before_ms, "after_ms": value.after_ms}
            for event_type, value in sorted(defaults.items())
        },
        ensure_ascii=True,
    )


def decode_padding_defaults(payload: str) -> dict[str, TypePadding]:
    if not payload:
        return {}
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise ValueError("Event padding defaults must contain an object.")
    result: dict[str, TypePadding] = {}
    for event_type, value in parsed.items():
        if not isinstance(value, dict):
            continue
        result[str(event_type)] = TypePadding(
            before_ms=max(0, int(value.get("before_ms", 0))),
            after_ms=max(0, int(value.get("after_ms", 0))),
        )
    return result


def apply_padding_defaults(events: Iterable[Event], defaults: dict[str, TypePadding]) -> None:
    for event in events:
        padding = defaults.get(event.type)
        if padding:
            event.before_ms = padding.before_ms
            event.after_ms = padding.after_ms


def collect_type_paddings(events: Iterable[Event]) -> dict[str, TypePadding]:
    result: dict[str, TypePadding] = {}
    for event in events:
        result.setdefault(event.type, TypePadding(event.before_ms, event.after_ms))
    return result
