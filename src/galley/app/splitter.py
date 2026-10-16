"""Horizontal splitter with a rail that reads the Input / Output ratio."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QLabel,
    QSplitter,
    QSplitterHandle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from galley.app.style import muted_text_style
from galley.app.theme import GUTTER_BG, WINDOW_BG

RAIL_WIDTH = 32


class SplitterRail(QSplitterHandle):
    """Swap, layout, and the live percent readout. Dragging still moves the split."""

    def __init__(self, orientation: Qt.Orientation, splitter: QSplitter) -> None:
        super().__init__(orientation, splitter)
        self.setStyleSheet(f"background: {WINDOW_BG};")

        self.swap_button = _rail_button("⇆", "Swap", "swapButton")
        self.layout_button = _rail_button("▥", "Layout", "layoutButton")
        self.ratio_label = QLabel("50\n50")
        self.ratio_label.setObjectName("splitRatio")
        self.ratio_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ratio_label.setAccessibleName("50 / 50")
        self.ratio_label.setStyleSheet(muted_text_style() + " font-size: 9px;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(4)
        layout.addStretch(1)
        layout.addWidget(self.swap_button, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.layout_button, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.ratio_label)
        layout.addStretch(1)

        splitter.splitterMoved.connect(self.refresh_ratio)

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
        splitter = self.splitter()
        if isinstance(splitter, PaneSplitter):
            splitter.apply_even_split()
        event.accept()


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

    def apply_even_split(self) -> None:
        """Give Input and Output the same width."""
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
