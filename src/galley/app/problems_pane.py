"""Problems pane: one row per located error, docked to the left of Input."""

from collections.abc import Sequence

from PySide6.QtCore import (
    QEvent,
    QModelIndex,
    QPersistentModelIndex,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFocusEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPalette,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from galley.app.problem_text import DisplayProblem
from galley.app.style import muted_text_style
from galley.app.theme import (
    ERROR,
    composite,
    contrast_ratio,
    selected_problem_palette,
    visible_edge,
)

# Off until Replace tabs with spaces is approved. The control stays hidden.
SHOW_REPLACE_TABS_BUTTON = False

_ROW_HEIGHT = 56
_TEXT_CONTRAST = 4.5


class ProblemsPane(QWidget):
    """List of problems, with minimize, close, and a flagged tab repair."""

    jump_requested = Signal(int)
    close_requested = Signal()
    minimize_requested = Signal()
    expand_requested = Signal()
    replace_tabs_requested = Signal()
    width_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("problemsPane")
        self.setAccessibleName("Problems")
        self._problems: tuple[DisplayProblem, ...] = ()
        self._minimized = False
        self._rows: list[tuple[QListWidgetItem, _ProblemRow]] = []

        self._title = QLabel("Problems")
        self._title.setObjectName("problemsTitle")
        self._title.setStyleSheet("font-weight: 700;")

        self._badge = QLabel("0")
        self._badge.setObjectName("problemsBadge")
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._badge.setStyleSheet(
            f"background: {ERROR}; color: palette(highlighted-text); "
            "border-radius: 8px; padding: 0 6px; font-weight: 700;"
        )

        self._minimize = _header_button("–", "Minimize", "problemsMinimize")
        self._close = _header_button("×", "Close", "problemsClose")
        self._minimize.clicked.connect(self.minimize_requested.emit)
        self._close.clicked.connect(self.close_requested.emit)

        header = QWidget()
        header.setObjectName("problemsHeader")
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(8, 4, 4, 4)
        header_row.setSpacing(8)
        header_row.addWidget(self._title)
        header_row.addWidget(self._badge)
        header_row.addStretch(1)
        header_row.addWidget(self._minimize)
        header_row.addWidget(self._close)

        self._list = QListWidget()
        self._list.setObjectName("problemsList")
        self._list.setAccessibleName("Problems")
        self._list.setFrameShape(QListWidget.Shape.NoFrame)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.setTabKeyNavigation(False)
        self._delegate = _RowWidgetDelegate(self._list)
        self._list.setItemDelegate(self._delegate)
        self._list.setStyleSheet(
            "QListWidget { background: transparent; border: none; }"
            "QListWidget::item { background: transparent; border: none; }"
            "QListWidget::item:selected { background: transparent; }"
        )
        self._list.itemClicked.connect(self._activate)
        self._list.itemActivated.connect(self._activate)
        self._list.currentItemChanged.connect(self._sync_selection)
        self._list.installEventFilter(self)
        self._list.viewport().installEventFilter(self)
        self._list_focused = False
        # The outline shows for keyboard focus only, like :focus-visible.
        self._keyboard_focus = False

        self._replace = QPushButton("Replace tabs with spaces")
        self._replace.setObjectName("replaceTabsButton")
        self._replace.setAccessibleName("Replace tabs with spaces")
        self._replace.setToolTip("Replace tabs with spaces")
        self._replace.clicked.connect(self.replace_tabs_requested.emit)
        self._replace.hide()
        self._replace.setEnabled(False)

        self._body = QWidget()
        self._body.setObjectName("problemsBody")
        body = QVBoxLayout(self._body)
        body.setContentsMargins(0, 0, 0, 8)
        body.setSpacing(0)
        body.addWidget(header)
        body.addWidget(self._list, 1)
        body.addWidget(self._replace)

        self._strip = QPushButton("0")
        self._strip.setObjectName("problemsStrip")
        self._strip.setToolTip("Show problems")
        self._strip.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._strip.setStyleSheet(
            "QPushButton {"
            f" background: {ERROR}; color: palette(highlighted-text);"
            " border: none; font-weight: 700; }"
        )
        self._strip.clicked.connect(self.expand_requested.emit)
        self._strip.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._body)
        layout.addWidget(self._strip)

    def eventFilter(self, watched: QWidget, event: QEvent) -> bool:
        """Return and Enter jump to the current problem.

        Focus changes on the list move the focus outline, a click hides it,
        and viewport resizes refit the row heights to the new width.
        """
        if watched is self._list and isinstance(event, QFocusEvent):
            self._list_focused = event.type() == QEvent.Type.FocusIn
            if self._list_focused:
                self._note_focus_reason(event.reason())
            self._sync_selection()
        if watched is self._list.viewport():
            if event.type() == QEvent.Type.Resize:
                self._sync_row_heights()
            elif event.type() == QEvent.Type.MouseButtonPress:
                self._keyboard_focus = False
                self._sync_selection()
        if (
            watched is self._list
            and event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        ):
            item = self._list.currentItem()
            if item is not None:
                self._activate(item)
            return True
        return super().eventFilter(watched, event)

    def list_widget(self) -> QListWidget:
        """The problem list. Tab order uses this widget."""
        return self._list

    def minimize_button(self) -> QPushButton:
        return self._minimize

    def close_button(self) -> QPushButton:
        return self._close

    def count(self) -> int:
        return len(self._problems)

    def messages(self) -> list[str]:
        return [problem.message for problem in self._problems]

    def lines(self) -> list[int]:
        return [problem.lineno for problem in self._problems]

    def set_problems(self, problems: Sequence[DisplayProblem]) -> None:
        """Replace the rows. An empty sequence is not shown by the window."""
        self._problems = tuple(problems)
        self._list.clear()
        self._rows = []
        for problem in self._problems:
            item = QListWidgetItem()
            item.setText(problem.accessible)
            item.setData(Qt.ItemDataRole.UserRole, problem.lineno)
            # Not editable: on macOS, Return opens an editor and skips itemActivated.
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            item.setSizeHint(QSize(0, _ROW_HEIGHT))
            row = _ProblemRow(problem)
            row.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._list.addItem(item)
            self._list.setItemWidget(item, row)
            self._rows.append((item, row))
            row.clicked.connect(self._activate_line)
        self._sync_row_heights()
        if self._rows:
            self._list.setCurrentItem(self._rows[0][0])
        self._sync_badge()
        self._sync_replace()
        self._sync_selection()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._minimized or not self.isVisible():
            return
        if self.width() >= 200:
            self.width_changed.emit(self.width())

    def set_minimized(self, minimized: bool) -> None:
        """Collapse to the count strip, or show the list."""
        self._minimized = minimized
        self._body.setVisible(not minimized)
        self._strip.setVisible(minimized)
        self._sync_badge()

    def _sync_badge(self) -> None:
        count = str(len(self._problems))
        noun = "problem" if len(self._problems) == 1 else "problems"
        self._badge.setText(count)
        self._badge.setAccessibleName(f"{count} {noun}")
        self._strip.setText(count)
        self._strip.setAccessibleName(f"{count} {noun}. Expand.")

    def _sync_replace(self) -> None:
        has_tabs = any(problem.code == "yaml_tab" for problem in self._problems)
        show = SHOW_REPLACE_TABS_BUTTON and has_tabs
        self._replace.setEnabled(show)
        self._replace.setVisible(show)

    def _sync_selection(self) -> None:
        current = self._list.currentItem()
        for item, row in self._rows:
            row.set_state(
                selected=item is current,
                focused=(
                    item is current and self._list_focused and self._keyboard_focus
                ),
            )

    def _note_focus_reason(self, reason: Qt.FocusReason) -> None:
        if reason == Qt.FocusReason.MouseFocusReason:
            self._keyboard_focus = False
        elif reason not in (
            Qt.FocusReason.ActiveWindowFocusReason,
            Qt.FocusReason.PopupFocusReason,
        ):
            # Tab, Shift-Tab, shortcuts, and focus moved by a command.
            # Returning to the window or closing a popup keeps the last mode.
            self._keyboard_focus = True

    def _sync_row_heights(self) -> None:
        # Fit each row to the list's width, so a wrapped message that
        # rewraps to fewer lines leaves no gap under the line number.
        width = self._list.viewport().width()
        for item, row in self._rows:
            height = max(row.heightForWidth(width), _ROW_HEIGHT)
            item.setSizeHint(QSize(0, height))

    def _activate(self, item: QListWidgetItem) -> None:
        lineno = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(lineno, int):
            self.jump_requested.emit(lineno)

    def _activate_line(self, lineno: int) -> None:
        for item, _row in self._rows:
            if item.data(Qt.ItemDataRole.UserRole) == lineno:
                self._list.setCurrentItem(item)
                break
        self.jump_requested.emit(lineno)


