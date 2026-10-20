"""Readability: system font, undo availability, editor page, and the reset rail."""

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QPalette,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QPushButton,
    QSplitter,
    QToolButton,
    QWidget,
)

from galley.app.actions import COMING_SOON, is_unbuilt
from galley.app.editor import CodeEditor
from galley.app.fonts import ui_font
from galley.app.main import create_main_window
from galley.app.splitter import RESET_NAME
from galley.app.theme import ACCENT

_ICON_ONLY = ("Outdent", "Indent", "Expand all", "Collapse all")


def _show(qtbot):
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def _action(window, text: str) -> QAction:
    found = [item for item in window.findChildren(QAction) if item.text() == text]
    assert len(found) == 1, text
    return found[0]


def _is_serif(font: QFont) -> bool:
    if font.styleHint() == QFont.StyleHint.Serif:
        return True
    family = font.family().casefold().strip()
    if family.startswith("sans"):
        return False
    return family in {
        "serif",
        "times",
        "times new roman",
        "georgia",
        "liberation serif",
        "nimbus roman",
        "nimbus roman no9 l",
        "dejavu serif",
    }


def test_ui_font_is_the_system_font_and_editors_stay_mono(qtbot) -> None:
    window = _show(qtbot)
    system = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    assert window.font().family() == system.family()
    assert ui_font().family() == system.family()
    assert not _is_serif(window.font())
    editors = []
    for widget in window.findChildren(QWidget):
        assert "font-family" not in widget.styleSheet().casefold()
        if isinstance(widget, CodeEditor):
            editors.append(widget)
            assert widget.font().fixedPitch()
            assert widget.font().styleHint() == QFont.StyleHint.Monospace
            base = widget.palette().color(QPalette.ColorRole.Base)
            app_base = QApplication.palette().color(QPalette.ColorRole.Base)
            assert base.name() == app_base.name()
            assert (
                widget.gutter_color().name()
                == widget.palette().color(QPalette.ColorRole.AlternateBase).name()
            )
        else:
            assert not _is_serif(widget.font())
    assert len(editors) == 2


def test_undo_enables_after_paste_and_follows_the_editor_stack(qtbot) -> None:
    window = _show(qtbot)
    undo = _action(window, "Undo")
    redo = _action(window, "Redo")
    editor = window.findChild(CodeEditor, "inputEditor")
    assert isinstance(editor, CodeEditor)
    assert not undo.isEnabled()
    assert not redo.isEnabled()
    qtbot.waitUntil(lambda: editor.hasFocus())
    QApplication.clipboard().setText('{"a": 1}')
    _action(window, "Paste").trigger()
    qtbot.waitUntil(lambda: undo.isEnabled())
    assert editor.toPlainText() == '{"a": 1}'
    assert not redo.isEnabled()
    undo.trigger()
    qtbot.waitUntil(lambda: editor.toPlainText() == "")
    assert not undo.isEnabled()
    assert redo.isEnabled()
    redo.trigger()
    assert editor.toPlainText() == '{"a": 1}'
    assert undo.isEnabled()
    assert not redo.isEnabled()


def test_reset_split_has_a_name_and_returns_to_even(qtbot) -> None:
    window = _show(qtbot)
    splitter = window.findChild(QSplitter, "mainSplitter")
    assert isinstance(splitter, QSplitter)
    qtbot.waitUntil(lambda: sum(splitter.sizes()) > 600)
    rail = splitter.handle(1)
    reset = rail.findChild(QToolButton, "resetSplitButton")
    ratio = rail.findChild(QLabel, "splitRatio")
    assert isinstance(reset, QToolButton)
    assert isinstance(ratio, QLabel)
    assert reset.accessibleName() == RESET_NAME
    assert reset.toolTip() == RESET_NAME
    assert ratio.text().split() == ["50", "50"]
    assert not reset.isEnabled()
    total = sum(splitter.sizes())
    leading = int(total * 0.49)
    splitter.setSizes([leading, max(1, total - leading)])
    qtbot.waitUntil(lambda: ratio.text().split()[0] != "50")
    assert reset.isEnabled()
    qtbot.mouseClick(reset, Qt.MouseButton.LeftButton)

    def _even() -> bool:
        sizes = splitter.sizes()
        whole = sum(sizes)
        return whole > 0 and abs(sizes[0] - sizes[1]) / whole < 0.02

    qtbot.waitUntil(_even)
    if ratio.text().split() == ["50", "50"]:
        assert not reset.isEnabled()


def test_caption_and_status_are_readable(qtbot) -> None:
    window = _show(qtbot)
    caption = window.findChild(QLabel, "editingCaption")
    status = window.findChild(QLabel, "statusMessage")
    direction = window.findChild(QLabel, "directionLabel")
    assert caption is not None and status is not None and direction is not None
    assert "Input 1" in caption.text()
    assert caption.minimumWidth() >= caption.fontMetrics().horizontalAdvance(
        "Editing Input 1"
    )
    assert caption.font().pixelSize() == 11
    assert status.font().pixelSize() >= 12
    assert direction.font().pixelSize() >= 12
    assert status.contentsMargins().left() >= 8
    assert status.contentsMargins().right() >= 8
    layout = window.statusBar().layout()
    assert layout is not None
    margins = layout.contentsMargins()
    assert margins.left() >= 8
    assert margins.right() >= 8


