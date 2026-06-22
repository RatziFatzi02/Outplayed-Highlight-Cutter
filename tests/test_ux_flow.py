import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QPushButton

from outplayed_highlight_cutter.ffmpeg import MediaInfo
from outplayed_highlight_cutter.gui import MainWindow, VideoEntry
from outplayed_highlight_cutter.models import Event, MediaRecord


def make_window(tmp_path: Path) -> MainWindow:
    app = QApplication.instance() or QApplication([])
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    QSettings("LocalTools", "OutplayedHighlightCutter").clear()
    window = MainWindow()
    app.processEvents()
    return window


def make_entry(name: str = "clip.mp4", selected: bool = True) -> VideoEntry:
    event = Event("kill", 1000, 1000, selected=selected, resolved=True)
    return VideoEntry(
        Path(name),
        MediaRecord(Path(name), 0, 10_000, 10_000, [event]),
        MediaInfo(10, 1),
    )


def button_texts(window: MainWindow) -> list[str]:
    return [button.text() for button in window.findChildren(QPushButton)]


def test_match_folder_is_primary_queue_action(tmp_path: Path) -> None:
    window = make_window(tmp_path)
    texts = button_texts(window)
    assert texts.index("Add match folder...") < texts.index("Add videos...")
    assert "Add video folder recursively" not in texts
    window.close()


def test_all_videos_mode_keeps_preview_placeholder_and_open_first_video(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = make_window(tmp_path)
    window.show()
    app.processEvents()
    window.entries = [make_entry("first.mp4"), make_entry("second.mp4")]
    window.refresh_queue()
    window.show_all_video_paddings()
    assert window.all_videos_mode
    assert window.active_index is None
    assert window.preview_hint.isVisible()
    assert window.preview_hint.text() == "Select a video to preview markers."
    assert window.open_preview_button.text() == "Open first video"
    assert not window.event_table.isVisible()

    window.open_preview_button.click()
    app.processEvents()
    assert not window.all_videos_mode
    assert window.active_index == 0
    assert window.padding_scope_label.text() == "Per Video Event Paddings: first.mp4"
    assert window.event_table.isVisible()
    assert window.event_table.rowCount() == 1
    window.close()


def test_selected_queue_item_changes_preview_cta(tmp_path: Path) -> None:
    window = make_window(tmp_path)
    window.entries = [make_entry("first.mp4"), make_entry("second.mp4")]
    window.refresh_queue()
    window.show_all_video_paddings()
    window.queue_table.selectRow(1)
    window.update_preview_action()
    assert window.open_preview_button.text() == "Open selected video"
    window.open_preview_button.click()
    assert window.active_index == 1
    window.close()


def test_export_button_validation_reasons(tmp_path: Path) -> None:
    window = make_window(tmp_path)
    window.output_edit.clear()
    assert not window.export_button.isEnabled()
    assert window.export_button.toolTip() == "Add videos or a match folder."

    window.entries = [make_entry(selected=True)]
    window.refresh_queue()
    window.show_all_video_paddings()
    assert not window.export_button.isEnabled()
    assert window.export_button.toolTip() == "Choose an output folder."

    window.output_edit.setText(str(tmp_path / "Highlights"))
    assert window.export_button.isEnabled()
    assert window.export_button.toolTip() == "Ready to export the current queue."

    window.entries[0].record.events[0].selected = False
    window.refresh_queue()
    assert not window.export_button.isEnabled()
    assert window.export_button.toolTip() == "Select at least one resolved marker."
    window.close()
