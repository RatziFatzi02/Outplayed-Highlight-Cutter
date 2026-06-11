from __future__ import annotations

import os
import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .models import CutRange, Event


ProgressCallback = Callable[[float, str], None]

EVENT_COLORS = {
    "kill": "ef5350",
    "assist": "42a5f5",
    "death": "9e9e9e",
    "headshot": "ffca28",
    "elimination": "ab47bc",
    "respawn": "66bb6a",
    "spike_defused": "26c6da",
}


@dataclass
class MediaInfo:
    duration_seconds: float
    audio_streams: int
    width: int | None = None
    height: int | None = None
    fps: float | None = None


@dataclass
class RenderOptions:
    encoder: str = "auto"
    quality: int = 20
    preset: str = "balanced"
    resolution: str = "source"
    fps: int = 0
    audio_bitrate: int = 192
    show_markers: bool = False
    marker_duration: float = 1.5
    marker_font_size: int = 42
    marker_position: str = "top"
    marker_prefix: str = ""
    marker_box_opacity: float = 0.55
    font_path: Path = field(default_factory=lambda: Path(r"C:\Windows\Fonts\segoeuib.ttf"))


@dataclass
class ExportSource:
    source: Path
    cuts: list[CutRange]
    media: MediaInfo


def default_ffmpeg_path() -> Path:
    project_root = Path(__file__).resolve().parents[2]
    bundled = project_root / ".tools" / "ffmpeg.exe"
    if bundled.exists():
        return bundled
    known = Path(r"D:\Projects\70_Tools-PortableApps\TwitchDownloader\ffmpeg.exe")
    return known if known.exists() else Path("ffmpeg")


