"""Code editor with a line-number gutter and syntax colors."""

from bisect import bisect_left
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final, Literal

from PySide6.QtCore import (
    QEvent,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QFontMetrics,
    QHelpEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPalette,
    QPen,
    QPolygonF,
    QResizeEvent,
    QTextBlock,
    QTextCursor,
    QTextDocument,
    QTextFormat,
    QTextLayout,
    QTextOption,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QPlainTextDocumentLayout,
    QPlainTextEdit,
    QTextEdit,
    QToolTip,
    QWidget,
)

from galley.app.file_drop import dropped_paths
from galley.app.fonts import code_font
from galley.app.highlighter import SyntaxHighlighter, SyntaxMode
from galley.app.limits import HTML_BUSY_BYTES, JSON_BUSY_BYTES, XML_BUSY_BYTES
from galley.app.theme import (
    change_color,
    change_tint,
    error_color,
    find_match_colors,
    problem_line_tint,
)
from galley.app.theme import find_highlight_color as brand_find_highlight
from galley.app.theme import line_number_color as theme_line_number_color
from galley.core.diff import ChangeKind, Span
from galley.core.folds import FoldKind, FoldRange, fold_ranges, line_count
from galley.core.formats import HTML, sniff_format

GUTTER_MIN_WIDTH = 50
_GUTTER_PAD = 8
_MARKER_SLOT = 16
# The error marker matches the Problems pane's ⊗: a 12 px ring with a ×.
_ERROR_MARKER_SIZE = 12
_ERROR_MARKER_PEN = 1.6
# How far the × arms start inside the ring's pixel centers. The arms stop
# short of the ring, as the glyph's do.
_ERROR_MARKER_INSET = 4
# The fold arrows, ▾ and ▸, are drawn as triangles in a box this size in
# the middle of the fold lane, so they do not depend on the code font.
_FOLD_ARROW_BOX = 10
# Quiet time after an edit in a folding editor before its ranges are found
# again. Folds open on the edit itself.
_FOLD_RESCAN_MS = 300

PROBLEM_MARKERS = "problem"
CHANGE_MARKERS = "change"
FOLD_MARKERS = "fold"

# Compare's two panes.
CompareSide = Literal["left", "right"]
# Bill's glyphs (#65): "−" is U+2212 and "⇄" is U+21C4.
CHANGE_GLYPHS: Final[dict[ChangeKind, str]] = {
    "added": "+",
    "removed": "\u2212",
    "changed": "~",
    "moved": "\u21c4",
}
# Which panes band each kind's lines (Bill, #65 slice 3b). Removed text is
# only on the left and Added text only on the right. A Moved change bands
# both ends.
CHANGE_TINT_SIDES: Final[dict[ChangeKind, frozenset[CompareSide]]] = {
    "added": frozenset({"right"}),
    "removed": frozenset({"left"}),
    "changed": frozenset({"left", "right"}),
    "moved": frozenset({"left", "right"}),
}

# Spaces per indent level. The Tab key types spaces up to the next multiple
# of this (#91 item 6). 2 matches the fixed "2 spaces" indent while the
# indent picker is Coming soon.
INDENT_WIDTH = 2

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
    # The fold arrows. Only an editor with folding on sets them.
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
class ChangeMarker(LineMarker):
    """Diff marker: the kind's glyph in its ink, so a change is not color alone.

    It goes on the first line of a change's span. ``name`` is the change
    row's spoken name, such as "Changed, orders 2 total, 40 to 45", and is
    the tooltip and accessible text. The band over the whole span comes
    from ``CodeEditor.set_change_tints``, not from the marker. A problem
    marker on the same line is drawn instead (see ``GUTTER_LANES``).
    """

    kind: ChangeKind = "changed"
    name: str = ""

    @property
    def glyph(self) -> str:
        return CHANGE_GLYPHS[self.kind]

    def paint(self, painter: QPainter, rect: QRect, palette: QPalette) -> None:
        painter.setPen(change_color(self.kind, palette))
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), self.glyph)

    def description(self) -> str:
        return self.name


@dataclass(frozen=True)
class FoldMarker(LineMarker):
    """Fold arrow: ▾ while the block is open, ▸ while it is folded.

    It uses the line-number color, and Text while the pointer is on it. It
    never changes the number color or tints the line.
    """

    kind: FoldKind = FoldKind.OBJECT
    folded: bool = False
    hovered: bool = False

    def paint(self, painter: QPainter, rect: QRect, palette: QPalette) -> None:
        role_color = (
            palette.color(QPalette.ColorRole.Text)
            if self.hovered
            else theme_line_number_color(palette)
        )
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(role_color)
        box = _FOLD_ARROW_BOX
        left = rect.left() + (rect.width() - box) / 2
        top = rect.top() + max(0, rect.height() - box) / 2
        if self.folded:
            points = ((3, 1), (3, 9), (8, 5))
        else:
            points = ((1, 3), (9, 3), (5, 8))
        painter.drawPolygon(QPolygonF([QPointF(left + x, top + y) for x, y in points]))

    def description(self) -> str:
        verb = "Unfold" if self.folded else "Fold"
        return f"{verb} {_kind_noun(self.kind)}"


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
        self.setMouseTracking(True)

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
            if hit is not None and hit.kind == FOLD_MARKERS:
                # Alt-click (Option on macOS) toggles the nested blocks too.
                nested = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
                self._editor.toggle_fold(hit.lineno, nested=nested)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        hit = self._editor.marker_at(event.position().toPoint())
        lineno = hit.lineno if hit is not None and hit.kind == FOLD_MARKERS else 0
        self._editor.set_fold_hover(lineno)
        if lineno:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self._editor.set_fold_hover(0)
        self.unsetCursor()
        super().leaveEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        self._editor.paint_line_numbers(event)


