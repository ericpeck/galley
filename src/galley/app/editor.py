"""Code editor with a line-number gutter and syntax colors."""

from PySide6.QtCore import QEvent, QRect, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPaintEvent,
    QPalette,
    QResizeEvent,
    QTextCursor,
    QTextFormat,
)
from PySide6.QtWidgets import QApplication, QFrame, QPlainTextEdit, QTextEdit, QWidget

from galley.app.fonts import code_font
from galley.app.highlighter import SyntaxHighlighter, SyntaxMode
from galley.app.theme import contrast_ratio, error_color, problem_line_tint
from galley.app.theme import find_highlight_color as brand_find_highlight
from galley.app.theme import line_number_color as theme_line_number_color

GUTTER_MIN_WIDTH = 50
_GUTTER_PAD = 8
_MARKER_SLOT = 16


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
    """Editor surface: palette Base, current-line band, placeholder line numbers."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._adopting_palette = False
        self._problem_lines: set[int] = set()
        self._overlay: QWidget | None = None
        self.setFont(code_font())
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.document().setDocumentMargin(10)
        self._syntax_mode_set = False
        # Connected before the highlighter so a mode change from detection
        # lands before the highlighter reformats the same edit.
        self.document().contentsChange.connect(self._detect_syntax)
        self._highlighter = SyntaxHighlighter(self.document())
        self._highlighter.follow_base(self.palette().color(QPalette.ColorRole.Base))
        self._line_numbers = LineNumberArea(self)
        self._line_numbers.setObjectName("lineNumberArea")
        self.blockCountChanged.connect(self._update_line_number_width)
        self.updateRequest.connect(self._update_line_number_area)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self._update_line_number_width(0)
        self._highlight_current_line()
        # setDocumentMargin records an edit. Drop it so Undo starts disabled.
        self.document().clearUndoRedoStacks()

    def find_highlight_color(self) -> QColor:
        """Brand color for a find match. Not the text-selection role."""
        return brand_find_highlight()

    def line_number_color(self) -> QColor:
        """Gutter numerals: placeholder text, or the dark-mode number color."""
        return theme_line_number_color(self.palette())

    def gutter_color(self) -> QColor:
        """Fill behind the line numbers. This is ``AlternateBase``."""
        return self.palette().color(QPalette.ColorRole.AlternateBase)

    def follow_application_palette(self) -> None:
        """Copy the application palette when an ancestor stylesheet froze Base.

        A pane stylesheet stops the editor inheriting a later dark scheme,
        which would leave the page white. Skip the copy when Base already
        matches so this does not fight the native palette.
        """
        if self._adopting_palette:
            return
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return
        palette = QPalette(app.palette())
        current = self.palette().color(QPalette.ColorRole.Base).rgba()
        if current == palette.color(QPalette.ColorRole.Base).rgba():
            self._repaint_line_colors()
            return
        self._adopting_palette = True
        try:
            self.setPalette(palette)
        finally:
            self._adopting_palette = False
        self._repaint_line_colors()

    def _repaint_line_colors(self) -> None:
        """Rebuild the line bands, which keep the colors they were made with."""
        self._highlight_current_line()
        self._line_numbers.update()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if not hasattr(self, "_line_numbers"):
            return
        if event.type() not in (
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
        ):
            return
        self.follow_application_palette()
        self._highlighter.follow_base(self.palette().color(QPalette.ColorRole.Base))

    def line_number_area_width(self) -> int:
        digits = max(3, len(str(max(1, self.blockCount()))))
        measured = _GUTTER_PAD + self.fontMetrics().horizontalAdvance("9") * digits
        if self._problem_lines:
            measured += _MARKER_SLOT
        return max(GUTTER_MIN_WIDTH, measured)

    def line_number_area(self) -> QWidget:
        """Gutter widget. Tests read the painted marker from it."""
        return self._line_numbers

    def problem_lines(self) -> set[int]:
        """1-based lines that show an error marker."""
        return set(self._problem_lines)

    def set_problem_lines(self, lines: set[int]) -> None:
        """Mark ``lines`` with an error tint and a gutter marker."""
        self._problem_lines = set(lines)
        self._update_line_number_width(0)
        self._highlight_current_line()
        self._line_numbers.update()

    def syntax_mode(self) -> SyntaxMode:
        """``"JSON"`` or ``"YAML"``, the colors the editor applies."""
        return self._highlighter.mode()

    def set_syntax_mode(self, mode: SyntaxMode) -> None:
        """Color as ``mode`` from now on and stop guessing from the text.

        Until this is called, the first non-blank character picks the mode:
        ``{`` or ``[`` means JSON, anything else YAML.
        """
        self._syntax_mode_set = True
        self._highlighter.set_mode(mode)

    def syntax_colors_enabled(self) -> bool:
        return self._highlighter.is_enabled()

    def set_syntax_colors_enabled(self, enabled: bool) -> None:
        """Turn syntax colors on or off, for example for large documents."""
        self._highlighter.set_enabled(enabled)

    def reveal_line(self, lineno: int) -> None:
        """Move the cursor to ``lineno``, scroll it into view, and focus."""
        block = self.document().findBlockByNumber(max(lineno, 1) - 1)
        if not block.isValid():
            block = self.document().lastBlock()
        cursor = self.textCursor()
        cursor.setPosition(block.position())
        self.setTextCursor(cursor)
        self.centerCursor()
        self.setFocus()

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
        painter.fillRect(event.rect(), self.gutter_color())
        painter.setPen(self.line_number_color())
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
                lineno = number + 1
                if lineno in self._problem_lines:
                    self._paint_error_marker(painter, top)
                    painter.setPen(error_color(self.palette()))
                else:
                    painter.setPen(self.line_number_color())
                painter.drawText(
                    _MARKER_SLOT if self._problem_lines else 0,
                    top,
                    area_width - (_MARKER_SLOT if self._problem_lines else 0),
                    self.fontMetrics().height(),
                    int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                    str(lineno),
                )
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            number += 1
        painter.end()

    def _detect_syntax(self, position: int, _removed: int, added: int) -> None:
        if self._syntax_mode_set:
            return
        document = self.document()
        count = document.characterCount()
        index = 0
        while index < count and document.characterAt(index).isspace():
            index += 1
        if index >= count:
            return
        mode: SyntaxMode = "JSON" if document.characterAt(index) in "{[" else "YAML"
        # An edit that replaced everything is reformatted by the highlighter
        # right after this slot, so a full pass here would be wasted.
        whole = position == 0 and added >= count - 1
        self._highlighter.set_mode(mode, rehighlight=not whole)

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
        selections: list[QTextEdit.ExtraSelection] = []
        current = self.textCursor().blockNumber() + 1
        if current not in self._problem_lines:
            band = self.palette().color(QPalette.ColorRole.Midlight)
            selections.append(self._line_band(self.textCursor(), band))
        block = self.document().firstBlock()
        while block.isValid():
            if block.blockNumber() + 1 in self._problem_lines:
                tint = problem_line_tint(self.palette())
                selections.append(self._line_band(QTextCursor(block), tint))
            block = block.next()
        self.setExtraSelections(selections)

    def _line_band(
        self, cursor: QTextCursor, color: QColor
    ) -> QTextEdit.ExtraSelection:
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(color)
        selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        selection.cursor = cursor
        selection.cursor.clearSelection()
        return selection

    def _paint_error_marker(self, painter: QPainter, top: int) -> None:
        """Draw a circled exclamation so the error is not color alone."""
        diameter = 12
        marker_y = top + max(0, (self.fontMetrics().height() - diameter) // 2)
        fill = error_color(self.palette())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawEllipse(2, marker_y, diameter, diameter)
        painter.setPen(self._marker_ink(fill))
        painter.drawText(
            2,
            marker_y,
            diameter,
            diameter,
            int(Qt.AlignmentFlag.AlignCenter),
            "!",
        )

    def _marker_ink(self, fill: QColor) -> QColor:
        """White on the light-mode error, the dark Base on the dark one."""
        palette = self.palette()
        label = palette.color(QPalette.ColorRole.HighlightedText)
        base = palette.color(QPalette.ColorRole.Base)
        if contrast_ratio(label, fill) >= contrast_ratio(base, fill):
            return label
        return base
