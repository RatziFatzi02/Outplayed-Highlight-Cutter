from __future__ import annotations

import logging
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QColor, QPainter, QPen
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton, QSlider,
    QPlainTextEdit, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget,
)

from .ffmpeg import (
    ExportSource, FfmpegRunner, MediaInfo, ProgressUpdate, RenderOptions,
    default_ffmpeg_path,
)
from .models import Event, MediaRecord, build_cut_ranges, normalize_path
from .naming import FilenameContext, FilenameTemplates, render_filename
from .outplayed_db import OutplayedDatabase, default_database_path, find_standard_recording_directory
from .profiles import RenderProfile, decode_profiles, encode_profiles
from .queue_utils import collect_video_files, move_item, stable_sort, without_duplicate_paths
from .padding_defaults import (
    TypePadding, apply_padding_defaults, collect_type_paddings,
    decode_padding_defaults, encode_padding_defaults,
)


LOGGER = logging.getLogger("outplayed_highlight_cutter.gui")
EVENT_COLORS = {
    "kill": QColor("#ef5350"), "assist": QColor("#42a5f5"),
    "death": QColor("#9e9e9e"), "headshot": QColor("#ffca28"),
    "elimination": QColor("#ab47bc"),
}


@dataclass
class VideoEntry:
    source: Path
    record: MediaRecord
    media_info: MediaInfo
    enabled: bool = True
    recording_time: datetime | None = None

    @property
    def game(self) -> str:
        parent = self.source.parent.name.strip()
        return parent or (f"Game {self.record.game_id}" if self.record.game_id else "Unknown")


def format_seconds(seconds: float | None) -> str:
    if seconds is None:
        return "unresolved"
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(int(minutes), 60)
    return f"{hours}:{minutes:02d}:{secs:06.3f}" if hours else f"{minutes:02d}:{secs:06.3f}"


