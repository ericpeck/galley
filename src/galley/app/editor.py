"""Code editor with a line-number gutter and syntax colors."""

from collections.abc import Mapping
from dataclasses import dataclass

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
from galley.app.theme import error_color, ink_on, problem_line_tint
from galley.app.theme import find_highlight_color as brand_find_highlight
from galley.app.theme import line_number_color as theme_line_number_color

GUTTER_MIN_WIDTH = 50
_GUTTER_PAD = 8
_MARKER_SLOT = 16

PROBLEM_MARKERS = "problem"
CHANGE_MARKERS = "change"
FOLD_MARKERS = "fold"


@dataclass(frozen=True)
class GutterLane:
    """One lane of the gutter, one ``width`` column wide.

    The kinds in ``kinds`` share the column. On each line, the first kind
    listed that has a marker there is the one drawn. A ``reserve`` lane
    keeps its width while it is empty, so the text does not move when the
    first marker appears. Any other lane takes no width until one of its
    kinds has markers. The numbers lane has no kinds and gets the rest of
    the gutter.
    """

    name: str
    kinds: tuple[str, ...] = ()
    width: int = _MARKER_SLOT
    reserve: bool = False


# Gutter lanes, left to right, ahead of the right padding. This order is
# also the marker priority: where kinds share a line, the first one listed
# is drawn and picks the line-number color and the line tint. A problem
# outranks a change, and the Changes pane still lists the change.
GUTTER_LANES: tuple[GutterLane, ...] = (
    GutterLane("markers", (PROBLEM_MARKERS, CHANGE_MARKERS), reserve=True),
    GutterLane("numbers"),
    # Reserved for the collapse arrows in #64.
    GutterLane("fold", (FOLD_MARKERS,)),
)
MARKER_KINDS: tuple[str, ...] = tuple(
    kind for lane in GUTTER_LANES for kind in lane.kinds
)


class LineMarker:
    """What one marker kind shows on one line of the gutter."""

    def paint(self, painter: QPainter, rect: QRect, palette: QPalette) -> None:
        """Draw in ``rect``, this marker's lane on one line."""
        raise NotImplementedError

    def number_color(self, palette: QPalette) -> QColor | None:
        """Color for this line's number, or None for the usual color."""
        del palette
        return None

    def line_tint(self, palette: QPalette) -> QColor | None:
        """Band behind the whole line, or None for no band."""
        del palette
        return None


