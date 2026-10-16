"""Plain-text editor with a line-number gutter."""

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPaintEvent,
    QPalette,
    QResizeEvent,
    QTextFormat,
)
from PySide6.QtWidgets import QFrame, QPlainTextEdit, QTextEdit, QWidget

from galley.app.fonts import code_font
from galley.app.theme import (
    EDITOR_BG,
    GUTTER_BG,
    LINE_CURRENT,
    SELECTION,
    TEXT,
    TEXT_MUTED,
)

GUTTER_MIN_WIDTH = 50
_GUTTER_PAD = 8


class LineNumberArea(QWidget):
    """Gutter painted by its editor."""

    def __init__(self, editor: "CodeEditor") -> None:
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event: QPaintEvent) -> None:
        self._editor.paint_line_numbers(event)


class CodeEditor(QPlainTextEdit):
    """Editor surface: grey page, current-line band, muted line numbers."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._line_number_color = QColor(TEXT_MUTED)
        self._gutter_color = QColor(GUTTER_BG)
        self._overlay: QWidget | None = None
        self.setFont(code_font())
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.document().setDocumentMargin(10)
        self._apply_palette()
        self._line_numbers = LineNumberArea(self)
        self.blockCountChanged.connect(self._update_line_number_width)
        self.updateRequest.connect(self._update_line_number_area)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self._update_line_number_width(0)
        self._highlight_current_line()

    def line_number_color(self) -> QColor:
        """Color used for gutter numerals. This is ``text.muted``."""
        return self._line_number_color

    def gutter_color(self) -> QColor:
        """Fill used behind the line numbers. This is ``gutter.bg``."""
        return self._gutter_color

    def line_number_area_width(self) -> int:
        digits = max(3, len(str(max(1, self.blockCount()))))
        measured = _GUTTER_PAD + self.fontMetrics().horizontalAdvance("9") * digits
        return max(GUTTER_MIN_WIDTH, measured)

    def attach_overlay(self, overlay: QWidget) -> None:
        """Center ``overlay`` in the text viewport."""
        overlay.setParent(self.viewport())
        self._overlay = overlay
        self.place_overlay()

    def place_overlay(self) -> None:
        if self._overlay is None:
            return
        self._overlay.adjustSize()
        hint = self._overlay.sizeHint()
        area = self.viewport().rect()
        width = min(hint.width(), area.width())
        height = min(hint.height(), area.height())
        x = max(0, (area.width() - width) // 2)
        y = max(0, (area.height() - height) // 2)
        self._overlay.setGeometry(x, y, max(width, 0), max(height, 0))
        self._overlay.raise_()

    def refresh_gutter(self) -> None:
        """Recompute gutter width after the code font changes."""
        self._update_line_number_width(0)
        self._line_numbers.update()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        rect = self.contentsRect()
        self._line_numbers.setGeometry(
            QRect(rect.left(), rect.top(), self.line_number_area_width(), rect.height())
        )
        self.place_overlay()

    def paint_line_numbers(self, event: QPaintEvent) -> None:
        painter = QPainter(self._line_numbers)
        painter.fillRect(event.rect(), self._gutter_color)
        painter.setPen(self._line_number_color)
        painter.setFont(self.font())
        block = self.firstVisibleBlock()
        number = block.blockNumber()
        top = int(
            self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        )
        bottom = top + int(self.blockBoundingRect(block).height())
        area_width = self._line_numbers.width() - _GUTTER_PAD
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.drawText(
                    0,
                    top,
                    area_width,
                    self.fontMetrics().height(),
                    int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                    str(number + 1),
                )
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            number += 1
        painter.end()

    def _apply_palette(self) -> None:
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Base, QColor(EDITOR_BG))
        palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(SELECTION))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(TEXT))
        self.setPalette(palette)

    def _update_line_number_width(self, _block_count: int) -> None:
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_line_number_area(self, rect: QRect, dy: int) -> None:
        if dy:
            self._line_numbers.scroll(0, dy)
        else:
            self._line_numbers.update(
                0,
                rect.y(),
                self._line_numbers.width(),
                rect.height(),
            )
        if rect.contains(self.viewport().rect()):
            self._update_line_number_width(0)

    def _highlight_current_line(self) -> None:
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(QColor(LINE_CURRENT))
        selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        selection.cursor = self.textCursor()
        selection.cursor.clearSelection()
        self.setExtraSelections([selection])
