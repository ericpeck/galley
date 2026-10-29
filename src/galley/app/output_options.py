"""The Output Options popover (ui-spec 6h) from the Output footer's Options link.

It is a second view of the toolbar's indent size picker and Sort keys
toggle and keeps no settings of its own. v1 shows Indent and Sort keys
only. Changes apply on the next Format; the popover never reformats Output.

Tab moves between the Indent row, Sort keys and Reset to defaults. The
arrow keys move within Indent and choose. Space toggles Sort keys. Esc or
a click outside closes it and puts focus back on the Options link.
"""

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QHelpEvent, QHideEvent, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from galley.app.actions import not_available_reason
from galley.app.fonts import ui_font
from galley.app.indent import INDENT_CHOICES
from galley.app.style import link_button_style, muted_text_style, segment_style

OUTPUT_OPTIONS_TITLE = "Output options"
OUTPUT_SCOPE = "Output · {label}"
INDENT_LABEL = "Indent"
SORT_KEYS_LABEL = "Sort keys"
SORT_KEYS_CHOICE = "A–Z at every level"
RESET_TO_DEFAULTS = "Reset to defaults"
APPLIES_NOTE = "Applies on the next Format"
# Segment labels for the indent size picker's choices, in its order.
_SEGMENT_TEXT = {"2 spaces": "2", "4 spaces": "4", "Tab": "Tab"}
# Space between the Options link and the popover.
_GAP = 4
_BACK = (Qt.Key.Key_Left, Qt.Key.Key_Up)
_ON = (Qt.Key.Key_Right, Qt.Key.Key_Down)


class _TipHost(QWidget):
    """Shows a greyed-out child's tooltip. Qt shows none for a disabled widget."""

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip and isinstance(event, QHelpEvent):
            child = self.childAt(event.pos())
            if child is not None and not child.isEnabled() and child.toolTip():
                QToolTip.showText(event.globalPos(), child.toolTip(), self)
                return True
        return super().event(event)


class _IndentSegments(_TipHost):
    """``2 | 4 | Tab``. The chosen segment is the row's only Tab stop.

    ``chosen(str)`` reports a choice the user made, such as ``4 spaces``.
    """

    chosen = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("outputOptionsIndent")
        self.setAccessibleName(INDENT_LABEL)
        self.setStyleSheet(segment_style())
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QToolButton] = {}
        for choice in INDENT_CHOICES:
            button = QToolButton()
            text = _SEGMENT_TEXT[choice]
            button.setObjectName(f"outputOptionsIndent{text}")
            button.setText(text)
            button.setAccessibleName(choice)
            button.setToolTip(choice)
            button.setCheckable(True)
            button.installEventFilter(self)
            button.clicked.connect(
                lambda _checked=False, value=choice: self._choose(value)
            )
            self._group.addButton(button)
            self._buttons[choice] = button
            row.addWidget(button)
        self._sync_tab_stop()

    def set_choice(self, choice: str) -> None:
        """Show ``choice`` as chosen, as the picker does."""
        button = self._buttons.get(choice)
        if button is not None and not button.isChecked():
            button.setChecked(True)
        self._sync_tab_stop()

    def set_unavailable(self, choice: str, reason: str) -> None:
        """Grey out ``choice`` with ``reason``, or "". A chosen one stays shown."""
        button = self._buttons[choice]
        button.setEnabled(not reason)
        button.setToolTip(reason or choice)
        button.setAccessibleDescription(reason)
        self._sync_tab_stop()

    def tab_stop(self) -> QToolButton:
        """The chosen segment, or the first one that isn't greyed out."""
        buttons = list(self._buttons.values())
        checked = self._group.checkedButton()
        if isinstance(checked, QToolButton) and checked.isEnabled():
            return checked
        return next((button for button in buttons if button.isEnabled()), buttons[0])

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
            and isinstance(watched, QToolButton)
            and event.key() in (*_BACK, *_ON)
        ):
            self._step(watched, -1 if event.key() in _BACK else 1)
            return True
        return super().eventFilter(watched, event)

    def _step(self, current: QToolButton, direction: int) -> None:
        buttons = list(self._buttons.values())
        index = buttons.index(current) + direction
        while 0 <= index < len(buttons):
            button = buttons[index]
            if button.isEnabled():
                button.click()
                button.setFocus(Qt.FocusReason.TabFocusReason)
                return
            index += direction

    def _choose(self, choice: str) -> None:
        self._sync_tab_stop()
        self.chosen.emit(choice)

    def _sync_tab_stop(self) -> None:
        stop = self.tab_stop()
        for button in self._buttons.values():
            policy = Qt.FocusPolicy.StrongFocus
            if button is not stop:
                policy = Qt.FocusPolicy.ClickFocus
            button.setFocusPolicy(policy)


