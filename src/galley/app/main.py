"""Galley application shell."""

import sys

from PySide6.QtWidgets import QApplication, QMainWindow

from galley.app.fonts import ui_font
from galley.app.main_window import MainWindow
from galley.app.runtime import configure_runtime


def create_main_window() -> QMainWindow:
    """Return the Galley window in its empty state."""
    return MainWindow()


def main() -> int:
    """Open one Galley window and run its event loop."""
    configure_runtime()
    application = QApplication(sys.argv)
    application.setApplicationName("Galley")
    application.setApplicationDisplayName("Galley")
    application.setFont(ui_font())
    window = create_main_window()
    window.show()
    return application.exec()