class ProblemMarker(LineMarker):
    """Error marker: a circled exclamation, so the error is not color alone."""

    def paint(self, painter: QPainter, rect: QRect, palette: QPalette) -> None:
        diameter = 12
        marker_y = rect.top() + max(0, (rect.height() - diameter) // 2)
        fill = error_color(palette)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawEllipse(rect.left() + 2, marker_y, diameter, diameter)
        painter.setPen(ink_on(fill, palette))
        painter.drawText(
            rect.left() + 2,
            marker_y,
            diameter,
            diameter,
            int(Qt.AlignmentFlag.AlignCenter),
            "!",
        )

    def number_color(self, palette: QPalette) -> QColor | None:
        return error_color(palette)

    def line_tint(self, palette: QPalette) -> QColor | None:
        return problem_line_tint(palette)


PROBLEM_MARKER = ProblemMarker()


@dataclass(frozen=True)
class GutterLayout:
    """Where each lane sits, as ``(left, width)``."""

    lanes: dict[str, tuple[int, int]]


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
        self._markers: dict[str, dict[int, LineMarker]] = {}
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
        measured += sum(self._marker_lane_widths().values())
        return max(GUTTER_MIN_WIDTH, measured)

    def line_number_area(self) -> QWidget:
        """Gutter widget. Tests read the painted marker from it."""
        return self._line_numbers

    def gutter_layout(self, width: int | None = None) -> GutterLayout:
        """Lanes for a gutter ``width`` pixels wide.

        Marker lanes take their own width, or none while unused. The
        numbers lane takes what is left before the right padding. ``width``
        defaults to the gutter's current width.
        """
        if width is None:
            width = self._line_numbers.width()
        lane_widths = self._marker_lane_widths()
        lane_widths["numbers"] = max(0, width - _GUTTER_PAD - sum(lane_widths.values()))
        lanes: dict[str, tuple[int, int]] = {}
        left = 0
        for lane in GUTTER_LANES:
            lanes[lane.name] = (left, lane_widths[lane.name])
            left += lane_widths[lane.name]
        return GutterLayout(lanes)

    def line_markers(self, kind: str) -> dict[int, LineMarker]:
        """1-based lines and their ``kind`` markers."""
        return dict(self._markers.get(_known_kind(kind), {}))

    def set_line_markers(self, kind: str, markers: Mapping[int, LineMarker]) -> None:
        """Show ``markers`` (1-based line to marker) as this kind's markers.

        They replace the kind's previous markers. An empty mapping clears
        the kind. A lane that is not reserved takes no width again once
        none of its kinds has markers.
        """
        kind = _known_kind(kind)
        if markers:
            self._markers[kind] = dict(markers)
        else:
            self._markers.pop(kind, None)
        self._update_line_number_width(0)
        self._highlight_current_line()
        self._line_numbers.update()

    def clear_line_markers(self, kind: str) -> None:
        """Remove every ``kind`` marker."""
        self.set_line_markers(kind, {})

    def problem_lines(self) -> set[int]:
        """1-based lines that show an error marker."""
        return set(self._markers.get(PROBLEM_MARKERS, {}))

    def set_problem_lines(self, lines: set[int]) -> None:
        """Mark ``lines`` with an error tint and a gutter marker."""
        self.set_line_markers(PROBLEM_MARKERS, dict.fromkeys(lines, PROBLEM_MARKER))

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
        palette = self.palette()
        layout = self.gutter_layout()
        numbers_left, numbers_width = layout.lanes["numbers"]
        line_height = self.fontMetrics().height()
        # Kinds in priority order, so the first marker found on a line wins.
        lanes = [
            (
                layout.lanes[lane.name],
                [self._markers[kind] for kind in lane.kinds if kind in self._markers],
            )
            for lane in GUTTER_LANES
            if lane.kinds and layout.lanes[lane.name][1]
        ]
        ranked = [self._markers[kind] for kind in MARKER_KINDS if kind in self._markers]
        block = self.firstVisibleBlock()
        number = block.blockNumber()
        top = int(
            self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        )
        bottom = top + int(self.blockBoundingRect(block).height())
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                lineno = number + 1
                for (lane_left, lane_width), kinds in lanes:
                    marker = _first_marker(kinds, lineno)
                    if marker is None:
                        continue
                    painter.save()
                    marker.paint(
                        painter,
                        QRect(lane_left, top, lane_width, line_height),
                        palette,
                    )
                    painter.restore()
                number_color = None
                marker = _first_marker(ranked, lineno)
                if marker is not None:
                    number_color = marker.number_color(palette)
                if number_color is None:
                    number_color = self.line_number_color()
                painter.setPen(number_color)
                painter.drawText(
                    numbers_left,
                    top,
                    numbers_width,
                    line_height,
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
        tints = self._line_tints()
        current = self.textCursor().blockNumber() + 1
        if current not in tints:
            band = self.palette().color(QPalette.ColorRole.Midlight)
            selections.append(self._line_band(self.textCursor(), band))
        document = self.document()
        for lineno in sorted(tints):
            block = document.findBlockByNumber(lineno - 1)
            if block.isValid():
                selections.append(self._line_band(QTextCursor(block), tints[lineno]))
        self.setExtraSelections(selections)

    def _line_tints(self) -> dict[int, QColor]:
        """Band color per 1-based line, from the highest-priority marker."""
        palette = self.palette()
        tints: dict[int, QColor] = {}
        for kind in MARKER_KINDS:
            for lineno, marker in self._markers.get(kind, {}).items():
                if lineno in tints:
                    continue
                tint = marker.line_tint(palette)
                if tint is not None:
                    tints[lineno] = tint
        return tints

    def _marker_lane_widths(self) -> dict[str, int]:
        """Width of each marker lane: reserved, in use, or 0."""
        return {
            lane.name: lane.width
            if lane.reserve or any(kind in self._markers for kind in lane.kinds)
            else 0
            for lane in GUTTER_LANES
            if lane.kinds
        }

    def _line_band(
        self, cursor: QTextCursor, color: QColor
    ) -> QTextEdit.ExtraSelection:
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(color)
        selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        selection.cursor = cursor
        selection.cursor.clearSelection()
        return selection


def _first_marker(
    ranked: list[dict[int, LineMarker]], lineno: int
) -> LineMarker | None:
    for markers in ranked:
        marker = markers.get(lineno)
        if marker is not None:
            return marker
    return None


def _known_kind(kind: str) -> str:
    if kind not in MARKER_KINDS:
        raise ValueError(f"unknown marker kind {kind!r}; add it to GUTTER_LANES")
    return kind
