from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .gui import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Outplayed Highlight Cutter")
    app.setOrganizationName("LocalTools")
    window = MainWindow()
    window.show()
    return app.exec()