class OutputOptionsPopover(QFrame):
    """Indent and Sort keys for the next Format, anchored to the Options link.

    ``indent_chosen(str)`` reports an indent size choice. ``reset_requested``
    asks for 2 spaces and Sort keys off. Sort keys is ``sort_keys`` itself:
    the checkbox follows its checked state, whether it is greyed out, and
    its not-available reason.
    """

    indent_chosen = Signal(str)
    reset_requested = Signal()

    def __init__(self, sort_keys: QAction, parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("outputOptions")
        self.setAccessibleName(OUTPUT_OPTIONS_TITLE)
        self.setAutoFillBackground(True)
        self.setStyleSheet(
            "QFrame#outputOptions {"
            " background: palette(window); border: 1px solid palette(mid); }"
        )
        self._anchor: QPushButton | None = None
        self._sort_keys = sort_keys

        title = QLabel(OUTPUT_OPTIONS_TITLE)
        title.setObjectName("outputOptionsTitle")
        font = ui_font()
        font.setBold(True)
        title.setFont(font)
        self._scope = QLabel(OUTPUT_SCOPE.format(label="JSON"))
        self._scope.setObjectName("outputOptionsScope")
        self._scope.setStyleSheet(muted_text_style())
        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self._scope)

        self._indent = _IndentSegments()
        self._indent.chosen.connect(self.indent_chosen)
        self._sort_box = QCheckBox(SORT_KEYS_CHOICE)
        self._sort_box.setObjectName("outputOptionsSortKeys")
        self._sort_box.setAccessibleName(SORT_KEYS_LABEL)
        self._sort_box.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._sort_box.toggled.connect(self._on_sort_box)
        sort_host = _TipHost()
        sort_row = QHBoxLayout(sort_host)
        sort_row.setContentsMargins(0, 0, 0, 0)
        sort_row.addWidget(self._sort_box)
        rows = QGridLayout()
        rows.setHorizontalSpacing(24)
        rows.setVerticalSpacing(12)
        rows.addWidget(QLabel(INDENT_LABEL), 0, 0)
        rows.addWidget(self._indent, 0, 1, Qt.AlignmentFlag.AlignLeft)
        rows.addWidget(QLabel(SORT_KEYS_LABEL), 1, 0)
        rows.addWidget(sort_host, 1, 1, Qt.AlignmentFlag.AlignLeft)
        rows.setColumnStretch(1, 1)

        self._reset = QPushButton(RESET_TO_DEFAULTS)
        self._reset.setObjectName("outputOptionsReset")
        self._reset.setAccessibleName(RESET_TO_DEFAULTS)
        self._reset.setFlat(True)
        self._reset.setStyleSheet(link_button_style())
        self._reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reset.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._reset.clicked.connect(self.reset_requested)
        note = QLabel(APPLIES_NOTE)
        note.setObjectName("outputOptionsNote")
        note.setStyleSheet(muted_text_style())
        footer = QHBoxLayout()
        footer.addWidget(self._reset)
        footer.addStretch(1)
        footer.addWidget(note)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(16)
        layout.addLayout(header)
        layout.addLayout(rows)
        layout.addLayout(footer)

        sort_keys.toggled.connect(self._sort_box.setChecked)
        sort_keys.changed.connect(self._sync_sort_keys)
        self._sync_sort_keys()

    def open_from(self, anchor: QPushButton) -> None:
        """Show above ``anchor`` (below when there is no room) and press it."""
        self._anchor = anchor
        self._sync_sort_keys()
        self.adjustSize()
        self.move(self._position(anchor))
        self.show()
        # The chosen segment's bold label settles the width once shown.
        self.move(self._position(anchor))
        anchor.setChecked(True)
        self.activateWindow()
        self._indent.tab_stop().setFocus(Qt.FocusReason.PopupFocusReason)

    def set_output_format(self, label: str) -> None:
        """Name Output's format in the header, such as ``Output · YAML``."""
        self._scope.setText(OUTPUT_SCOPE.format(label=label))

    def set_indent_choice(self, choice: str) -> None:
        """Show the indent size picker's ``choice``."""
        self._indent.set_choice(choice)

    def set_tab_unavailable(self, reason: str) -> None:
        """Grey out Tab with ``reason``, or "". A chosen Tab keeps showing."""
        self._indent.set_unavailable("Tab", reason)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """A press on the Options link only closes; it doesn't reopen."""
        anchor = self._anchor
        if anchor is not None and not self.rect().contains(event.position().toPoint()):
            at = anchor.mapFromGlobal(event.globalPosition().toPoint())
            self.setAttribute(
                Qt.WidgetAttribute.WA_NoMouseReplay, anchor.rect().contains(at)
            )
        super().mousePressEvent(event)

    def hideEvent(self, event: QHideEvent) -> None:
        super().hideEvent(event)
        anchor = self._anchor
        if anchor is not None:
            anchor.setChecked(False)
            # After Qt has put focus back where it was before the popup.
            QTimer.singleShot(0, anchor, self._focus_anchor)

    def _focus_anchor(self) -> None:
        anchor = self._anchor
        if anchor is not None and not self.isVisible():
            anchor.activateWindow()
            anchor.setFocus(Qt.FocusReason.PopupFocusReason)

    def _position(self, anchor: QPushButton) -> QPoint:
        size = self.size()
        corner = anchor.mapToGlobal(QPoint(0, 0))
        x = corner.x()
        y = corner.y() - size.height() - _GAP
        screen = anchor.screen().availableGeometry()
        if y < screen.top():
            y = corner.y() + anchor.height() + _GAP
        x = max(screen.left(), min(x, screen.right() + 1 - size.width()))
        return QPoint(x, y)

    def _on_sort_box(self, checked: bool) -> None:
        if self._sort_keys.isChecked() != checked:
            self._sort_keys.setChecked(checked)

    def _sync_sort_keys(self) -> None:
        action = self._sort_keys
        box = self._sort_box
        box.setChecked(action.isChecked())
        box.setEnabled(action.isEnabled())
        reason = "" if action.isEnabled() else not_available_reason(action)
        box.setToolTip(reason)
        box.setAccessibleDescription(reason or SORT_KEYS_CHOICE)
