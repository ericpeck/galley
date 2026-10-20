"""Format stays fully visible; secondary labels are never cut mid-word."""

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QImage, QPainter, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QMenu,
    QPushButton,
    QStyle,
    QStyleOptionMenuItem,
    QToolButton,
    QWidget,
)

from galley.app.actions import COMING_SOON, is_unbuilt
from galley.app.appearance import set_application_palette
from galley.app.main import create_main_window

_PERMANENT_ICON = frozenset({"Outdent", "Indent", "Expand all", "Collapse all"})


def _show(qtbot, width: int):
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    window.resize(width, 800)
    toolbar = window.findChild(QWidget, "mainToolbar")
    assert isinstance(toolbar, QWidget)
    qtbot.waitUntil(lambda: window.width() == width and toolbar.width() == width)
    return window, toolbar


def _fully_inside(widget: QWidget, ancestor: QWidget) -> bool:
    if not widget.isVisible():
        return False
    bounds = ancestor.rect()
    top_left = widget.mapTo(ancestor, widget.rect().topLeft())
    bottom_right = widget.mapTo(ancestor, widget.rect().bottomRight())
    return bounds.contains(top_left) and bounds.contains(bottom_right)


def _secondary_buttons(toolbar: QWidget) -> list[QToolButton]:
    buttons: list[QToolButton] = []
    for button in toolbar.findChildren(QToolButton):
        if button.objectName() == "toolbarOverflow":
            continue
        if isinstance(button.defaultAction(), QAction):
            buttons.append(button)
    return buttons


def _text_is_squeezed(button: QToolButton) -> bool:
    """True when a visible label is narrower than the text it is meant to draw."""
    if button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly:
        return False
    return button.width() < button.sizeHint().width()


def _overflow_button(toolbar: QWidget) -> QToolButton:
    button = toolbar.findChild(QToolButton, "toolbarOverflow")
    assert isinstance(button, QToolButton)
    return button


def _assert_same_action(action: QAction) -> None:
    hits: list[QAction] = []

    def remember(checked: bool = False) -> None:
        del checked
        hits.append(action)

    action.triggered.connect(remember)
    try:
        action.trigger()
        if action.isEnabled():
            assert hits == [action]
        else:
            assert hits == []
    finally:
        action.triggered.disconnect(remember)


@pytest.mark.parametrize("width", [1280, 1024])
def test_format_stays_visible_and_labels_are_not_elided(qtbot, width: int) -> None:
    window, toolbar = _show(qtbot, width)
    button = window.findChild(QPushButton, "formatButton")
    assert isinstance(button, QPushButton)
    assert _fully_inside(button, window)
    assert _fully_inside(button, toolbar)
    assert button.width() >= button.sizeHint().width()
    assert "Format" in button.text()

    overflow = _overflow_button(toolbar)
    menu = overflow.menu()
    assert menu is not None
    overflowed: list[QAction] = []
    for tool in _secondary_buttons(toolbar):
        action = tool.defaultAction()
        assert isinstance(action, QAction)
        if not tool.isVisible() or not _fully_inside(tool, toolbar):
            overflowed.append(action)
            continue
        assert _fully_inside(tool, toolbar)
        if tool.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly:
            if is_unbuilt(action):
                label = f"{action.text()} — coming soon"
                assert tool.accessibleName() == label
                assert tool.toolTip() == label
            else:
                assert tool.accessibleName() == action.text()
                if action.text() not in _PERMANENT_ICON:
                    assert tool.toolTip() == action.text()
        else:
            assert not _text_is_squeezed(tool)

    if overflowed:
        assert overflow.isVisible()
        assert overflow.text() == "»"
        for action in overflowed:
            matches = [item for item in menu.actions() if item is action]
            assert matches == [action]
            _assert_same_action(action)
    else:
        assert not overflow.isVisible()
        assert menu.actions() == []


def _actions_in_toolbar_order(toolbar: QWidget) -> list[tuple[QToolButton, QAction]]:
    found: list[tuple[QToolButton, QAction]] = []

    def walk(widget: QWidget) -> None:
        layout = widget.layout()
        if layout is None:
            return
        for index in range(layout.count()):
            item = layout.itemAt(index)
            if item is None:
                continue
            child = item.widget()
            if child is None:
                continue
            if (
                isinstance(child, QToolButton)
                and child.objectName() != "toolbarOverflow"
            ):
                action = child.defaultAction()
                if isinstance(action, QAction):
                    found.append((child, action))
            walk(child)

    walk(toolbar)
    return found


