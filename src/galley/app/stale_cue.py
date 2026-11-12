"""The Stale tag and the 6a banner over a result Output no longer matches.

#91 D2, from Bill's D3 and his answers on #91. The tag sits after the
Output title and never sheds. The banner sits between the tab strip and
the editor, pushes the text down, wraps, and is never a Tab stop. Both
use AlternateBase with a clock in the text color.
"""

from PySide6.QtCore import QDateTime, QLocale, Qt
from PySide6.QtGui import QPainter, QPaintEvent, QPalette
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from galley.app.icons import glyph
from galley.app.style import stale_style

STALE_TEXT = "Stale"
_ICON = 16


def stale_tooltip(action: str) -> str:
    """The tag's tooltip, naming the command that made Output (Bill, Q4)."""
    return (
        "Output doesn't match the current input and settings. "
        f"Press {action} to update it."
    )


def stale_when(made: QDateTime, now: QDateTime, locale: QLocale) -> str:
    """When the result was made: the short time, with the date on another day."""
    short = QLocale.FormatType.ShortFormat
    if made.date() == now.date():
        return locale.toString(made.time(), short)
    return locale.toString(made, short)


def banner_text(when: str) -> str:
    """The 6a banner sentence, from the spec."""
    return (
        f"Showing the last good result, from {when}. "
        "Fix the input and press Format to update it."
    )


class _ClockLabel(QLabel):
    """A label that paints the clock in its left padding, never focused."""

    def __init__(self, text: str, object_name: str, parent: QWidget | None) -> None:
        super().__init__(text, parent)
        self.setObjectName(object_name)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setStyleSheet(stale_style(object_name))
        self.hide()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        app = QApplication.instance()
        palette = app.palette() if isinstance(app, QApplication) else QPalette()
        pixmap = glyph("Clock", palette.color(QPalette.ColorRole.WindowText))
        if pixmap is None:
            return
        painter = QPainter(self)
        painter.drawPixmap(self._icon_x(), self._icon_y(), pixmap)
        painter.end()

    def _icon_x(self) -> int:
        return 6

    def _icon_y(self) -> int:
        return max(0, (self.height() - _ICON) // 2)


class StaleTag(_ClockLabel):
    """``Stale`` after the Output title. Its description is the tooltip."""

    def __init__(self, object_name: str, parent: QWidget | None = None) -> None:
        super().__init__(STALE_TEXT, object_name, parent)
        self.setAccessibleName(STALE_TEXT)

    def set_tooltip(self, text: str) -> None:
        self.setToolTip(text)
        self.setAccessibleDescription(text)


class StaleBanner(_ClockLabel):
    """The 6a banner. It wraps and never elides; the clock sits on line one."""

    def __init__(self, object_name: str, parent: QWidget | None = None) -> None:
        super().__init__("", object_name, parent)
        self.setWordWrap(True)

    def set_text(self, text: str) -> None:
        self.setText(text)
        self.setAccessibleName(text)

    def _icon_x(self) -> int:
        return 8

    def _icon_y(self) -> int:
        # The banner's top padding, then the clock centred on the first line.
        return 6 + max(0, (self.fontMetrics().height() - _ICON) // 2)