class _FoldAwareLayout(QPlainTextDocumentLayout):
    """Plain-text layout that gives a hidden block no size without laying it out.

    QPlainTextDocumentLayout lays a block out before it asks whether the
    block is shown, about 8 µs a line. Qt walks hidden lines when it maps a
    click to text, so a folded 2 MB document would lay out every line it
    hides.
    """

    def blockBoundingRect(self, block: QTextBlock) -> QRectF:
        if not block.isVisible():
            return QRectF()
        return super().blockBoundingRect(block)


class CodeEditor(QPlainTextEdit):
    """Editor surface: palette Base, current-line band, placeholder line numbers."""

    # 1-based line of an error marker the user clicked.
    problem_marker_clicked = Signal(int)
    # The fold ranges or the folded blocks changed.
    folds_changed = Signal()
    # A block was folded (True) or unfolded (False) from the gutter or the
    # summary: the range and the new state.
    fold_toggled = Signal(object, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        document = QTextDocument(self)
        document.setDocumentLayout(_FoldAwareLayout(document))
        self.setDocument(document)
        self._adopting_palette = False
        self._markers: dict[str, dict[int, LineMarker]] = {}
        # Change spans as (first line, last line, kind), 1-based, for this
        # pane's side, and the band per line that markers and spans give,
        # kept until either changes or the palette does.
        self._change_spans: tuple[tuple[int, int, ChangeKind], ...] = ()
        self._tints: dict[int, QColor] | None = None
        # The glyph each spanned line shows when it has no marker of its own,
        # built with the bands.
        self._span_glyphs: dict[int, ChangeMarker] = {}
        self._overlay: QWidget | None = None
        # Folding state. Lines here are 0-based, as in FoldRange.
        self._folding = False
        self._folds: dict[int, FoldRange] = {}
        self._folded: set[int] = set()
        self._fold_hover = 0
        self._fold_summaries: list[tuple[QRect, int]] = []
        self._fold_rescan = QTimer(self)
        self._fold_rescan.setSingleShot(True)
        self._fold_rescan.timeout.connect(self.rescan_folds)
        # Find matches as sorted UTF-16 ranges, and the current one's index.
        # Only the matches in the shown blocks become extra selections.
        self._find_starts: list[int] = []
        self._find_ends: list[int] = []
        self._find_current = -1
        # The matches given extra selections, outlined after each paint.
        self._outlined: list[tuple[int, int]] = []
        # True while setExtraSelections runs. See _highlight_current_line.
        self._setting_selections = False
        self._selections_again = QTimer(self)
        self._selections_again.setSingleShot(True)
        self._selections_again.setInterval(0)
        self._selections_again.timeout.connect(self._highlight_current_line)
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
        self.cursorPositionChanged.connect(self._reveal_cursor_line)
        self.document().contentsChange.connect(self._drop_folds_on_edit)
        # Line bands and find matches exist only for what is on screen, so
        # they are picked again whenever other text comes into view.
        self.verticalScrollBar().valueChanged.connect(self._refresh_shown_selections)
        self.horizontalScrollBar().valueChanged.connect(self._refresh_shown_selections)
        self.document().contentsChanged.connect(self._refresh_shown_selections)
        self.folds_changed.connect(self._refresh_shown_selections)
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
        self._tints = None
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

    # A dragged file or folder is left for the window (#86), so its path is
    # never typed in as text. Other drags, such as text, drop as usual.
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if dropped_paths(event.mimeData()):
            event.ignore()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if dropped_paths(event.mimeData()):
            event.ignore()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        if dropped_paths(event.mimeData()):
            event.ignore()
            return
        super().dropEvent(event)

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
        if kind != FOLD_MARKERS:
            # Fold arrows never tint a line, and a fold toggle resets them.
            self._tints = None
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

    def change_tints(self) -> tuple[tuple[int, int, ChangeKind], ...]:
        """The change spans this pane bands, as ``set_change_tints`` kept them."""
        return self._change_spans

    def set_change_tints(
        self, side: CompareSide, tints: Iterable[tuple[int, int, ChangeKind]]
    ) -> None:
        """Band each ``(first line, last line, kind)`` span, 1-based.

        ``side`` is the pane this editor is. A kind that does not show on
        that side is left out (``CHANGE_TINT_SIDES``). Where spans overlap,
        the later one wins, so a change inside another keeps its own band.
        A problem marker's band wins over any change. Nothing replaces the
        spans but another call; an empty one clears them.
        """
        self._change_spans = tuple(
            (first, last, kind)
            for first, last, kind in tints
            if side in CHANGE_TINT_SIDES[kind]
        )
        self._tints = None
        self._highlight_current_line()
        self._line_numbers.update()

    def select_span(self, span: Span) -> None:
        """Select ``span`` with the native selection and scroll to its start.

        Lines and columns are 1-based characters with an exclusive end, as
        in ``Span``. The selection paints in the Highlight role. Folds that
        hide either end open first. Focus stays where it is.
        """
        self.ensure_line_visible(span.line)
        self.ensure_line_visible(span.end_line)
        cursor = QTextCursor(self.document())
        cursor.setPosition(self._position_of(span.end_line, span.end_column))
        cursor.setPosition(
            self._position_of(span.line, span.column),
            QTextCursor.MoveMode.KeepAnchor,
        )
        self.setTextCursor(cursor)
        self.centerCursor()

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
                    if marker is None and kind == CHANGE_MARKERS:
                        marker = self._span_glyph_markers().get(lineno)
                    if marker is not None:
                        rect = QRect(left, top, width, line_height)
                        return MarkerHit(kind, lineno, marker, rect)
        return None

    def _lane_markers(self, lane: GutterLane) -> list[Mapping[int, LineMarker]]:
        """The markers ``lane`` draws, in priority order. A change span's
        glyphs come right after the change markers."""
        ranked: list[Mapping[int, LineMarker]] = []
        for kind in lane.kinds:
            if kind in self._markers:
                ranked.append(self._markers[kind])
            if kind == CHANGE_MARKERS and self._change_spans:
                ranked.append(self._span_glyph_markers())
        return ranked

    def syntax_mode(self) -> SyntaxMode:
        """``"JSON"``, ``"YAML"``, ``"XML"``, or ``"HTML"``: the colors applied."""
        return self._highlighter.mode()

    def set_syntax_mode(self, mode: SyntaxMode) -> None:
        """Color as ``mode`` from now on and stop guessing from the text.

        Until this is called, the first non-blank character picks the mode:
        ``{`` or ``[`` means JSON, and anything else but ``<`` YAML. A ``<``
        means HTML when the text opens like an HTML document (the doctype,
        or an ``html``, ``head``, or ``body`` tag, after any comments), and
        XML otherwise. :meth:`guess_syntax_mode` goes back to guessing.
        """
        self._syntax_mode_set = True
        changed = mode != self._highlighter.mode()
        self._highlighter.set_mode(mode)
        if changed:
            # XML and HTML have their own busy sizes for colors.
            self._apply_display_options()

    def guess_syntax_mode(self) -> None:
        """Pick the colors from the text again, as before ``set_syntax_mode``."""
        self._syntax_mode_set = False
        self._detect_syntax(0, 0, 0)

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
        the document is over its busy size: ``XML_BUSY_BYTES`` for XML,
        ``HTML_BUSY_BYTES`` for HTML, and ``JSON_BUSY_BYTES`` otherwise. The
        long-line notice will read this.
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
        limit = _COLOR_LIMITS.get(self.syntax_mode(), JSON_BUSY_BYTES)
        over_bytes = self.document().characterCount() - 1 > limit
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

    def reveal_line(self, lineno: int, colno: int = 0) -> None:
        """Move the cursor to ``lineno``, scroll it into view, and focus.

        ``colno`` is a 1-based column in characters; 0 is the line start.
        Folded blocks that hide the line open first.
        """
        self.ensure_line_visible(lineno)
        cursor = self.textCursor()
        cursor.setPosition(self._position_of(lineno, colno))
        self.setTextCursor(cursor)
        self.centerCursor()
        self.setFocus()

    def _position_of(self, lineno: int, colno: int) -> int:
        """Document position of 1-based ``lineno`` and character ``colno``.

        Column 0 or 1 is the line start. A line past the end is the last
        line, and a column past the end of its line is the line's end.
        """
        block = self.document().findBlockByNumber(max(lineno, 1) - 1)
        if not block.isValid():
            block = self.document().lastBlock()
        offset = _utf16_length(block.text()[: max(colno - 1, 0)])
        return block.position() + min(offset, block.length() - 1)

    def cursor_place(self) -> tuple[int, int]:
        """The cursor's 1-based line and column, in characters."""
        cursor = self.textCursor()
        before = cursor.block().text().encode("utf-16-le")
        before = before[: 2 * cursor.positionInBlock()]
        return cursor.blockNumber() + 1, len(before.decode("utf-16-le")) + 1

    def folding_enabled(self) -> bool:
        return self._folding

    def set_folding_enabled(self, enabled: bool) -> None:
        """Let objects and arrays fold. Off by default; only Output folds."""
        if enabled == self._folding:
            return
        self._folding = enabled
        if enabled:
            self.rescan_folds()
        else:
            self._fold_rescan.stop()
            self._replace_folds([])

    def has_fold_ranges(self) -> bool:
        return bool(self._folds)

    def fold_ranges(self) -> list[FoldRange]:
        """Foldable blocks, sorted by start line. Lines are 0-based."""
        return list(self._folds.values())

    def folded_lines(self) -> set[int]:
        """1-based first lines of the folded blocks."""
        return {start + 1 for start in self._folded}

    def set_fold_ranges(self, ranges: Sequence[FoldRange], lines: int) -> None:
        """Use ``ranges``, found for this text, with every block open.

        ``lines`` is the line count of the text they were found in. Qt also
        starts a line at U+2029, so when the count is not the document's,
        the ranges are dropped rather than put on the wrong lines.
        """
        self._fold_rescan.stop()
        if not self._folding:
            return
        if lines != self.document().blockCount():
            ranges = []
        self._replace_folds(ranges)

    def rescan_folds(self) -> None:
        """Find the fold ranges of the current text now. XML has none yet."""
        self._fold_rescan.stop()
        if not self._folding:
            return
        mode = self.syntax_mode()
        if mode in ("XML", "HTML"):
            self._replace_folds([])
            return
        text = self.toPlainText()
        self.set_fold_ranges(fold_ranges(text, mode), line_count(text))

    def fold_line(self, lineno: int, *, nested: bool = False) -> FoldRange | None:
        """Fold the block that starts on 1-based ``lineno``.

        ``nested`` folds the blocks inside it too. Returns the block, or
        None when no block starts there.
        """
        return self._toggle(lineno, fold=True, nested=nested)

    def unfold_line(self, lineno: int, *, nested: bool = False) -> FoldRange | None:
        """Unfold the block that starts on 1-based ``lineno``."""
        return self._toggle(lineno, fold=False, nested=nested)

    def toggle_fold(self, lineno: int, *, nested: bool = False) -> FoldRange | None:
        """Fold an open block, or unfold a folded one, at 1-based ``lineno``."""
        self._flush_fold_rescan()
        fold = (lineno - 1) not in self._folded
        return self._toggle(lineno, fold=fold, nested=nested)

    def fold_at_cursor(self) -> FoldRange | None:
        """Fold the block on the caret line, or the innermost open one around it."""
        self._flush_fold_rescan()
        line = self.textCursor().blockNumber()
        target = self._folds.get(line)
        if target is None or line in self._folded:
            target = self._innermost(line, folded=False)
        if target is None:
            return None
        return self.fold_line(target.start_line + 1)

    def unfold_at_cursor(self) -> FoldRange | None:
        """Unfold the folded block on the caret line, or the one around it."""
        self._flush_fold_rescan()
        line = self.textCursor().blockNumber()
        if line in self._folded:
            return self.unfold_line(line + 1)
        target = self._innermost(line, folded=True)
        if target is None:
            return None
        return self.unfold_line(target.start_line + 1)

    def collapse_all(self) -> int:
        """Fold every block but the root, which stays open. Returns the count.

        Bill's #64 answer: the root's children fold, so the root reads as
        an outline. The blocks inside them fold too, so each opens one level
        at a time.
        """
        self._flush_fold_rescan()
        if not self._folds:
            return 0
        before = self._folded
        self._folded = {start for start, item in self._folds.items() if item.depth}
        self._park_cursor()
        self._refold(before)
        self._sync_fold_markers()
        self.folds_changed.emit()
        return len(self._folded)

    def expand_all(self) -> int:
        """Unfold every folded block. Returns how many were folded."""
        self._flush_fold_rescan()
        count = len(self._folded)
        if not count:
            return 0
        before = self._folded
        self._folded = set()
        self._refold(before)
        self._sync_fold_markers()
        self.folds_changed.emit()
        return count

    def ensure_line_visible(self, lineno: int) -> bool:
        """Unfold the blocks that hide 1-based ``lineno``. True if any did."""
        line = lineno - 1
        opened = [
            start
            for start in self._folded
            if start < line <= self._folds[start].end_line
        ]
        if not opened:
            return False
        before = set(self._folded)
        self._folded.difference_update(opened)
        self._refold(before)
        self._sync_fold_markers()
        self.folds_changed.emit()
        return True

    def set_fold_hover(self, lineno: int) -> None:
        """Draw the fold arrow on 1-based ``lineno`` in the hover ink. 0 is none."""
        if lineno == self._fold_hover:
            return
        markers = self._markers.get(FOLD_MARKERS, {})
        for line, hovered in ((self._fold_hover, False), (lineno, True)):
            marker = markers.get(line)
            if isinstance(marker, FoldMarker):
                markers[line] = replace(marker, hovered=hovered)
        self._fold_hover = lineno
        self._line_numbers.update()

    def fold_summary(self, lineno: int) -> tuple[str, str] | None:
        """What a folded block on 1-based ``lineno`` shows after its text.

        The first part is the ``…`` and, for JSON, the closing bracket
        line. The second is the italic, muted count. None if no block
        starts there.
        """
        item = self._folds.get(lineno - 1)
        if item is None:
            return None
        tail = " …"
        if self.syntax_mode() == "JSON":
            end = self.document().findBlockByNumber(item.end_line)
            tail = f" … {end.text().strip()}"
        return tail, fold_count_text(item)

    def set_find_matches(
        self, starts: Sequence[int], ends: Sequence[int], current: int
    ) -> None:
        """Highlight find matches: sorted UTF-16 ``starts`` and ``ends``.

        ``current`` indexes the current match, or is -1 for none. The
        current match is the accent fill with an underline, so it is not
        told apart by color alone. Matched text is drawn in Text.
        """
        self._find_starts = list(starts)
        self._find_ends = list(ends)
        self._find_current = current
        self._highlight_current_line()

    def clear_find_matches(self) -> None:
        if not self._find_starts:
            return
        self.set_find_matches((), (), -1)

    def find_matches(self) -> list[tuple[int, int]]:
        """Every highlighted match as ``(start, end)``."""
        return list(zip(self._find_starts, self._find_ends, strict=True))

    def current_find_match(self) -> int:
        return self._find_current

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

    def paintEvent(self, event: QPaintEvent) -> None:
        self._fold_summaries = []
        if not self._folded:
            super().paintEvent(event)
        else:
            self._paint_shown_blocks(event.rect())
            self._paint_fold_summaries(event.rect())
        if self._outlined:
            self._paint_find_outlines()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            point = event.position().toPoint()
            for rect, start in self._fold_summaries:
                if rect.contains(point):
                    self.unfold_line(start + 1)
                    event.accept()
                    return
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Tab types spaces. Ctrl+Tab moves focus out, as Tab does elsewhere."""
        key = event.key()
        if self.isReadOnly() or key not in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            super().keyPressEvent(event)
            return
        modifiers = event.modifiers()
        # Qt reports the Control key as Meta on macOS, where Ctrl is ⌘.
        focus_keys = (
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier
        )
        if modifiers & focus_keys:
            forward = key == Qt.Key.Key_Tab and not (
                modifiers & Qt.KeyboardModifier.ShiftModifier
            )
            # QPlainTextEdit's own override keeps focus in an editable editor.
            QWidget.focusNextPrevChild(self, forward)
            event.accept()
            return
        if key == Qt.Key.Key_Tab and modifiers == Qt.KeyboardModifier.NoModifier:
            # A selection over several lines is left alone until Indent
            # ships, rather than replaced by spaces.
            if not self._selection_spans_lines():
                self._insert_indent()
            event.accept()
            return
        super().keyPressEvent(event)

    def _selection_spans_lines(self) -> bool:
        cursor = self.textCursor()
        document = self.document()
        first = document.findBlock(cursor.selectionStart())
        return first != document.findBlock(cursor.selectionEnd())

    def _insert_indent(self) -> None:
        """Replace the selection with spaces up to the next INDENT_WIDTH stop."""
        cursor = self.textCursor()
        start = cursor.selectionStart()
        column = start - self.document().findBlock(start).position()
        cursor.beginEditBlock()
        cursor.insertText(" " * (INDENT_WIDTH - column % INDENT_WIDTH))
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        rect = self.contentsRect()
        self._line_numbers.setGeometry(
            QRect(rect.left(), rect.top(), self.line_number_area_width(), rect.height())
        )
        self.place_overlay()
        self._refresh_shown_selections()

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
            (layout.lanes[lane.name], self._lane_markers(lane))
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
            block = self._next_shown_block(block)
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())

    def _next_shown_block(self, block: QTextBlock) -> QTextBlock:
        """The block after ``block``, past the lines a fold on it hides."""
        item = self._folds.get(block.blockNumber())
        if item is not None and item.start_line in self._folded:
            return self.document().findBlockByNumber(item.end_line + 1)
        return block.next()

    def _paint_shown_blocks(self, area: QRect) -> None:
        """QPlainTextEdit's paint, jumping over the lines each fold hides.

        Qt's own paint steps through every hidden line until the viewport
        is full, which after Collapse all is the whole document. This is
        the same drawing for the lines that are shown: the block
        background, the selections and line bands, the text, and the caret.
        """
        painter = QPainter(self.viewport())
        document = self.document()
        offset = QPointF(self.contentOffset())
        viewport_height = self.viewport().rect().height()
        widest = document.documentLayout().documentSize().width()
        right = int(
            offset.x()
            + max(self.viewport().rect().width(), widest)
            - document.documentMargin()
            + self.cursorWidth()
        )
        clip = QRect(area)
        clip.setRight(min(clip.right(), right))
        painter.setBrushOrigin(offset)
        painter.setClipRect(clip)
        context = self.getPaintContext()
        painter.setPen(context.palette.text().color())
        caret = context.cursorPosition
        keyboard = Qt.TextInteractionFlag.TextSelectableByKeyboard
        show_caret = not self.isReadOnly() or bool(
            self.textInteractionFlags() & keyboard
        )
        block = self.firstVisibleBlock()
        while block.isValid():
            bounds = self.blockBoundingRect(block).translated(offset)
            if block.isVisible() and bounds.bottom() >= clip.top():
                if bounds.top() > clip.bottom():
                    break
                layout = block.layout()
                background = block.blockFormat().background()
                if background.style() != Qt.BrushStyle.NoBrush:
                    painter.fillRect(bounds.toAlignedRect(), background)
                start = block.position()
                length = block.length()
                ranges = _selection_ranges(context.selections, block, layout)
                layout.draw(painter, offset, ranges, QRectF(clip))
                if show_caret and start <= caret < start + length:
                    width = self.cursorWidth()
                    layout.drawCursor(painter, offset, caret - start, width)
            offset.setY(offset.y() + bounds.height())
            if offset.y() > viewport_height:
                break
            block = self._next_shown_block(block)
        painter.end()

    def _paint_find_outlines(self) -> None:
        """A 1 px accent outline around each find match on screen.

        The match fills are under 3:1 against Base and the line tints; the
        outline clears 3:1 against all of them. It traces the fill on each
        row of the match, so a match that wraps gets a box per row.
        """
        painter = QPainter(self.viewport())
        painter.setPen(QPen(find_match_colors(self.palette()).edge, 0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        document = self.document()
        offset = self.contentOffset()
        for start, end in self._outlined:
            block = document.findBlock(start)
            while block.isValid() and block.position() < end:
                layout = block.layout()
                if block.isVisible() and layout.lineCount():
                    rect = self.blockBoundingGeometry(block).translated(offset)
                    origin = rect.topLeft() + layout.position()
                    first = max(start, block.position()) - block.position()
                    last = min(end, block.position() + block.length() - 1)
                    last -= block.position()
                    # Only the rows the match is on, for one very long line.
                    row = layout.lineForTextPosition(first).lineNumber()
                    while 0 <= row < layout.lineCount():
                        line = layout.lineAt(row)
                        if line.textStart() >= last:
                            break
                        row += 1
                        left = max(first, line.textStart())
                        right = min(last, line.textStart() + line.textLength())
                        # Right-to-left text puts the end left of the start.
                        x1, x2 = sorted(
                            (line.cursorToX(left)[0], line.cursorToX(right)[0])
                        )
                        if x2 <= x1:
                            # Nothing to trace, such as a lone combining mark.
                            continue
                        # Aligned outward, as Qt fills the match.
                        box = QRectF(x1, line.y(), x2 - x1, line.height())
                        box = box.translated(origin).toAlignedRect()
                        painter.drawRect(box.adjusted(0, 0, -1, -1))
                block = block.next()
        painter.end()

    def _paint_fold_summaries(self, area: QRect) -> None:
        """Paint ``… }`` and the italic count after each shown folded block.

        The summary is painted, never inserted, so Copy and Save see the
        real text.
        """
        painter = QPainter(self.viewport())
        text_ink = self.palette().color(QPalette.ColorRole.Text)
        muted_ink = self.line_number_color()
        font = self.font()
        italic = QFont(font)
        italic.setItalic(True)
        metrics = self.fontMetrics()
        italic_metrics = QFontMetrics(italic)
        offset = self.contentOffset()
        block = self.firstVisibleBlock()
        while block.isValid():
            geometry = self.blockBoundingGeometry(block).translated(offset)
            if geometry.top() > area.bottom():
                break
            start = block.blockNumber()
            if block.isVisible() and start in self._folded:
                summary = self.fold_summary(start + 1)
                layout = block.layout()
                if summary is not None and layout.lineCount():
                    last = layout.lineAt(layout.lineCount() - 1)
                    x = int(offset.x() + last.x() + last.naturalTextWidth())
                    top = int(geometry.top() + last.y())
                    baseline = int(geometry.top() + last.y() + last.ascent())
                    tail, count = summary
                    painter.setFont(font)
                    painter.setPen(text_ink)
                    painter.drawText(x, baseline, tail)
                    count_x = x + metrics.horizontalAdvance(tail + " ")
                    painter.setFont(italic)
                    painter.setPen(muted_ink)
                    painter.drawText(count_x, baseline, count)
                    right = count_x + italic_metrics.horizontalAdvance(count)
                    height = int(last.height())
                    self._fold_summaries.append(
                        (QRect(x, top, right - x, height), start)
                    )
            block = self._next_shown_block(block)
        painter.end()

    def _replace_folds(self, ranges: Sequence[FoldRange]) -> None:
        if self._folded:
            before = self._folded
            self._folded = set()
            self._refold(before)
        self._folds = {item.start_line: item for item in ranges}
        self._sync_fold_markers()
        self.folds_changed.emit()

    def _drop_folds_on_edit(self, position: int, _removed: int, added: int) -> None:
        """An edit opens every fold, and the ranges are found again."""
        if not self._folding:
            return
        whole = position == 0 and added >= self.document().characterCount() - 1
        if self._folded:
            if whole:
                # New blocks start shown.
                self._folded.clear()
            else:
                # The edit moved lines, so the old ranges no longer say
                # which blocks are hidden.
                self._folded.clear()
                self._show_every_block()
            self._sync_fold_markers()
            self.folds_changed.emit()
        self._fold_rescan.start(0 if whole else _FOLD_RESCAN_MS)

    def _flush_fold_rescan(self) -> None:
        """Find the ranges now if an edit is still waiting for them."""
        if self._fold_rescan.isActive():
            self.rescan_folds()

    def _toggle(self, lineno: int, *, fold: bool, nested: bool) -> FoldRange | None:
        self._flush_fold_rescan()
        item = self._folds.get(lineno - 1)
        if item is None:
            return None
        starts = [item.start_line]
        if nested:
            starts.extend(
                start
                for start in self._folds
                if item.start_line < start <= item.end_line
            )
        before = set(self._folded)
        if fold:
            self._folded.update(starts)
            self._park_cursor()
        else:
            self._folded.difference_update(starts)
        if self._folded == before:
            return item
        self._refold(before)
        self._sync_fold_markers()
        self.folds_changed.emit()
        self.fold_toggled.emit(item, fold)
        return item

    def _innermost(self, line: int, *, folded: bool) -> FoldRange | None:
        """The innermost block around 0-based ``line`` in the given state."""
        found: FoldRange | None = None
        for start, item in self._folds.items():
            if start > line:
                break
            if item.end_line >= line and (start in self._folded) == folded:
                found = item
        return found

    def _park_cursor(self) -> None:
        """Move the caret out of the lines about to be hidden.

        It goes to the end of the first line of the outermost fold that
        hides it.
        """
        line = self.textCursor().blockNumber()
        owners = [
            start
            for start in self._folded
            if start < line <= self._folds[start].end_line
        ]
        if not owners:
            return
        block = self.document().findBlockByNumber(min(owners))
        cursor = self.textCursor()
        cursor.setPosition(block.position() + block.length() - 1)
        self.setTextCursor(cursor)

    def _reveal_cursor_line(self) -> None:
        """A caret that lands in hidden lines unfolds what hides them."""
        if not self._folded or self.textCursor().block().isVisible():
            return
        self.ensure_line_visible(self.textCursor().blockNumber() + 1)

    def _refold(self, before: set[int]) -> None:
        """Hide and show blocks to go from the ``before`` folds to the current.

        Only the lines whose state changes are touched, and the layout is
        told once for the span they cover.
        """
        old = self._hidden_spans(before)
        new = self._hidden_spans(self._folded)
        changes = [(span, False) for span in _subtract(new, old)]
        changes += [(span, True) for span in _subtract(old, new)]
        if not changes:
            return
        document = self.document()
        # Unbound methods: this loop runs once a line, 100,000 times for
        # Collapse all on 2 MB.
        set_visible, next_block = QTextBlock.setVisible, QTextBlock.next
        for (first, last), visible in changes:
            block = document.findBlockByNumber(first)
            for _line in range(first, last + 1):
                set_visible(block, visible)
                block = next_block(block)
        first = min(span[0] for span, _visible in changes)
        last = max(span[1] for span, _visible in changes)
        self._mark_lines_dirty(first, last)

    def _hidden_spans(self, folded: set[int]) -> list[tuple[int, int]]:
        """The 0-based lines ``folded`` hides, as sorted, merged spans."""
        spans: list[tuple[int, int]] = []
        for start in sorted(folded):
            end = self._folds[start].end_line
            if spans and start <= spans[-1][1]:
                # Inside the last span, or opening on its last line.
                if end > spans[-1][1]:
                    spans[-1] = (spans[-1][0], end)
                continue
            spans.append((start + 1, end))
        return spans

    def _show_every_block(self) -> None:
        document = self.document()
        block = document.firstBlock()
        changed = False
        while block.isValid():
            if not block.isVisible():
                block.setVisible(True)
                changed = True
            block = block.next()
        if changed:
            self._mark_lines_dirty(0, document.blockCount() - 1)

    def _mark_lines_dirty(self, first: int, last: int) -> None:
        """Have the layout measure lines ``first`` to ``last`` again."""
        document = self.document()
        first_block = document.findBlockByNumber(first)
        end_block = document.findBlockByNumber(min(last, document.blockCount() - 1))
        span = end_block.position() + end_block.length() - first_block.position()
        document.markContentsDirty(first_block.position(), span)
        self.viewport().update()
        self._line_numbers.update()

    def _sync_fold_markers(self) -> None:
        folded = self._folded
        self._fold_hover = 0
        self.set_line_markers(
            FOLD_MARKERS,
            {
                start + 1: _FOLD_MARKERS[item.kind, start in folded]
                for start, item in self._folds.items()
            },
        )

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
        mode = _mode_for(document.characterAt(index))
        if mode == "XML" and _opens_like_html(document, index):
            mode = "HTML"
        # An edit that replaced everything is reformatted by the highlighter
        # right after this slot, so a full pass here would be wasted.
        whole = position == 0 and added >= count - 1
        changed = mode != self._highlighter.mode()
        self._highlighter.set_mode(mode, rehighlight=not whole)
        if changed:
            # XML and HTML have their own busy sizes for colors.
            self._apply_display_options()

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
        """The current-line band, then line bands and find matches on screen.

        A band per tinted line in the whole document would make every caret
        move cost as much as the number of tinted lines: Qt walks all extra
        selections for each block it paints. Lines off screen get theirs
        when they scroll into view.
        """
        if self._setting_selections:
            # setExtraSelections lays blocks out, which can move the scroll
            # bar and call back here. Qt is still walking the old selections
            # then, and a nested call frees them under it: a crash on macOS
            # and Windows CI. Build them again once the outer call returns.
            self._selections_again.start()
            return
        selections: list[QTextEdit.ExtraSelection] = []
        tints = self._line_tints()
        current = self.textCursor().blockNumber() + 1
        if current not in tints:
            band = self.palette().color(QPalette.ColorRole.Midlight)
            selections.append(self._line_band(self.textCursor(), band))
        if tints:
            document = self.document()
            for lineno, _top in self._visible_line_tops(self.viewport().rect()):
                tint = tints.get(lineno)
                if tint is not None:
                    block = document.findBlockByNumber(lineno - 1)
                    selections.append(self._block_band(block, tint))
        selections.extend(self._find_selections())
        self._setting_selections = True
        try:
            self.setExtraSelections(selections)
        finally:
            self._setting_selections = False

    def _refresh_shown_selections(self) -> None:
        """Pick the bands and matches to draw again after a scroll, resize,
        fold, or edit."""
        if self._find_starts or self._line_tints():
            self._highlight_current_line()

    def _find_selections(self) -> list[QTextEdit.ExtraSelection]:
        """Extra selections for the matches in the blocks on screen.

        Qt walks every extra selection for each block it paints, so a
        selection per match would make a common letter in a 2 MB document
        slow to scroll. Only shown blocks count, so a match inside a fold
        is skipped, and the first and last blocks are cut at the viewport's
        corners, so one very long line gives only the part on screen.
        """
        self._outlined = []
        if not self._find_starts:
            return []
        colors = find_match_colors(self.palette())
        starts = self._find_starts
        ends = self._find_ends
        document = self.document()
        # Matches wait for the bar to search again after an edit, so they
        # can run past a shorter document until then.
        limit = document.characterCount() - 1
        selections: list[QTextEdit.ExtraSelection] = []
        current: QTextEdit.ExtraSelection | None = None
        for first, last in self._shown_spans():
            index = bisect_left(ends, first + 1)
            while index < len(starts) and starts[index] <= last:
                if ends[index] > limit:
                    break
                selection = QTextEdit.ExtraSelection()
                cursor = QTextCursor(document)
                cursor.setPosition(starts[index])
                cursor.setPosition(ends[index], QTextCursor.MoveMode.KeepAnchor)
                selection.cursor = cursor
                selection.format.setForeground(colors.ink)
                if index == self._find_current:
                    selection.format.setBackground(colors.current)
                    selection.format.setFontUnderline(True)
                    selection.format.setUnderlineColor(colors.ink)
                    current = selection
                else:
                    selection.format.setBackground(colors.other)
                    selections.append(selection)
                index += 1
        if current is not None:
            # Last, so it paints over a neighbor.
            selections.append(current)
        self._outlined = [
            (selection.cursor.selectionStart(), selection.cursor.selectionEnd())
            for selection in selections
        ]
        return selections

    def _shown_spans(self) -> list[tuple[int, int]]:
        """UTF-16 ``(first, last)`` of each shown block on screen.

        Runs of adjacent blocks are merged. The first span starts at the
        viewport's top-left character and the last ends at its bottom-right,
        read from those blocks' own layouts: ``cursorForPosition`` walks
        the document and costs tens of milliseconds after a fold.
        """
        area = self.viewport().rect()
        offset = self.contentOffset()
        spans: list[tuple[int, int]] = []
        shown: list[tuple[QTextBlock, QRectF]] = []
        block = self.firstVisibleBlock()
        while block.isValid():
            rect = self.blockBoundingGeometry(block).translated(offset)
            if rect.top() > area.bottom():
                break
            if block.isVisible():
                shown.append((block, rect))
                first = block.position()
                last = first + block.length()
                if spans and spans[-1][1] == first:
                    spans[-1] = (spans[-1][0], last)
                else:
                    spans.append((first, last))
            block = self._next_shown_block(block)
        if not spans:
            return []
        top = _position_at(*shown[0], QPointF(area.topLeft()))
        bottom = _position_at(*shown[-1], QPointF(area.bottomRight()))
        spans[0] = (max(spans[0][0], top), spans[0][1])
        spans[-1] = (spans[-1][0], min(spans[-1][1], bottom + 1))
        return [(first, last) for first, last in spans if first <= last]

    def _line_tints(self) -> dict[int, QColor]:
        """Band color per 1-based line, built once per marker or span change.

        Change spans come first, the later span winning, and then each
        marker kind in priority order, so a problem's band wins over a
        change's.
        """
        if self._tints is None:
            self._tints = self._build_line_tints()
        return self._tints

    def _span_glyph_markers(self) -> dict[int, ChangeMarker]:
        """The change glyph for every line a change span covers.

        A tint alone would tell a change's kind by color on every line
        but the first, where its ``ChangeMarker`` is. Each spanned line
        repeats the kind's glyph, named as the span's own marker, so the
        kind reads by shape on every line. A line's own marker still wins.
        """
        self._line_tints()
        return self._span_glyphs

    def _build_line_tints(self) -> dict[int, QColor]:
        palette = self.palette()
        named = self._markers.get(CHANGE_MARKERS, {})
        spans: dict[int, QColor] = {}
        glyphs: dict[int, ChangeMarker] = {}
        for first, last, kind in self._change_spans:
            color = change_tint(kind, palette)
            own = named.get(first)
            glyph = ChangeMarker(
                kind, own.name if isinstance(own, ChangeMarker) else ""
            )
            for lineno in range(max(first, 1), last + 1):
                spans[lineno] = color
                glyphs[lineno] = glyph
        self._span_glyphs = glyphs
        tints: dict[int, QColor] = {}
        for kind in MARKER_KINDS:
            if kind == FOLD_MARKERS:
                # Fold arrows never tint a line, and there can be thousands.
                continue
            for lineno, marker in self._markers.get(kind, {}).items():
                if lineno in tints:
                    continue
                tint = marker.line_tint(palette)
                if tint is not None:
                    tints[lineno] = tint
        spans.update(tints)
        return spans

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

    def _block_band(self, block: QTextBlock, color: QColor) -> QTextEdit.ExtraSelection:
        """A band over every row of ``block``, wrapped or not.

        A full-width selection with no extent bands only the row its cursor
        is on, so a soft-wrapped line would be tinted on its first row only.
        Selecting the whole block, line break included, bands each row it
        spans out to the right edge. Qt skips the next block, where the
        selection ends at position 0.
        """
        selection = self._line_band(QTextCursor(block), color)
        cursor = selection.cursor
        cursor.setPosition(
            block.position() + block.length() - 1, QTextCursor.MoveMode.KeepAnchor
        )
        cursor.movePosition(
            QTextCursor.MoveOperation.NextCharacter, QTextCursor.MoveMode.KeepAnchor
        )
        return selection


_FOLD_MARKERS: dict[tuple[FoldKind, bool], FoldMarker] = {
    (kind, folded): FoldMarker(kind, folded)
    for kind in FoldKind
    for folded in (False, True)
}


def _selection_ranges(
    selections: Sequence[object], block: QTextBlock, layout: QTextLayout
) -> list[QTextLayout.FormatRange]:
    """The paint context's selections and line bands that fall in ``block``."""
    start = block.position()
    length = block.length()
    ranges: list[QTextLayout.FormatRange] = []
    for selection in selections:
        cursor = selection.cursor
        first = cursor.selectionStart() - start
        last = cursor.selectionEnd() - start
        item = QTextLayout.FormatRange()
        item.format = selection.format
        if first < length and last > 0 and last > first:
            item.start = first
            item.length = last - first
        elif (
            not cursor.hasSelection()
            and selection.format.hasProperty(QTextFormat.Property.FullWidthSelection)
            and block.contains(cursor.position())
        ):
            line = layout.lineForTextPosition(cursor.position() - start)
            item.start = line.textStart()
            item.length = line.textLength()
            if item.start + item.length == length - 1:
                item.length += 1
        else:
            continue
        ranges.append(item)
    return ranges


