from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
PLACEHOLDER = re.compile(r"\{([a-z_]+)(?::([^}]+))?\}")
RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


@dataclass(frozen=True)
class FilenameTemplates:
    individual: str = "{source}_{index:03}_{events}"
    per_video: str = "{source}_highlights"
    combined: str = "combined_highlights_{export_date}_{export_time}"

    def for_mode(self, mode: str) -> str:
        return {
            "individual": self.individual,
            "per-video": self.per_video,
            "combined": self.combined,
        }[mode]


@dataclass(frozen=True)
class FilenameContext:
    source: str = "video"
    game: str = "unknown-game"
    recording_time: datetime | None = None
    export_time: datetime | None = None
    index: int = 1
    events: str = "clip"
    mode: str = "highlight"
    codec: str = "h264"


def sanitize_filename(value: str) -> str:
    cleaned = INVALID_FILENAME.sub("_", value).strip().rstrip(". ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = "highlight"
    if cleaned.upper() in RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned[:220]


def render_filename(template: str, context: FilenameContext, extension: str = ".mp4") -> str:
    recording = context.recording_time or datetime.fromtimestamp(0)
    exported = context.export_time or datetime.now()
    values: dict[str, object] = {
        "source": context.source,
        "game": context.game,
        "recording_date": recording.strftime("%Y-%m-%d"),
        "recording_time": recording.strftime("%H-%M-%S"),
        "export_date": exported.strftime("%Y-%m-%d"),
        "export_time": exported.strftime("%H-%M-%S"),
        "index": context.index,
        "events": context.events,
        "mode": context.mode,
        "codec": context.codec,
    }

    def replace(match: re.Match[str]) -> str:
        key, spec = match.group(1), match.group(2)
        if key not in values:
            raise ValueError(f"Unknown filename placeholder: {{{key}}}")
        value = values[key]
        try:
            return format(value, spec or "")
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid format for placeholder {{{key}}}: {spec}") from exc

    stem = sanitize_filename(PLACEHOLDER.sub(replace, template))
    suffix = extension if extension.startswith(".") else f".{extension}"
    return f"{stem}{suffix}"


def unique_output_path(directory: Path, filename: str, reserved: set[Path] | None = None) -> Path:
    reserved = reserved if reserved is not None else set()
    candidate = directory / filename
    counter = 2
    while candidate.exists() or candidate in reserved:
        candidate = directory / f"{Path(filename).stem}_{counter}{Path(filename).suffix}"
        counter += 1
    reserved.add(candidate)
    return candidate
