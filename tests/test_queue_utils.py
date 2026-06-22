from pathlib import Path

from outplayed_highlight_cutter.queue_utils import (
    collect_video_files,
    move_item,
    stable_sort,
    without_duplicate_paths,
)


def test_recursive_import_and_case_insensitive_duplicate_detection(tmp_path: Path) -> None:
    nested = tmp_path / "nested"; nested.mkdir()
    first = tmp_path / "one.MP4"; second = nested / "two.mp4"; ignored = nested / "note.txt"
    first.touch(); second.touch(); ignored.touch()
    assert set(collect_video_files(tmp_path)) == {first, second}
    assert without_duplicate_paths([first, first, second], [first]) == [second]


def test_stable_sort_then_manual_move() -> None:
    values = [(2, "a"), (1, "first"), (1, "second")]
    ordered = stable_sort(values, key=lambda item: item[0])
    assert ordered == [(1, "first"), (1, "second"), (2, "a")]
    move_item(ordered, 2, 0)
    assert ordered == [(2, "a"), (1, "first"), (1, "second")]