def test_overflow_menu_triggers_the_same_actions(qtbot) -> None:
    """A row that cannot fit still reaches each hidden action through »."""
    window, toolbar = _show(qtbot, 1024)
    window.setMinimumWidth(280)
    width = 1024
    overflow = _overflow_button(toolbar)

    def _enabled_hidden() -> bool:
        return any(
            action.isEnabled()
            for button, action in _actions_in_toolbar_order(toolbar)
            if not button.isVisible()
        )

    while width > 280:
        window.resize(width, 800)
        qtbot.waitUntil(lambda: toolbar.width() == window.width())
        button = window.findChild(QPushButton, "formatButton")
        assert isinstance(button, QPushButton)
        if (
            overflow.isVisible()
            and _enabled_hidden()
            and button.width() >= button.sizeHint().width()
            and _fully_inside(button, window)
        ):
            break
        width -= 40
    assert overflow.isVisible()
    assert overflow.text() == "»"
    assert _fully_inside(window.findChild(QPushButton, "formatButton"), window)

    ordered = _actions_in_toolbar_order(toolbar)
    hidden = [action for button, action in ordered if not button.isVisible()]
    shown = [action for button, action in ordered if button.isVisible()]
    menu = overflow.menu()
    assert menu is not None
    menu_actions = [action for action in menu.actions() if action.menu() is None]
    assert menu_actions == hidden
    assert hidden
    assert any(action.isEnabled() for action in hidden)
    for action in hidden:
        _assert_same_action(action)
    for action in shown:
        assert all(item is not action for item in menu.actions())


_UNBUILT_ICON_NAMES = (
    "Indent",
    "Sort keys",
    "Minify",
    "Expand all",
    "Collapse all",
    "Validate",
    "Find…",
)


def _shrink(qtbot, window, toolbar: QWidget, ready, start: int = 1024) -> int:
    """Narrow the window until ``ready`` is true. Return the width that fit."""
    window.setMinimumWidth(280)
    width = start
    while width > 280:
        window.resize(width, 800)
        qtbot.waitUntil(lambda: toolbar.width() == window.width())
        if ready():
            return width
        width -= 20
    window.resize(width, 800)
    qtbot.waitUntil(lambda: toolbar.width() == window.width())
    return width


def test_icon_only_unbuilt_actions_keep_their_name(qtbot) -> None:
    """An icon-only Coming soon button still says which action it is."""
    _window, toolbar = _show(qtbot, 1280)
    pending = set(_UNBUILT_ICON_NAMES)
    seen: set[str] = set()

    def _collect() -> bool:
        for tool in _secondary_buttons(toolbar):
            action = tool.defaultAction()
            assert isinstance(action, QAction)
            name = action.text()
            if name not in pending or not tool.isVisible():
                continue
            if tool.toolButtonStyle() != Qt.ToolButtonStyle.ToolButtonIconOnly:
                continue
            if not _fully_inside(tool, toolbar):
                continue
            label = f"{name} — coming soon"
            assert tool.toolTip() == label
            assert tool.accessibleName() == label
            host = tool.parentWidget()
            assert host is not None
            assert host.property("comingSoonHost") is True
            assert host.toolTip() == label
            assert host.accessibleName() == label
            assert action.toolTip() == COMING_SOON
            seen.add(name)
            pending.discard(name)
        return not pending

    _shrink(qtbot, _window, toolbar, _collect, start=1280)
    assert seen == set(_UNBUILT_ICON_NAMES)


def test_hidden_indent_size_is_an_overflow_submenu(qtbot) -> None:
    window, toolbar = _show(qtbot, 1024)
    combo = window.findChild(QComboBox, "indentSize")
    assert isinstance(combo, QComboBox)
    overflow = _overflow_button(toolbar)
    wide_menu = overflow.menu()
    assert wide_menu is not None
    assert all(item.menu() is None for item in wide_menu.actions())

    _shrink(qtbot, window, toolbar, lambda: not combo.isVisible())
    window.resize(500, 800)
    qtbot.waitUntil(lambda: toolbar.width() == 500)
    assert not combo.isVisible()
    menu = overflow.menu()
    assert menu is not None
    submenus = [item.menu() for item in menu.actions() if item.menu() is not None]
    assert len(submenus) == 1
    submenu = submenus[0]
    assert isinstance(submenu, QMenu)
    assert submenu.title() == "Indent size"
    assert submenu.objectName() == "indentSizeMenu"
    labels = [item.text() for item in submenu.actions()]
    assert labels == ["2 spaces", "4 spaces", "Tab"]
    assert submenu.actions()[0].isChecked()
    four = submenu.actions()[1]
    four.trigger()
    assert combo.currentText() == "4 spaces"
    assert four.isChecked()
    assert not submenu.actions()[0].isChecked()
    combo.setCurrentText("Tab")
    assert submenu.actions()[2].isChecked()
    assert not four.isChecked()


