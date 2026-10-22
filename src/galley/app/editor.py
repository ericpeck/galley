"""Code editor with a line-number gutter and syntax colors."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QHelpEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPalette,
    QPen,
    QResizeEvent,
    QTextCursor,
    QTextFormat,
    QTextOption,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QPlainTextEdit,
    QTextEdit,
    QToolTip,
    QWidget,
)

from galley.app.fonts import code_font
from galley.app.highlighter import SyntaxHighlighter, SyntaxMode
from galley.app.limits import JSON_BUSY_BYTES
from galley.app.theme import error_color, problem_line_tint
from galley.app.theme import find_highlight_color as brand_find_highlight
from galley.app.theme import line_number_color as theme_line_number_color

GUTTER_MIN_WIDTH = 50
_GUTTER_PAD = 8
_MARKER_SLOT = 16
# The error marker matches the Problems pane's ⊗: a 12 px ring with a ×.
_ERROR_MARKER_SIZE = 12
_ERROR_MARKER_PEN = 1.6
# How far the × arms start inside the ring's pixel centers. The arms stop
# short of the ring, as the glyph's do.
_ERROR_MARKER_INSET = 4

PROBLEM_MARKERS = "problem"
CHANGE_MARKERS = "change"
FOLD_MARKERS = "fold"

# Display gates for very long lines, in characters of one block (#91).
#
# QPlainTextEdit lays out a whole block on every edit to it, so one giant
# line costs what a whole document would. Measured on main with a 1.6 MB
# one-line JSON document in an offscreen editor, wrap on:
#
#   Show Whitespace on, colors on    setText 21.0 s  keystroke 10.4 s
#   Show Whitespace off, colors on   setText  1.2 s  keystroke 0.54 s
#   both off                         setText 0.67 s  keystroke 0.36 s
#   same text pretty-printed         setText 0.14 s  keystroke 0.011 s
#
# Plain layout is linear in the line: 0.1 MB is 31 ms a keystroke, 0.5 MB
# is 114 ms, 1.6 MB is 364 ms. Whitespace dots grow faster than that: they
# double a keystroke at 50_000 characters (49 ms against 26 ms) and
# multiply it by about 20 at 1.6 MB, so they stop at 50_000. Colors add
# about the layout cost again (130 ms against 61 ms at 250_000), so they
# stop at 250_000. Wrap stays as the user set it: wrap off is slower on a
# long line.
WHITESPACE_LONG_LINE_CHARS = 50_000
COLORS_LONG_LINE_CHARS = 250_000

# Which display limit applies. ``display_limits`` lists these.
WHITESPACE_LIMIT = "whitespace"
COLORS_LIMIT = "colors"


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

    def description(self) -> str:
        """Tooltip and accessible text for this marker, or "" for none."""
        return ""


@dataclass(frozen=True)
class ProblemMarker(LineMarker):
    """Error marker: the Problems pane's ⊗, so the error is not color alone.

    ``message`` is the text of the line's Problems row. The line number
    keeps its usual color; the marker alone shows the error.
    """

    message: str = ""

    def paint(self, painter: QPainter, rect: QRect, palette: QPalette) -> None:
        size = _ERROR_MARKER_SIZE
        pen = QPen(error_color(palette), _ERROR_MARKER_PEN)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # Centering the strokes on pixel centers, as icons.py does, draws
        # the ring's outer rows and columns at full color.
        painter.translate(
            rect.left() + (rect.width() - size) // 2 + 0.5,
            rect.top() + max(0, (rect.height() - size) // 2) + 0.5,
        )
        # Pixel centers of the ring's first and last rows and columns.
        far = size - 1
        painter.drawEllipse(0, 0, far, far)
        near, end = _ERROR_MARKER_INSET, far - _ERROR_MARKER_INSET
        painter.drawLine(near, near, end, end)
        painter.drawLine(near, end, end, near)

    def line_tint(self, palette: QPalette) -> QColor | None:
        return problem_line_tint(palette)

    def description(self) -> str:
        return self.message


PROBLEM_MARKER = ProblemMarker()


@dataclass(frozen=True)
class MarkerHit:
    """A marker under a gutter point: its kind, 1-based line, and lane rect."""

    kind: str
    lineno: int
    marker: LineMarker
    rect: QRect


@dataclass(frozen=True)
class GutterLayout:
    """Where each lane sits, as ``(left, width)``."""

    lanes: dict[str, tuple[int, int]]


class LineNumberArea(QWidget):
    """Gutter painted by its editor.

    Markers are painted, not widgets, so the gutter answers for them: a
    tooltip shows the marker under the pointer, and the accessible
    description lists the error messages.
    """

    def __init__(self, editor: "CodeEditor") -> None:
        super().__init__(editor)
        self._editor = editor
        self.setAccessibleName("Line numbers")

    def sizeHint(self) -> QSize:
        return QSize(self._editor.line_number_area_width(), 0)

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip and isinstance(event, QHelpEvent):
            hit = self._editor.marker_at(event.pos())
            text = hit.marker.description() if hit is not None else ""
            if hit is not None and text:
                QToolTip.showText(event.globalPos(), text, self, hit.rect)
            else:
                QToolTip.hideText()
                event.ignore()
            return True
        return super().event(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            hit = self._editor.marker_at(event.position().toPoint())
            if hit is not None and hit.kind == PROBLEM_MARKERS:
                self._editor.problem_marker_clicked.emit(hit.lineno)
                event.accept()
                return
        super().mousePressEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        self._editor.paint_line_numbers(event)


class CodeEditor(QPlainTextEdit):
    """Editor surface: palette Base, current-line band, placeholder line numbers."""

    # 1-based line of an error marker the user clicked.
    problem_marker_clicked = Signal(int)

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
        self._whitespace_wanted = False
        self._colors_wanted = True
        # Longest line, in characters, and which block holds it. See
        # _track_longest_line for when the length is an upper bound.
        self._longest_chars = 0
        self._longest_block = 0
        self._block_count = 1
        # Connected before the highlighter so a mode change from detection
        # lands before the highlighter reformats the same edit, and so a
        # long line detaches the highlighter before it colors that edit.
        self.document().contentsChange.connect(self._track_longest_line)
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
        self._line_numbers.setAccessibleDescription(
            "\n".join(
                marker.description()
                for _lineno, marker in sorted(
                    self._markers.get(PROBLEM_MARKERS, {}).items()
                )
                if marker.description()
            )
        )
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

    def set_problem_messages(self, messages: Mapping[int, str]) -> None:
        """Mark each line in ``messages`` with an error marker.

        The message is the marker's tooltip and accessible text.
        """
        self.set_line_markers(
            PROBLEM_MARKERS,
            {lineno: ProblemMarker(message) for lineno, message in messages.items()},
        )

    def marker_at(self, pos: QPoint) -> MarkerHit | None:
        """The marker drawn at gutter point ``pos``, or None.

        A marker answers anywhere in its lane on the first row of its line,
        the rect it is painted in.
        """
        layout = self.gutter_layout()
        line_height = self.fontMetrics().height()
        for lane in GUTTER_LANES:
            left, width = layout.lanes[lane.name]
            if not lane.kinds or not left <= pos.x() < left + width:
                continue
            for lineno, top in self._visible_line_tops(QRect(pos, QSize(1, 1))):
                if not top <= pos.y() < top + line_height:
                    continue
                for kind in lane.kinds:
                    marker = self._markers.get(kind, {}).get(lineno)
                    if marker is not None:
                        rect = QRect(left, top, width, line_height)
                        return MarkerHit(kind, lineno, marker, rect)
        return None

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
        """Whether the highlighter is attached: wanted, and no limit applies."""
        return self._highlighter.is_enabled()

    def set_syntax_colors_enabled(self, enabled: bool) -> None:
        """Turn syntax colors on or off. The same as ``set_syntax_colors_wanted``."""
        self.set_syntax_colors_wanted(enabled)

    def syntax_colors_wanted(self) -> bool:
        return self._colors_wanted

    def set_syntax_colors_wanted(self, wanted: bool) -> None:
        """Record that the user wants colors. Long lines still switch them off."""
        self._colors_wanted = wanted
        self._apply_display_options()

    def whitespace_wanted(self) -> bool:
        return self._whitespace_wanted

    def set_whitespace_wanted(self, wanted: bool) -> None:
        """Record the Show Whitespace choice. Long lines still hide the dots."""
        self._whitespace_wanted = wanted
        self._apply_display_options()

    def whitespace_shown(self) -> bool:
        """Whether the document draws tabs and spaces right now."""
        flags = self.document().defaultTextOption().flags()
        return bool(flags & QTextOption.Flag.ShowTabsAndSpaces)

    def display_limits(self) -> tuple[str, ...]:
        """The display choices this document's size overrides, if any.

        ``WHITESPACE_LIMIT`` means Show Whitespace is on but a line is longer
        than ``WHITESPACE_LONG_LINE_CHARS``. ``COLORS_LIMIT`` means colors
        are wanted but a line is longer than ``COLORS_LONG_LINE_CHARS`` or
        the document is over ``JSON_BUSY_BYTES``. The long-line notice will
        read this.
        """
        limits: list[str] = []
        if self._whitespace_wanted and self._whitespace_limited():
            limits.append(WHITESPACE_LIMIT)
        if self._colors_wanted and self._colors_limited():
            limits.append(COLORS_LIMIT)
        return tuple(limits)

    def display_limited(self) -> bool:
        """True when a wanted display option is off because of the document."""
        return bool(self.display_limits())

    def longest_line_chars(self) -> int:
        """Characters in the longest line, as the display gates see it.

        Exact after a full replace or a recount. After an edit shortens the
        longest line without crossing a gate, this keeps the old length, an
        upper bound, rather than walk every block to find the next longest.
        """
        return self._longest_chars

    def _whitespace_limited(self) -> bool:
        return self._longest_chars > WHITESPACE_LONG_LINE_CHARS

    def _colors_limited(self) -> bool:
        # characterCount is a lower bound on the UTF-8 size and costs nothing
        # to read; the exact byte count would mean copying the document.
        over_bytes = self.document().characterCount() - 1 > JSON_BUSY_BYTES
        return self._longest_chars > COLORS_LONG_LINE_CHARS or over_bytes

    def _apply_display_options(self) -> None:
        """Apply the effective display state: what is wanted, less the gates.

        Each option changes only when its effective state differs from what
        the document shows, because turning the whitespace flag lays the
        whole document out again.
        """
        show_whitespace = self._whitespace_wanted and not self._whitespace_limited()
        if show_whitespace != self.whitespace_shown():
            document = self.document()
            option = document.defaultTextOption()
            flags = option.flags()
            flag = QTextOption.Flag.ShowTabsAndSpaces
            option.setFlags(flags | flag if show_whitespace else flags & ~flag)
            document.setDefaultTextOption(option)
        self._highlighter.set_enabled(
            self._colors_wanted and not self._colors_limited()
        )

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
        for lineno, top in self._visible_line_tops(event.rect()):
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
        painter.end()

    def _visible_line_tops(self, rect: QRect) -> Iterator[tuple[int, int]]:
        """1-based line and gutter top of each shown line that meets ``rect``."""
        block = self.firstVisibleBlock()
        top = int(
            self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        )
        bottom = top + int(self.blockBoundingRect(block).height())
        while block.isValid() and top <= rect.bottom():
            if block.isVisible() and bottom >= rect.top():
                yield block.blockNumber() + 1, top
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())

    def _track_longest_line(self, position: int, removed: int, added: int) -> None:
        """Keep the longest line current from the blocks an edit touched.

        A full walk of the document happens only when the whole text is
        replaced or cleared, or when the longest line shrinks across a
        display gate and another line may now be the longest. A keystroke
        reads the block it landed in and nothing else.
        """
        document = self.document()
        count = document.characterCount()
        if position == 0 and added >= count - 1:
            self._rescan_longest_line()
        else:
            self._track_changed_blocks(position, added)
        # A cheap compare: each option moves only when its effective state
        # differs from what the document shows.
        self._apply_display_options()

    def _track_changed_blocks(self, position: int, added: int) -> None:
        document = self.document()
        new_count = document.blockCount()
        delta = new_count - self._block_count
        self._block_count = new_count
        first_block = document.findBlock(position)
        first = first_block.blockNumber()
        last = document.findBlock(position + added).blockNumber()
        changed_chars = 0
        changed_block = first
        block = first_block
        while block.isValid() and block.blockNumber() <= last:
            chars = block.length() - 1
            if chars > changed_chars:
                changed_chars, changed_block = chars, block.blockNumber()
            block = block.next()
        if changed_chars >= self._longest_chars:
            self._longest_chars, self._longest_block = changed_chars, changed_block
            return
        if self._longest_block < first:
            return
        if self._longest_block > last - delta:
            # After the edit: the block kept its text and moved by ``delta``.
            self._longest_block += delta
            return
        # The longest line was edited and is shorter now. If it crossed a
        # gate, another line may now set the display state: recount. If
        # not, the old length stays as an upper bound and no gate moves.
        if _gate_level(changed_chars) != _gate_level(self._longest_chars):
            self._rescan_longest_line()
            return
        self._longest_block = changed_block

    def _rescan_longest_line(self) -> None:
        """Walk every block. Only for a full replace or a gate crossing."""
        document = self.document()
        self._block_count = document.blockCount()
        longest, number = 0, 0
        block = document.firstBlock()
        while block.isValid():
            chars = block.length() - 1
            if chars > longest:
                longest, number = chars, block.blockNumber()
            block = block.next()
        self._longest_chars, self._longest_block = longest, number

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


def _gate_level(chars: int) -> int:
    """How many of the two long-line gates ``chars`` is past."""
    return (chars > WHITESPACE_LONG_LINE_CHARS) + (chars > COLORS_LONG_LINE_CHARS)


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
