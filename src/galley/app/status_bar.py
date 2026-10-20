"""Status bar built from fixed slots.

Left to right:

- **progress:** the busy spinner, which sits before the state text.
- **state:** the status message. A problem count opens the Problems pane.
- **counts:** a short tally such as ``5 differences``.
- **progress:** the write progress bar and Cancel.
- **info:** permanent facts on the right. Today that is the format
  direction; encoding, line endings and the cursor join it later.

The state slot stretches, so counts and progress hold their place when the
state text changes. A slot with nothing to show is hidden and takes no space.
"""

from enum import Enum, auto

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QProgressBar, QPushButton, QStatusBar, QWidget

from galley.app.fonts import ui_font
from galley.app.status_message import StatusMessage
from galley.app.style import muted_text_style, progress_style, status_bar_style

SPINNER_WIDTH_PX = 80
PROGRESS_WIDTH_PX = 160
STATUS_FONT_PX = 13


class Busy(Enum):
    """What the progress slot shows."""

    IDLE = auto()
    SPINNER = auto()
    PROGRESS = auto()


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
        self._message = StatusMessage("Ready")
        self._message.setObjectName("statusMessage")
        self._message.setStyleSheet(muted_text_style())
        self._message.setFont(font)
        self._message.clicked.connect(self.state_clicked)
        self._counts = _text_slot("statusCounts", font)
        self._counts.setContentsMargins(8, 0, 8, 0)
        self._counts.hide()
        self._progress = _busy_bar("writeProgress", PROGRESS_WIDTH_PX)
        self._cancel = QPushButton("Cancel")
        self._cancel.setObjectName("cancelButton")
        self._cancel.setAccessibleName("Cancel")
        self._cancel.hide()
        self._cancel.clicked.connect(self.cancel_clicked)
        self._direction = _text_slot("directionLabel", font)
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

    def set_state(self, text: str) -> None:
        """Show ordinary state text that does not open the pane."""
        self._message.show_text(text)

    def set_problem_count(self, count: int) -> None:
        """Show ``N problems`` in the state slot and accept a click."""
        self._message.show_problem_count(count)

    def state_text(self) -> str:
        return self._message.text()

    def set_counts(self, text: str) -> None:
        """Show a tally such as ``5 differences``. Empty text hides the slot.

        While shown, the slot keeps its widest width so a shorter tally
        does not move its neighbors.
        """
        if not text:
            self._counts.clear()
            self._counts.setAccessibleName("")
            self._counts.setMinimumWidth(0)
            self._counts.hide()
            return
        self._counts.setText(text)
        self._counts.setAccessibleName(text)
        _keep_widest(self._counts)
        self._counts.show()

    def counts_text(self) -> str:
        return self._counts.text()

    def set_info(self, text: str) -> None:
        """Show the right-hand info text, today the format direction."""
        self._direction.setText(text)

    def info_text(self) -> str:
        return self._direction.text()

    def set_busy(self, busy: Busy) -> None:
        """Show the spinner, the progress bar with Cancel, or neither."""
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
    bar.setAccessibleName("Formatting")
    bar.setRange(0, 0)
    bar.setTextVisible(False)
    bar.setFixedWidth(width)
    bar.setStyleSheet(progress_style())
    bar.hide()
    return bar


def _text_slot(name: str, font: QFont) -> QLabel:
    label = QLabel()
    label.setObjectName(name)
    label.setStyleSheet(muted_text_style())
    label.setFont(font)
    return label


def _keep_widest(label: QLabel) -> None:
    label.setMinimumWidth(max(label.minimumWidth(), label.sizeHint().width()))