def format_eta(seconds: float | None) -> str:
    if seconds is None:
        return "ETA calculating..."
    seconds = max(0, round(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"ETA {hours:d}:{minutes:02d}:{secs:02d}" if hours else f"ETA {minutes:02d}:{secs:02d}"


class MarkerSlider(QSlider):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.events: list[Event] = []
        self.duration_ms = 0
        self.setMinimumHeight(28)

    def set_markers(self, events: list[Event], duration_ms: int) -> None:
        self.events, self.duration_ms = events, duration_ms
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
            painter.setPen(QPen(EVENT_COLORS.get(marker.type, QColor("#26a69a")), 2 if marker.selected else 1))
            painter.drawLine(int(x), 2, int(x), 10 if marker.selected else 7)


class ImportFailuresDialog(QDialog):
    def __init__(self, failures: list[str], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Some videos could not be loaded")
        self.setModal(True)
        self.setFixedSize(760, 480)
        layout = QVBoxLayout(self)
        summary = QLabel(
            f"{len(failures)} video(s) were skipped because no matching Outplayed data "
            "could be loaded. Successfully matched videos remain in the queue."
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)
        details = QPlainTextEdit()
        details.setReadOnly(True)
        details.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        details.setPlainText("\n\n".join(failures))
        details.moveCursor(details.textCursor().MoveOperation.Start)
        layout.addWidget(details, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class QueueTable(QTableWidget):
    move_requested = Signal(int, int)

    def dropEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        rows = self.selectionModel().selectedRows()
        if not rows:
            event.ignore()
            return
        source = rows[0].row()
        destination = self.indexAt(event.position().toPoint()).row()
        if destination < 0:
            destination = self.rowCount() - 1
        self.move_requested.emit(source, destination)
        event.acceptProposedAction()


class AdvancedOptionsDialog(QDialog):
    def __init__(
        self, options: RenderOptions, profiles: list[RenderProfile], default_profile: str,
        templates: FilenameTemplates, destination_mode: str, fixed_output: str,
        database_path: str, ffmpeg_path: str, transition: str, transition_seconds: float,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Advanced Options")
        self.setMinimumWidth(650)
        self.profiles = [RenderProfile.from_dict(profile.to_dict()) for profile in profiles]
        self.default_profile = default_profile
        layout = QVBoxLayout(self)

        profile_row = QHBoxLayout()
        self.profile_combo = QComboBox()
        self.profile_combo.addItems([profile.name for profile in self.profiles])
        self.profile_combo.currentTextChanged.connect(self.load_profile)
        save_as = QPushButton("Save as new...")
        save_as.clicked.connect(self.save_profile_as)
        update = QPushButton("Update profile")
        update.clicked.connect(self.update_profile)
        rename = QPushButton("Rename...")
        rename.clicked.connect(self.rename_profile)
        delete = QPushButton("Delete")
        delete.clicked.connect(self.delete_profile)
        set_default = QPushButton("Set as default")
        set_default.clicked.connect(self.set_default_profile)
        profile_row.addWidget(QLabel("Render profile"))
        profile_row.addWidget(self.profile_combo, 1)
        profile_row.addWidget(save_as)
        profile_row.addWidget(update)
        profile_row.addWidget(rename)
        profile_row.addWidget(delete)
        profile_row.addWidget(set_default)
        layout.addLayout(profile_row)
        self.default_label = QLabel()
        layout.addWidget(self.default_label)

        tabs = QTabWidget()
        layout.addWidget(tabs)
        render_page = QWidget()
        render_form = QFormLayout(render_page)
        self.encoder = QComboBox()
        for label, value in (
            ("Automatic H.264 (GPU, CPU fallback)", "auto"), ("NVIDIA NVENC", "h264_nvenc"),
            ("AMD AMF H.264", "h264_amf"), ("AMD AMF H.265 / HEVC", "hevc_amf"),
            ("AMD AMF AV1", "av1_amf"), ("CPU libx264", "libx264"),
        ):
            self.encoder.addItem(label, value)
        self.quality = QSpinBox(); self.quality.setRange(0, 51)
        self.preset = QComboBox()
        for label, value in (("Fast", "fast"), ("Balanced", "balanced"), ("Quality", "quality")):
            self.preset.addItem(label, value)
        self.resolution = QComboBox()
        for label, value in (("Source / first video", "source"), ("1280x720", "720p"), ("1920x1080", "1080p"), ("2560x1440", "1440p"), ("3840x2160", "2160p")):
            self.resolution.addItem(label, value)
        self.fps = QComboBox()
        for label, value in (("Source", 0), ("30 FPS", 30), ("60 FPS", 60)):
            self.fps.addItem(label, value)
        self.audio_bitrate = QComboBox()
        for bitrate in (128, 192, 256, 320):
            self.audio_bitrate.addItem(f"{bitrate} kbit/s", bitrate)
        self.transition = QComboBox()
        for label, value in (("Hard cuts", "hard"), ("Crossfade", "crossfade"), ("Dip to black", "dip-black")):
            self.transition.addItem(label, value)
        self.transition_seconds = QDoubleSpinBox(); self.transition_seconds.setRange(0.05, 3); self.transition_seconds.setSuffix(" s")
        for label, widget in (("Encoder", self.encoder), ("Quality", self.quality), ("Preset", self.preset), ("Resolution", self.resolution), ("Frame rate", self.fps), ("Audio bitrate", self.audio_bitrate), ("Transition", self.transition), ("Transition duration", self.transition_seconds)):
            render_form.addRow(label, widget)
        tabs.addTab(render_page, "Rendering")

        marker_page = QWidget(); marker_form = QFormLayout(marker_page)
        self.show_markers = QCheckBox("Show event type around each marker")
        self.marker_duration = QDoubleSpinBox(); self.marker_duration.setRange(0.2, 10); self.marker_duration.setSuffix(" s")
        self.marker_font_size = QSpinBox(); self.marker_font_size.setRange(12, 120)
        self.marker_position = QComboBox()
        for label, value in (("Top", "top"), ("Center", "center"), ("Bottom", "bottom")):
            self.marker_position.addItem(label, value)
        self.marker_prefix = QLineEdit()
        self.marker_box_opacity = QDoubleSpinBox(); self.marker_box_opacity.setRange(0, 1); self.marker_box_opacity.setSingleStep(0.05)
        self.font_path = QLineEdit()
        font_button = QPushButton("Browse..."); font_button.clicked.connect(self.select_font)
        font_row = QWidget(); font_layout = QHBoxLayout(font_row); font_layout.setContentsMargins(0, 0, 0, 0); font_layout.addWidget(self.font_path); font_layout.addWidget(font_button)
        marker_form.addRow(self.show_markers)
        for label, widget in (("Display duration", self.marker_duration), ("Font size", self.marker_font_size), ("Position", self.marker_position), ("Text prefix", self.marker_prefix), ("Background opacity", self.marker_box_opacity), ("Font", font_row)):
            marker_form.addRow(label, widget)
        tabs.addTab(marker_page, "Marker Overlay")

        general_page = QWidget(); general_form = QFormLayout(general_page)
        self.destination_mode = QComboBox(); self.destination_mode.addItem("Highlights next to each source", "source"); self.destination_mode.addItem("Fixed output folder", "fixed")
        self.fixed_output = QLineEdit(fixed_output)
        fixed_button = QPushButton("Browse..."); fixed_button.clicked.connect(self.select_fixed_output)
        fixed_row = QWidget(); fixed_layout = QHBoxLayout(fixed_row); fixed_layout.setContentsMargins(0, 0, 0, 0); fixed_layout.addWidget(self.fixed_output); fixed_layout.addWidget(fixed_button)
        self.individual_template = QLineEdit(templates.individual)
        self.per_video_template = QLineEdit(templates.per_video)
        self.combined_template = QLineEdit(templates.combined)
        self.template_preview = QLabel(); self.template_preview.setWordWrap(True)
        self.destination_mode.setCurrentIndex(max(0, self.destination_mode.findData(destination_mode)))
        for edit in (self.individual_template, self.per_video_template, self.combined_template):
            edit.textChanged.connect(self.update_template_preview)
        general_form.addRow("Default output", self.destination_mode)
        general_form.addRow("Fixed folder", fixed_row)
        general_form.addRow("Individual clips", self.individual_template)
        general_form.addRow("Per-video highlight", self.per_video_template)
        general_form.addRow("Combined highlight", self.combined_template)
        general_form.addRow("Preview", self.template_preview)
        general_form.addRow(QLabel("Placeholders: {source}, {game}, {recording_date}, {recording_time}, {export_date}, {export_time}, {index}, {events}, {mode}, {codec}"))
        tabs.addTab(general_page, "General")

        expert_page = QWidget(); expert_form = QFormLayout(expert_page)
        self.database_path = QLineEdit(database_path); database_button = QPushButton("Browse..."); database_button.clicked.connect(self.select_database)
        database_row = QWidget(); database_layout = QHBoxLayout(database_row); database_layout.setContentsMargins(0, 0, 0, 0); database_layout.addWidget(self.database_path); database_layout.addWidget(database_button)
        self.ffmpeg_path = QLineEdit(ffmpeg_path); ffmpeg_button = QPushButton("Browse..."); ffmpeg_button.clicked.connect(self.select_ffmpeg)
        ffmpeg_row = QWidget(); ffmpeg_layout = QHBoxLayout(ffmpeg_row); ffmpeg_layout.setContentsMargins(0, 0, 0, 0); ffmpeg_layout.addWidget(self.ffmpeg_path); ffmpeg_layout.addWidget(ffmpeg_button)
        expert_form.addRow("Outplayed database", database_row); expert_form.addRow("FFmpeg", ffmpeg_row)
        tabs.addTab(expert_page, "Expert")

        self.apply_options(options, transition, transition_seconds)
        self.profile_combo.blockSignals(True)
        self.profile_combo.setCurrentText(default_profile)
        self.profile_combo.blockSignals(False)
        self.refresh_default_label(); self.update_template_preview()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.validate_and_accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _select(self, combo: QComboBox, value: object) -> None:
        combo.setCurrentIndex(max(0, combo.findData(value)))

    def apply_options(self, options: RenderOptions, transition: str, transition_seconds: float) -> None:
        self._select(self.encoder, options.encoder); self.quality.setValue(options.quality)
        self._select(self.preset, options.preset); self._select(self.resolution, options.resolution)
        self._select(self.fps, options.fps); self._select(self.audio_bitrate, options.audio_bitrate)
        self._select(self.transition, transition); self.transition_seconds.setValue(transition_seconds)
        self.show_markers.setChecked(options.show_markers); self.marker_duration.setValue(options.marker_duration)
        self.marker_font_size.setValue(options.marker_font_size); self._select(self.marker_position, options.marker_position)
        self.marker_prefix.setText(options.marker_prefix); self.marker_box_opacity.setValue(options.marker_box_opacity)
        self.font_path.setText(str(options.font_path))

    def options(self) -> RenderOptions:
        return RenderOptions(
            encoder=str(self.encoder.currentData()), quality=self.quality.value(), preset=str(self.preset.currentData()),
            resolution=str(self.resolution.currentData()), fps=int(self.fps.currentData()), audio_bitrate=int(self.audio_bitrate.currentData()),
            show_markers=self.show_markers.isChecked(), marker_duration=self.marker_duration.value(), marker_font_size=self.marker_font_size.value(),
            marker_position=str(self.marker_position.currentData()), marker_prefix=self.marker_prefix.text(), marker_box_opacity=self.marker_box_opacity.value(),
            font_path=Path(self.font_path.text()),
        )

    def templates(self) -> FilenameTemplates:
        return FilenameTemplates(self.individual_template.text(), self.per_video_template.text(), self.combined_template.text())

    def current_profile(self) -> RenderProfile:
        return RenderProfile(self.profile_combo.currentText() or "Profile", self.options(), str(self.transition.currentData()), self.transition_seconds.value())

    def load_profile(self, name: str) -> None:
        profile = next((item for item in self.profiles if item.name == name), None)
        if profile:
            self.apply_options(profile.options, profile.transition, profile.transition_seconds)

    def save_profile_as(self) -> None:
        name, ok = QInputDialog.getText(self, "Save render profile", "Profile name")
        name = name.strip()
        if not ok or not name:
            return
        if any(profile.name.casefold() == name.casefold() for profile in self.profiles):
            QMessageBox.warning(self, "Profile exists", "Choose a unique profile name.")
            return
        profile = self.current_profile(); profile.name = name; self.profiles.append(profile)
        self.profile_combo.addItem(name); self.profile_combo.setCurrentText(name)

    def update_profile(self) -> None:
        name = self.profile_combo.currentText()
        for index, profile in enumerate(self.profiles):
            if profile.name == name:
                self.profiles[index] = self.current_profile(); return

    def delete_profile(self) -> None:
        if len(self.profiles) <= 1:
            QMessageBox.warning(self, "Profile required", "At least one render profile must remain.")
            return
        name = self.profile_combo.currentText(); self.profiles = [profile for profile in self.profiles if profile.name != name]
        self.profile_combo.removeItem(self.profile_combo.currentIndex())
        if self.default_profile == name:
            self.default_profile = self.profiles[0].name
        self.refresh_default_label()

    def rename_profile(self) -> None:
        old_name = self.profile_combo.currentText()
        name, ok = QInputDialog.getText(self, "Rename render profile", "Profile name", text=old_name)
        name = name.strip()
        if not ok or not name or name == old_name:
            return
        if any(profile.name.casefold() == name.casefold() for profile in self.profiles):
            QMessageBox.warning(self, "Profile exists", "Choose a unique profile name.")
            return
        for profile in self.profiles:
            if profile.name == old_name:
                profile.name = name
                break
        if self.default_profile == old_name:
            self.default_profile = name
        index = self.profile_combo.currentIndex()
        self.profile_combo.setItemText(index, name)
        self.refresh_default_label()

    def set_default_profile(self) -> None:
        self.update_profile(); self.default_profile = self.profile_combo.currentText(); self.refresh_default_label()

    def refresh_default_label(self) -> None:
        self.default_label.setText(f"Default profile: {self.default_profile}")

    def update_template_preview(self) -> None:
        context = FilenameContext(source="Example Match", game="Valorant", recording_time=datetime(2026, 6, 11, 20, 30), export_time=datetime(2026, 6, 11, 21, 0), index=3, events="kill-assist", mode="individual", codec="h264_amf")
        try:
            names = [render_filename(edit.text(), context) for edit in (self.individual_template, self.per_video_template, self.combined_template)]
            self.template_preview.setText(" | ".join(names)); self.template_preview.setStyleSheet("")
        except ValueError as exc:
            self.template_preview.setText(str(exc)); self.template_preview.setStyleSheet("color: #ef5350")

    def validate_and_accept(self) -> None:
        try:
            context = FilenameContext()
            for template in (self.individual_template.text(), self.per_video_template.text(), self.combined_template.text()):
                render_filename(template, context)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid filename template", str(exc)); return
        if self.destination_mode.currentData() == "fixed" and not self.fixed_output.text().strip():
            QMessageBox.warning(self, "Output folder missing", "Choose a fixed output folder."); return
        self.update_profile(); self.accept()

    def select_font(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Select font", self.font_path.text(), "Fonts (*.ttf *.otf)")
        if filename: self.font_path.setText(filename)

    def select_fixed_output(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Select default output folder", self.fixed_output.text())
        if directory: self.fixed_output.setText(directory)

    def select_database(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Select Outplayed IndexedDB", self.database_path.text())
        if directory: self.database_path.setText(directory)

    def select_ffmpeg(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Select FFmpeg", self.ffmpeg_path.text(), "FFmpeg (ffmpeg.exe)")
        if filename: self.ffmpeg_path.setText(filename)


class ExportWorker(QObject):
    progress = Signal(int, str, str)
    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, runner: FfmpegRunner, sources: list[ExportSource], output_dir: Path, mode: str, transition: str, transition_seconds: float, options: RenderOptions, templates: FilenameTemplates):
        super().__init__(); self.runner = runner; self.sources = sources; self.output_dir = output_dir; self.mode = mode
        self.transition = transition; self.transition_seconds = transition_seconds; self.options = options; self.templates = templates

    @Slot()
    def run(self) -> None:
        started = time.monotonic()
        try:
            LOGGER.info("Export started: mode=%s sources=%d output=%s", self.mode, len(self.sources), self.output_dir)
            encoder = self.runner.detect_encoder(self.options.encoder)
            LOGGER.info("Selected encoder: %s", encoder)
            self.progress.emit(0, f"Encoder: {encoder}", "ETA calculating...")
            total = sum(cut.duration_seconds for source in self.sources for cut in source.cuts)
            outputs: list[Path] = []; exported_at = datetime.now()

            def emit_total(ratio: float, update: ProgressUpdate) -> None:
                elapsed = max(0.001, time.monotonic() - started)
                eta = elapsed * (1 - ratio) / ratio if ratio > 0.001 else None
                elapsed_text = format_seconds(elapsed).split(".")[0]
                current = f"Current {update.ratio * 100:.0f}% | {elapsed_text} elapsed | {update.speed:.2f}x | {format_eta(update.eta_seconds)}"
                finish = (datetime.now() + timedelta(seconds=eta)).strftime("%H:%M:%S") if eta is not None else "calculating"
                self.progress.emit(round(ratio * 100), current, f"Total {format_eta(eta)} | finish {finish}")

            if self.mode == "combined":
                def direct(update: ProgressUpdate) -> None: emit_total(update.ratio, update)
                outputs = self.runner.export_combined(self.sources, self.output_dir, encoder, self.transition, self.transition_seconds, direct, self.options, self.templates.combined, exported_at)
            else:
                completed = 0.0
                for source in self.sources:
                    source_duration = sum(cut.duration_seconds for cut in source.cuts)
                    def scaled(update: ProgressUpdate, base: float = completed, duration: float = source_duration) -> None:
                        emit_total((base + update.ratio * duration) / max(total, 0.001), update)
                    if self.mode == "individual":
                        created = self.runner.export_individual(source.source, source.cuts, self.output_dir, source.media, encoder, scaled, self.options, self.templates, source.game, source.recording_time, exported_at)
                    else:
                        created = self.runner.export_highlight(source.source, source.cuts, self.output_dir, source.media, encoder, self.transition, self.transition_seconds, scaled, self.options, self.templates.per_video, source.game, source.recording_time, exported_at)
                    outputs.extend(created); completed += source_duration
            LOGGER.info("Export finished: %s", ", ".join(str(path) for path in outputs))
            self.finished.emit([str(path) for path in outputs])
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Export failed"); self.failed.emit(str(exc))

    @Slot()
    def cancel(self) -> None:
        self.runner.cancel()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle("Outplayed Highlight Cutter"); self.resize(1480, 940)
        self.settings = QSettings("LocalTools", "OutplayedHighlightCutter")
        self.entries: list[VideoEntry] = []; self.active_index: int | None = None; self.event_filter: str | None = None
        self.all_videos_mode = True; self.all_scope_warning_accepted = False
        self.padding_defaults: dict[str, TypePadding] = {}
        self.render_options = RenderOptions(); self.templates = FilenameTemplates(); self.profiles: list[RenderProfile] = []
        self.default_profile = "Default"; self.destination_mode = "source"; self.fixed_output = ""
        self.database_path = str(default_database_path()); self.ffmpeg_path = str(default_ffmpeg_path())
        self.thread: QThread | None = None; self.worker: ExportWorker | None = None
        self.player = QMediaPlayer(self); self.audio_output = QAudioOutput(self); self.player.setAudioOutput(self.audio_output)
        self._restore_settings(); self._build_ui(); self._connect_player(); self.update_advanced_summary()

    @property
    def active_entry(self) -> VideoEntry | None:
        return self.entries[self.active_index] if self.active_index is not None and self.active_index < len(self.entries) else None

    @property
    def media_record(self) -> MediaRecord | None:
        return self.active_entry.record if self.active_entry else None

    def _build_ui(self) -> None:
        central = QWidget(); self.setCentralWidget(central); root = QVBoxLayout(central)
        output_group = QGroupBox("Output"); output_layout = QHBoxLayout(output_group)
        self.output_edit = QLineEdit(self.fixed_output if self.destination_mode == "fixed" else "")
        output_button = QPushButton("Choose output..."); output_button.clicked.connect(self.select_output)
        output_layout.addWidget(QLabel("Folder")); output_layout.addWidget(self.output_edit, 1); output_layout.addWidget(output_button)
        root.addWidget(output_group)

        queue_group = QGroupBox("Video queue"); queue_layout = QVBoxLayout(queue_group); queue_buttons = QHBoxLayout()
        folder_button = QPushButton("Add match folder..."); folder_button.clicked.connect(self.select_source_folder)
        folder_button.setToolTip("Import an Outplayed match or collection folder recursively.")
        folder_button.setStyleSheet("font-weight: 600; padding: 6px 12px")
        add_button = QPushButton("Add videos..."); add_button.clicked.connect(self.select_sources)
        add_button.setToolTip("Add one or more individual video files.")
        remove_button = QPushButton("Remove selected"); remove_button.clicked.connect(self.remove_selected_sources)
        open_button = QPushButton("Open in player"); open_button.clicked.connect(self.activate_selected_source)
        all_events_button = QPushButton("All videos event paddings"); all_events_button.clicked.connect(self.show_all_video_paddings)
        self.sort_combo = QComboBox()
        for label, value in (("Manual order", "manual"), ("Date: oldest first", "date-asc"), ("Date: newest first", "date-desc"), ("Filename", "name"), ("Duration", "duration"), ("Game", "game"), ("Marker count", "markers")):
            self.sort_combo.addItem(label, value)
        self.sort_combo.currentIndexChanged.connect(self.apply_queue_sort)
        for widget in (folder_button, add_button, remove_button, open_button, all_events_button): queue_buttons.addWidget(widget)
        queue_buttons.addStretch(); queue_buttons.addWidget(QLabel("Sort")); queue_buttons.addWidget(self.sort_combo); queue_layout.addLayout(queue_buttons)
        self.queue_table = QueueTable(0, 6); self.queue_table.setHorizontalHeaderLabels(["Use", "Video", "Date", "Game", "Markers", "Duration"])
        self.queue_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.queue_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.queue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.queue_table.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove); self.queue_table.setDragEnabled(True); self.queue_table.setAcceptDrops(True); self.queue_table.setDropIndicatorShown(True)
        self.queue_table.itemChanged.connect(self.queue_item_changed); self.queue_table.cellDoubleClicked.connect(lambda row, _column: self.activate_entry(row)); self.queue_table.move_requested.connect(self.move_queue_entry)
        self.queue_table.itemSelectionChanged.connect(self.update_preview_action)
        self.queue_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.queue_table.customContextMenuRequested.connect(self.show_queue_menu)
        self.queue_table.setMaximumHeight(210); queue_layout.addWidget(self.queue_table); root.addWidget(queue_group)

        splitter = QSplitter(Qt.Orientation.Horizontal); root.addWidget(splitter, 1)
        preview_panel = QWidget(); preview_layout = QVBoxLayout(preview_panel)
        self.active_title = QLabel("No video selected"); self.active_title.setStyleSheet("font-weight: 600; font-size: 14px"); preview_layout.addWidget(self.active_title)
        self.preview_hint = QLabel("Select a video to preview markers.")
        self.preview_hint.setWordWrap(True)
        self.preview_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_hint.setStyleSheet("color: #9aa0a6; padding: 10px; border: 1px dashed #555; border-radius: 6px")
        self.open_preview_button = QPushButton("Open first video")
        self.open_preview_button.clicked.connect(self.open_preview_video)
        preview_layout.addWidget(self.preview_hint)
        preview_layout.addWidget(self.open_preview_button)
        self.video_widget = QVideoWidget(); self.video_widget.setMinimumSize(560, 315); self.player.setVideoOutput(self.video_widget); preview_layout.addWidget(self.video_widget, 1)
        self.timeline = MarkerSlider(); self.timeline.sliderMoved.connect(self.player.setPosition); preview_layout.addWidget(self.timeline)
        controls = QHBoxLayout(); self.play_button = QPushButton("Play"); self.play_button.clicked.connect(self.toggle_playback)
        previous_button = QPushButton("Previous event"); previous_button.clicked.connect(lambda: self.jump_event(-1)); next_button = QPushButton("Next event"); next_button.clicked.connect(lambda: self.jump_event(1))
        self.time_label = QLabel("00:00.000 / 00:00.000")
        for widget in (self.play_button, previous_button, next_button): controls.addWidget(widget)
        controls.addStretch(); controls.addWidget(self.time_label); preview_layout.addLayout(controls); splitter.addWidget(preview_panel)

        events_panel = QWidget(); events_layout = QVBoxLayout(events_panel)
        scope_row = QHBoxLayout(); self.padding_scope_label = QLabel("All Videos Event Paddings (global defaults)"); self.padding_scope_label.setStyleSheet("font-weight: 600; font-size: 14px")
        self.save_padding_defaults_button = QPushButton("Save type paddings as defaults"); self.save_padding_defaults_button.clicked.connect(self.save_type_padding_defaults)
        scope_row.addWidget(self.padding_scope_label); scope_row.addStretch(); scope_row.addWidget(self.save_padding_defaults_button); events_layout.addLayout(scope_row)
        self.scope_warning = QLabel("Global changes overwrite matching per-video and individual settings after confirmation.")
        self.scope_warning.setWordWrap(True); self.scope_warning.setStyleSheet("color: #d98c00"); events_layout.addWidget(self.scope_warning)
        events_layout.addWidget(QLabel("Event type selection and per-type padding"))
        self.type_table = QTableWidget(0, 4); self.type_table.setHorizontalHeaderLabels(["Enabled", "Type", "Before", "After"]); self.type_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch); self.type_table.setMaximumHeight(180); events_layout.addWidget(self.type_table)
        filter_row = QHBoxLayout(); self.filter_label = QLabel("Showing all events"); self.clear_filter_button = QPushButton("Clear filter"); self.clear_filter_button.clicked.connect(self.clear_event_filter); self.clear_filter_button.setEnabled(False); filter_row.addWidget(self.filter_label); filter_row.addStretch(); filter_row.addWidget(self.clear_filter_button); events_layout.addLayout(filter_row)
        self.individual_padding_label = QLabel("Event Padding Settings (individual markers)"); self.individual_padding_label.setStyleSheet("font-weight: 600"); events_layout.addWidget(self.individual_padding_label)
        self.event_table = QTableWidget(0, 6); self.event_table.setHorizontalHeaderLabels(["Use", "Type", "Time", "Before", "After", "Status"]); self.event_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch); self.event_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.event_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.event_table.itemChanged.connect(self.event_item_changed); self.event_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.event_table.customContextMenuRequested.connect(self.show_event_menu); events_layout.addWidget(self.event_table, 1)
        splitter.addWidget(events_panel); splitter.setSizes([740, 700])

        export_group = QGroupBox("Export"); export_layout = QHBoxLayout(export_group)
        self.mode_combo = QComboBox(); self.mode_combo.addItem("One combined highlight from all videos", "combined"); self.mode_combo.addItem("One highlight per video", "per-video"); self.mode_combo.addItem("Individual event clips", "individual")
        advanced_button = QPushButton("Advanced Options..."); advanced_button.clicked.connect(self.open_advanced_options)
        self.advanced_summary = QLabel(); self.export_button = QPushButton("Export queue"); self.export_button.clicked.connect(self.start_export)
        self.cancel_button = QPushButton("Cancel"); self.cancel_button.setEnabled(False); self.cancel_button.clicked.connect(self.cancel_export)
        self.progress = QProgressBar(); self.progress.setMinimumWidth(190); self.status_label = QLabel("Ready"); self.eta_label = QLabel("")
        for widget in (self.mode_combo, advanced_button, self.advanced_summary, self.export_button, self.cancel_button, self.progress, self.status_label, self.eta_label): export_layout.addWidget(widget)
        export_layout.setStretch(6, 1); root.addWidget(export_group)
        self.output_edit.textChanged.connect(self.update_export_state)
        self.update_preview_action()
        self.update_export_state()

    def _connect_player(self) -> None:
        self.player.positionChanged.connect(self.position_changed); self.player.durationChanged.connect(self.duration_changed)
        self.player.playbackStateChanged.connect(lambda state: self.play_button.setText("Pause" if state == QMediaPlayer.PlaybackState.PlayingState else "Play"))

    def _legacy_options(self) -> RenderOptions:
        return RenderOptions(
            encoder=str(self.settings.value("render/encoder", "auto")), quality=int(self.settings.value("render/quality", 20)), preset=str(self.settings.value("render/preset", "balanced")), resolution=str(self.settings.value("render/resolution", "source")), fps=int(self.settings.value("render/fps", 0)), audio_bitrate=int(self.settings.value("render/audio_bitrate", 192)),
            show_markers=str(self.settings.value("marker/enabled", "false")).lower() == "true", marker_duration=float(self.settings.value("marker/duration", 1.5)), marker_font_size=int(self.settings.value("marker/font_size", 42)), marker_position=str(self.settings.value("marker/position", "top")), marker_prefix=str(self.settings.value("marker/prefix", "")), marker_box_opacity=float(self.settings.value("marker/box_opacity", 0.55)), font_path=Path(str(self.settings.value("marker/font_path", r"C:\Windows\Fonts\segoeuib.ttf"))),
        )

    def _restore_settings(self) -> None:
        self.database_path = str(self.settings.value("database", self.database_path)); self.ffmpeg_path = str(self.settings.value("ffmpeg", self.ffmpeg_path))
        self.templates = FilenameTemplates(str(self.settings.value("naming/individual", FilenameTemplates().individual)), str(self.settings.value("naming/per_video", FilenameTemplates().per_video)), str(self.settings.value("naming/combined", FilenameTemplates().combined)))
        self.destination_mode = str(self.settings.value("output/mode", "source")); self.fixed_output = str(self.settings.value("output/fixed", ""))
        try: self.padding_defaults = decode_padding_defaults(str(self.settings.value("event_padding/defaults", "")))
        except (ValueError, TypeError, json.JSONDecodeError): self.padding_defaults = {}
        try: self.profiles = decode_profiles(str(self.settings.value("profiles/data", "")))
        except (ValueError, TypeError): self.profiles = []
        if not self.profiles:
            name = "Previous settings" if self.settings.contains("render/encoder") else "Default"
            self.profiles = [RenderProfile(name, self._legacy_options())]; self.default_profile = name; self._save_profiles()
        self.default_profile = str(self.settings.value("profiles/default", self.profiles[0].name))
        selected = next((profile for profile in self.profiles if profile.name == self.default_profile), self.profiles[0])
        self.render_options = RenderProfile.from_dict(selected.to_dict()).options
        self.transition_value = selected.transition; self.transition_seconds_value = selected.transition_seconds

    def _save_profiles(self) -> None:
        self.settings.setValue("profiles/data", encode_profiles(self.profiles)); self.settings.setValue("profiles/default", self.default_profile)

    @Slot()
    def select_sources(self) -> None:
        start = self.source_dialog_directory(); filenames, _ = QFileDialog.getOpenFileNames(self, "Select Outplayed videos", str(start) if start else "", "Video files (*.mp4 *.mkv *.mov)")
        if filenames: self.settings.setValue("last_source_directory", str(Path(filenames[0]).parent)); self.add_sources([Path(name) for name in filenames])

    @Slot()
    def select_source_folder(self) -> None:
        start = self.source_dialog_directory(); directory = QFileDialog.getExistingDirectory(self, "Add match folder", str(start) if start else "")
        if not directory: return
        self.settings.setValue("last_source_directory", directory)
        files = collect_video_files(Path(directory), recursive=True)
        if not files: QMessageBox.information(self, "No videos found", "No supported video files were found in this folder or its subfolders."); return
        self.add_sources(files)

    def source_dialog_directory(self) -> Path | None:
        last = Path(str(self.settings.value("last_source_directory", "")))
        if str(last) not in {"", "."} and last.is_dir(): return last
        try:
            detected = OutplayedDatabase(Path(self.database_path)).find_recording_directory()
            if detected and detected.is_dir(): return detected
        except Exception: pass  # noqa: BLE001
        return find_standard_recording_directory()

    def add_sources(self, sources: list[Path]) -> None:
        pending = without_duplicate_paths(sources, (entry.source for entry in self.entries))
        if not pending: self.status_label.setText("All selected videos are already in the queue"); return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor); failures: list[str] = []
        try:
            runner = FfmpegRunner(self.ffmpeg_path); runner.validate(); database = OutplayedDatabase(Path(self.database_path))
            for index, source in enumerate(pending, 1):
                self.status_label.setText(f"Reading {index}/{len(pending)}: {source.name}"); QApplication.processEvents()
                try:
                    media = runner.probe(source); record = database.find_media(source); record.duration_ms = media.duration_seconds * 1000
                    apply_padding_defaults(record.events, self.padding_defaults)
                    for event in record.events:
                        if event.local_time_ms is not None and event.local_time_ms > record.duration_ms + 250: event.local_time_ms = None; event.resolved = False; event.selected = False
                    timestamp = record.recording_time or datetime.fromtimestamp(source.stat().st_mtime)
                    self.entries.append(VideoEntry(source, record, media, recording_time=timestamp))
                    LOGGER.info("Added source: %s (%d markers)", source, len(record.events))
                except Exception as exc: failures.append(f"{source.name}: {exc}"); LOGGER.exception("Could not add source %s", source)
            self.refresh_queue()
            self.show_all_video_paddings()
            self.apply_default_output(); self.status_label.setText(f"{len(self.entries)} video(s) in queue")
        finally: QApplication.restoreOverrideCursor()
        if failures: ImportFailuresDialog(failures, self).exec()

    def apply_default_output(self, force: bool = False) -> None:
        if self.destination_mode == "fixed" and self.fixed_output: self.output_edit.setText(self.fixed_output)
        elif self.entries and (force or not self.output_edit.text()): self.output_edit.setText(str(self.entries[0].source.parent / "Highlights"))

    def refresh_queue(self) -> None:
        active_path = normalize_path(self.active_entry.source) if self.active_entry else None
        self.queue_table.blockSignals(True); self.queue_table.setRowCount(len(self.entries))
        for row, entry in enumerate(self.entries):
            use = QTableWidgetItem(); use.setFlags(use.flags() | Qt.ItemFlag.ItemIsUserCheckable); use.setCheckState(Qt.CheckState.Checked if entry.enabled else Qt.CheckState.Unchecked); self.queue_table.setItem(row, 0, use)
            video = QTableWidgetItem(entry.source.name); video.setToolTip(str(entry.source)); self.queue_table.setItem(row, 1, video)
            self.queue_table.setItem(row, 2, QTableWidgetItem(entry.recording_time.strftime("%Y-%m-%d %H:%M") if entry.recording_time else "Unknown")); self.queue_table.setItem(row, 3, QTableWidgetItem(entry.game))
            selected = sum(1 for event in entry.record.events if event.selected and event.resolved); self.queue_table.setItem(row, 4, QTableWidgetItem(f"{selected} / {len(entry.record.events)}")); self.queue_table.setItem(row, 5, QTableWidgetItem(format_seconds(entry.media_info.duration_seconds)))
        self.queue_table.blockSignals(False)
        if active_path:
            self.active_index = next((i for i, entry in enumerate(self.entries) if normalize_path(entry.source) == active_path), None)
        if self.active_index is not None: self.queue_table.selectRow(self.active_index)
        self.update_preview_action()
        self.update_export_state()

    def apply_queue_sort(self) -> None:
        mode = str(self.sort_combo.currentData())
        if mode == "manual": return
        keys = {"date-asc": lambda e: e.recording_time or datetime.min, "date-desc": lambda e: e.recording_time or datetime.min, "name": lambda e: e.source.name.casefold(), "duration": lambda e: e.media_info.duration_seconds, "game": lambda e: e.game.casefold(), "markers": lambda e: len(e.record.events)}
        self.entries = stable_sort(self.entries, keys[mode], reverse=mode == "date-desc"); self.refresh_queue()

    @Slot(int, int)
    def move_queue_entry(self, source: int, destination: int) -> None:
        active_path = self.active_entry.source if self.active_entry else None; move_item(self.entries, source, destination); self.sort_combo.blockSignals(True); self.sort_combo.setCurrentIndex(0); self.sort_combo.blockSignals(False)
        self.active_index = next((i for i, entry in enumerate(self.entries) if active_path and normalize_path(entry.source) == normalize_path(active_path)), None); self.refresh_queue()

    def queue_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 0 and item.row() < len(self.entries): self.entries[item.row()].enabled = item.checkState() == Qt.CheckState.Checked
        self.update_export_state()

    def selected_queue_row(self) -> int | None:
        rows = self.queue_table.selectionModel().selectedRows(); return rows[0].row() if rows else None

    def show_queue_menu(self, position) -> None:  # type: ignore[no-untyped-def]
        row = self.queue_table.rowAt(position.y())
        if row < 0: return
        self.queue_table.selectRow(row); menu = QMenu(self)
        open_action = menu.addAction("Open in player"); toggle_action = menu.addAction("Exclude from export" if self.entries[row].enabled else "Include in export")
        menu.addSeparator(); up_action = menu.addAction("Move up"); down_action = menu.addAction("Move down"); remove_action = menu.addAction("Remove from queue")
        action = menu.exec(self.queue_table.viewport().mapToGlobal(position))
        if action == open_action: self.activate_entry(row)
        elif action == toggle_action: self.entries[row].enabled = not self.entries[row].enabled; self.refresh_queue()
        elif action == up_action: self.move_queue_entry(row, row - 1)
        elif action == down_action: self.move_queue_entry(row, row + 1)
        elif action == remove_action: self.remove_rows([row])

    def remove_rows(self, rows: list[int]) -> None:
        active_path = self.active_entry.source if self.active_entry else None
        for row in sorted(set(rows), reverse=True):
            if 0 <= row < len(self.entries): self.entries.pop(row)
        self.active_index = next((i for i, entry in enumerate(self.entries) if active_path and normalize_path(entry.source) == normalize_path(active_path)), None)
        if self.active_index is None: self.player.setSource(QUrl()); self.active_title.setText("All Videos Event Paddings"); self.event_table.setRowCount(0); self.type_table.setRowCount(0); self.timeline.set_markers([], 0)
        self.refresh_queue()
        if self.entries and self.active_index is None: self.show_all_video_paddings()
        self.update_export_state()

    def remove_selected_sources(self) -> None:
        self.remove_rows([index.row() for index in self.queue_table.selectionModel().selectedRows()])

    def activate_selected_source(self) -> None:
        row = self.selected_queue_row()
        if row is not None: self.activate_entry(row)

    def activate_entry(self, index: int) -> None:
        if not 0 <= index < len(self.entries): return
        self.all_videos_mode = False; self.all_scope_warning_accepted = False; self.active_index = index; entry = self.entries[index]; self.event_filter = None; self.active_title.setText(entry.source.name); self.active_title.setToolTip(str(entry.source)); self.player.setSource(QUrl.fromLocalFile(str(entry.source))); self.populate_events(); self.queue_table.selectRow(index); self.status_label.setText(f"Viewing {entry.source.name}")
        self.update_preview_action()
        self.update_export_state()

    def show_all_video_paddings(self) -> None:
        self.all_videos_mode = True; self.all_scope_warning_accepted = False; self.active_index = None; self.event_filter = None
        self.player.setSource(QUrl()); self.active_title.setText("All Videos Event Paddings"); self.active_title.setToolTip("")
        self.queue_table.clearSelection(); self.timeline.set_markers([], 0); self.event_table.setRowCount(0)
        self.populate_events(); self.status_label.setText("Editing event types across all loaded videos")
        self.update_preview_action()
        self.update_export_state()

    def update_preview_action(self) -> None:
        has_entries = bool(self.entries)
        selected = self.selected_queue_row()
        all_mode = self.all_videos_mode or self.active_index is None
        self.preview_hint.setVisible(all_mode)
        self.open_preview_button.setVisible(all_mode)
        self.open_preview_button.setEnabled(has_entries)
        if not has_entries:
            self.preview_hint.setText("Add a match folder or video to preview markers.")
            self.open_preview_button.setText("Open first video")
        elif selected is not None:
            self.preview_hint.setText("Select a video to preview markers, or open the selected queue item.")
            self.open_preview_button.setText("Open selected video")
        else:
            self.preview_hint.setText("Select a video to preview markers.")
            self.open_preview_button.setText("Open first video")

    def open_preview_video(self) -> None:
        if not self.entries:
            return
        row = self.selected_queue_row()
        self.activate_entry(row if row is not None else 0)

    def select_output(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Select output folder", self.output_edit.text())
        if directory: self.output_edit.setText(directory)
        self.update_export_state()

    def open_advanced_options(self) -> None:
        dialog = AdvancedOptionsDialog(self.render_options, self.profiles, self.default_profile, self.templates, self.destination_mode, self.fixed_output, self.database_path, self.ffmpeg_path, self.transition_value, self.transition_seconds_value, self)
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        self.render_options = dialog.options(); self.profiles = dialog.profiles; self.default_profile = dialog.default_profile; self.templates = dialog.templates(); self.destination_mode = str(dialog.destination_mode.currentData()); self.fixed_output = dialog.fixed_output.text(); self.database_path = dialog.database_path.text(); self.ffmpeg_path = dialog.ffmpeg_path.text(); self.transition_value = str(dialog.transition.currentData()); self.transition_seconds_value = dialog.transition_seconds.value()
        self._save_all_settings(); self.apply_default_output(force=True); self.update_advanced_summary()

    def update_advanced_summary(self) -> None:
        overlay = "overlay on" if self.render_options.show_markers else "overlay off"; self.advanced_summary.setText(f"{self.render_options.encoder}, {self.render_options.resolution}, Q{self.render_options.quality}, {overlay}")

    def populate_events(self) -> None:
        if self.all_videos_mode:
            self.event_table.blockSignals(True); self.event_table.setRowCount(0); self.event_table.blockSignals(False)
            self._populate_type_filters(); self.padding_scope_label.setText("All Videos Event Paddings (global defaults)")
            self.scope_warning.show(); self.save_padding_defaults_button.show()
            self.individual_padding_label.setText("Event Padding Settings (select a video to edit individual markers)")
            self.individual_padding_label.setVisible(True); self.event_table.setVisible(False)
            self.filter_label.setText("Select a video to edit individual markers"); self.clear_filter_button.setEnabled(False)
            return
        record = self.media_record
        if not record: return
        visible = [(index, event) for index, event in enumerate(record.events) if self.event_filter is None or event.type == self.event_filter]
        self.event_table.blockSignals(True); self.event_table.setRowCount(len(visible))
        for row, (index, event) in enumerate(visible):
            use = QTableWidgetItem(); use.setData(Qt.ItemDataRole.UserRole, index); use.setFlags(use.flags() | Qt.ItemFlag.ItemIsUserCheckable); use.setCheckState(Qt.CheckState.Checked if event.selected else Qt.CheckState.Unchecked); self.event_table.setItem(row, 0, use)
            self.event_table.setItem(row, 1, QTableWidgetItem(event.type)); self.event_table.setItem(row, 2, QTableWidgetItem(format_seconds(event.local_seconds)))
            before = QDoubleSpinBox(); before.setRange(0, 120); before.setValue(event.before_ms / 1000); before.setSuffix(" s"); before.valueChanged.connect(lambda value, item_index=index: self.set_event_padding(item_index, before=value)); self.event_table.setCellWidget(row, 3, before)
            after = QDoubleSpinBox(); after.setRange(0, 120); after.setValue(event.after_ms / 1000); after.setSuffix(" s"); after.valueChanged.connect(lambda value, item_index=index: self.set_event_padding(item_index, after=value)); self.event_table.setCellWidget(row, 4, after)
            self.event_table.setItem(row, 5, QTableWidgetItem("OK" if event.resolved else "Unresolved time"))
        self.event_table.blockSignals(False); self._populate_type_filters(); self.timeline.set_markers(record.events, round(record.duration_ms)); self.filter_label.setText(f"Filtered by: {self.event_filter}" if self.event_filter else "Showing all events"); self.clear_filter_button.setEnabled(self.event_filter is not None)
        self.padding_scope_label.setText(f"Per Video Event Paddings: {self.active_entry.source.name if self.active_entry else ''}")
        self.scope_warning.hide(); self.save_padding_defaults_button.hide()
        self.individual_padding_label.setText("Event Padding Settings (individual markers)")
        self.individual_padding_label.setVisible(True); self.event_table.setVisible(True)

    def _populate_type_filters(self) -> None:
        events = [event for entry in self.entries for event in entry.record.events] if self.all_videos_mode else (self.media_record.events if self.media_record else [])
        event_types = sorted({event.type for event in events}); self.type_table.setRowCount(len(event_types))
        for row, event_type in enumerate(event_types):
            typed_events = [event for event in events if event.type == event_type]
            enabled = QCheckBox(); enabled.setChecked(any(event.selected for event in typed_events)); enabled.setToolTip("Some videos differ" if len({event.selected for event in typed_events}) > 1 else ""); enabled.toggled.connect(lambda checked, kind=event_type: self.set_type_enabled(kind, checked)); self.type_table.setCellWidget(row, 0, enabled); self.type_table.setItem(row, 1, QTableWidgetItem(event_type))
            sample = typed_events[0]
            before = QDoubleSpinBox(); before.setRange(0, 120); before.setValue(sample.before_ms / 1000); before.setSuffix(" s"); before.valueChanged.connect(lambda value, kind=event_type: self.set_type_padding(kind, before=value)); self.type_table.setCellWidget(row, 2, before)
            after = QDoubleSpinBox(); after.setRange(0, 120); after.setValue(sample.after_ms / 1000); after.setSuffix(" s"); after.valueChanged.connect(lambda value, kind=event_type: self.set_type_padding(kind, after=value)); self.type_table.setCellWidget(row, 3, after)
            if len({event.before_ms for event in typed_events}) > 1: before.setToolTip("Loaded videos currently use different values. Changing this overwrites all of them.")
            if len({event.after_ms for event in typed_events}) > 1: after.setToolTip("Loaded videos currently use different values. Changing this overwrites all of them.")

    def event_index_for_row(self, row: int) -> int | None:
        item = self.event_table.item(row, 0); return int(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def show_event_menu(self, position) -> None:  # type: ignore[no-untyped-def]
        row = self.event_table.rowAt(position.y()); index = self.event_index_for_row(row)
        if index is None or not self.media_record: return
        self.event_table.selectRow(row); event = self.media_record.events[index]; menu = QMenu(self)
        jump_action = menu.addAction("Jump to event"); filter_action = menu.addAction(f"Show only '{event.type}'"); clear_action = menu.addAction("Show all events"); menu.addSeparator(); toggle_action = menu.addAction("Deselect marker" if event.selected else "Select marker"); padding_action = menu.addAction("Edit marker padding...")
        action = menu.exec(self.event_table.viewport().mapToGlobal(position))
        if action == jump_action and event.local_time_ms is not None: self.player.setPosition(round(event.local_time_ms))
        elif action == filter_action: self.event_filter = event.type; self.populate_events()
        elif action == clear_action: self.clear_event_filter()
        elif action == toggle_action: event.selected = event.resolved and not event.selected; self.populate_events(); self.refresh_queue()
        elif action == padding_action: self.edit_event_padding(index)

    def clear_event_filter(self) -> None:
        self.event_filter = None; self.populate_events()

    def edit_event_padding(self, index: int) -> None:
        if not self.media_record: return
        event = self.media_record.events[index]; before, ok = QInputDialog.getDouble(self, "Marker padding", "Seconds before", event.before_ms / 1000, 0, 120, 2)
        if not ok: return
        after, ok = QInputDialog.getDouble(self, "Marker padding", "Seconds after", event.after_ms / 1000, 0, 120, 2)
        if ok: event.before_ms = round(before * 1000); event.after_ms = round(after * 1000); self.populate_events()

    def set_event_padding(self, index: int, before: float | None = None, after: float | None = None) -> None:
        if not self.media_record: return
        event = self.media_record.events[index]
        if before is not None: event.before_ms = round(before * 1000)
        if after is not None: event.after_ms = round(after * 1000)

    def set_type_padding(self, event_type: str, before: float | None = None, after: float | None = None) -> None:
        if self.all_videos_mode and not self.confirm_all_videos_override(): self._populate_type_filters(); return
        events = [event for entry in self.entries for event in entry.record.events] if self.all_videos_mode else (self.media_record.events if self.media_record else [])
        for event in events:
            if event.type == event_type:
                if before is not None: event.before_ms = round(before * 1000)
                if after is not None: event.after_ms = round(after * 1000)
        self.populate_events()

    def set_type_enabled(self, event_type: str, enabled: bool) -> None:
        if self.all_videos_mode and not self.confirm_all_videos_override(): self._populate_type_filters(); return
        events = [event for entry in self.entries for event in entry.record.events] if self.all_videos_mode else (self.media_record.events if self.media_record else [])
        for event in events:
            if event.type == event_type and event.resolved: event.selected = enabled
        self.populate_events(); self.refresh_queue(); self.update_export_state()

    def confirm_all_videos_override(self) -> bool:
        if not self.all_videos_mode or self.all_scope_warning_accepted: return True
        result = QMessageBox.warning(
            self, "Overwrite per-video settings",
            "Changes in 'All Videos Event Paddings' overwrite existing individual settings for this event type in every loaded video.\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        self.all_scope_warning_accepted = result == QMessageBox.StandardButton.Yes
        return self.all_scope_warning_accepted

    def save_type_padding_defaults(self) -> None:
        if not self.entries: QMessageBox.information(self, "No event types", "Load at least one video first."); return
        if not self.confirm_all_videos_override(): return
        all_events = [event for entry in self.entries for event in entry.record.events]
        visible_defaults: dict[str, TypePadding] = {}
        for row in range(self.type_table.rowCount()):
            type_item = self.type_table.item(row, 1)
            before = self.type_table.cellWidget(row, 2); after = self.type_table.cellWidget(row, 3)
            if type_item and isinstance(before, QDoubleSpinBox) and isinstance(after, QDoubleSpinBox):
                visible_defaults[type_item.text()] = TypePadding(round(before.value() * 1000), round(after.value() * 1000))
        self.padding_defaults.update(visible_defaults or collect_type_paddings(all_events))
        apply_padding_defaults(all_events, self.padding_defaults)
        self.settings.setValue("event_padding/defaults", encode_padding_defaults(self.padding_defaults))
        self.populate_events(); self.refresh_queue(); self.update_export_state()
        QMessageBox.information(self, "Defaults saved", f"Saved padding defaults for {len(self.padding_defaults)} event type(s). Loaded and future videos now use these values.")

    def event_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0 or not self.media_record: return
        index = item.data(Qt.ItemDataRole.UserRole)
        if index is None: return
        event = self.media_record.events[int(index)]; event.selected = event.resolved and item.checkState() == Qt.CheckState.Checked; self.timeline.update(); self.refresh_queue(); self.update_export_state()

    def toggle_playback(self) -> None:
        self.player.pause() if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState else self.player.play()

    def position_changed(self, position: int) -> None:
        if not self.timeline.isSliderDown(): self.timeline.setValue(position)
        self.time_label.setText(f"{format_seconds(position / 1000)} / {format_seconds(self.player.duration() / 1000)}")

    def duration_changed(self, duration: int) -> None:
        self.timeline.setRange(0, duration)
        if self.media_record: self.timeline.set_markers(self.media_record.events, duration)

    def jump_event(self, direction: int) -> None:
        if not self.media_record: return
        resolved = [event for event in self.media_record.events if event.local_time_ms is not None]; current = self.player.position()
        target = next((event for event in (resolved if direction > 0 else reversed(resolved)) if ((event.local_time_ms or 0) > current + 50 if direction > 0 else (event.local_time_ms or 0) < current - 50)), (resolved[0] if resolved and direction > 0 else resolved[-1] if resolved else None))
        if target and target.local_time_ms is not None: self.player.setPosition(round(target.local_time_ms))

    def export_sources(self) -> list[ExportSource]:
        result: list[ExportSource] = []
        for entry in self.entries:
            if not entry.enabled: continue
            cuts = build_cut_ranges(entry.record.events, entry.media_info.duration_seconds)
            if cuts: result.append(ExportSource(entry.source, cuts, entry.media_info, entry.game, entry.recording_time))
        return result

    def export_validation_message(self) -> str | None:
        if not self.entries:
            return "Add videos or a match folder."
        if not any(entry.enabled for entry in self.entries):
            return "Enable at least one queued video."
        if not self.output_edit.text().strip():
            return "Choose an output folder."
        if not self.export_sources():
            return "Select at least one resolved marker."
        return None

    def update_export_state(self) -> None:
        if self.worker:
            self.export_button.setEnabled(False)
            return
        message = self.export_validation_message()
        self.export_button.setEnabled(message is None)
        self.export_button.setToolTip("Ready to export the current queue." if message is None else message)
        if message is not None:
            self.status_label.setText(message)
        else:
            self.status_label.setText("Ready to export")

    def start_export(self) -> None:
        sources = self.export_sources()
        if not sources: QMessageBox.warning(self, "Nothing to export", "Add videos and select at least one resolved marker."); return
        if self.render_options.show_markers and not self.render_options.font_path.exists(): QMessageBox.warning(self, "Font not found", f"Marker font does not exist:\n{self.render_options.font_path}"); return
        output_dir = Path(self.output_edit.text().strip())
        if not self.output_edit.text().strip(): QMessageBox.warning(self, "Output missing", "Select an output folder."); return
        runner = FfmpegRunner(self.ffmpeg_path); self.thread = QThread(self); self.worker = ExportWorker(runner, sources, output_dir, str(self.mode_combo.currentData()), self.transition_value, self.transition_seconds_value, self.render_options, self.templates); self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run); self.worker.progress.connect(self.export_progress); self.worker.finished.connect(self.export_finished); self.worker.failed.connect(self.export_failed); self.worker.finished.connect(self.thread.quit); self.worker.failed.connect(self.thread.quit); self.thread.finished.connect(self.thread.deleteLater)
        self.export_button.setEnabled(False); self.cancel_button.setEnabled(True); self.progress.setValue(0); self.status_label.setText("Exporting queue..."); self.eta_label.setText("ETA calculating..."); self.thread.start()

    @Slot(int, str, str)
    def export_progress(self, value: int, message: str, eta: str) -> None:
        self.progress.setValue(value); self.eta_label.setText(eta)
        if message.startswith("Encoder:"): self.status_label.setText(message)

    @Slot(list)
    def export_finished(self, outputs: list[str]) -> None:
        self.progress.setValue(100); self.status_label.setText(f"Created {len(outputs)} file(s)"); self.eta_label.setText("Complete"); self._reset_export_controls(); QMessageBox.information(self, "Export complete", "\n".join(outputs))

    @Slot(str)
    def export_failed(self, error: str) -> None:
        self.status_label.setText("Export failed"); self.eta_label.clear(); self._reset_export_controls(); QMessageBox.critical(self, "Export failed", error)

    def _reset_export_controls(self) -> None:
        self.cancel_button.setEnabled(False); self.worker = None; self.thread = None; self.update_export_state()

    def cancel_export(self) -> None:
        if self.worker: self.status_label.setText("Cancelling..."); self.worker.cancel()

    def _save_all_settings(self) -> None:
        self.settings.setValue("database", self.database_path); self.settings.setValue("ffmpeg", self.ffmpeg_path); self.settings.setValue("naming/individual", self.templates.individual); self.settings.setValue("naming/per_video", self.templates.per_video); self.settings.setValue("naming/combined", self.templates.combined); self.settings.setValue("output/mode", self.destination_mode); self.settings.setValue("output/fixed", self.fixed_output); self.settings.setValue("event_padding/defaults", encode_padding_defaults(self.padding_defaults)); self._save_profiles()

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self.worker: self.worker.cancel()
        self._save_all_settings(); super().closeEvent(event)