def test_empty_heading_wraps_in_a_bounded_width(qtbot) -> None:
    window = _show(qtbot)
    heading = window.findChild(QLabel, "inputEmptyTitle")
    hint = window.findChild(QLabel, "inputEmptyHint")
    assert heading is not None and hint is not None
    assert heading.maximumWidth() == 360
    assert heading.font().pixelSize() == 15
    assert heading.font().weight() >= QFont.Weight.DemiBold
    assert heading.alignment() & Qt.AlignmentFlag.AlignCenter
    assert not _is_serif(heading.font())


def _is_white(color: QColor) -> bool:
    return color.red() == 255 and color.green() == 255 and color.blue() == 255


def _explicit_dark(palette: QPalette, light: QColor) -> QPalette:
    """A dark application palette whose Base is not the light-mode Base."""
    dark = QPalette(palette)
    base = QColor(28, 28, 28)
    if base.rgba() == light.rgba():
        base = QColor(12, 12, 12)
    for group in (
        QPalette.ColorGroup.Active,
        QPalette.ColorGroup.Inactive,
        QPalette.ColorGroup.Disabled,
    ):
        dark.setColor(group, QPalette.ColorRole.Base, base)
        dark.setColor(group, QPalette.ColorRole.AlternateBase, QColor(36, 36, 36))
        dark.setColor(group, QPalette.ColorRole.Window, QColor(24, 24, 24))
        dark.setColor(group, QPalette.ColorRole.Text, QColor(240, 240, 240))
        dark.setColor(group, QPalette.ColorRole.WindowText, QColor(240, 240, 240))
    return dark


def test_editor_base_is_not_white_in_dark_mode(qtbot) -> None:
    window = _show(qtbot)
    editor = window.findChild(CodeEditor, "inputEditor")
    assert isinstance(editor, CodeEditor)
    light = QColor(editor.palette().color(QPalette.ColorRole.Base))
    app = QApplication.instance()
    assert isinstance(app, QApplication)
    previous_palette = QPalette(app.palette())
    hints = QGuiApplication.styleHints()
    previous_scheme = hints.colorScheme()
    try:
        setter = getattr(hints, "setColorScheme", None)
        if setter is not None:
            setter(Qt.ColorScheme.Dark)
            app.processEvents()
        dark = editor.palette().color(QPalette.ColorRole.Base)
        if dark.rgba() == light.rgba() or _is_white(dark):
            app.setPalette(_explicit_dark(previous_palette, light))
            app.processEvents()
            dark = editor.palette().color(QPalette.ColorRole.Base)
        assert dark.rgba() != light.rgba()
        assert not _is_white(dark)
        assert dark.rgba() == app.palette().color(QPalette.ColorRole.Base).rgba()
    finally:
        app.setPalette(previous_palette)
        restore = getattr(hints, "setColorScheme", None)
        if restore is not None:
            restore(previous_scheme)
            app.processEvents()
            assert hints.colorScheme() == previous_scheme


def test_brand_color_is_format_fill_and_find_highlight(qtbot) -> None:
    window = _show(qtbot)
    button = window.findChild(QPushButton, "formatButton")
    assert button is not None
    sheet = button.styleSheet()
    assert ACCENT in sheet
    assert sheet.lower().count(ACCENT.lower()) == 1
    editor = window.findChild(CodeEditor, "inputEditor")
    assert isinstance(editor, CodeEditor)
    assert editor.find_highlight_color().name() == ACCENT.lower()
    accent = QApplication.palette().color(QPalette.ColorRole.Accent)
    assert accent.name() == ACCENT.lower()


def test_icon_only_toolbar_buttons_have_tooltips(qtbot) -> None:
    window = _show(qtbot)
    for name in _ICON_ONLY:
        buttons = [
            button
            for button in window.findChildren(QToolButton)
            if isinstance(button.defaultAction(), QAction)
            and button.defaultAction().text() == name
        ]
        assert len(buttons) == 1, name
        button = buttons[0]
        assert button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
        assert button.accessibleName() == name
        assert not button.icon().isNull()
        action = button.defaultAction()
        assert isinstance(action, QAction)
        if is_unbuilt(action):
            assert button.toolTip() == COMING_SOON
        else:
            assert button.toolTip()
            assert button.toolTip() != COMING_SOON


def test_format_pickers_keep_their_items(qtbot) -> None:
    window = _show(qtbot)
    picker = window.findChild(QComboBox, "inputFormat")
    pop = next(
        button
        for button in window.findChildren(QPushButton)
        if button.text() == "Pop out"
    )
    assert isinstance(picker, QComboBox)
    assert [picker.itemText(index) for index in range(picker.count())] == [
        "JSON",
        "YAML",
    ]
    assert pop.accessibleName() == "Pop out into window"
