from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .ffmpeg import ExportSource, FfmpegRunner, MediaInfo, RenderOptions, default_ffmpeg_path
from .models import CutRange, Event, MediaRecord, build_cut_ranges
from .outplayed_db import OutplayedDatabase, default_database_path, find_standard_recording_directory


EVENT_COLORS = {
    "kill": QColor("#ef5350"),
    "assist": QColor("#42a5f5"),
    "death": QColor("#9e9e9e"),
    "headshot": QColor("#ffca28"),
    "elimination": QColor("#ab47bc"),
}


@dataclass
class VideoEntry:
    source: Path
    record: MediaRecord
    media_info: MediaInfo
    enabled: bool = True


def format_seconds(seconds: float | None) -> str:
    if seconds is None:
        return "unresolved"
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(int(minutes), 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:06.3f}"
    return f"{minutes:02d}:{secs:06.3f}"


class MarkerSlider(QSlider):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.events: list[Event] = []
        self.duration_ms = 0
        self.setMinimumHeight(28)

    def set_markers(self, events: list[Event], duration_ms: int) -> None:
        self.events = events
        self.duration_ms = duration_ms
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().paintEvent(event)
        if not self.duration_ms:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        left, width = 8, max(1, self.width() - 16)
        for marker in self.events:
            if marker.local_time_ms is None:
                continue
            x = left + width * marker.local_time_ms / self.duration_ms
            color = EVENT_COLORS.get(marker.type, QColor("#26a69a"))
            painter.setPen(QPen(color, 2 if marker.selected else 1))
            painter.drawLine(int(x), 2, int(x), 10 if marker.selected else 7)


