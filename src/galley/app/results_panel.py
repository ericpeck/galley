"""Results panel: tabs of result rows, with a count badge, minimize, and close.

Problems is the only tab today, and the tab bar stays hidden until a second
tab is added (Compare's Changes tab, #65). The rows, keyboard handling, and
activation are shared, so every tab behaves the same way.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial

from PySide6.QtCore import (
    QEvent,
    QModelIndex,
    QObject,
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
    QStackedWidget,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from galley.app.theme import (
    composite,
    contrast_ratio,
    ink_on,
    selected_problem_palette,
    visible_edge,
)

ROW_HEIGHT = 56
# Room for the 3 px bar and 2 px outline, so focus does not shift text.
ROW_MARGINS = (11, 4, 8, 4)
_TEXT_CONTRAST = 4.5


class ResultRow(QWidget):
    """One result. Paints the selection fill, the bar, and the focus outline.

    Subclasses add their labels and recolor them in ``sync_text_colors``.
    """

    pressed = Signal()

    def __init__(self, accessible: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName(accessible)
        self.setMinimumHeight(ROW_HEIGHT)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._selected = False
        self._focused = False

    def is_selected(self) -> bool:
        return self._selected

    def set_state(self, *, selected: bool, focused: bool) -> None:
        """Tint and bar when selected, plus a 2 px outline when focused."""
        self._selected = selected
        self._focused = focused
        self.sync_text_colors()
        self.update()

    def sync_text_colors(self) -> None:
        """Recolor the row's labels for the palette and selection state."""

    def selected_fill(self) -> QColor:
        return selected_problem_palette(self.palette()).color(
            QPalette.ColorRole.Highlight
        )

    def readable_on_fill(self, role: QPalette.ColorRole) -> QColor:
        """``role`` made opaque on the selection fill.

        The palette's text roles can carry alpha (macOS dark muted text is
        25 % white), which fades on the selection fill.
        """
        palette = selected_problem_palette(self.palette())
        return _readable_on(self.selected_fill(), palette, role)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() in (
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
        ):
            self.sync_text_colors()

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
            painter.fillRect(self.rect(), fill)
            painter.fillRect(QRect(0, 0, 3, height), accent)
        if self._focused:
            painter.fillRect(QRect(0, 0, width, 2), accent)
            painter.fillRect(QRect(0, height - 2, width, 2), accent)
            painter.fillRect(QRect(0, 0, 3, height), accent)
            painter.fillRect(QRect(width - 2, 0, 2, height), accent)
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.pressed.emit()
        super().mousePressEvent(event)


