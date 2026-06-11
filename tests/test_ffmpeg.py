from pathlib import Path

from outplayed_highlight_cutter.ffmpeg import ExportSource, FfmpegRunner, MediaInfo, RenderOptions
from outplayed_highlight_cutter.models import CutRange, Event


def cuts() -> list[CutRange]:
    return [
        CutRange(10, 15, [Event("kill", 12_000, 12_000)]),
        CutRange(30, 36, [Event("assist", 32_000, 32_000)]),
    ]


def test_individual_command_mixes_all_audio_streams() -> None:
    runner = FfmpegRunner("ffmpeg.exe")
    command = runner.build_individual_command(
        Path("source.mp4"), cuts()[0], Path("output.mp4"), MediaInfo(100, 3), "libx264"
    )
    filter_graph = command[command.index("-filter_complex") + 1]
    assert "[0:a:0][0:a:1][0:a:2]amix=inputs=3" in filter_graph
    assert "-crf" in command


def test_hard_cut_highlight_uses_concat() -> None:
    runner = FfmpegRunner("ffmpeg.exe")
    command, duration = runner.build_highlight_command(
        Path("source.mp4"), cuts(), Path("output.mp4"), MediaInfo(100, 1), "libx264", "hard", 0.25
    )
    filter_graph = command[command.index("-filter_complex") + 1]
    assert "concat=n=2:v=1:a=1" in filter_graph
    assert duration == 11


def test_crossfade_and_dip_black_filters() -> None:
    runner = FfmpegRunner("ffmpeg.exe")
    crossfade, crossfade_duration = runner.build_highlight_command(
        Path("source.mp4"), cuts(), Path("crossfade.mp4"), MediaInfo(100, 1), "h264_nvenc", "crossfade", 0.25
    )
    dip, _ = runner.build_highlight_command(
        Path("source.mp4"), cuts(), Path("dip.mp4"), MediaInfo(100, 1), "h264_amf", "dip-black", 0.25
    )
    assert "transition=fade:" in crossfade[crossfade.index("-filter_complex") + 1]
    assert "acrossfade=d=0.250" in crossfade[crossfade.index("-filter_complex") + 1]
    assert "transition=fadeblack:" in dip[dip.index("-filter_complex") + 1]
    assert crossfade_duration == 10.75


def test_video_without_audio_maps_no_audio() -> None:
    runner = FfmpegRunner("ffmpeg.exe")
    command, _ = runner.build_highlight_command(
        Path("source.mp4"), cuts(), Path("output.mp4"), MediaInfo(100, 0), "libx264", "hard", 0.25
    )
    assert "-an" in command
    assert "concat=n=2:v=1:a=0" in command[command.index("-filter_complex") + 1]


def test_advanced_resolution_fps_quality_and_audio_settings() -> None:
    runner = FfmpegRunner("ffmpeg.exe")
    options = RenderOptions(
        resolution="1080p", fps=60, quality=17, preset="quality", audio_bitrate=320
    )
    command = runner.build_individual_command(
        Path("source.mp4"), cuts()[0], Path("output.mp4"), MediaInfo(100, 2, 2560, 1440, 60),
        "libx264", options,
    )
    graph = command[command.index("-filter_complex") + 1]
    assert "scale=1920:1080" in graph
    assert "fps=60" in graph
    assert command[command.index("-crf") + 1] == "17"
    assert command[command.index("-preset") + 1] == "slow"
    assert command[command.index("-b:a") + 1] == "320k"


def test_marker_type_overlay_is_timed_inside_cut() -> None:
    runner = FfmpegRunner("ffmpeg.exe")
    options = RenderOptions(show_markers=True, marker_duration=2, marker_prefix="EVENT: ")
    command = runner.build_individual_command(
        Path("source.mp4"), cuts()[0], Path("output.mp4"), MediaInfo(100, 1, 1920, 1080, 60),
        "libx264", options,
    )
    graph = command[command.index("-filter_complex") + 1]
    assert "drawtext=" in graph
    assert "EVENT\\: KILL" in graph
    assert "between(t,1.000,3.000)" in graph


def test_combined_highlight_accepts_multiple_source_videos() -> None:
    runner = FfmpegRunner("ffmpeg.exe")
    sources = [
        ExportSource(Path("first.mp4"), [cuts()[0]], MediaInfo(100, 1, 1920, 1080, 60)),
        ExportSource(Path("second.mp4"), [cuts()[1]], MediaInfo(100, 1, 1280, 720, 30)),
    ]
    command, duration = runner.build_multi_highlight_command(
        sources, Path("combined.mp4"), "libx264", "hard", 0.25, RenderOptions()
    )
    graph = command[command.index("-filter_complex") + 1]
    assert command.count("first.mp4") == 1
    assert command.count("second.mp4") == 1
    assert graph.count("scale=1920:1080") == 2
    assert "concat=n=2:v=1:a=1" in graph
    assert duration == 11


def test_amf_probe_uses_supported_resolution() -> None:
    import inspect

    source = inspect.getsource(FfmpegRunner.detect_encoder)
    assert "1920x1080" in source
    assert "64x64" not in source


def test_amd_av1_and_hevc_encoder_arguments() -> None:
    options = RenderOptions(quality=18, preset="quality")
    av1 = FfmpegRunner._encoder_args("av1_amf", options)
    hevc = FfmpegRunner._encoder_args("hevc_amf", options)
    assert av1[:2] == ["-c:v", "av1_amf"]
    assert "qvbr" in av1
    assert FfmpegRunner._container_args("av1_amf") == ["-tag:v", "av01"]
    assert hevc[:2] == ["-c:v", "hevc_amf"]
    assert "cqp" in hevc
    assert FfmpegRunner._container_args("hevc_amf") == ["-tag:v", "hvc1"]
