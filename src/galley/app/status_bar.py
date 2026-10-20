"""Status bar built from fixed slots.

Left to right:

- **spinner:** the busy spinner, which sits before the state text.
- **state:** the status message. A problem count opens the Problems pane.
  Text too long for the slot is elided, with the full text in its tooltip.
- **counts:** a short tally such as ``5 differences``.
- **progress:** the write progress bar and Cancel.
- **info:** permanent facts on the right. Today that is the format
  direction; the cursor position joins it later.

The state slot stretches. Counts and info reserve the width of their widest
expected text, so no slot moves another when its text changes. A slot with
nothing to show is hidden and takes no space.
"""

from enum import Enum, auto

from PySide6.QtCore import QEvent, QRect, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QProgressBar, QPushButton, QStatusBar, QWidget

from galley.app.fonts import ui_font
from galley.app.status_message import StatusMessage
from galley.app.style import muted_text_style, progress_style, status_bar_style

SPINNER_WIDTH_PX = 80
PROGRESS_WIDTH_PX = 160
STATUS_FONT_PX = 13
SPINNER_ACCESSIBLE_NAME = "Formatting"
PROGRESS_ACCESSIBLE_NAME = "Formatting progress"

# The widest text each slot expects. The slot reserves that width up front.
COUNTS_RESERVE = ("99999 differences",)
_FORMAT_NAMES = ("JSON", "YAML", "Python")
INFO_RESERVE = (
    *(f"{left} → {right}" for left in _FORMAT_NAMES for right in _FORMAT_NAMES),
    "Ln 99999, Col 999",
)


class Busy(Enum):
    """What the spinner and progress slots show."""

    IDLE = auto()
    SPINNER = auto()
    PROGRESS = auto()


class ReservedLabel(QLabel):
    """Left-aligned text slot at least as wide as its widest expected text.

    The reserve follows font and style changes. Text wider than the
    reserve still widens the slot.
    """

    def __init__(self, reserve: tuple[str, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._reserve = reserve
        self._reserve_width()

    def reserved_width(self) -> int:
        metrics = self.fontMetrics()
        widest = 0
        for text in self._reserve:
            bounds = metrics.boundingRect(QRect(), Qt.TextFlag.TextSingleLine, text)
            widest = max(widest, bounds.width(), metrics.horizontalAdvance(text))
        margins = self.contentsMargins()
        return widest + margins.left() + margins.right()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() in (
            QEvent.Type.FontChange,
            QEvent.Type.StyleChange,
            QEvent.Type.ContentsRectChange,
        ):
            self._reserve_width()

    def _reserve_width(self) -> None:
        self.setMinimumWidth(self.reserved_width())


class StatusBar(QStatusBar):
    """Galley's status bar. See the module docstring for the slot order."""

    state_clicked = Signal()
    cancel_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(status_bar_style())
        self.setSizeGripEnabled(True)
        font = ui_font()
        font.setPixelSize(STATUS_FONT_PX)
        self._spinner = _busy_bar("formatSpinner", SPINNER_WIDTH_PX)
        self._spinner.setAccessibleName(SPINNER_ACCESSIBLE_NAME)
        self._message = StatusMessage("Ready")
        self._message.setObjectName("statusMessage")
        self._message.setStyleSheet(muted_text_style())
        self._message.setFont(font)
        self._message.clicked.connect(self.state_clicked)
        self._counts = _text_slot("statusCounts", font, COUNTS_RESERVE)
        self._counts.setContentsMargins(8, 0, 8, 0)
        self._counts.hide()
        self._progress = _busy_bar("writeProgress", PROGRESS_WIDTH_PX)
        self._progress.setAccessibleName(PROGRESS_ACCESSIBLE_NAME)
        self._cancel = QPushButton("Cancel")
        self._cancel.setObjectName("cancelButton")
        self._cancel.setAccessibleName("Cancel")
        self._cancel.hide()
        self._cancel.clicked.connect(self.cancel_clicked)
        self._direction = _text_slot("directionLabel", font, INFO_RESERVE)
        self.addWidget(self._spinner)
        self.addWidget(self._message, 1)
        self.addWidget(self._counts)
        self.addWidget(self._progress)
        self.addWidget(self._cancel)
        self.addPermanentWidget(self._direction)
        # Adding a widget rebuilds the layout, so set the margins last.
        layout = self.layout()
        if layout is not None:
            layout.setContentsMargins(8, 0, 8, 0)

    def set_state(self, text: str, detail: str = "") -> None:
        """Show ordinary state text that does not open the pane.

        ``detail`` goes in the tooltip and accessible description only.
        """
        self._message.show_text(text, detail)

    def set_problem_count(self, count: int) -> None:
        """Show ``N problems`` in the state slot and accept a click."""
        self._message.show_problem_count(count)

    def state_text(self) -> str:
        """The full state text, even while the slot shows it elided."""
        return self._message.full_text()

    def set_counts(self, text: str) -> None:
        """Show a tally such as ``5 differences``. Empty text hides the slot.

        The slot reserves room for ``99999 differences``. A longer tally
        widens it until the slot is cleared.
        """
        counts = self._counts
        if not text:
            counts.clear()
            counts.setAccessibleName("")
            counts.setMinimumWidth(counts.reserved_width())
            counts.hide()
            return
        counts.setText(text)
        counts.setAccessibleName(text)
        counts.setMinimumWidth(max(counts.minimumWidth(), counts.sizeHint().width()))
        counts.show()

    def counts_text(self) -> str:
        return self._counts.text()

    def set_info(self, text: str) -> None:
        """Show the right-hand info text, today the format direction."""
        self._direction.setText(text)

    def info_text(self) -> str:
        return self._direction.text()

    def set_busy(self, busy: Busy, name: str = SPINNER_ACCESSIBLE_NAME) -> None:
        """Show the spinner, the progress bar with Cancel, or neither.

        ``name`` is the spinner's accessible name, such as ``Opening``.
        """
        self._spinner.setAccessibleName(name)
        if busy is Busy.PROGRESS:
            self._progress.show()
            self._spinner.hide()
            self._cancel.show()
        elif busy is Busy.SPINNER:
            self._spinner.show()
            self._progress.hide()
            self._cancel.hide()
        else:
            self._progress.hide()
            self._spinner.hide()
            self._cancel.hide()

    def progress_visible(self) -> bool:
        """True while the progress bar is on screen."""
        return self._progress.isVisible()


def _busy_bar(name: str, width: int) -> QProgressBar:
    bar = QProgressBar()
    bar.setObjectName(name)
    bar.setRange(0, 0)
    bar.setTextVisible(False)
    bar.setFixedWidth(width)
    bar.setStyleSheet(progress_style())
    bar.hide()
    return bar


def _text_slot(name: str, font: QFont, reserve: tuple[str, ...]) -> ReservedLabel:
    label = ReservedLabel(reserve)
    label.setObjectName(name)
    label.setStyleSheet(muted_text_style())
    label.setFont(font)
    return label
