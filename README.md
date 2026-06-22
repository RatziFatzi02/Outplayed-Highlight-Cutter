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

1. Use **Add match folder...** for an Outplayed match or collection folder.
   Use **Add videos...** only when you want to add individual files.
2. Start in **All Videos Event Paddings** to review every discovered event type.
3. Open a queued video for **Per Video Event Paddings** and individual marker settings.
4. Use **Save type paddings as defaults** to persist values per event type. Known types such as `kill` or `victory` then receive the same padding when future videos are loaded.
5. Changes in the all-videos view require confirmation because they overwrite matching per-video and individual settings in every loaded video.
6. Export one combined multi-video highlight, one highlight per video, or individual clips.

## Advanced Options

- Automatic H.264, NVIDIA NVENC, AMD AMF H.264, AMD AMF HEVC, AMD AMF AV1, or CPU libx264 encoding.
- Quality, speed preset, output resolution, frame rate, and audio bitrate.
- Optional event-type overlays rendered at the exact marker time.
- Configurable marker duration, font, font size, position, prefix, and background opacity.
- Named render profiles, including transitions, codecs, quality and overlay settings.
- Separate filename templates for individual, per-video and combined exports.
- Expert-only Outplayed database and FFmpeg paths.

The queue can be sorted by recording date, filename, duration, game or marker count and then rearranged manually with drag-and-drop. Right-click videos and events for the available queue, playback, filtering and marker actions.

Exports show an estimated remaining time. Detailed timestamped logs are printed to the terminal and written to `%LOCALAPPDATA%\OutplayedHighlightCutter\logs\application.log` with rotation.

Overlapping marker ranges are merged automatically. The encoder is selected in this order: NVIDIA NVENC, AMD AMF, then `libx264`.

The Windows video picker opens in the detected Outplayed recording directory. After a selection, it remembers the last folder used.
