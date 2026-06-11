# Outplayed Highlight Cutter

Windows desktop app that reads Outplayed's local IndexedDB markers and creates individual clips or a combined highlight video.

## Setup

```powershell
.\setup.ps1
.\start.bat
```

The setup creates a project-local `.venv`, installs PySide6 and the IndexedDB parser, and copies the existing FFmpeg 8 binary into `.tools` when available.

The app only reads Outplayed metadata and never modifies source videos or the Outplayed database.

## Workflow

1. Add one or more Outplayed MP4 files to the video queue.
2. Open each queued video to review markers and enable the event types you want.
3. Adjust global, per-type, or per-marker padding.
4. Export one combined multi-video highlight, one highlight per video, or individual clips.
5. Select hard cuts, crossfade, or dip-to-black and export.

## Advanced Options

- Automatic H.264, NVIDIA NVENC, AMD AMF H.264, AMD AMF HEVC, AMD AMF AV1, or CPU libx264 encoding.
- Quality, speed preset, output resolution, frame rate, and audio bitrate.
- Optional event-type overlays rendered at the exact marker time.
- Configurable marker duration, font, font size, position, prefix, and background opacity.

Overlapping marker ranges are merged automatically. The encoder is selected in this order: NVIDIA NVENC, AMD AMF, then `libx264`.

The Windows video picker opens in the detected Outplayed recording directory. After a selection, it remembers the last folder used.
