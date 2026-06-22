from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .ffmpeg import RenderOptions


@dataclass
class RenderProfile:
    name: str
    options: RenderOptions
    transition: str = "hard"
    transition_seconds: float = 0.25

    def to_dict(self) -> dict[str, object]:
        options = asdict(self.options)
        options["font_path"] = str(options["font_path"])
        return {
            "name": self.name,
            "options": options,
            "transition": self.transition,
            "transition_seconds": self.transition_seconds,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "RenderProfile":
        raw_options = dict(value.get("options", {}))
        raw_options["font_path"] = Path(str(raw_options.get("font_path", r"C:\Windows\Fonts\segoeuib.ttf")))
        return cls(
            name=str(value.get("name", "Profile")),
            options=RenderOptions(**raw_options),
            transition=str(value.get("transition", "hard")),
            transition_seconds=float(value.get("transition_seconds", 0.25)),
        )


def encode_profiles(profiles: list[RenderProfile]) -> str:
    return json.dumps([profile.to_dict() for profile in profiles], ensure_ascii=True)


def decode_profiles(payload: str) -> list[RenderProfile]:
    if not payload:
        return []
    parsed = json.loads(payload)
    if not isinstance(parsed, list):
        raise ValueError("Render profile storage must contain a list.")
    return [RenderProfile.from_dict(item) for item in parsed if isinstance(item, dict)]
