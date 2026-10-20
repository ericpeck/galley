"""Input / Output splitter with a rail that reads the split ratio."""

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPalette
from PySide6.QtWidgets import (
    QBoxLayout,
    QLabel,
    QSplitter,
    QSplitterHandle,
    QToolButton,
    QWidget,
)

from galley.app.style import muted_text_style
from galley.app.theme import TEXT_MUTED, WINDOW_BG

RAIL_WIDTH = 32
RESET_NAME = "Reset to 50 | 50"


class SplitterRail(QSplitterHandle):
    """Reset control and the live percent readout. Dragging still moves the split."""

    def __init__(self, orientation: Qt.Orientation, splitter: QSplitter) -> None:
        super().__init__(orientation, splitter)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(WINDOW_BG))
        self.setPalette(palette)
        self.setAutoFillBackground(True)

        self.reset_button = _rail_button("↔", RESET_NAME, "resetSplitButton")
        self.reset_button.setEnabled(False)
        self.reset_button.clicked.connect(self._reset_even)
        self.ratio_label = QLabel("50\n50")
        self.ratio_label.setObjectName("splitRatio")
        self.ratio_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ratio_label.setAccessibleName("50 / 50")
        self.ratio_label.setStyleSheet(muted_text_style())
        ratio_font = self.ratio_label.font()
        ratio_font.setPixelSize(11)
        self.ratio_label.setFont(ratio_font)
        self.ratio_label.installEventFilter(self)

        self._box = QBoxLayout(QBoxLayout.Direction.TopToBottom, self)
        self._box.setSpacing(4)
        self._box.addStretch(1)
        self._box.addWidget(self.reset_button, 0, Qt.AlignmentFlag.AlignCenter)
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
        self.reset_button.setEnabled(left != 50)

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

    def setSizes(self, sizes: list[int]) -> None:
        """Apply ``sizes`` and refresh the readout. ``setSizes`` emits no move."""
        super().setSizes(sizes)
        if self.rail is not None:
            self.rail.refresh_ratio()

    def apply_even_split(self) -> None:
        """Give Input and Output the same share of the splitter."""
        self.setSizes([1, 1])


def _rail_button(text: str, name: str, object_name: str) -> QToolButton:
    button = QToolButton()
    button.setText(text)
    button.setAccessibleName(name)
    button.setToolTip(name)
    button.setObjectName(object_name)
    button.setAutoRaise(True)
    button.setFixedSize(24, 24)
    palette = button.palette()
    color = QColor(TEXT_MUTED)
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        palette.setColor(group, QPalette.ColorRole.ButtonText, color)
        palette.setColor(group, QPalette.ColorRole.WindowText, color)
    button.setPalette(palette)
    return button
