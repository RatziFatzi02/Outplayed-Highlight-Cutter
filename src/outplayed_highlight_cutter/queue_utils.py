from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TypeVar

from .models import normalize_path


VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov"}
T = TypeVar("T")


def collect_video_files(directory: Path, recursive: bool = True) -> list[Path]:
    if not directory.is_dir():
        raise NotADirectoryError(directory)
    iterator = directory.rglob("*") if recursive else directory.iterdir()
    return sorted(
        (path for path in iterator if path.is_file() and path.suffix.casefold() in VIDEO_EXTENSIONS),
        key=lambda path: normalize_path(path),
    )


def without_duplicate_paths(paths: Iterable[Path], existing: Iterable[Path] = ()) -> list[Path]:
    seen = {normalize_path(path) for path in existing}
    result: list[Path] = []
    for path in paths:
        key = normalize_path(path)
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def stable_sort(items: list[T], key: Callable[[T], object], reverse: bool = False) -> list[T]:
    return sorted(items, key=key, reverse=reverse)


def move_item(items: list[T], source: int, destination: int) -> None:
    if source < 0 or source >= len(items):
        return
    destination = max(0, min(destination, len(items) - 1))
    item = items.pop(source)
    items.insert(destination, item)
