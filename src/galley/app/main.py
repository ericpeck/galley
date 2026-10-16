"""Galley application shell."""

import sys

from PySide6.QtWidgets import QApplication, QMainWindow

from galley.app.runtime import configure_runtime


def create_main_window() -> QMainWindow:
    """Return an empty main window titled Galley."""
    window = QMainWindow()
    window.setWindowTitle("Galley")
    return window


def main() -> int:
    """Open one empty Galley window and run its event loop."""
    configure_runtime()
    application = QApplication(sys.argv)
    window = create_main_window()
    window.show()
    return application.exec()