def _parse_timestamp(value: str) -> float:
    hours, minutes, seconds = value.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _escape_drawtext(value: str) -> str:
    return (
        value.replace("\\", r"\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace("%", r"\%")
        .replace(",", r"\,")
        .replace("[", r"\[")
        .replace("]", r"\]")
    )


class FfmpegError(RuntimeError):
    pass


class FfmpegRunner:
    def __init__(self, executable: Path | str | None = None):
        self.executable = Path(executable) if executable else default_ffmpeg_path()
        self._process: subprocess.Popen[str] | None = None
        self._cancelled = threading.Event()

    def validate(self) -> str:
        result = subprocess.run(
            [str(self.executable), "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode != 0:
            raise FfmpegError(result.stderr.strip() or "FFmpeg could not be started.")
        return result.stdout.splitlines()[0]

    def probe(self, source: Path) -> MediaInfo:
        result = subprocess.run(
            [str(self.executable), "-hide_banner", "-i", str(source)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        output = result.stderr + result.stdout
        duration_match = re.search(r"Duration:\s*(\d{2}:\d{2}:\d{2}(?:\.\d+)?)", output)
        if not duration_match:
            raise FfmpegError(f"Could not determine video duration for {source}")
        video_match = re.search(r"Video:.*?(\d{2,5})x(\d{2,5}).*?(\d+(?:\.\d+)?)\s*fps", output)
        audio_streams = len(
            re.findall(r"Stream #\d+:\d+(?:\[[^]]+\])?(?:\([^)]*\))?: Audio:", output)
        )
        return MediaInfo(
            duration_seconds=_parse_timestamp(duration_match.group(1)),
            audio_streams=audio_streams,
            width=int(video_match.group(1)) if video_match else None,
            height=int(video_match.group(2)) if video_match else None,
            fps=float(video_match.group(3)) if video_match else None,
        )

    def detect_encoder(self, preference: str = "auto") -> str:
        candidates = {
            "auto": ("h264_nvenc", "h264_amf", "libx264"),
            "h264_nvenc": ("h264_nvenc",),
            "h264_amf": ("h264_amf",),
            "hevc_amf": ("hevc_amf",),
            "av1_amf": ("av1_amf",),
            "libx264": ("libx264",),
        }.get(preference, (preference,))
        for encoder in candidates:
            command = [
                str(self.executable), "-hide_banner", "-loglevel", "error",
                # AMD AMF rejects very small frames even when the encoder is
                # fully available. Probe at a normal production resolution.
                "-f", "lavfi", "-i", "color=size=1920x1080:rate=30:duration=0.1",
                "-c:v", encoder, "-f", "null", "NUL" if os.name == "nt" else "/dev/null",
            ]
            result = subprocess.run(
                command,
                capture_output=True,
                timeout=20,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if result.returncode == 0:
                return encoder
        if preference != "auto":
            raise FfmpegError(f"The selected encoder is not available: {preference}")
        raise FfmpegError("No usable H.264 encoder was found.")

    @staticmethod
    def _encoder_args(encoder: str, options: RenderOptions | None = None) -> list[str]:
        options = options or RenderOptions()
        quality = max(0, min(51, options.quality))
        if encoder == "h264_nvenc":
            presets = {"fast": "p3", "balanced": "p5", "quality": "p7"}
            return ["-c:v", encoder, "-preset", presets.get(options.preset, "p5"), "-cq", str(quality)]
        if encoder == "h264_amf":
            presets = {"fast": "speed", "balanced": "balanced", "quality": "quality"}
            return [
                "-c:v", encoder, "-quality", presets.get(options.preset, "balanced"),
                "-rc", "cqp", "-qp_i", str(quality), "-qp_p", str(min(51, quality + 2)),
            ]
        if encoder == "hevc_amf":
            presets = {"fast": "speed", "balanced": "balanced", "quality": "quality"}
            return [
                "-c:v", encoder, "-quality", presets.get(options.preset, "balanced"),
                "-rc", "cqp", "-qp_i", str(quality), "-qp_p", str(min(51, quality + 2)),
            ]
        if encoder == "av1_amf":
            presets = {"fast": "speed", "balanced": "balanced", "quality": "quality"}
            return [
                "-c:v", encoder, "-quality", presets.get(options.preset, "balanced"),
                "-rc", "qvbr", "-qvbr_quality_level", str(quality),
            ]
        presets = {"fast": "veryfast", "balanced": "medium", "quality": "slow"}
        return ["-c:v", "libx264", "-preset", presets.get(options.preset, "medium"), "-crf", str(quality)]

    @staticmethod
    def _container_args(encoder: str) -> list[str]:
        if encoder == "hevc_amf":
            return ["-tag:v", "hvc1"]
        if encoder == "av1_amf":
            return ["-tag:v", "av01"]
        return []

    @staticmethod
    def _audio_filter(input_index: int, audio_streams: int, output_label: str) -> str | None:
        if audio_streams <= 0:
            return None
        inputs = "".join(f"[{input_index}:a:{index}]" for index in range(audio_streams))
        if audio_streams == 1:
            return f"{inputs}aformat=sample_rates=48000:channel_layouts=stereo,asetpts=PTS-STARTPTS[{output_label}]"
        return (
            f"{inputs}amix=inputs={audio_streams}:normalize=1:dropout_transition=0,"
            f"aformat=sample_rates=48000:channel_layouts=stereo,asetpts=PTS-STARTPTS[{output_label}]"
        )

    @staticmethod
    def _target_size(options: RenderOptions, media: list[MediaInfo]) -> tuple[int | None, int | None]:
        sizes = {"720p": (1280, 720), "1080p": (1920, 1080), "1440p": (2560, 1440), "2160p": (3840, 2160)}
        if options.resolution in sizes:
            return sizes[options.resolution]
        first = media[0] if media else None
        return (first.width, first.height) if first else (None, None)

    @staticmethod
    def _video_filters(
        input_index: int,
        cut: CutRange,
        options: RenderOptions,
        target_size: tuple[int | None, int | None],
        output_label: str,
    ) -> str:
        filters = [f"[{input_index}:v:0]settb=AVTB", "setpts=PTS-STARTPTS"]
        width, height = target_size
        if width and height:
            filters.extend(
                [
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease",
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black",
                ]
            )
        if options.fps:
            filters.append(f"fps={options.fps}")
        filters.append("format=yuv420p")
        if options.show_markers:
            filters.extend(FfmpegRunner._marker_filters(cut, options))
        return ",".join(filters) + f"[{output_label}]"

    @staticmethod
    def _marker_filters(cut: CutRange, options: RenderOptions) -> list[str]:
        grouped: list[tuple[float, list[Event]]] = []
        for event in sorted(cut.events, key=lambda item: item.local_time_ms or -1):
            if event.local_seconds is None:
                continue
            relative = event.local_seconds - cut.start_seconds
            if grouped and abs(grouped[-1][0] - relative) <= 0.1:
                grouped[-1][1].append(event)
            else:
                grouped.append((relative, [event]))

        y_positions = {
            "top": "h*0.08",
            "center": "(h-text_h)/2",
            "bottom": "h-text_h-h*0.08",
        }
        font_path = _escape_drawtext(str(options.font_path).replace("\\", "/"))
        filters: list[str] = []
        for relative, events in grouped:
            start = max(0.0, relative - options.marker_duration / 2.0)
            end = min(cut.duration_seconds, relative + options.marker_duration / 2.0)
            types = list(dict.fromkeys(event.type for event in events))
            text = options.marker_prefix + " + ".join(item.replace("_", " ").upper() for item in types)
            color = EVENT_COLORS.get(types[0], "ffffff")
            filters.append(
                "drawtext="
                f"fontfile='{font_path}':text='{_escape_drawtext(text)}':"
                f"fontcolor=0x{color}:fontsize={options.marker_font_size}:"
                "x=(w-text_w)/2:"
                f"y={y_positions.get(options.marker_position, y_positions['top'])}:"
                "box=1:boxcolor=black@"
                f"{options.marker_box_opacity:.2f}:boxborderw=14:"
                f"enable='between(t,{start:.3f},{end:.3f})'"
            )
        return filters

    def build_individual_command(
        self,
        source: Path,
        cut: CutRange,
        output: Path,
        media: MediaInfo,
        encoder: str,
        options: RenderOptions | None = None,
    ) -> list[str]:
        options = options or RenderOptions()
        command = [
            str(self.executable), "-y", "-hide_banner", "-ss", f"{cut.start_seconds:.3f}",
            "-t", f"{cut.duration_seconds:.3f}", "-i", str(source),
        ]
        filters = [self._video_filters(0, cut, options, self._target_size(options, [media]), "v")]
        audio_filter = self._audio_filter(0, media.audio_streams, "a")
        if audio_filter:
            filters.append(audio_filter)
        command += ["-filter_complex", ";".join(filters), "-map", "[v]"]
        if audio_filter:
            command += ["-map", "[a]"]
        else:
            command += ["-an"]
        command += self._encoder_args(encoder, options)
        command += self._container_args(encoder)
        if audio_filter:
            command += ["-c:a", "aac", "-b:a", f"{options.audio_bitrate}k"]
        command += ["-pix_fmt", "yuv420p", "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(output)]
        return command

    def build_multi_highlight_command(
        self,
        sources: list[ExportSource],
        output: Path,
        encoder: str,
        transition: str,
        transition_seconds: float,
        options: RenderOptions | None = None,
    ) -> tuple[list[str], float]:
        options = options or RenderOptions()
        flattened = [(source, cut) for source in sources for cut in source.cuts]
        if not flattened:
            raise ValueError("At least one cut range is required.")
        command = [str(self.executable), "-y", "-hide_banner"]
        for source, cut in flattened:
            command += ["-ss", f"{cut.start_seconds:.3f}", "-t", f"{cut.duration_seconds:.3f}", "-i", str(source.source)]

        target_size = self._target_size(options, [source.media for source in sources])
        filters: list[str] = []
        has_audio = all(source.media.audio_streams > 0 for source, _cut in flattened)
        for index, (source, cut) in enumerate(flattened):
            filters.append(self._video_filters(index, cut, options, target_size, f"v{index}"))
            if has_audio:
                audio_filter = self._audio_filter(index, source.media.audio_streams, f"a{index}")
                if audio_filter:
                    filters.append(audio_filter)

        cuts = [cut for _source, cut in flattened]
        if len(cuts) == 1:
            video_label = "v0"
            audio_label = "a0" if has_audio else None
            total_duration = cuts[0].duration_seconds
        elif transition == "hard":
            concat_inputs = "".join(
                f"[v{index}]" + (f"[a{index}]" if has_audio else "") for index in range(len(cuts))
            )
            filters.append(
                f"{concat_inputs}concat=n={len(cuts)}:v=1:a={1 if has_audio else 0}[vout]"
                + ("[aout]" if has_audio else "")
            )
            video_label, audio_label = "vout", "aout" if has_audio else None
            total_duration = sum(cut.duration_seconds for cut in cuts)
        else:
            transition_seconds = max(0.05, min(transition_seconds, min(cut.duration_seconds for cut in cuts) / 2.0))
            xfade_name = "fadeblack" if transition == "dip-black" else "fade"
            video_label = "v0"
            audio_label = "a0" if has_audio else None
            elapsed = cuts[0].duration_seconds
            for index in range(1, len(cuts)):
                next_video = f"vx{index}"
                offset = elapsed - transition_seconds
                filters.append(
                    f"[{video_label}][v{index}]xfade=transition={xfade_name}:"
                    f"duration={transition_seconds:.3f}:offset={offset:.3f}[{next_video}]"
                )
                video_label = next_video
                if has_audio and audio_label:
                    next_audio = f"ax{index}"
                    filters.append(
                        f"[{audio_label}][a{index}]acrossfade=d={transition_seconds:.3f}:c1=tri:c2=tri[{next_audio}]"
                    )
                    audio_label = next_audio
                elapsed += cuts[index].duration_seconds - transition_seconds
            total_duration = elapsed

        command += ["-filter_complex", ";".join(filters), "-map", f"[{video_label}]"]
        command += ["-map", f"[{audio_label}]"] if audio_label else ["-an"]
        command += self._encoder_args(encoder, options)
        command += self._container_args(encoder)
        if audio_label:
            command += ["-c:a", "aac", "-b:a", f"{options.audio_bitrate}k"]
        command += ["-pix_fmt", "yuv420p", "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(output)]
        return command, total_duration

    def build_highlight_command(
        self,
        source: Path,
        cuts: list[CutRange],
        output: Path,
        media: MediaInfo,
        encoder: str,
        transition: str,
        transition_seconds: float,
        options: RenderOptions | None = None,
    ) -> tuple[list[str], float]:
        return self.build_multi_highlight_command(
            [ExportSource(source, cuts, media)], output, encoder, transition, transition_seconds, options
        )

    def _run(self, command: list[str], expected_seconds: float, callback: ProgressCallback | None) -> None:
        self._cancelled.clear()
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self._process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=flags,
        )
        log_lines: list[str] = []
        assert self._process.stdout is not None
        for line in self._process.stdout:
            clean = line.strip()
            log_lines.append(clean)
            if len(log_lines) > 200:
                log_lines.pop(0)
            if clean.startswith("out_time_ms="):
                raw_time = clean.split("=", 1)[1]
                if raw_time != "N/A":
                    current = float(raw_time) / 1_000_000.0
                    if callback:
                        callback(min(1.0, current / max(expected_seconds, 0.001)), clean)
            if self._cancelled.is_set():
                self._process.terminate()
                break
        return_code = self._process.wait()
        self._process = None
        if self._cancelled.is_set():
            raise FfmpegError("Export cancelled.")
        if return_code != 0:
            raise FfmpegError("FFmpeg export failed:\n" + "\n".join(log_lines[-40:]))

    def cancel(self) -> None:
        self._cancelled.set()
        if self._process and self._process.poll() is None:
            self._process.terminate()

    def export_individual(
        self,
        source: Path,
        cuts: list[CutRange],
        output_dir: Path,
        media: MediaInfo,
        encoder: str,
        callback: ProgressCallback | None = None,
        options: RenderOptions | None = None,
    ) -> list[Path]:
        options = options or RenderOptions()
        output_dir.mkdir(parents=True, exist_ok=True)
        outputs: list[Path] = []
        total = sum(cut.duration_seconds for cut in cuts)
        completed = 0.0
        for index, cut in enumerate(cuts, start=1):
            event_names = "-".join(sorted({event.type for event in cut.events})) or "clip"
            output = output_dir / f"{source.stem}_{index:03d}_{event_names}.mp4"

            def scaled(progress: float, message: str, base: float = completed, duration: float = cut.duration_seconds) -> None:
                if callback:
                    callback((base + progress * duration) / max(total, 0.001), message)

            command = self.build_individual_command(source, cut, output, media, encoder, options)
            self._run(command, cut.duration_seconds, scaled)
            completed += cut.duration_seconds
            outputs.append(output)
        return outputs

    def export_highlight(
        self,
        source: Path,
        cuts: list[CutRange],
        output_dir: Path,
        media: MediaInfo,
        encoder: str,
        transition: str,
        transition_seconds: float,
        callback: ProgressCallback | None = None,
        options: RenderOptions | None = None,
    ) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / f"{source.stem}_highlights.mp4"
        command, duration = self.build_highlight_command(
            source, cuts, output, media, encoder, transition, transition_seconds, options
        )
        self._run(command, duration, callback)
        return [output]

    def export_combined(
        self,
        sources: list[ExportSource],
        output_dir: Path,
        encoder: str,
        transition: str,
        transition_seconds: float,
        callback: ProgressCallback | None = None,
        options: RenderOptions | None = None,
    ) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / "combined_highlights.mp4"
        command, duration = self.build_multi_highlight_command(
            sources, output, encoder, transition, transition_seconds, options
        )
        self._run(command, duration, callback)
        return [output]
