from datetime import datetime
from pathlib import Path

import pytest

from outplayed_highlight_cutter.naming import (
    FilenameContext,
    render_filename,
    sanitize_filename,
    unique_output_path,
)


def test_renders_all_filename_placeholders() -> None:
    context = FilenameContext(
        source="Match", game="Valorant", recording_time=datetime(2026, 1, 2, 3, 4, 5),
        export_time=datetime(2026, 6, 7, 8, 9, 10), index=7, events="kill-assist",
        mode="individual", codec="h264_amf",
    )
    result = render_filename(
        "{source}_{game}_{recording_date}_{recording_time}_{export_date}_{export_time}_{index:03}_{events}_{mode}_{codec}",
        context,
    )
    assert result == "Match_Valorant_2026-01-02_03-04-05_2026-06-07_08-09-10_007_kill-assist_individual_h264_amf.mp4"


def test_sanitizes_windows_names_and_rejects_unknown_placeholder() -> None:
    assert sanitize_filename('bad<>:"/\\|?*name. ') == "bad_________name"
    assert sanitize_filename("CON") == "_CON"
    with pytest.raises(ValueError, match="Unknown filename placeholder"):
        render_filename("{missing}", FilenameContext())


def test_output_collision_gets_numbered_suffix(tmp_path: Path) -> None:
    (tmp_path / "clip.mp4").touch()
    reserved: set[Path] = set()
    first = unique_output_path(tmp_path, "clip.mp4", reserved)
    second = unique_output_path(tmp_path, "clip.mp4", reserved)
    assert first.name == "clip_2.mp4"
    assert second.name == "clip_3.mp4"
