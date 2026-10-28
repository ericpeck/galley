"""Changes pane: Compare's list of differences (#65 slice 3c).

The rows come from 3a's ``CompareReport``, built in the worker. A
``ChangesModel`` lays them out as a flat list: change rows, muted info rows,
and group rows whose children show while the group is expanded. The list
view paints each row with ``ChangesDelegate``. Next and previous change
walk the rows in engine order: a collapsed group is one stop, an expanded
one is stepped through, info rows are skipped, and neither end wraps.

The pane is a results-panel page; Compare mode adds it as the Changes tab
and wires the jump, Esc, and Compare Again signals (3d). Selection and the
keyboard-focus outline are painted with the Problems list's code (#68).
"""

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import (
    QAbstractListModel,
    QEvent,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QPointF,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFocusEvent,
    QFont,
    QFontMetrics,
    QKeyEvent,
    QPainter,
    QPalette,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QLabel,
    QListView,
    QPushButton,
    QStackedWidget,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from galley.app.editor import CHANGE_GLYPHS
from galley.app.results_panel import (
    ROW_MARGINS,
    keyboard_focus_after,
    paint_row_state,
    readable_on,
)
from galley.app.style import secondary_button_style
from galley.app.theme import change_color, selected_problem_palette, visible_edge
from galley.core.compare_report import (
    KIND_LABELS,
    ChangeRow,
    CompareReport,
    GroupRow,
    InfoRow,
    ReportRow,
)
from galley.core.diff import ChangeKind

# Bill's #65 wording (3c UI criteria and the PR 3 addendum).
NO_DIFFERENCES = "No differences."
FIX_ERRORS = "Fix errors to compare."
ADD_SECOND_DOCUMENT = "Add a second document to compare."
COMPARISON_CANCELED = "Comparison canceled."
COMPARISON_STOPPED = (
    "The comparison stopped unexpectedly. Your documents weren't changed."
)
COMPARE_AGAIN = "Compare Again"
# The count's singular and plural, for the results panel's badge.
CHANGES_NOUNS = ("difference", "differences")

_CHECK_GLYPH = "✓"
_INFO_GLYPH = "ⓘ"

# Model roles past the standard ones.
ROW_ROLE = Qt.ItemDataRole.UserRole + 1
DEPTH_ROLE = Qt.ItemDataRole.UserRole + 2
EXPANDED_ROLE = Qt.ItemDataRole.UserRole + 3

# A group's children sit this far right of the group row.
_CHILD_INDENT = 20
# The glyph column, the disclosure arrow box, and the gaps between columns.
_GLYPH_WIDTH = 16
_ARROW_BOX = 10
_GAP = 8
_ROW_PADDING = 12
# The least room a change's detail keeps when its path is long.
_DETAIL_MIN_CHARS = 16
_MIN_ROW_HEIGHT = 28


def difference_count_text(count: int) -> str:
    """``1 difference`` or ``12 differences``, as the badge says it."""
    singular, plural = CHANGES_NOUNS
    return f"{count:,} {singular if count == 1 else plural}"


class ChangesState(Enum):
    """What the pane shows: the rows, or one of the messages."""

    ROWS = "rows"
    NO_DIFFERENCES = "no differences"
    EMPTY_RIGHT = "empty right"
    ERRORS = "errors"
    CANCELED = "canceled"
    STOPPED = "stopped"


_MESSAGES = {
    ChangesState.NO_DIFFERENCES: NO_DIFFERENCES,
    ChangesState.EMPTY_RIGHT: ADD_SECOND_DOCUMENT,
    ChangesState.ERRORS: FIX_ERRORS,
    ChangesState.CANCELED: COMPARISON_CANCELED,
    ChangesState.STOPPED: COMPARISON_STOPPED,
}


@dataclass(frozen=True)
class _Entry:
    """One shown row. ``group`` is the index in ``report.rows`` of the group
    it belongs to or is, and ``None`` for a top-level change or info row."""

    row: ChangeRow | GroupRow | InfoRow
    depth: int
    group: int | None


class ChangesModel(QAbstractListModel):
    """The report's rows as a flat list, groups collapsed until expanded."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._report: CompareReport | None = None
        self._entries: list[_Entry] = []
        self._expanded: set[int] = set()
        self._stops: list[int] | None = None
        self._line_texts: tuple[str, ...] = ()

    def set_report(self, report: CompareReport | None) -> None:
        """Show ``report``'s rows with every group collapsed."""
        self.beginResetModel()
        self._report = report
        self._expanded = set()
        rows: tuple[ReportRow, ...] = () if report is None else report.rows
        self._entries = [
            _Entry(row, 0, index if isinstance(row, GroupRow) else None)
            for index, row in enumerate(rows)
        ]
        self._stops = None
        self._line_texts = _line_texts(rows)
        self.endResetModel()

    def line_texts(self) -> tuple[str, ...]:
        """Every distinct line-column text, group children included, so the
        column can fit the widest one. A new tuple for each report."""
        return self._line_texts

    def report(self) -> CompareReport | None:
        return self._report

    def count(self) -> int:
        """Every change, grouped ones included. Info rows are not counted."""
        return 0 if self._report is None else self._report.count

    def rowCount(
        self, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> int:
        if parent is not None and parent.isValid():
            return 0
        return len(self._entries)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        if not index.isValid() or not 0 <= index.row() < len(self._entries):
            return None
        entry = self._entries[index.row()]
        if role in (
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.AccessibleTextRole,
        ):
            if isinstance(entry.row, GroupRow):
                state = "expanded" if entry.group in self._expanded else "collapsed"
                return f"{entry.row.accessible_name}, {state}"
            return entry.row.accessible_name
        if role == Qt.ItemDataRole.ToolTipRole:
            return tooltip_text(entry.row)
        if role == ROW_ROLE:
            return entry.row
        if role == DEPTH_ROLE:
            return entry.depth
        if role == EXPANDED_ROLE:
            if isinstance(entry.row, GroupRow):
                return entry.group in self._expanded
            return None
        return None

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        """Info rows are notes: they can't be selected or reached by arrow keys."""
        if not index.isValid() or isinstance(self.row_at(index.row()), InfoRow):
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def row_at(self, row: int) -> ChangeRow | GroupRow | InfoRow | None:
        if 0 <= row < len(self._entries):
            return self._entries[row].row
        return None

    def group_row_of(self, row: int) -> int | None:
        """The model row of the group that row ``row`` is a child of."""
        if not 0 <= row < len(self._entries) or self._entries[row].depth == 0:
            return None
        for above in range(row - 1, -1, -1):
            if self._entries[above].depth == 0:
                return above
        return None

    def is_expanded(self, row: int) -> bool:
        return self.data(self.index(row), EXPANDED_ROLE) is True

    def set_expanded(self, row: int, expanded: bool) -> None:
        """Expand or collapse the group at model row ``row``."""
        if not 0 <= row < len(self._entries):
            return
        entry = self._entries[row]
        group = entry.row
        if not isinstance(group, GroupRow) or entry.group is None:
            return
        if (entry.group in self._expanded) == expanded:
            return
        children = len(group.children)
        if expanded:
            self.beginInsertRows(QModelIndex(), row + 1, row + children)
            self._entries[row + 1 : row + 1] = [
                _Entry(child, 1, entry.group) for child in group.children
            ]
            self._expanded.add(entry.group)
            self.endInsertRows()
        else:
            self.beginRemoveRows(QModelIndex(), row + 1, row + children)
            del self._entries[row + 1 : row + 1 + children]
            self._expanded.discard(entry.group)
            self.endRemoveRows()
        self._stops = None
        changed = self.index(row)
        self.dataChanged.emit(
            changed,
            changed,
            [
                EXPANDED_ROLE,
                Qt.ItemDataRole.DisplayRole,
                Qt.ItemDataRole.AccessibleTextRole,
            ],
        )

    def next_stop(self, row: int) -> int | None:
        """The next change stop after model row ``row``, or ``None`` at the end.

        ``row`` is -1 before the first row.
        """
        stops = self._nav_stops()
        position = bisect_right(stops, row)
        return stops[position] if position < len(stops) else None

    def previous_stop(self, row: int) -> int | None:
        """The change stop before model row ``row``, or ``None`` at the start."""
        stops = self._nav_stops()
        position = bisect_left(stops, row)
        return stops[position - 1] if position > 0 else None

    def _nav_stops(self) -> list[int]:
        """Change rows and collapsed groups. Expanded groups and info rows
        are not stops; an expanded group's change rows are."""
        if self._stops is None:
            stops: list[int] = []
            for index, entry in enumerate(self._entries):
                row = entry.row
                if isinstance(row, ChangeRow):
                    stops.append(index)
                elif isinstance(row, GroupRow) and entry.group not in self._expanded:
                    stops.append(index)
            self._stops = stops
        return self._stops


def _line_texts(rows: tuple[ReportRow, ...]) -> tuple[str, ...]:
    texts: set[str] = set()
    for row in rows:
        children = row.children if isinstance(row, GroupRow) else ()
        texts.update(line_text(shown) for shown in (row, *children))
    texts.discard("")
    return tuple(sorted(texts))


@dataclass(frozen=True)
class RowColors:
    """The colors one row paints with, on ``background``."""

    background: QColor
    text: QColor
    muted: QColor
    glyph: QColor


def row_colors(
    palette: QPalette, *, selected: bool, kind: ChangeKind | None = None
) -> RowColors:
    """Text at 4.5:1 and the kind glyph at 3:1 on the row's background.

    The background is the selection fill on a selected row and the window
    elsewhere, as for the Problems rows. Text roles with alpha are made
    opaque; muted text that would fall under 4.5:1 uses full text instead.
    A kind ink that misses 3:1 on the fill moves toward the text color, so
    the glyph keeps its hue where it can. Info and group rows have no kind:
    their glyph is muted or text.
    """
    tinted = selected_problem_palette(palette)
    if selected:
        background = tinted.color(QPalette.ColorRole.Highlight)
    else:
        background = palette.color(QPalette.ColorRole.Window)
    text = readable_on(background, tinted, QPalette.ColorRole.WindowText)
    muted = readable_on(background, tinted, QPalette.ColorRole.PlaceholderText)
    if kind is None:
        glyph = text
    else:
        glyph = visible_edge(change_color(kind, palette), (background,), text)
    return RowColors(background, text, muted, glyph)


class ChangesDelegate(QStyledItemDelegate):
    """Paints one row on a single line.

    A change row: glyph, label, key path, ``old → new`` (cut with "…"), and
    the line number at the right. An info row: a muted ⓘ and its text. A
    group row: a disclosure arrow, its text, and its first line.
    """

    def __init__(self, view: "ChangesList") -> None:
        super().__init__(view)
        self._view = view
        # (line texts, font key, width) for the last report and font.
        self._line_column: tuple[tuple[str, ...], str, int] | None = None

    def sizeHint(
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> QSize:
        del index
        return QSize(0, row_height(option.fontMetrics))

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        row = index.data(ROW_ROLE)
        if row is None:
            return
        rect = QRect(option.rect)
        palette = self._view.row_palette()
        current = index.row() == self._view.currentIndex().row()
        selected = current and not isinstance(row, InfoRow)
        focused = selected and self._view.outline_visible()
        painter.save()
        paint_row_state(painter, rect, palette, selected=selected, focused=focused)
        kind = row.kind if isinstance(row, ChangeRow) else None
        colors = row_colors(palette, selected=selected, kind=kind)
        depth = index.data(DEPTH_ROLE) or 0
        left = rect.left() + ROW_MARGINS[0] + depth * _CHILD_INDENT
        right = rect.right() - ROW_MARGINS[2]
        text_rect = QRect(left, rect.top(), max(0, right - left), rect.height())
        font = QFont(option.font)
        if isinstance(row, ChangeRow):
            self._paint_change(painter, text_rect, font, row, colors)
        elif isinstance(row, GroupRow):
            expanded = index.data(EXPANDED_ROLE) is True
            self._paint_group(painter, text_rect, font, row, colors, expanded)
        else:
            self._paint_info(painter, text_rect, font, row, colors)
        painter.restore()

    def _paint_change(
        self,
        painter: QPainter,
        rect: QRect,
        font: QFont,
        row: ChangeRow,
        colors: RowColors,
    ) -> None:
        bold = QFont(font)
        bold.setBold(True)
        bold_metrics = QFontMetrics(bold)
        metrics = QFontMetrics(font)
        line_width = self._line_width(font)
        x = rect.left()
        painter.setFont(bold)
        painter.setPen(colors.glyph)
        _draw(painter, x, rect, _GLYPH_WIDTH, CHANGE_GLYPHS[row.kind], center=True)
        x += _GLYPH_WIDTH + _GAP
        label_width = max(
            bold_metrics.horizontalAdvance(label) for label in KIND_LABELS.values()
        )
        painter.setPen(colors.text)
        _draw(painter, x, rect, label_width, row.label)
        x += label_width + _GAP
        painter.setFont(font)
        line_left = rect.right() - line_width
        room = max(0, line_left - _GAP - x)
        detail = change_detail(row)
        path_room = path_width_for(metrics, room, detail)
        # Cut in the middle, so the last key, the one that changed, stays.
        path = metrics.elidedText(
            row.path_text, Qt.TextElideMode.ElideMiddle, path_room
        )
        path_width = min(metrics.horizontalAdvance(path), path_room)
        _draw(painter, x, rect, path_width, path)
        x += path_width + _GAP
        if detail:
            painter.setPen(colors.muted)
            width = max(0, line_left - _GAP - x)
            shown = metrics.elidedText(detail, Qt.TextElideMode.ElideRight, width)
            _draw(painter, x, rect, width, shown)
        painter.setPen(colors.muted)
        _draw(painter, line_left, rect, line_width, line_text(row), right=True)

    def _line_width(self, font: QFont) -> int:
        """The line column: the widest of the report's line texts in
        ``font``, measured, not counted, since "22 → 24" is wider than
        "Line 18". Cached per report and font."""
        texts = self._view.model().line_texts()
        key = font.key()
        cached = self._line_column
        if cached is not None and cached[0] is texts and cached[1] == key:
            return cached[2]
        metrics = QFontMetrics(font)
        width = max((metrics.horizontalAdvance(text) for text in texts), default=0)
        self._line_column = (texts, key, width)
        return width

    def _paint_group(
        self,
        painter: QPainter,
        rect: QRect,
        font: QFont,
        row: GroupRow,
        colors: RowColors,
        expanded: bool,
    ) -> None:
        metrics = QFontMetrics(font)
        _paint_arrow(painter, rect, colors.text, expanded=expanded)
        x = rect.left() + _GLYPH_WIDTH + _GAP
        line_width = self._line_width(font)
        line_left = rect.right() - line_width
        width = max(0, line_left - _GAP - x)
        painter.setFont(font)
        painter.setPen(colors.text)
        text = metrics.elidedText(row.text, Qt.TextElideMode.ElideRight, width)
        _draw(painter, x, rect, width, text)
        painter.setPen(colors.muted)
        _draw(painter, line_left, rect, line_width, line_text(row), right=True)

    def _paint_info(
        self,
        painter: QPainter,
        rect: QRect,
        font: QFont,
        row: InfoRow,
        colors: RowColors,
    ) -> None:
        metrics = QFontMetrics(font)
        painter.setFont(font)
        painter.setPen(colors.muted)
        _draw(painter, rect.left(), rect, _GLYPH_WIDTH, _INFO_GLYPH, center=True)
        x = rect.left() + _GLYPH_WIDTH + _GAP
        width = max(0, rect.right() - x)
        text = metrics.elidedText(row.text, Qt.TextElideMode.ElideRight, width)
        _draw(painter, x, rect, width, text)


def row_height(metrics: QFontMetrics) -> int:
    """Every row is one line of text, so the list can use uniform sizes."""
    return max(metrics.height() + _ROW_PADDING, _MIN_ROW_HEIGHT)


def path_width_for(metrics: QFontMetrics, room: int, detail: str) -> int:
    """The path's share of ``room``: whatever the detail doesn't need.

    The detail keeps its measured width, or at least ``_DETAIL_MIN_CHARS``
    average characters when that is less, so a long path never pushes a
    value out of sight.
    """
    if not detail:
        return room
    needed = metrics.horizontalAdvance(detail)
    floor = metrics.averageCharWidth() * _DETAIL_MIN_CHARS
    return max(0, room - min(needed, floor) - _GAP)


def change_detail(row: ChangeRow) -> str:
    """``old → new`` for Changed, the one value for Added and Removed, and
    ``→ path`` for Moved. The view cuts it with "…" to fit.

    Each value is a Unicode direction isolate, so a right-to-left value
    neither flips the row's direction nor puts new before old.
    """
    if row.kind == "moved":
        return f"→ {_isolated(row.moved_to_text or '')}"
    if row.old is not None and row.new is not None:
        return f"{_isolated(row.old.text)} → {_isolated(row.new.text)}"
    summary = row.new if row.old is None else row.old
    return "" if summary is None else _isolated(summary.text)


def _isolated(text: str) -> str:
    """``text`` between FIRST STRONG ISOLATE and POP DIRECTIONAL ISOLATE."""
    return f"\u2068{text}\u2069"


def tooltip_text(row: ChangeRow | GroupRow | InfoRow) -> str:
    """The whole row, for when it is cut to fit: a change row's label, path,
    and detail, or a group or info row's text. The path is a direction
    isolate too, so a right-to-left key stays between its separators."""
    if isinstance(row, ChangeRow):
        path = _isolated(row.path_text)
        return f"{row.label} · {path} · {change_detail(row)}"
    return row.text


def line_text(row: ChangeRow | GroupRow | InfoRow) -> str:
    """The line column (Bill, #65 3c review): ``Line 22`` on the side the
    path names, which is Right for Added and Left otherwise, and ``22 → 24``
    from Left to Right for Moved. A group shows its first line."""
    if isinstance(row, InfoRow):
        return ""
    if isinstance(row, ChangeRow):
        if row.kind == "moved" and None not in (row.left_line, row.right_line):
            return f"{row.left_line} → {row.right_line}"
        if row.kind == "added":
            line = row.right_line
        else:
            line = row.left_line if row.left_line is not None else row.right_line
    else:
        line = row.left_line if row.left_line is not None else row.right_line
    return "" if line is None else f"Line {line}"


def _draw(
    painter: QPainter,
    x: int,
    rect: QRect,
    width: int,
    text: str,
    *,
    center: bool = False,
    right: bool = False,
) -> None:
    if width <= 0 or not text:
        return
    align = Qt.AlignmentFlag.AlignVCenter
    if center:
        align |= Qt.AlignmentFlag.AlignHCenter
    elif right:
        align |= Qt.AlignmentFlag.AlignRight
    else:
        align |= Qt.AlignmentFlag.AlignLeft
    painter.drawText(QRect(x, rect.top(), width, rect.height()), int(align), text)


def _paint_arrow(
    painter: QPainter, rect: QRect, color: QColor, *, expanded: bool
) -> None:
    """▾ open or ▸ closed, drawn as a triangle like the gutter's fold arrows,
    so it does not depend on the font."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    box = _ARROW_BOX
    left = rect.left() + (_GLYPH_WIDTH - box) / 2
    top = rect.top() + (rect.height() - box) / 2
    if expanded:
        points = ((1, 3), (9, 3), (5, 8))
    else:
        points = ((3, 1), (3, 9), (8, 5))
    painter.drawPolygon(QPolygonF([QPointF(left + x, top + y) for x, y in points]))
    painter.setBrush(Qt.BrushStyle.NoBrush)


class ChangesList(QListView):
    """The change rows. Return jumps, Left and Right fold groups, Esc leaves.

    ``activated`` carries the row to jump to: a ``ChangeRow``, or a
    ``GroupRow``, which jumps to its first item. ``escape_pressed`` fires on
    Esc, for Compare mode to leave on.
    """

    activated = Signal(object)
    escape_pressed = Signal()

    def __init__(self, model: ChangesModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("changesList")
        self.setAccessibleName("Changes")
        self.setModel(model)
        self._model = model
        self.setFrameShape(QListView.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setTabKeyNavigation(False)
        self.setUniformItemSizes(True)
        self.setItemDelegate(ChangesDelegate(self))
        self.setStyleSheet(
            "QListView { background: transparent; border: none; }"
            "QListView::item { background: transparent; border: none; }"
            "QListView::item:selected { background: transparent; }"
        )
        self._list_focused = False
        # The outline shows for keyboard focus only, like the Problems list.
        self._keyboard_focus = False
        self.clicked.connect(self._on_clicked)
        self.viewport().installEventFilter(self)

    def row_palette(self) -> QPalette:
        """The palette rows paint with: the parent's.

        The list's own palette carries the transparent background its
        stylesheet sets, which would read as a dark window.
        """
        parent = self.parentWidget()
        return parent.palette() if parent is not None else QApplication.palette()

    def outline_visible(self) -> bool:
        """Whether the current row shows the #68 focus outline."""
        return self._list_focused and self._keyboard_focus

    def current_row(self) -> int:
        return self.currentIndex().row()

    def select_row(self, row: int) -> None:
        """Make ``row`` current and scroll to it, without jumping."""
        index = self._model.index(row)
        self.setCurrentIndex(index)
        self.scrollTo(index)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        row = self.current_row()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._activate(row)
            event.accept()
            return
        if key == Qt.Key.Key_Escape:
            self.escape_pressed.emit()
            event.accept()
            return
        if key == Qt.Key.Key_Right and isinstance(self._model.row_at(row), GroupRow):
            self._model.set_expanded(row, True)
            event.accept()
            return
        if key == Qt.Key.Key_Left:
            if isinstance(self._model.row_at(row), GroupRow):
                self._model.set_expanded(row, False)
            else:
                group = self._model.group_row_of(row)
                if group is not None:
                    self.select_row(group)
            event.accept()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event: QFocusEvent) -> None:
        super().focusInEvent(event)
        self._list_focused = True
        self._keyboard_focus = keyboard_focus_after(
            event.reason(), self._keyboard_focus
        )
        self.viewport().update()

    def focusOutEvent(self, event: QFocusEvent) -> None:
        super().focusOutEvent(event)
        self._list_focused = False
        self.viewport().update()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """A click in the list hides the outline, as in the Problems list."""
        if watched is self.viewport() and event.type() == QEvent.Type.MouseButtonPress:
            self._keyboard_focus = False
            self.viewport().update()
        return super().eventFilter(watched, event)

    def currentChanged(
        self,
        current: QModelIndex | QPersistentModelIndex,
        previous: QModelIndex | QPersistentModelIndex,
    ) -> None:
        super().currentChanged(current, previous)
        self.viewport().update()

    def _on_clicked(self, index: QModelIndex) -> None:
        """A click toggles a group and jumps to a change."""
        row = index.row()
        if isinstance(self._model.row_at(row), GroupRow):
            self._model.set_expanded(row, not self._model.is_expanded(row))
            return
        self._activate(row)

    def _activate(self, row: int) -> None:
        target = self._model.row_at(row)
        if isinstance(target, (ChangeRow, GroupRow)):
            self.activated.emit(target)


class ChangesPane(QWidget):
    """The Changes tab's page: the rows, or a message in their place.

    Each message is Bill's wording verbatim. "No differences." shows a ✓.
    The stopped message has a **Compare Again** button, which sends
    ``compare_again_requested``.
    """

    activated = Signal(object)
    escape_pressed = Signal()
    compare_again_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("changesPage")
        self.setAccessibleName("Changes")
        self._state = ChangesState.EMPTY_RIGHT

        self._model = ChangesModel(self)
        self._list = ChangesList(self._model)
        self._list.activated.connect(self.activated.emit)
        self._list.escape_pressed.connect(self.escape_pressed.emit)

        self._glyph = QLabel(_CHECK_GLYPH)
        self._glyph.setObjectName("changesMessageGlyph")
        self._glyph.setAccessibleName("")
        self._message = QLabel()
        self._message.setObjectName("changesMessage")
        self._message.setWordWrap(True)
        self._again = QPushButton(COMPARE_AGAIN)
        self._again.setObjectName("changesCompareAgain")
        self._again.setAccessibleName(COMPARE_AGAIN)
        self._again.setStyleSheet(secondary_button_style())
        self._again.clicked.connect(self.compare_again_requested.emit)

        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(_GAP)
        line.addWidget(self._glyph, 0, Qt.AlignmentFlag.AlignTop)
        line.addWidget(self._message, 1)

        message_page = QWidget()
        message_page.setObjectName("changesMessagePage")
        page = QVBoxLayout(message_page)
        page.setContentsMargins(ROW_MARGINS[0], 8, ROW_MARGINS[2], 8)
        page.setSpacing(8)
        page.addLayout(line)
        page.addWidget(self._again, 0, Qt.AlignmentFlag.AlignLeft)
        page.addStretch(1)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._list)
        self._stack.addWidget(message_page)
        self._message_page = message_page

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)
        self._show_message(ChangesState.EMPTY_RIGHT)

    def list_view(self) -> ChangesList:
        """The change list. Tab order uses this widget."""
        return self._list

    def model(self) -> ChangesModel:
        return self._model

    def state(self) -> ChangesState:
        return self._state

    def message(self) -> str:
        """The message shown in place of the rows, or "" while rows show."""
        return "" if self._state == ChangesState.ROWS else self._message.text()

    def message_glyph(self) -> str:
        """The glyph beside the message: ✓ for "No differences.", else ""."""
        return self._glyph.text() if self._glyph.isVisibleTo(self) else ""

    def compare_again_button(self) -> QPushButton:
        return self._again

    def count(self) -> int:
        """Every change in the shown report, grouped ones included."""
        return self._model.count() if self._state == ChangesState.ROWS else 0

    def set_report(self, report: CompareReport) -> None:
        """Show ``report``'s rows, or "No differences." when it has none."""
        if not report.rows:
            self._model.set_report(None)
            self._show_message(ChangesState.NO_DIFFERENCES)
            return
        self._model.set_report(report)
        self._state = ChangesState.ROWS
        self._stack.setCurrentWidget(self._list)
        first = self._model.next_stop(-1)
        if first is not None:
            self._list.select_row(first)

    def show_empty_right(self) -> None:
        self._show_message(ChangesState.EMPTY_RIGHT)

    def show_errors(self) -> None:
        self._show_message(ChangesState.ERRORS)

    def show_canceled(self) -> None:
        self._show_message(ChangesState.CANCELED)

    def show_stopped(self) -> None:
        self._show_message(ChangesState.STOPPED)

    def next_change(self) -> bool:
        """Select and jump to the next change. False at the last one."""
        return self._step(self._model.next_stop(self._list.current_row()))

    def previous_change(self) -> bool:
        """Select and jump to the previous change. False at the first one."""
        row = self._list.current_row()
        if row < 0:
            return False
        return self._step(self._model.previous_stop(row))

    def _step(self, row: int | None) -> bool:
        if self._state != ChangesState.ROWS or row is None:
            return False
        self._list.select_row(row)
        target = self._model.row_at(row)
        if target is not None:
            self.activated.emit(target)
        return True

    def _show_message(self, state: ChangesState) -> None:
        self._model.set_report(None)
        self._state = state
        text = _MESSAGES[state]
        self._message.setText(text)
        self._message.setAccessibleName(text)
        self._glyph.setVisible(state == ChangesState.NO_DIFFERENCES)
        self._again.setVisible(state == ChangesState.STOPPED)
        self._stack.setCurrentWidget(self._message_page)