def _header_button(text: str, name: str, object_name: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName(object_name)
    button.setAccessibleName(name)
    button.setToolTip(name)
    button.setFixedSize(24, 24)
    button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    return button


class _RowWidgetDelegate(QStyledItemDelegate):
    """Paints nothing, so the item text stays for accessibility only.

    Each item carries a _ProblemRow that draws the whole row. Painting the
    item's own text under it left a ghost "Line" behind the marker.
    """

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        del painter, option, index


class _ProblemRow(QWidget):
    """A red marker, a bold line number, and the message under it."""

    clicked = Signal(int)

    def __init__(self, problem: DisplayProblem, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lineno = problem.lineno
        self.setObjectName("problemRow")
        self.setAccessibleName(problem.accessible)
        self.setMinimumHeight(_ROW_HEIGHT)

        marker = QLabel("!")
        marker.setObjectName("problemMarker")
        marker.setAlignment(Qt.AlignmentFlag.AlignCenter)
        marker.setFixedSize(16, 16)
        marker.setStyleSheet(
            f"background: {ERROR}; color: palette(highlighted-text); "
            "border-radius: 8px;"
        )

        line = QLabel(f"Line {problem.lineno}")
        line.setStyleSheet("font-weight: 700;")
        self._line = line

        message = QLabel(problem.message)
        message.setWordWrap(True)
        message.setStyleSheet(muted_text_style())
        self._message = message

        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(0)
        text_column.addWidget(line)
        text_column.addWidget(message)
        text_column.addStretch(1)

        row = QHBoxLayout(self)
        # Room for the 3 px bar and 2 px outline, so focus does not shift text.
        row.setContentsMargins(11, 4, 8, 4)
        row.setSpacing(8)
        row.addWidget(marker, 0, Qt.AlignmentFlag.AlignTop)
        row.addLayout(text_column, 1)
        self._selected = False
        self._focused = False

    def set_state(self, *, selected: bool, focused: bool) -> None:
        """Tint and bar when selected, plus a 2 px outline when focused."""
        self._selected = selected
        self._focused = focused
        self._sync_text_colors()
        self.update()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() in (
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
        ):
            self._sync_text_colors()

    def _sync_text_colors(self) -> None:
        # The palette's text roles can carry alpha (macOS dark muted text is
        # 25 % white), which fades on the selection fill. A selected row
        # paints its text in an opaque color that reads on the fill.
        if not self._selected:
            self._line.setStyleSheet("font-weight: 700;")
            self._message.setStyleSheet(muted_text_style())
            return
        palette = selected_problem_palette(self.palette())
        fill = palette.color(QPalette.ColorRole.Highlight)
        line = _readable_on(fill, palette, QPalette.ColorRole.WindowText)
        message = _readable_on(fill, palette, QPalette.ColorRole.PlaceholderText)
        self._line.setStyleSheet(f"font-weight: 700; color: {line.name()};")
        self._message.setStyleSheet(f"color: {message.name()};")

    def paintEvent(self, event: QPaintEvent) -> None:
        # Painted here, not by a stylesheet: stylesheet palette() reads the
        # application palette, and a row stylesheet cascades to its labels.
        del event
        if not self._selected and not self._focused:
            return
        palette = selected_problem_palette(self.palette())
        fill = palette.color(QPalette.ColorRole.Highlight)
        accent = visible_edge(
            palette.color(QPalette.ColorRole.Accent),
            (fill, palette.color(QPalette.ColorRole.Window)),
            _readable_on(fill, palette, QPalette.ColorRole.Text),
        )
        width, height = self.width(), self.height()
        painter = QPainter(self)
        if self._selected:
            painter.fillRect(self.rect(), palette.color(QPalette.ColorRole.Highlight))
            painter.fillRect(QRect(0, 0, 3, height), accent)
        if self._focused:
            painter.fillRect(QRect(0, 0, width, 2), accent)
            painter.fillRect(QRect(0, height - 2, width, 2), accent)
            painter.fillRect(QRect(0, 0, 3, height), accent)
            painter.fillRect(QRect(width - 2, 0, 2, height), accent)
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.clicked.emit(self._lineno)
        super().mousePressEvent(event)


def _readable_on(fill: QColor, palette: QPalette, role: QPalette.ColorRole) -> QColor:
    """``role`` made opaque on ``fill``, or full text if that is too faint."""
    color = composite(palette.color(role), fill)
    if contrast_ratio(color, fill) >= _TEXT_CONTRAST:
        return color
    return composite(palette.color(QPalette.ColorRole.Text), fill)