def test_tab_reaches_the_overflow_button(qtbot) -> None:
    window, toolbar = _show(qtbot, 1024)
    overflow = _overflow_button(toolbar)
    _shrink(qtbot, window, toolbar, overflow.isVisible)
    assert overflow.isVisible()
    assert overflow.focusPolicy() & Qt.FocusPolicy.TabFocus

    overflow.setFocus()
    qtbot.waitUntil(lambda: window.focusWidget() is overflow)
    qtbot.keyClick(overflow, Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
    previous = window.focusWidget()
    assert previous is not None
    assert previous is not overflow
    qtbot.keyClick(previous, Qt.Key.Key_Tab)
    assert window.focusWidget() is overflow


def test_overflow_button_hides_the_menu_indicator(qtbot) -> None:
    _window, toolbar = _show(qtbot, 1280)
    overflow = _overflow_button(toolbar)
    plain = QToolButton()
    plain.setText(overflow.text())
    plain.setAutoRaise(True)
    plain.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    plain.setFont(overflow.font())
    plain.ensurePolished()
    overflow.ensurePolished()
    assert overflow.sizeHint().width() <= plain.sizeHint().width()


def _channel_distance(left: QColor, right: QColor) -> int:
    return (
        abs(left.red() - right.red())
        + abs(left.green() - right.green())
        + abs(left.blue() - right.blue())
    )


def _most_opaque(icon: QIcon, mode: QIcon.Mode) -> QColor:
    image = icon.pixmap(16, 16, mode).toImage()
    chosen: QColor | None = None
    alpha = 0
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.alpha() > alpha:
                chosen = color
                alpha = color.alpha()
    assert chosen is not None
    assert alpha > 200
    return chosen


def _contrast_palette(*, dark: bool) -> QPalette:
    """Active and disabled ink far enough apart to sample after antialiasing."""
    palette = QPalette()
    if dark:
        window = QColor("#1C1C1C")
        base = QColor("#121212")
        text = QColor("#F4F1EC")
        disabled_text = QColor("#6A625C")
        placeholder = QColor("#E4DDD4")
        disabled_placeholder = QColor("#4A433E")
    else:
        window = QColor("#F3F2F2")
        base = QColor("#FFFFFF")
        text = QColor("#201E1D")
        placeholder = QColor("#5E5B5B")
        # Fake ink, not the app palette. Orange is disabled window text and
        # green is disabled placeholder, so a swapped role cannot pass.
        disabled_text = QColor("#C2410C")
        disabled_placeholder = QColor("#148C2A")
    groups = (
        QPalette.ColorGroup.Active,
        QPalette.ColorGroup.Inactive,
        QPalette.ColorGroup.Disabled,
    )
    for group in groups:
        disabled = group == QPalette.ColorGroup.Disabled
        palette.setColor(group, QPalette.ColorRole.Window, window)
        palette.setColor(group, QPalette.ColorRole.Base, base)
        palette.setColor(group, QPalette.ColorRole.Button, window)
        palette.setColor(
            group, QPalette.ColorRole.WindowText, disabled_text if disabled else text
        )
        palette.setColor(
            group, QPalette.ColorRole.Text, disabled_text if disabled else text
        )
        palette.setColor(
            group,
            QPalette.ColorRole.ButtonText,
            disabled_text if disabled else text,
        )
        palette.setColor(
            group,
            QPalette.ColorRole.PlaceholderText,
            disabled_placeholder if disabled else placeholder,
        )
    return palette


def _strongest_ink(image: QImage, rect: QRect, background: QColor) -> QColor:
    """The pixel in ``rect`` furthest from the menu background."""
    chosen = background
    distance = -1
    for y in range(rect.top(), rect.bottom()):
        for x in range(rect.left(), rect.right()):
            color = image.pixelColor(x, y)
            gap = _channel_distance(color, background)
            if gap > distance:
                chosen = color
                distance = gap
    assert distance > 40
    return chosen


def _paint_menu_item(menu: QMenu, action: QAction) -> QImage:
    image = QImage(260, 32, QImage.Format.Format_ARGB32_Premultiplied)
    background = menu.palette().color(QPalette.ColorRole.Window)
    image.fill(background)
    painter = QPainter(image)
    option = QStyleOptionMenuItem()
    option.initFrom(menu)
    option.rect = QRect(0, 0, 260, 32)
    option.text = action.text()
    option.icon = action.icon()
    option.maxIconWidth = 16
    option.menuItemType = QStyleOptionMenuItem.MenuItemType.Normal
    option.palette = menu.palette()
    state = QStyle.StateFlag.State_None
    if action.isEnabled():
        state |= QStyle.StateFlag.State_Enabled
    option.state = state
    menu.style().drawControl(QStyle.ControlElement.CE_MenuItem, option, painter, menu)
    painter.end()
    return image


@pytest.mark.parametrize("dark", [False, True])
def test_overflow_disabled_items_use_the_disabled_palette(qtbot, dark: bool) -> None:
    app = QApplication.instance()
    assert isinstance(app, QApplication)
    previous = QPalette(app.palette())
    try:
        # The window applies the saved appearance when it is created, so
        # the test palette goes in afterwards, the way a scheme switch would.
        window, toolbar = _show(qtbot, 1024)
        set_application_palette(app, _contrast_palette(dark=dark))
        overflow = _overflow_button(toolbar)

        def _mixed_menu() -> bool:
            menu = overflow.menu()
            if menu is None or not overflow.isVisible():
                return False
            actions = [item for item in menu.actions() if item.menu() is None]
            return any(item.isEnabled() for item in actions) and any(
                not item.isEnabled() for item in actions
            )

        _shrink(qtbot, window, toolbar, _mixed_menu)
        palette = app.palette()
        role = QPalette.ColorRole.PlaceholderText
        active_ink = palette.color(QPalette.ColorGroup.Active, role)
        disabled_ink = palette.color(QPalette.ColorGroup.Disabled, role)
        assert _channel_distance(active_ink, disabled_ink) > 120

        disabled_button = next(
            tool
            for tool in _secondary_buttons(toolbar)
            if isinstance(tool.defaultAction(), QAction)
            and tool.defaultAction().text() == "Indent"
        )
        icon = disabled_button.icon()
        normal = _most_opaque(icon, QIcon.Mode.Normal)
        disabled = _most_opaque(icon, QIcon.Mode.Disabled)
        assert _channel_distance(normal, active_ink) < _channel_distance(
            normal, disabled_ink
        )
        assert _channel_distance(disabled, disabled_ink) < _channel_distance(
            disabled, active_ink
        )
        assert _channel_distance(disabled, disabled_ink) < 80

        menu = overflow.menu()
        assert isinstance(menu, QMenu)
        background = palette.color(QPalette.ColorRole.Window)
        active_text = palette.color(
            QPalette.ColorGroup.Active, QPalette.ColorRole.WindowText
        )
        disabled_text = palette.color(
            QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText
        )
        enabled_item = next(item for item in menu.actions() if item.isEnabled())
        disabled_item = next(
            item
            for item in menu.actions()
            if item.menu() is None and not item.isEnabled()
        )
        enabled_image = _paint_menu_item(menu, enabled_item)
        disabled_image = _paint_menu_item(menu, disabled_item)
        icon_rect = QRect(4, 6, 18, 20)
        text_rect = QRect(40, 4, 200, 24)
        enabled_icon = _strongest_ink(enabled_image, icon_rect, background)
        disabled_icon = _strongest_ink(disabled_image, icon_rect, background)
        enabled_text = _strongest_ink(enabled_image, text_rect, background)
        disabled_label = _strongest_ink(disabled_image, text_rect, background)
        assert _channel_distance(enabled_icon, active_ink) < _channel_distance(
            enabled_icon, disabled_ink
        )
        assert _channel_distance(disabled_icon, disabled_ink) < _channel_distance(
            disabled_icon, active_ink
        )
        assert _channel_distance(enabled_text, active_text) < _channel_distance(
            enabled_text, disabled_text
        )
        assert _channel_distance(disabled_label, disabled_text) < _channel_distance(
            disabled_label, active_text
        )
        assert _channel_distance(disabled_label, disabled_text) < 80
    finally:
        app.setPalette(previous)
