"""Main toolbar that keeps Format visible.

Secondary buttons give up their labels before any of them leave the row.
A label that does not fit becomes icon-only: the accessible name and the
tooltip stay the action text. An unbuilt icon-only button keeps its name
in both, as "Name — coming soon". If the row is still too narrow, trailing
controls move into the » menu and keep the same actions. The indent size
picker becomes an Indent size submenu there. Format stays at the right
edge, outside that menu.
"""

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QKeySequence,
    QPainter,
    QPaintEvent,
    QPalette,
    QResizeEvent,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from galley.app.actions import (
    COMING_SOON,
    ShellActions,
    coming_soon_wrapper,
    is_unbuilt,
)
from galley.app.icons import toolbar_icon
from galley.app.style import menu_style

_ICON_ONLY = frozenset({"Outdent", "Indent", "Expand all", "Collapse all"})
_MARGIN = 12
_CLUSTER_GAP = 8
_OVERFLOW_GAP = 4
_FORMAT_GAP = 12
_OVERFLOW_TEXT = "»"
_MENU_INDICATOR_STYLE = (
    "QToolButton::menu-indicator { image: none; width: 0px; height: 0px; }"
)
_INDENT_CHOICES = ("2 spaces", "4 spaces", "Tab")


@dataclass
class _Control:
    widget: QWidget
    actions: tuple[QAction, ...]
    buttons: tuple[tuple[QToolButton, QAction], ...]
    textual: bool
    group: int
    text_width: int = 0
    icon_width: int = 0


