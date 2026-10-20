"""Status-bar message. A problem count can be activated to show the pane."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QLabel, QWidget

from galley.app.problem_text import problem_count_text


class StatusMessage(QLabel):
    """Left-hand status text. Problem counts open the Problems pane."""

    clicked = Signal()

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._clickable = False
        self.setContentsMargins(8, 0, 8, 0)
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def show_text(self, text: str) -> None:
        """Show ordinary status text that does not open the pane."""
        self._clickable = False
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setText(text)
        self.setAccessibleName(text)

    def show_problem_count(self, count: int) -> None:
        """Show ``N problems`` and accept a click."""
        label = problem_count_text(count)
        self._clickable = True
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setText(label)
        self.setAccessibleName(f"{label}. Show problems.")

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._clickable and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)