class ResultsList(QListWidget):
    """Rows drawn by ``ResultRow`` widgets, activated by click or Return.

    ``activated`` carries the data of the row that was activated.
    ``escape_pressed`` fires on Esc, for the owner to move focus on.
    """

    activated = Signal(object)
    escape_pressed = Signal()

    def __init__(
        self, accessible: str, object_name: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setAccessibleName(accessible)
        self.setFrameShape(QListWidget.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setTabKeyNavigation(False)
        self._delegate = _RowWidgetDelegate(self)
        self.setItemDelegate(self._delegate)
        self.setStyleSheet(
            "QListWidget { background: transparent; border: none; }"
            "QListWidget::item { background: transparent; border: none; }"
            "QListWidget::item:selected { background: transparent; }"
        )
        self._rows: list[tuple[QListWidgetItem, ResultRow]] = []
        self._list_focused = False
        # The outline shows for keyboard focus only, like :focus-visible.
        self._keyboard_focus = False
        self.itemClicked.connect(self._activate)
        self.itemActivated.connect(self._activate)
        self.currentItemChanged.connect(self._sync_selection)
        self.viewport().installEventFilter(self)

    def set_rows(self, rows: Sequence[tuple[object, ResultRow]]) -> None:
        """Replace the rows. Each pairs the data ``activated`` sends with a row.

        A row with ``None`` data is a note: it cannot be selected,
        activated, or reached with the arrow keys.
        """
        self.clear()
        self._rows = []
        first: QListWidgetItem | None = None
        for data, row in rows:
            item = QListWidgetItem()
            item.setText(row.accessibleName())
            item.setData(Qt.ItemDataRole.UserRole, data)
            # Not editable: on macOS, Return opens an editor and skips itemActivated.
            if data is None:
                item.setFlags(Qt.ItemFlag.NoItemFlags)
            else:
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            item.setSizeHint(QSize(0, ROW_HEIGHT))
            self.addItem(item)
            self.setItemWidget(item, row)
            self._rows.append((item, row))
            if data is not None:
                row.pressed.connect(partial(self._press, item))
                first = first or item
        self._sync_row_heights()
        if first is not None:
            self.setCurrentItem(first)
        self._sync_selection()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Return and Enter activate the current row. Esc hands focus back."""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            item = self.currentItem()
            if item is not None:
                self._activate(item)
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.escape_pressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event: QFocusEvent) -> None:
        super().focusInEvent(event)
        self._list_focused = True
        self._note_focus_reason(event.reason())
        self._sync_selection()

    def focusOutEvent(self, event: QFocusEvent) -> None:
        super().focusOutEvent(event)
        self._list_focused = False
        self._sync_selection()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Viewport resizes refit the row heights, and a click hides the outline."""
        if watched is self.viewport():
            if event.type() == QEvent.Type.Resize:
                self._sync_row_heights()
            elif event.type() == QEvent.Type.MouseButtonPress:
                self._keyboard_focus = False
                self._sync_selection()
        return super().eventFilter(watched, event)

    def _sync_selection(self) -> None:
        current = self.currentItem()
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
        # rewraps to fewer lines leaves no gap under its heading.
        width = self.viewport().width()
        for item, row in self._rows:
            height = max(row.heightForWidth(width), ROW_HEIGHT)
            item.setSizeHint(QSize(0, height))

    def _activate(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        if data is not None:
            self.activated.emit(data)

    def _press(self, item: QListWidgetItem) -> None:
        self.setCurrentItem(item)
        self._activate(item)


@dataclass
class _Tab:
    page: QWidget
    title: str
    singular: str
    plural: str
    fill: Callable[[QPalette], QColor]
    count: int = 0


class ResultsPanel(QWidget):
    """Tabs of results under one header, with minimize and close.

    The header shows the current tab's title and count. With two or more
    tabs, a tab bar takes the title's place. Minimized, the panel is a
    strip with the current tab's count.
    """

    close_requested = Signal()
    minimize_requested = Signal()
    expand_requested = Signal()
    width_changed = Signal(int)

    def __init__(self, prefix: str, parent: QWidget | None = None) -> None:
        """``prefix`` starts every object name, as in ``problemsBadge``."""
        super().__init__(parent)
        self.setObjectName(f"{prefix}Pane")
        self._tabs: list[_Tab] = []
        self._minimized = False

        self._title = QLabel()
        self._title.setObjectName(f"{prefix}Title")
        self._title.setStyleSheet("font-weight: 700;")

        self._badge = QLabel("0")
        self._badge.setObjectName(f"{prefix}Badge")
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._tab_bar = QTabBar()
        self._tab_bar.setObjectName(f"{prefix}Tabs")
        self._tab_bar.setDocumentMode(True)
        self._tab_bar.setExpanding(False)
        self._tab_bar.setDrawBase(False)
        self._tab_bar.hide()

        self._minimize = _header_button("–", "Minimize", f"{prefix}Minimize")
        self._close = _header_button("×", "Close", f"{prefix}Close")
        self._minimize.clicked.connect(self.minimize_requested.emit)
        self._close.clicked.connect(self.close_requested.emit)

        header = QWidget()
        header.setObjectName(f"{prefix}Header")
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(8, 4, 4, 4)
        header_row.setSpacing(8)
        header_row.addWidget(self._title)
        header_row.addWidget(self._badge)
        header_row.addWidget(self._tab_bar)
        header_row.addStretch(1)
        header_row.addWidget(self._minimize)
        header_row.addWidget(self._close)

        self._stack = QStackedWidget()
        self._stack.setObjectName(f"{prefix}Stack")
        self._tab_bar.currentChanged.connect(self._on_tab_changed)

        self._body = QWidget()
        self._body.setObjectName(f"{prefix}Body")
        body = QVBoxLayout(self._body)
        body.setContentsMargins(0, 0, 0, 8)
        body.setSpacing(0)
        body.addWidget(header)
        body.addWidget(self._stack, 1)

        self._strip = QPushButton("0")
        self._strip.setObjectName(f"{prefix}Strip")
        self._strip.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._strip.clicked.connect(self.expand_requested.emit)
        self._strip.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._body)
        layout.addWidget(self._strip)

    def add_tab(
        self,
        page: QWidget,
        title: str,
        nouns: tuple[str, str],
        fill: Callable[[QPalette], QColor],
    ) -> None:
        """Add a tab of results.

        ``nouns`` is the singular and plural for the count's accessible
        name, and ``fill`` gives the badge color for a palette.
        """
        singular, plural = nouns
        self._tabs.append(_Tab(page, title, singular, plural, fill))
        self._stack.addWidget(page)
        self._tab_bar.addTab(title)
        self._sync_tabs()

    def remove_tab(self, page: QWidget) -> None:
        """Remove a tab. The page is hidden, not deleted."""
        index = self._index_of(page)
        del self._tabs[index]
        self._stack.removeWidget(page)
        self._tab_bar.removeTab(index)
        self._stack.setCurrentIndex(self._tab_bar.currentIndex())
        self._sync_tabs()

    def tab_bar(self) -> QTabBar:
        return self._tab_bar

    def current_page(self) -> QWidget | None:
        return self._stack.currentWidget()

    def set_current_page(self, page: QWidget) -> None:
        self._tab_bar.setCurrentIndex(self._index_of(page))

    def set_count(self, page: QWidget, count: int) -> None:
        """Set the count a tab shows in the badge, its tab, and the strip."""
        self._tabs[self._index_of(page)].count = count
        self._sync_tabs()

    def minimize_button(self) -> QPushButton:
        return self._minimize

    def close_button(self) -> QPushButton:
        return self._close

    def set_minimized(self, minimized: bool) -> None:
        """Collapse to the count strip, or show the results."""
        self._minimized = minimized
        self._body.setVisible(not minimized)
        self._strip.setVisible(minimized)
        self._sync_badge()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._minimized or not self.isVisible():
            return
        if self.width() >= 200:
            self.width_changed.emit(self.width())

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self._sync_colors()

    def _index_of(self, page: QWidget) -> int:
        for index, tab in enumerate(self._tabs):
            if tab.page is page:
                return index
        raise ValueError("not a tab of this panel")

    def _current_tab(self) -> _Tab | None:
        index = self._stack.currentIndex()
        return self._tabs[index] if 0 <= index < len(self._tabs) else None

    def _on_tab_changed(self, index: int) -> None:
        if 0 <= index < self._stack.count():
            self._stack.setCurrentIndex(index)
        self._sync_badge()

    def _sync_tabs(self) -> None:
        tabbed = len(self._tabs) > 1
        for index, tab in enumerate(self._tabs):
            self._tab_bar.setTabText(index, f"{tab.title} {tab.count}")
        self._tab_bar.setVisible(tabbed)
        self._title.setVisible(not tabbed)
        self._badge.setVisible(not tabbed)
        self._sync_badge()

    def _sync_badge(self) -> None:
        tab = self._current_tab()
        if tab is None:
            return
        count = str(tab.count)
        noun = tab.singular if tab.count == 1 else tab.plural
        self._title.setText(tab.title)
        self._badge.setText(count)
        self._badge.setAccessibleName(f"{count} {noun}")
        self._strip.setText(count)
        self._strip.setAccessibleName(f"{count} {noun}. Expand.")
        self._strip.setToolTip(f"Show {tab.title.lower()}")
        self._sync_colors()

    def _sync_colors(self) -> None:
        """Badge and strip in the tab's color with a label that reads.

        The colors are written into the stylesheets, so they are rebuilt
        whenever the palette changes.
        """
        tab = self._current_tab()
        if tab is None:
            return
        palette = self.palette()
        fill = tab.fill(palette)
        label = ink_on(fill, palette)
        set_style(
            self._badge,
            f"background: {fill.name()}; color: {label.name()}; "
            "border-radius: 8px; padding: 0 6px; font-weight: 700;",
        )
        set_style(
            self._strip,
            "QPushButton {"
            f" background: {fill.name()}; color: {label.name()};"
            " border: none; font-weight: 700; }",
        )


def set_style(widget: QWidget, sheet: str) -> None:
    """Set ``sheet`` only when it changed.

    Setting a stylesheet repolishes and can send PaletteChange again.
    """
    if widget.styleSheet() != sheet:
        widget.setStyleSheet(sheet)


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

    Each item carries a ResultRow that draws the whole row. Painting the
    item's own text under it left a ghost "Line" behind the marker.
    """

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        del painter, option, index


def _readable_on(fill: QColor, palette: QPalette, role: QPalette.ColorRole) -> QColor:
    """``role`` made opaque on ``fill``, or full text if that is too faint."""
    color = composite(palette.color(role), fill)
    if contrast_ratio(color, fill) >= _TEXT_CONTRAST:
        return color
    return composite(palette.color(QPalette.ColorRole.Text), fill)