class MainToolbar(QWidget):
    """One toolbar row: secondary actions, then Format pinned on the right."""

    def __init__(
        self,
        actions: ShellActions,
        format_button: QPushButton,
        caption: QLabel,
    ) -> None:
        super().__init__()
        self.setObjectName("mainToolbar")
        self.setMinimumHeight(56)
        self.setAutoFillBackground(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._format_button = format_button
        self._caption = caption
        self._busy = False
        self._format_width = 0
        self._overflow_width = 0

        self._column = QWidget()
        column = QVBoxLayout(self._column)
        column.setContentsMargins(0, 2, 0, 2)
        column.setSpacing(0)
        column.addWidget(format_button)
        column.addWidget(caption)

        self._overflow = QToolButton()
        self._overflow.setObjectName("toolbarOverflow")
        self._overflow.setText(_OVERFLOW_TEXT)
        self._overflow.setAccessibleName("More actions")
        self._overflow.setToolTip("More actions")
        self._overflow.setAutoRaise(True)
        self._overflow.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._overflow.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._overflow.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._overflow.setStyleSheet(_MENU_INDICATOR_STYLE)
        self._menu = QMenu(self._overflow)
        self._menu.setObjectName("toolbarOverflowMenu")
        self._menu.setStyleSheet(menu_style())
        self._overflow.setMenu(self._menu)

        indent = QComboBox()
        indent.setObjectName("indentSize")
        indent.setAccessibleName("Indent size")
        indent.setToolTip("Indent size")
        indent.addItems(list(_INDENT_CHOICES))
        self._indent = indent
        self._indent_menu = self._build_indent_menu()

        tray = _UndoRedoTray()
        tray_row = QHBoxLayout(tray)
        tray_row.setContentsMargins(2, 2, 2, 2)
        tray_row.setSpacing(0)
        tray_row.addWidget(_tool_button(actions.undo))
        tray_row.addWidget(_tool_button(actions.redo))

        self._left: list[_Control] = []
        self._left_gaps: list[tuple[QWidget, _Control, _Control]] = []
        row = QHBoxLayout(self)
        row.setContentsMargins(_MARGIN, 4, _MARGIN, 4)
        row.setSpacing(0)
        row.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self._row = row

        self._add_left(_control(_tool_button(actions.paste), group=0))
        self._add_left(_control(_tool_button(actions.copy), group=0), gap=4)
        self._add_left(_control(tray, group=1), gap=20)
        self._add_left(_control(_tool_button(actions.outdent), group=2), gap=20)
        self._add_left(_control(_tool_button(actions.indent), group=2), gap=4)
        self._add_left(_control(indent, group=2, textual=False), gap=4)
        self._add_left(_control(_tool_button(actions.sort_keys), group=3), gap=20)
        self._add_left(_control(_tool_button(actions.minify), group=3), gap=4)
        self._add_left(_control(_tool_button(actions.expand_all), group=4), gap=20)
        self._add_left(_control(_tool_button(actions.collapse_all), group=4), gap=4)
        self._add_left(_control(_tool_button(actions.validate), group=5), gap=20)
        self._add_left(_control(_tool_button(actions.convert_python), group=5), gap=4)

        row.addStretch(1)
        self._cluster_gap = _spacer(_CLUSTER_GAP)
        row.addWidget(self._cluster_gap, 0, Qt.AlignmentFlag.AlignVCenter)
        self._find = _control(_tool_button(actions.find), group=6)
        row.addWidget(self._find.widget, 0, Qt.AlignmentFlag.AlignVCenter)
        self._pair_gap = _spacer(_OVERFLOW_GAP)
        row.addWidget(self._pair_gap, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self._overflow, 0, Qt.AlignmentFlag.AlignVCenter)
        self._format_gap = _spacer(_FORMAT_GAP)
        row.addWidget(self._format_gap, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self._column, 0, Qt.AlignmentFlag.AlignVCenter)
        self._controls = [*self._left, self._find]

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._relayout()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        if self._busy or self.width() <= 0:
            return
        self._busy = True
        try:
            icon, hidden = self._plan()
            self._apply(icon, hidden)
            guard = 0
            while self._too_wide() and guard < len(self._controls) + 2:
                guard += 1
                icon, hidden = self._tighten(icon, hidden)
                self._apply(icon, hidden)
            self._row.activate()
        finally:
            self._busy = False

    def _plan(self) -> tuple[dict[int, bool], int]:
        """Icon-only flags and how many trailing controls leave the row."""
        self._measure()
        icon = {id(control): not control.textual for control in self._controls}
        hidden = 0
        limit = len(self._controls)
        while hidden < limit and self._needed(icon, hidden) > self.width():
            if not self._drop_text(icon, hidden):
                hidden += 1
        return icon, hidden

    def _tighten(
        self, icon: dict[int, bool], hidden: int
    ) -> tuple[dict[int, bool], int]:
        if self._drop_text(icon, hidden):
            return icon, hidden
        return icon, min(len(self._controls), hidden + 1)

    def _drop_text(self, icon: dict[int, bool], hidden: int) -> bool:
        """Drop the rightmost visible label. Return False when none remain."""
        visible = self._controls[: len(self._controls) - hidden]
        for control in reversed(visible):
            if control.textual and not icon[id(control)]:
                icon[id(control)] = True
                return True
        return False

    def _too_wide(self) -> bool:
        self._row.invalidate()
        return self._row.minimumSize().width() > self.width()

    def _measure(self) -> None:
        for control in self._controls:
            if control.textual:
                self._restyle(control, icon_only=False)
                control.text_width = _natural_width(control.widget)
                self._restyle(control, icon_only=True)
                control.icon_width = _natural_width(control.widget)
            else:
                width = _natural_width(control.widget)
                control.text_width = width
                control.icon_width = width
        self._overflow_width = _natural_width(self._overflow)
        self._format_width = self._measure_format()
        height = self._column.sizeHint().height() + 8
        if height > self.minimumHeight():
            self.setMinimumHeight(max(56, height))

    def _measure_format(self) -> int:
        button = self._format_button
        button.setMinimumWidth(0)
        button.setMaximumWidth(16777215)
        button.ensurePolished()
        button.updateGeometry()
        text_width = button.sizeHint().width()
        button.setMinimumWidth(text_width)
        caption = self._caption
        return max(text_width, caption.minimumWidth(), caption.sizeHint().width(), 1)

    def _needed(self, icon: dict[int, bool], hidden: int) -> int:
        hidden_ids = self._hidden_ids(hidden)
        total = _MARGIN * 2 + _FORMAT_GAP + self._format_width
        left = [control for control in self._left if id(control) not in hidden_ids]
        for index, control in enumerate(left):
            if index:
                previous = left[index - 1]
                total += 4 if previous.group == control.group else 20
            total += self._width_for(control, icon)
        find_shown = id(self._find) not in hidden_ids
        overflow_shown = hidden > 0
        if left and (find_shown or overflow_shown):
            total += _CLUSTER_GAP
        if find_shown:
            total += self._width_for(self._find, icon)
        if find_shown and overflow_shown:
            total += _OVERFLOW_GAP
        if overflow_shown:
            total += self._overflow_width
        return total

    def _apply(self, icon: dict[int, bool], hidden: int) -> None:
        hidden_ids = self._hidden_ids(hidden)
        shown: dict[int, bool] = {}
        for control in self._controls:
            visible = id(control) not in hidden_ids
            shown[id(control)] = visible
            self._restyle(control, icon_only=icon[id(control)])
            control.widget.setVisible(visible)
            if visible:
                control.widget.setFixedWidth(self._width_for(control, icon))
        for spacer, left, right in self._left_gaps:
            spacer.setVisible(shown[id(left)] and shown[id(right)])
        find_shown = shown[id(self._find)]
        overflow_shown = hidden > 0
        left_shown = any(shown[id(control)] for control in self._left)
        self._cluster_gap.setVisible(left_shown and (find_shown or overflow_shown))
        self._pair_gap.setVisible(find_shown and overflow_shown)
        self._overflow.setVisible(overflow_shown)
        if overflow_shown:
            self._overflow.setFixedWidth(self._overflow_width)
            previous = self._tab_before_overflow(shown)
            if previous is not None:
                QWidget.setTabOrder(previous, self._overflow)
            QWidget.setTabOrder(self._overflow, self._format_button)
        self._column.setFixedWidth(self._format_width)
        self._sync_menu(hidden_ids)

    def _sync_menu(self, hidden_ids: set[int]) -> None:
        menu = self._menu
        for action in list(menu.actions()):
            menu.removeAction(action)
        for control in self._controls:
            if id(control) not in hidden_ids:
                continue
            if control.widget is self._indent:
                menu.addMenu(self._indent_menu)
                continue
            for action in control.actions:
                menu.addAction(action)

    def _build_indent_menu(self) -> QMenu:
        menu = QMenu("Indent size", self)
        menu.setObjectName("indentSizeMenu")
        menu.setStyleSheet(menu_style())
        group = QActionGroup(menu)
        group.setExclusive(True)
        for label in _INDENT_CHOICES:
            choice = menu.addAction(label)
            choice.setCheckable(True)
            group.addAction(choice)
            choice.triggered.connect(
                lambda checked=False, text=label: self._choose_indent(text)
            )
        self._indent_menu = menu
        self._indent.currentTextChanged.connect(self._sync_indent_checks)
        self._sync_indent_checks(self._indent.currentText())
        return menu

    def _choose_indent(self, text: str) -> None:
        self._indent.setCurrentText(text)

    def _sync_indent_checks(self, text: str) -> None:
        for action in self._indent_menu.actions():
            action.setChecked(action.text() == text)

    def _tab_before_overflow(self, shown: dict[int, bool]) -> QWidget | None:
        for control in reversed(self._controls):
            if not shown[id(control)]:
                continue
            found = _last_tab_stop(control.widget)
            if found is not None:
                return found
        return None

    def _hidden_ids(self, hidden: int) -> set[int]:
        if hidden <= 0:
            return set()
        return {id(control) for control in self._controls[-hidden:]}

    def _width_for(self, control: _Control, icon: dict[int, bool]) -> int:
        if icon[id(control)]:
            return control.icon_width
        return control.text_width

    def _add_left(self, control: _Control, gap: int | None = None) -> None:
        if gap is not None and self._left:
            spacer = _spacer(gap)
            self._row.addWidget(spacer, 0, Qt.AlignmentFlag.AlignVCenter)
            self._left_gaps.append((spacer, self._left[-1], control))
        self._row.addWidget(control.widget, 0, Qt.AlignmentFlag.AlignVCenter)
        self._left.append(control)

    def _restyle(self, control: _Control, *, icon_only: bool) -> None:
        for button, action in control.buttons:
            _present(button, action, icon_only=icon_only or not control.textual)


def _control(widget: QWidget, *, group: int, textual: bool | None = None) -> _Control:
    buttons = _buttons(widget)
    if textual is None:
        textual = not buttons or any(
            action.text() not in _ICON_ONLY for _, action in buttons
        )
    actions = tuple(action for _, action in buttons)
    return _Control(widget, actions, buttons, textual, group)


def _buttons(widget: QWidget) -> tuple[tuple[QToolButton, QAction], ...]:
    if isinstance(widget, QToolButton):
        found: list[QToolButton] = [widget]
    else:
        found = list(widget.findChildren(QToolButton))
    paired: list[tuple[QToolButton, QAction]] = []
    for button in found:
        action = button.defaultAction()
        if isinstance(action, QAction):
            paired.append((button, action))
    return tuple(paired)


def _unbuilt_icon_label(action: QAction) -> str:
    return f"{action.text()} — coming soon"


def _coming_soon_host(button: QToolButton) -> QWidget | None:
    host = button.parentWidget()
    if host is not None and host.property("comingSoonHost") is True:
        return host
    return None


def _present(button: QToolButton, action: QAction, *, icon_only: bool) -> None:
    icon = toolbar_icon(action.text(), pad=not icon_only)
    if not icon.isNull():
        action.setIcon(icon)
        button.setIcon(icon)
    if icon_only:
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    elif not icon.isNull():
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    else:
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    host = _coming_soon_host(button)
    if is_unbuilt(action) and icon_only:
        label = _unbuilt_icon_label(action)
        button.setAccessibleName(label)
        button.setToolTip(label)
        if host is not None:
            host.setToolTip(label)
            host.setAccessibleName(label)
        return
    button.setAccessibleName(action.text() if icon_only else "")
    if is_unbuilt(action):
        button.setToolTip(COMING_SOON)
        if host is not None:
            host.setToolTip(COMING_SOON)
            host.setAccessibleName("")
    elif icon_only and action.text() not in _ICON_ONLY:
        button.setToolTip(action.text())
    elif icon_only:
        shortcut = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
        tip = action.text() if not shortcut else f"{action.text()} ({shortcut})"
        button.setToolTip(tip)
    else:
        button.setToolTip("")


def _takes_tab(widget: QWidget) -> bool:
    return (
        widget.isVisible()
        and widget.isEnabled()
        and bool(widget.focusPolicy() & Qt.FocusPolicy.TabFocus)
    )


def _last_tab_stop(widget: QWidget) -> QWidget | None:
    candidate = widget if _takes_tab(widget) else None
    layout = widget.layout()
    if layout is None:
        return candidate
    for index in range(layout.count()):
        item = layout.itemAt(index)
        child = item.widget() if item is not None else None
        if child is None or not child.isVisible():
            continue
        nested = _last_tab_stop(child)
        if nested is not None:
            candidate = nested
    return candidate


def _natural_width(widget: QWidget) -> int:
    widget.setMinimumWidth(0)
    widget.setMaximumWidth(16777215)
    widget.ensurePolished()
    layout = widget.layout()
    if layout is not None:
        layout.invalidate()
    widget.updateGeometry()
    return max(widget.sizeHint().width(), widget.minimumSizeHint().width(), 1)


def _spacer(width: int) -> QWidget:
    spacer = QWidget()
    spacer.setFixedWidth(width)
    return spacer


def _tool_button(action: QAction) -> QWidget:
    button = QToolButton()
    button.setDefaultAction(action)
    icon_only = action.text() in _ICON_ONLY
    icon = toolbar_icon(action.text(), pad=not icon_only)
    if not icon.isNull():
        action.setIcon(icon)
        button.setIcon(icon)
    if icon_only:
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setAccessibleName(action.text())
        if not is_unbuilt(action):
            shortcut = action.shortcut().toString(
                QKeySequence.SequenceFormat.NativeText
            )
            tip = action.text() if not shortcut else f"{action.text()} ({shortcut})"
            button.setToolTip(tip)
    elif not icon.isNull():
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    else:
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    button.setAutoRaise(True)
    return coming_soon_wrapper(button, action)


class _UndoRedoTray(QFrame):
    """Shared rounded tray. No stylesheet, so the buttons keep platform text."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("undoRedoTray")

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.palette().color(QPalette.ColorRole.AlternateBase))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 4, 4)
        painter.end()
