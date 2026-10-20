"""Problems pane: one row per located error, docked to the left of Input."""

from collections.abc import Sequence

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent, QResizeEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from galley.app.problem_text import DisplayProblem
from galley.app.style import muted_text_style
from galley.app.theme import ACCENT, ACCENT_TINT, ERROR, TEXT, TEXT_ON_ACCENT

# Off until Replace tabs with spaces is approved. The control stays hidden.
SHOW_REPLACE_TABS_BUTTON = False

_ROW_HEIGHT = 56


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
        self._title.setStyleSheet(f"color: {TEXT}; font-weight: 700;")

        self._badge = QLabel("0")
        self._badge.setObjectName("problemsBadge")
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._badge.setStyleSheet(
            f"background: {ERROR}; color: {TEXT_ON_ACCENT}; "
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
        self._list.setStyleSheet(
            "QListWidget { background: transparent; border: none; }"
            "QListWidget::item { background: transparent; border: none; }"
            "QListWidget::item:selected { background: transparent; }"
        )
        self._list.itemClicked.connect(self._activate)
        self._list.itemActivated.connect(self._activate)
        self._list.currentItemChanged.connect(self._sync_selection)
        self._list.installEventFilter(self)

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
            f"QPushButton {{ background: {ERROR}; color: {TEXT_ON_ACCENT}; "
            "border: none; font-weight: 700; }"
        )
        self._strip.clicked.connect(self.expand_requested.emit)
        self._strip.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._body)
        layout.addWidget(self._strip)

    def eventFilter(self, watched: QWidget, event: QEvent) -> bool:
        """Return and Enter jump to the current problem."""
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
            item.setSizeHint(row.sizeHint().expandedTo(QSize(0, _ROW_HEIGHT)))
            self._rows.append((item, row))
            row.clicked.connect(self._activate_line)
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
            row.set_selected(item is current)

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


class _ProblemRow(QWidget):
    """A red marker, a bold line number, and the message under it."""

    clicked = Signal(int)

    def __init__(self, problem: DisplayProblem, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lineno = problem.lineno
        self.setAccessibleName(problem.accessible)
        self.setMinimumHeight(_ROW_HEIGHT)

        marker = QLabel("!")
        marker.setObjectName("problemMarker")
        marker.setAlignment(Qt.AlignmentFlag.AlignCenter)
        marker.setFixedSize(16, 16)
        marker.setStyleSheet(
            f"background: {ERROR}; color: {TEXT_ON_ACCENT}; border-radius: 8px;"
        )

        line = QLabel(f"Line {problem.lineno}")
        line.setStyleSheet(f"color: {TEXT}; font-weight: 700;")

        message = QLabel(problem.message)
        message.setWordWrap(True)
        message.setStyleSheet(muted_text_style())

        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(0)
        text_column.addWidget(line)
        text_column.addWidget(message)

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(8)
        row.addWidget(marker, 0, Qt.AlignmentFlag.AlignTop)
        row.addLayout(text_column, 1)
        self._selected = False
        self.set_selected(False)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        if selected:
            self.setStyleSheet(
                f"background: {ACCENT_TINT}; border-left: 3px solid {ACCENT};"
            )
            return
        self.setStyleSheet(
            "background: transparent; border-left: 3px solid transparent;"
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.clicked.emit(self._lineno)
        super().mousePressEvent(event)
