"""Status-bar message. A problem count can be activated to show the pane."""

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QMouseEvent, QResizeEvent, QShowEvent
from PySide6.QtWidgets import QLabel, QWidget

from galley.app.problem_text import problem_count_text


class StatusMessage(QLabel):
    """Left-hand status text. Problem counts open the Problems pane.

    Text too long for the slot is elided. The full text goes in the
    tooltip and the accessible description.
    """

    clicked = Signal()

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._full_text = text
        self._clickable = False
        self.setContentsMargins(8, 0, 8, 0)
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def show_text(self, text: str) -> None:
        """Show ordinary status text that does not open the pane."""
        self._clickable = False
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self._set_full_text(text)
        self.setAccessibleName(text)

    def show_problem_count(self, count: int) -> None:
        """Show ``N problems`` and accept a click."""
        label = problem_count_text(count)
        self._clickable = True
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._set_full_text(label)
        self.setAccessibleName(f"{label}. Show problems.")

    def full_text(self) -> str:
        """The text as set, before any eliding."""
        return self._full_text

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        margins = self.contentsMargins()
        full = self.fontMetrics().horizontalAdvance(self._full_text)
        return QSize(
            max(hint.width(), full + margins.left() + margins.right()), hint.height()
        )

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        margins = self.contentsMargins()
        ellipsis = self.fontMetrics().horizontalAdvance("…")
        return QSize(ellipsis + margins.left() + margins.right(), hint.height())

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._elide()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._elide()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._clickable and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def _set_full_text(self, text: str) -> None:
        self._full_text = text
        self._elide()

    def _elide(self) -> None:
        width = self.contentsRect().width()
        shown = self._full_text
        if self.isVisible() and width > 0:
            shown = self.fontMetrics().elidedText(
                self._full_text, Qt.TextElideMode.ElideRight, width
            )
        elided = shown != self._full_text
        if self.text() != shown:
            self.setText(shown)
        self.setToolTip(self._full_text if elided else "")
        self.setAccessibleDescription(self._full_text if elided else "")