class AdvancedOptionsDialog(QDialog):
    def __init__(self, options: RenderOptions, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Advanced Rendering Options")
        self.setMinimumWidth(470)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        render_page = QWidget()
        render_form = QFormLayout(render_page)
        self.encoder = QComboBox()
        for label, value in (
            ("Automatic H.264 (GPU, CPU fallback)", "auto"),
            ("NVIDIA NVENC", "h264_nvenc"),
            ("AMD AMF H.264", "h264_amf"),
            ("AMD AMF H.265 / HEVC", "hevc_amf"),
            ("AMD AMF AV1", "av1_amf"),
            ("CPU libx264", "libx264"),
        ):
            self.encoder.addItem(label, value)
        self.encoder.setCurrentIndex(max(0, self.encoder.findData(options.encoder)))
        self.quality = QSpinBox()
        self.quality.setRange(0, 51)
        self.quality.setValue(options.quality)
        self.quality.setToolTip("Lower values mean higher quality and larger files. 18-23 is typical.")
        self.preset = QComboBox()
        self.preset.addItem("Fast", "fast")
        self.preset.addItem("Balanced", "balanced")
        self.preset.addItem("Quality", "quality")
        self.preset.setCurrentIndex(max(0, self.preset.findData(options.preset)))
        self.resolution = QComboBox()
        for label, value in (
            ("Source / first video", "source"), ("1280x720", "720p"),
            ("1920x1080", "1080p"), ("2560x1440", "1440p"), ("3840x2160", "2160p"),
        ):
            self.resolution.addItem(label, value)
        self.resolution.setCurrentIndex(max(0, self.resolution.findData(options.resolution)))
        self.fps = QComboBox()
        self.fps.addItem("Source", 0)
        self.fps.addItem("30 FPS", 30)
        self.fps.addItem("60 FPS", 60)
        self.fps.setCurrentIndex(max(0, self.fps.findData(options.fps)))
        self.audio_bitrate = QComboBox()
        for bitrate in (128, 192, 256, 320):
            self.audio_bitrate.addItem(f"{bitrate} kbit/s", bitrate)
        self.audio_bitrate.setCurrentIndex(max(0, self.audio_bitrate.findData(options.audio_bitrate)))
        render_form.addRow("Encoder", self.encoder)
        render_form.addRow("Quality", self.quality)
        render_form.addRow("Preset", self.preset)
        render_form.addRow("Resolution", self.resolution)
        render_form.addRow("Frame rate", self.fps)
        render_form.addRow("Audio bitrate", self.audio_bitrate)
        tabs.addTab(render_page, "Rendering")

        marker_page = QWidget()
        marker_form = QFormLayout(marker_page)
        self.show_markers = QCheckBox("Show event type around each marker")
        self.show_markers.setChecked(options.show_markers)
        self.marker_duration = QDoubleSpinBox()
        self.marker_duration.setRange(0.2, 10.0)
        self.marker_duration.setValue(options.marker_duration)
        self.marker_duration.setSuffix(" s")
        self.marker_font_size = QSpinBox()
        self.marker_font_size.setRange(12, 120)
        self.marker_font_size.setValue(options.marker_font_size)
        self.marker_position = QComboBox()
        self.marker_position.addItem("Top", "top")
        self.marker_position.addItem("Center", "center")
        self.marker_position.addItem("Bottom", "bottom")
        self.marker_position.setCurrentIndex(max(0, self.marker_position.findData(options.marker_position)))
        self.marker_prefix = QLineEdit(options.marker_prefix)
        self.marker_box_opacity = QDoubleSpinBox()
        self.marker_box_opacity.setRange(0.0, 1.0)
        self.marker_box_opacity.setSingleStep(0.05)
        self.marker_box_opacity.setValue(options.marker_box_opacity)
        self.font_path = QLineEdit(str(options.font_path))
        font_button = QPushButton("Browse...")
        font_button.clicked.connect(self.select_font)
        font_row = QWidget()
        font_layout = QHBoxLayout(font_row)
        font_layout.setContentsMargins(0, 0, 0, 0)
        font_layout.addWidget(self.font_path)
        font_layout.addWidget(font_button)
        marker_form.addRow(self.show_markers)
        marker_form.addRow("Display duration", self.marker_duration)
        marker_form.addRow("Font size", self.marker_font_size)
        marker_form.addRow("Position", self.marker_position)
        marker_form.addRow("Text prefix", self.marker_prefix)
        marker_form.addRow("Background opacity", self.marker_box_opacity)
        marker_form.addRow("Font", font_row)
        tabs.addTab(marker_page, "Marker Overlay")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def select_font(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Select font", self.font_path.text(), "Fonts (*.ttf *.otf)")
        if filename:
            self.font_path.setText(filename)

    def options(self) -> RenderOptions:
        return RenderOptions(
            encoder=str(self.encoder.currentData()),
            quality=self.quality.value(),
            preset=str(self.preset.currentData()),
            resolution=str(self.resolution.currentData()),
            fps=int(self.fps.currentData()),
            audio_bitrate=int(self.audio_bitrate.currentData()),
            show_markers=self.show_markers.isChecked(),
            marker_duration=self.marker_duration.value(),
            marker_font_size=self.marker_font_size.value(),
            marker_position=str(self.marker_position.currentData()),
            marker_prefix=self.marker_prefix.text(),
            marker_box_opacity=self.marker_box_opacity.value(),
            font_path=Path(self.font_path.text()),
        )


class ExportWorker(QObject):
    progress = Signal(int, str)
    finished = Signal(list)
    failed = Signal(str)

    def __init__(
        self,
        runner: FfmpegRunner,
        sources: list[ExportSource],
        output_dir: Path,
        mode: str,
        transition: str,
        transition_seconds: float,
        options: RenderOptions,
    ):
        super().__init__()
        self.runner = runner
        self.sources = sources
        self.output_dir = output_dir
        self.mode = mode
        self.transition = transition
        self.transition_seconds = transition_seconds
        self.options = options

    @Slot()
    def run(self) -> None:
        try:
            encoder = self.runner.detect_encoder(self.options.encoder)
            self.progress.emit(0, f"Encoder: {encoder}")
            total = sum(cut.duration_seconds for source in self.sources for cut in source.cuts)
            outputs: list[Path] = []

            def direct(value: float, message: str) -> None:
                self.progress.emit(round(value * 100), message)

            if self.mode == "combined":
                outputs = self.runner.export_combined(
                    self.sources, self.output_dir, encoder, self.transition,
                    self.transition_seconds, direct, self.options,
                )
            else:
                completed = 0.0
                for source in self.sources:
                    source_duration = sum(cut.duration_seconds for cut in source.cuts)

                    def scaled(value: float, message: str, base: float = completed, duration: float = source_duration) -> None:
                        self.progress.emit(round((base + value * duration) / max(total, 0.001) * 100), message)

                    if self.mode == "individual":
                        created = self.runner.export_individual(
                            source.source, source.cuts, self.output_dir, source.media,
                            encoder, scaled, self.options,
                        )
                    else:
                        created = self.runner.export_highlight(
                            source.source, source.cuts, self.output_dir, source.media,
                            encoder, self.transition, self.transition_seconds, scaled, self.options,
                        )
                    outputs.extend(created)
                    completed += source_duration
            self.finished.emit([str(path) for path in outputs])
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))

    @Slot()
    def cancel(self) -> None:
        self.runner.cancel()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Outplayed Highlight Cutter")
        self.resize(1420, 920)
        self.settings = QSettings("LocalTools", "OutplayedHighlightCutter")
        self.entries: list[VideoEntry] = []
        self.active_index: int | None = None
        self.render_options = RenderOptions()
        self.thread: QThread | None = None
        self.worker: ExportWorker | None = None

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self._build_ui()
        self._connect_player()
        self._restore_settings()

    @property
    def active_entry(self) -> VideoEntry | None:
        if self.active_index is None or self.active_index >= len(self.entries):
            return None
        return self.entries[self.active_index]

    @property
    def media_record(self) -> MediaRecord | None:
        return self.active_entry.record if self.active_entry else None

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        paths = QGroupBox("Sources and tools")
        paths_layout = QGridLayout(paths)
        self.source_edit = QLineEdit()
        self.source_edit.setReadOnly(True)
        source_button = QPushButton("Add videos...")
        source_button.clicked.connect(self.select_sources)
        self.database_edit = QLineEdit(str(default_database_path()))
        database_button = QPushButton("Database...")
        database_button.clicked.connect(self.select_database)
        self.ffmpeg_edit = QLineEdit(str(default_ffmpeg_path()))
        ffmpeg_button = QPushButton("FFmpeg...")
        ffmpeg_button.clicked.connect(self.select_ffmpeg)
        self.output_edit = QLineEdit()
        output_button = QPushButton("Output...")
        output_button.clicked.connect(self.select_output)
        for row, (label, edit, button) in enumerate(
            [
                ("Active video", self.source_edit, source_button),
                ("Outplayed database", self.database_edit, database_button),
                ("FFmpeg", self.ffmpeg_edit, ffmpeg_button),
                ("Output folder", self.output_edit, output_button),
            ]
        ):
            paths_layout.addWidget(QLabel(label), row, 0)
            paths_layout.addWidget(edit, row, 1)
            paths_layout.addWidget(button, row, 2)
        root.addWidget(paths)

        queue_group = QGroupBox("Video queue")
        queue_layout = QVBoxLayout(queue_group)
        queue_buttons = QHBoxLayout()
        add_button = QPushButton("Add videos")
        add_button.clicked.connect(self.select_sources)
        remove_button = QPushButton("Remove selected")
        remove_button.clicked.connect(self.remove_selected_sources)
        load_button = QPushButton("Edit selected")
        load_button.clicked.connect(self.activate_selected_source)
        queue_buttons.addWidget(add_button)
        queue_buttons.addWidget(remove_button)
        queue_buttons.addWidget(load_button)
        queue_buttons.addStretch()
        queue_layout.addLayout(queue_buttons)
        self.queue_table = QTableWidget(0, 4)
        self.queue_table.setHorizontalHeaderLabels(["Use", "Video", "Markers", "Duration"])
        self.queue_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.queue_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.queue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.queue_table.itemChanged.connect(self.queue_item_changed)
        self.queue_table.cellDoubleClicked.connect(lambda row, _column: self.activate_entry(row))
        self.queue_table.setMaximumHeight(150)
        queue_layout.addWidget(self.queue_table)
        root.addWidget(queue_group)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)
        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumSize(560, 315)
        self.player.setVideoOutput(self.video_widget)
        preview_layout.addWidget(self.video_widget, 1)
        self.timeline = MarkerSlider()
        self.timeline.sliderMoved.connect(self.player.setPosition)
        preview_layout.addWidget(self.timeline)
        controls = QHBoxLayout()
        self.play_button = QPushButton("Play")
        self.play_button.clicked.connect(self.toggle_playback)
        previous_button = QPushButton("Previous event")
        previous_button.clicked.connect(lambda: self.jump_event(-1))
        next_button = QPushButton("Next event")
        next_button.clicked.connect(lambda: self.jump_event(1))
        self.time_label = QLabel("00:00.000 / 00:00.000")
        controls.addWidget(self.play_button)
        controls.addWidget(previous_button)
        controls.addWidget(next_button)
        controls.addStretch()
        controls.addWidget(self.time_label)
        preview_layout.addLayout(controls)
        splitter.addWidget(preview_panel)

        events_panel = QWidget()
        events_layout = QVBoxLayout(events_panel)
        padding_group = QGroupBox("Default padding for active video")
        padding_layout = QHBoxLayout(padding_group)
        self.global_before = QDoubleSpinBox()
        self.global_before.setRange(0, 120)
        self.global_before.setValue(10)
        self.global_before.setSuffix(" s before")
        self.global_after = QDoubleSpinBox()
        self.global_after.setRange(0, 120)
        self.global_after.setValue(5)
        self.global_after.setSuffix(" s after")
        apply_all = QPushButton("Apply to all markers")
        apply_all.clicked.connect(self.apply_global_padding)
        padding_layout.addWidget(self.global_before)
        padding_layout.addWidget(self.global_after)
        padding_layout.addWidget(apply_all)
        events_layout.addWidget(padding_group)
        events_layout.addWidget(QLabel("Event type filters and per-type padding"))
        self.type_table = QTableWidget(0, 4)
        self.type_table.setHorizontalHeaderLabels(["Enabled", "Type", "Before", "After"])
        self.type_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.type_table.setMaximumHeight(180)
        events_layout.addWidget(self.type_table)
        events_layout.addWidget(QLabel("Markers (double-click a row to jump)"))
        self.event_table = QTableWidget(0, 6)
        self.event_table.setHorizontalHeaderLabels(["Use", "Type", "Time", "Before", "After", "Status"])
        self.event_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.event_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.event_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.event_table.cellDoubleClicked.connect(self.jump_to_row)
        self.event_table.itemChanged.connect(self.event_item_changed)
        events_layout.addWidget(self.event_table, 1)
        splitter.addWidget(events_panel)
        splitter.setSizes([720, 680])

        export_group = QGroupBox("Export")
        export_layout = QHBoxLayout(export_group)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("One combined highlight from all videos", "combined")
        self.mode_combo.addItem("One highlight per video", "per-video")
        self.mode_combo.addItem("Individual event clips", "individual")
        self.transition_combo = QComboBox()
        self.transition_combo.addItem("Hard cuts", "hard")
        self.transition_combo.addItem("Crossfade", "crossfade")
        self.transition_combo.addItem("Dip to black", "dip-black")
        self.transition_duration = QDoubleSpinBox()
        self.transition_duration.setRange(0.05, 3.0)
        self.transition_duration.setSingleStep(0.05)
        self.transition_duration.setValue(0.25)
        self.transition_duration.setSuffix(" s")
        advanced_button = QPushButton("Advanced Options...")
        advanced_button.clicked.connect(self.open_advanced_options)
        self.advanced_summary = QLabel("Auto encoder, source resolution, marker overlay off")
        self.export_button = QPushButton("Export queue")
        self.export_button.clicked.connect(self.start_export)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_export)
        self.progress = QProgressBar()
        self.progress.setMinimumWidth(190)
        self.status_label = QLabel("Ready")
        export_layout.addWidget(self.mode_combo)
        export_layout.addWidget(self.transition_combo)
        export_layout.addWidget(self.transition_duration)
        export_layout.addWidget(advanced_button)
        export_layout.addWidget(self.advanced_summary)
        export_layout.addWidget(self.export_button)
        export_layout.addWidget(self.cancel_button)
        export_layout.addWidget(self.progress)
        export_layout.addWidget(self.status_label, 1)
        root.addWidget(export_group)

    def _connect_player(self) -> None:
        self.player.positionChanged.connect(self.position_changed)
        self.player.durationChanged.connect(self.duration_changed)
        self.player.playbackStateChanged.connect(
            lambda state: self.play_button.setText(
                "Pause" if state == QMediaPlayer.PlaybackState.PlayingState else "Play"
            )
        )

    def _restore_settings(self) -> None:
        self.database_edit.setText(self.settings.value("database", self.database_edit.text()))
        self.ffmpeg_edit.setText(self.settings.value("ffmpeg", self.ffmpeg_edit.text()))
        self.render_options = RenderOptions(
            encoder=str(self.settings.value("render/encoder", "auto")),
            quality=int(self.settings.value("render/quality", 20)),
            preset=str(self.settings.value("render/preset", "balanced")),
            resolution=str(self.settings.value("render/resolution", "source")),
            fps=int(self.settings.value("render/fps", 0)),
            audio_bitrate=int(self.settings.value("render/audio_bitrate", 192)),
            show_markers=str(self.settings.value("marker/enabled", "false")).lower() == "true",
            marker_duration=float(self.settings.value("marker/duration", 1.5)),
            marker_font_size=int(self.settings.value("marker/font_size", 42)),
            marker_position=str(self.settings.value("marker/position", "top")),
            marker_prefix=str(self.settings.value("marker/prefix", "")),
            marker_box_opacity=float(self.settings.value("marker/box_opacity", 0.55)),
            font_path=Path(str(self.settings.value("marker/font_path", r"C:\Windows\Fonts\segoeuib.ttf"))),
        )
        self.update_advanced_summary()

    def _save_render_settings(self) -> None:
        values = {
            "render/encoder": self.render_options.encoder,
            "render/quality": self.render_options.quality,
            "render/preset": self.render_options.preset,
            "render/resolution": self.render_options.resolution,
            "render/fps": self.render_options.fps,
            "render/audio_bitrate": self.render_options.audio_bitrate,
            "marker/enabled": self.render_options.show_markers,
            "marker/duration": self.render_options.marker_duration,
            "marker/font_size": self.render_options.marker_font_size,
            "marker/position": self.render_options.marker_position,
            "marker/prefix": self.render_options.marker_prefix,
            "marker/box_opacity": self.render_options.marker_box_opacity,
            "marker/font_path": str(self.render_options.font_path),
        }
        for key, value in values.items():
            self.settings.setValue(key, value)

    @Slot()
    def select_sources(self) -> None:
        start_directory = self.source_dialog_directory()
        filenames, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Outplayed videos",
            str(start_directory) if start_directory else "",
            "Video files (*.mp4 *.mkv *.mov)",
        )
        if filenames:
            self.settings.setValue("last_source_directory", str(Path(filenames[0]).parent))
            self.add_sources([Path(filename) for filename in filenames])

    def source_dialog_directory(self) -> Path | None:
        last_directory = Path(str(self.settings.value("last_source_directory", "")))
        if str(last_directory) not in {"", "."} and last_directory.is_dir():
            return last_directory
        try:
            detected = OutplayedDatabase(Path(self.database_edit.text())).find_recording_directory()
            if detected and detected.is_dir():
                return detected
        except Exception:  # noqa: BLE001
            pass
        return find_standard_recording_directory()

    def add_sources(self, sources: list[Path]) -> None:
        existing = {entry.source.resolve() for entry in self.entries}
        pending = [source for source in sources if source.resolve() not in existing]
        if not pending:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        failures: list[str] = []
        try:
            runner = FfmpegRunner(self.ffmpeg_edit.text())
            runner.validate()
            database = OutplayedDatabase(Path(self.database_edit.text()))
            for index, source in enumerate(pending, start=1):
                self.status_label.setText(f"Reading {index}/{len(pending)}: {source.name}")
                QApplication.processEvents()
                try:
                    media_info = runner.probe(source)
                    record = database.find_media(source)
                    record.duration_ms = media_info.duration_seconds * 1000.0
                    for event in record.events:
                        if event.local_time_ms is not None and event.local_time_ms > record.duration_ms + 250:
                            event.local_time_ms = None
                            event.resolved = False
                            event.selected = False
                    self.entries.append(VideoEntry(source, record, media_info))
                except Exception as exc:  # noqa: BLE001
                    failures.append(f"{source.name}: {exc}")
            self.refresh_queue()
            if self.entries and self.active_index is None:
                self.activate_entry(0)
            if self.entries and not self.output_edit.text():
                self.output_edit.setText(str(self.entries[0].source.parent / "Highlights"))
            self.status_label.setText(f"{len(self.entries)} video(s) in queue")
        finally:
            QApplication.restoreOverrideCursor()
        if failures:
            QMessageBox.warning(self, "Some videos could not be loaded", "\n\n".join(failures))

    def refresh_queue(self) -> None:
        self.queue_table.blockSignals(True)
        self.queue_table.setRowCount(len(self.entries))
        for row, entry in enumerate(self.entries):
            use = QTableWidgetItem()
            use.setFlags(use.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            use.setCheckState(Qt.CheckState.Checked if entry.enabled else Qt.CheckState.Unchecked)
            self.queue_table.setItem(row, 0, use)
            self.queue_table.setItem(row, 1, QTableWidgetItem(entry.source.name))
            self.queue_table.item(row, 1).setToolTip(str(entry.source))
            selected = sum(1 for event in entry.record.events if event.selected and event.resolved)
            self.queue_table.setItem(row, 2, QTableWidgetItem(f"{selected} / {len(entry.record.events)}"))
            self.queue_table.setItem(row, 3, QTableWidgetItem(format_seconds(entry.media_info.duration_seconds)))
        self.queue_table.blockSignals(False)
        if self.active_index is not None and self.active_index < len(self.entries):
            self.queue_table.selectRow(self.active_index)

    def queue_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 0 and item.row() < len(self.entries):
            self.entries[item.row()].enabled = item.checkState() == Qt.CheckState.Checked

    def remove_selected_sources(self) -> None:
        rows = sorted({index.row() for index in self.queue_table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        for row in rows:
            self.entries.pop(row)
        self.active_index = None
        self.player.setSource(QUrl())
        self.source_edit.clear()
        self.event_table.setRowCount(0)
        self.type_table.setRowCount(0)
        self.timeline.set_markers([], 0)
        self.refresh_queue()
        if self.entries:
            self.activate_entry(min(rows[-1], len(self.entries) - 1))

    def activate_selected_source(self) -> None:
        rows = self.queue_table.selectionModel().selectedRows()
        if rows:
            self.activate_entry(rows[0].row())

    def activate_entry(self, index: int) -> None:
        if index < 0 or index >= len(self.entries):
            return
        self.active_index = index
        entry = self.entries[index]
        self.source_edit.setText(str(entry.source))
        self.player.setSource(QUrl.fromLocalFile(str(entry.source)))
        self.populate_events()
        self.queue_table.selectRow(index)
        self.status_label.setText(f"Editing {entry.source.name}")

    def select_database(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Select Outplayed IndexedDB", self.database_edit.text())
        if directory:
            self.database_edit.setText(directory)
            self.settings.setValue("database", directory)

    def select_ffmpeg(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Select FFmpeg", self.ffmpeg_edit.text(), "FFmpeg (ffmpeg.exe)")
        if filename:
            self.ffmpeg_edit.setText(filename)
            self.settings.setValue("ffmpeg", filename)

    def select_output(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Select output folder", self.output_edit.text())
        if directory:
            self.output_edit.setText(directory)

    def open_advanced_options(self) -> None:
        dialog = AdvancedOptionsDialog(self.render_options, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.render_options = dialog.options()
            self._save_render_settings()
            self.update_advanced_summary()

    def update_advanced_summary(self) -> None:
        overlay = "marker overlay on" if self.render_options.show_markers else "marker overlay off"
        fps = f", {self.render_options.fps} FPS" if self.render_options.fps else ""
        self.advanced_summary.setText(
            f"{self.render_options.encoder}, {self.render_options.resolution}{fps}, Q{self.render_options.quality}, {overlay}"
        )

    def populate_events(self) -> None:
        record = self.media_record
        if not record:
            return
        events = record.events
        self.event_table.blockSignals(True)
        self.event_table.setRowCount(len(events))
        for row, event in enumerate(events):
            use = QTableWidgetItem()
            use.setFlags(use.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            use.setCheckState(Qt.CheckState.Checked if event.selected else Qt.CheckState.Unchecked)
            self.event_table.setItem(row, 0, use)
            self.event_table.setItem(row, 1, QTableWidgetItem(event.type))
            self.event_table.setItem(row, 2, QTableWidgetItem(format_seconds(event.local_seconds)))
            before = QDoubleSpinBox()
            before.setRange(0, 120)
            before.setValue(event.before_ms / 1000.0)
            before.setSuffix(" s")
            before.valueChanged.connect(lambda value, item_index=row: self.set_event_padding(item_index, before=value))
            self.event_table.setCellWidget(row, 3, before)
            after = QDoubleSpinBox()
            after.setRange(0, 120)
            after.setValue(event.after_ms / 1000.0)
            after.setSuffix(" s")
            after.valueChanged.connect(lambda value, item_index=row: self.set_event_padding(item_index, after=value))
            self.event_table.setCellWidget(row, 4, after)
            self.event_table.setItem(row, 5, QTableWidgetItem("OK" if event.resolved else "Unresolved time"))
        self.event_table.blockSignals(False)
        self._populate_type_filters()
        self.timeline.set_markers(events, round(record.duration_ms))

    def _populate_type_filters(self) -> None:
        record = self.media_record
        if not record:
            return
        event_types = sorted({event.type for event in record.events})
        self.type_table.setRowCount(len(event_types))
        for row, event_type in enumerate(event_types):
            enabled = QCheckBox()
            enabled.setChecked(any(event.selected for event in record.events if event.type == event_type))
            enabled.toggled.connect(lambda checked, kind=event_type: self.set_type_enabled(kind, checked))
            self.type_table.setCellWidget(row, 0, enabled)
            self.type_table.setItem(row, 1, QTableWidgetItem(event_type))
            sample = next(event for event in record.events if event.type == event_type)
            before = QDoubleSpinBox()
            before.setRange(0, 120)
            before.setValue(sample.before_ms / 1000.0)
            before.setSuffix(" s")
            before.valueChanged.connect(lambda value, kind=event_type: self.set_type_padding(kind, before=value))
            self.type_table.setCellWidget(row, 2, before)
            after = QDoubleSpinBox()
            after.setRange(0, 120)
            after.setValue(sample.after_ms / 1000.0)
            after.setSuffix(" s")
            after.valueChanged.connect(lambda value, kind=event_type: self.set_type_padding(kind, after=value))
            self.type_table.setCellWidget(row, 3, after)

    def set_event_padding(self, index: int, before: float | None = None, after: float | None = None) -> None:
        record = self.media_record
        if not record:
            return
        event = record.events[index]
        if before is not None:
            event.before_ms = round(before * 1000)
        if after is not None:
            event.after_ms = round(after * 1000)

    def set_type_padding(self, event_type: str, before: float | None = None, after: float | None = None) -> None:
        record = self.media_record
        if not record:
            return
        for row, event in enumerate(record.events):
            if event.type != event_type:
                continue
            event.before_ms = round(before * 1000) if before is not None else event.before_ms
            event.after_ms = round(after * 1000) if after is not None else event.after_ms
            widget = self.event_table.cellWidget(row, 3 if before is not None else 4)
            if isinstance(widget, QDoubleSpinBox):
                widget.blockSignals(True)
                widget.setValue((event.before_ms if before is not None else event.after_ms) / 1000.0)
                widget.blockSignals(False)

    def set_type_enabled(self, event_type: str, enabled: bool) -> None:
        record = self.media_record
        if not record:
            return
        self.event_table.blockSignals(True)
        for row, event in enumerate(record.events):
            if event.type == event_type and event.resolved:
                event.selected = enabled
                self.event_table.item(row, 0).setCheckState(
                    Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked
                )
        self.event_table.blockSignals(False)
        self.timeline.update()
        self.refresh_queue()

    def apply_global_padding(self) -> None:
        record = self.media_record
        if not record:
            return
        before, after = self.global_before.value(), self.global_after.value()
        for row, event in enumerate(record.events):
            event.before_ms, event.after_ms = round(before * 1000), round(after * 1000)
            for column, value in ((3, before), (4, after)):
                widget = self.event_table.cellWidget(row, column)
                if isinstance(widget, QDoubleSpinBox):
                    widget.blockSignals(True)
                    widget.setValue(value)
                    widget.blockSignals(False)
        self._populate_type_filters()

    def event_item_changed(self, item: QTableWidgetItem) -> None:
        record = self.media_record
        if item.column() != 0 or not record:
            return
        event = record.events[item.row()]
        event.selected = event.resolved and item.checkState() == Qt.CheckState.Checked
        self.timeline.update()
        self.refresh_queue()

    def toggle_playback(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def position_changed(self, position: int) -> None:
        if not self.timeline.isSliderDown():
            self.timeline.setValue(position)
        self.time_label.setText(
            f"{format_seconds(position / 1000.0)} / {format_seconds(self.player.duration() / 1000.0)}"
        )

    def duration_changed(self, duration: int) -> None:
        self.timeline.setRange(0, duration)
        if self.media_record:
            self.timeline.set_markers(self.media_record.events, duration)

    def jump_to_row(self, row: int, _column: int = 0) -> None:
        record = self.media_record
        if record and record.events[row].local_time_ms is not None:
            self.player.setPosition(round(record.events[row].local_time_ms or 0))

    def jump_event(self, direction: int) -> None:
        record = self.media_record
        if not record:
            return
        resolved = [event for event in record.events if event.local_time_ms is not None]
        current = self.player.position()
        if direction > 0:
            target = next((event for event in resolved if (event.local_time_ms or 0) > current + 50), resolved[0] if resolved else None)
        else:
            target = next((event for event in reversed(resolved) if (event.local_time_ms or 0) < current - 50), resolved[-1] if resolved else None)
        if target and target.local_time_ms is not None:
            self.player.setPosition(round(target.local_time_ms))

    def export_sources(self) -> list[ExportSource]:
        result: list[ExportSource] = []
        for entry in self.entries:
            if not entry.enabled:
                continue
            cuts = build_cut_ranges(entry.record.events, entry.media_info.duration_seconds)
            if cuts:
                result.append(ExportSource(entry.source, cuts, entry.media_info))
        return result

    def start_export(self) -> None:
        sources = self.export_sources()
        if not sources:
            QMessageBox.warning(self, "Nothing to export", "Add videos and select at least one resolved marker.")
            return
        if self.render_options.show_markers and not self.render_options.font_path.exists():
            QMessageBox.warning(self, "Font not found", f"Marker font does not exist:\n{self.render_options.font_path}")
            return
        output_dir = Path(self.output_edit.text())
        if not str(output_dir):
            QMessageBox.warning(self, "Output missing", "Select an output folder.")
            return
        runner = FfmpegRunner(self.ffmpeg_edit.text())
        self.thread = QThread(self)
        self.worker = ExportWorker(
            runner,
            sources,
            output_dir,
            str(self.mode_combo.currentData()),
            str(self.transition_combo.currentData()),
            self.transition_duration.value(),
            self.render_options,
        )
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.export_progress)
        self.worker.finished.connect(self.export_finished)
        self.worker.failed.connect(self.export_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self.thread.deleteLater)
        self.export_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setValue(0)
        self.status_label.setText("Exporting queue...")
        self.thread.start()

    @Slot(int, str)
    def export_progress(self, value: int, message: str) -> None:
        self.progress.setValue(value)
        if message.startswith("Encoder:"):
            self.status_label.setText(message)

    @Slot(list)
    def export_finished(self, outputs: list[str]) -> None:
        self.progress.setValue(100)
        self.status_label.setText(f"Created {len(outputs)} file(s)")
        self._reset_export_controls()
        QMessageBox.information(self, "Export complete", "\n".join(outputs))

    @Slot(str)
    def export_failed(self, error: str) -> None:
        self.status_label.setText("Export failed")
        self._reset_export_controls()
        QMessageBox.critical(self, "Export failed", error)

    def _reset_export_controls(self) -> None:
        self.export_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.worker = None
        self.thread = None

    def cancel_export(self) -> None:
        if self.worker:
            self.status_label.setText("Cancelling...")
            self.worker.cancel()

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self.worker:
            self.worker.cancel()
        self.settings.setValue("database", self.database_edit.text())
        self.settings.setValue("ffmpeg", self.ffmpeg_edit.text())
        self._save_render_settings()
        super().closeEvent(event)
