import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPlainTextEdit

from outplayed_highlight_cutter.gui import ImportFailuresDialog


def test_import_failure_dialog_has_fixed_initial_size_and_scrollable_details() -> None:
    app = QApplication.instance() or QApplication([])
    failures = [f"video-{index}.mp4: No Outplayed record" for index in range(200)]
    dialog = ImportFailuresDialog(failures)
    details = dialog.findChild(QPlainTextEdit)
    assert dialog.size().width() == 760
    assert dialog.size().height() == 480
    assert dialog.minimumSize() == dialog.maximumSize()
    assert details is not None
    assert details.isReadOnly()
    assert "video-199.mp4" in details.toPlainText()
    dialog.close()
    app.processEvents()