def _position_at(block: QTextBlock, rect: QRectF, point: QPointF) -> int:
    """Document position nearest ``point`` in ``block`` drawn at ``rect``.

    The line is found by a binary search, so a block wrapped into thousands
    of lines costs a few calls.
    """
    layout = block.layout()
    count = layout.lineCount()
    if count == 0:
        return block.position()
    origin = rect.topLeft() + layout.position()
    y = point.y() - origin.y()
    lo, hi = 0, count - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if layout.lineAt(mid).y() <= y:
            lo = mid
        else:
            hi = mid - 1
    line = layout.lineAt(lo)
    return block.position() + line.xToCursor(point.x() - origin.x())


def _subtract(
    spans: list[tuple[int, int]], minus: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """The lines in ``spans`` that are not in ``minus``. Both are sorted spans."""
    result: list[tuple[int, int]] = []
    index = 0
    for first, last in spans:
        while index < len(minus) and minus[index][1] < first:
            index += 1
        current = first
        probe = index
        while probe < len(minus) and minus[probe][0] <= last:
            if minus[probe][0] > current:
                result.append((current, minus[probe][0] - 1))
            current = max(current, minus[probe][1] + 1)
            probe += 1
        if current <= last:
            result.append((current, last))
    return result


# Busy sizes for syntax colors, by mode. Other modes use JSON_BUSY_BYTES.
_COLOR_LIMITS: dict[str, int] = {"XML": XML_BUSY_BYTES, "HTML": HTML_BUSY_BYTES}
# How much of the opening a ``<`` document reads to tell HTML from XML.
_SNIFF_BLOCKS = 20
_SNIFF_CHARS = 4096


def _opens_like_html(document: QTextDocument, index: int) -> bool:
    """True when the text from ``index`` opens like an HTML document.

    Only the first blocks are read, so a keystroke stays cheap. The rule is
    the format table's sniff, which skips leading comments.
    """
    block = document.findBlock(index)
    parts: list[str] = []
    size = 0
    while block.isValid() and len(parts) < _SNIFF_BLOCKS and size < _SNIFF_CHARS:
        text = block.text()
        parts.append(text)
        size += len(text)
        block = block.next()
    return sniff_format("\n".join(parts)) is HTML


def _mode_for(first: str) -> SyntaxMode:
    """The colors a document whose first non-blank character is ``first`` gets."""
    if first in "{[":
        return "JSON"
    if first == "<":
        return "XML"
    return "YAML"


def fold_count_text(item: FoldRange) -> str:
    """``1 key``, ``12 keys``, ``1 item`` or ``3 items``."""
    noun = "key" if item.kind is FoldKind.OBJECT else "item"
    return f"{item.count} {noun}" + ("" if item.count == 1 else "s")


def fold_status_text(item: FoldRange, folded: bool) -> str:
    """Status-bar text for one fold: ``Folded object at line 8, 12 keys``."""
    verb = "Folded" if folded else "Unfolded"
    noun = _kind_noun(item.kind)
    return f"{verb} {noun} at line {item.start_line + 1}, {fold_count_text(item)}"


def _kind_noun(kind: FoldKind) -> str:
    return "object" if kind is FoldKind.OBJECT else "array"


def _gate_level(chars: int) -> int:
    """How many of the two long-line gates ``chars`` is past."""
    return (chars > WHITESPACE_LONG_LINE_CHARS) + (chars > COLORS_LONG_LINE_CHARS)


def _first_marker(
    ranked: Sequence[Mapping[int, LineMarker]], lineno: int
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


def _utf16_length(text: str) -> int:
    """Qt counts positions in UTF-16 units, so astral characters count twice."""
    return len(text) + sum(1 for char in text if ord(char) > 0xFFFF)
