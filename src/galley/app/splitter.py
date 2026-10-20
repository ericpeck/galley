"""Input / Output splitter with a rail that reads the split ratio."""

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QBoxLayout,
    QLabel,
    QSplitter,
    QSplitterHandle,
    QToolButton,
    QWidget,
)

from galley.app.style import muted_text_style
from galley.app.theme import GUTTER_BG, WINDOW_BG

RAIL_WIDTH = 32


class SplitterRail(QSplitterHandle):
    """Swap and the live percent readout. Dragging still moves the split."""

    def __init__(self, orientation: Qt.Orientation, splitter: QSplitter) -> None:
        super().__init__(orientation, splitter)
        self.setStyleSheet(f"background: {WINDOW_BG};")

        self.swap_button = _rail_button("⇆", "Swap", "swapButton")
        self.ratio_label = QLabel("50\n50")
        self.ratio_label.setObjectName("splitRatio")
        self.ratio_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ratio_label.setAccessibleName("50 / 50")
        self.ratio_label.setStyleSheet(muted_text_style() + " font-size: 9px;")
        self.ratio_label.installEventFilter(self)

        self._box = QBoxLayout(QBoxLayout.Direction.TopToBottom, self)
        self._box.setSpacing(4)
        self._box.addStretch(1)
        self._box.addWidget(self.swap_button, 0, Qt.AlignmentFlag.AlignCenter)
        self._box.addWidget(self.ratio_label, 0, Qt.AlignmentFlag.AlignCenter)
        self._box.addStretch(1)
        self.adapt_to_orientation()

        splitter.splitterMoved.connect(self.refresh_ratio)

    def adapt_to_orientation(self) -> None:
        """Stack the rail controls, or lay them in a row, to match the split."""
        width = self.ratio_label.fontMetrics().horizontalAdvance("100")
        self.ratio_label.setMinimumWidth(width)
        if self.orientation() == Qt.Orientation.Horizontal:
            self._box.setDirection(QBoxLayout.Direction.TopToBottom)
            self._box.setContentsMargins(0, 8, 0, 8)
            return
        self._box.setDirection(QBoxLayout.Direction.LeftToRight)
        self._box.setContentsMargins(8, 0, 8, 0)

    def refresh_ratio(self, _pos: int = 0, _index: int = 0) -> None:
        sizes = self.splitter().sizes()
        total = sum(sizes)
        if total <= 0:
            return
        left = round(100 * sizes[0] / total)
        right = 100 - left
        self.ratio_label.setText(f"{left}\n{right}")
        self.ratio_label.setAccessibleName(f"{left} / {right}")

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """Reset to an even split. The spec marks this as optional."""
        self._reset_even()
        event.accept()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            watched is self.ratio_label
            and event.type() == QEvent.Type.MouseButtonDblClick
        ):
            self._reset_even()
            return True
        return super().eventFilter(watched, event)

    def _reset_even(self) -> None:
        splitter = self.splitter()
        if isinstance(splitter, PaneSplitter):
            splitter.apply_even_split()


class PaneSplitter(QSplitter):
    """Input and Output, equal stretch, with one rail handle."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setObjectName("mainSplitter")
        self.setHandleWidth(RAIL_WIDTH)
        self.setChildrenCollapsible(False)
        self.rail: SplitterRail | None = None

    def createHandle(self) -> QSplitterHandle:
        rail = SplitterRail(self.orientation(), self)
        self.rail = rail
        return rail

    def apply_stacked(self, stacked: bool) -> None:
        """Place Input over Output, or side by side, and keep the split ratio."""
        orientation = Qt.Orientation.Vertical if stacked else Qt.Orientation.Horizontal
        if self.orientation() == orientation:
            return
        sizes = self.sizes()
        self.setOrientation(orientation)
        if self.rail is not None:
            self.rail.adapt_to_orientation()
        if sum(sizes) > 0:
            self.setSizes(sizes)
        if self.rail is not None:
            self.rail.refresh_ratio()

    def apply_even_split(self) -> None:
        """Give Input and Output the same share of the splitter."""
        self.setSizes([1, 1])
        if self.rail is not None:
            self.rail.refresh_ratio()


def _rail_button(text: str, name: str, object_name: str) -> QToolButton:
    button = QToolButton()
    button.setText(text)
    button.setAccessibleName(name)
    button.setToolTip(name)
    button.setObjectName(object_name)
    button.setAutoRaise(True)
    button.setFixedSize(24, 24)
    button.setStyleSheet(
        f"QToolButton {{ background: {GUTTER_BG}; border-radius: 4px; }}"
    )
    return button
