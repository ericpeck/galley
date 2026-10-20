"""Problems pane, gutter markers, and plain errors in the window."""

from PySide6.QtCore import QPoint, Qt, QtMsgType, qInstallMessageHandler
from PySide6.QtGui import (
    QAction,
    QColor,
    QImage,
    QKeySequence,
    QPalette,
    QPixmap,
    QRegion,
)
from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QListWidget,
    QPushButton,
    QSplitter,
    QWidget,
)

from galley.app.editor import CodeEditor
from galley.app.main import create_main_window
from galley.app.problem_text import DisplayProblem, display_problem
from galley.app.problems_pane import SHOW_REPLACE_TABS_BUTTON, ProblemsPane
from galley.app.problems_settings import problems_settings
from galley.app.status_message import StatusMessage
from galley.app.style import muted_text_style
from galley.app.tab_repair import replace_leading_tabs
from galley.app.theme import ERROR, selected_problem_palette
from galley.core.problems import Problem

_MISSING = '{\n  "price": 4.5\n  "qty": 3\n}\n'
_TWO_TABS = "a:\n\tb: 1\n\tc: 2\n"


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


def _format(_qtbot, window, text: str, source: str = "JSON") -> StatusMessage:
    editor = window.findChild(CodeEditor, "inputEditor")
    assert isinstance(editor, CodeEditor)
    if source != "JSON":
        picker = window.findChild(QComboBox, "inputFormat")
        assert picker is not None
        picker.setCurrentText(source)
    editor.setPlainText(text)
    status = window.findChild(StatusMessage, "statusMessage")
    assert isinstance(status, StatusMessage)
    _action(window, "Format").trigger()
    return status


def _wait_status(qtbot, status: StatusMessage, text: str) -> None:
    qtbot.waitUntil(lambda: status.text() == text, timeout=15000)


def _pane(window) -> ProblemsPane:
    pane = window.findChild(ProblemsPane, "problemsPane")
    assert isinstance(pane, ProblemsPane)
    return pane


def _editor(window) -> CodeEditor:
    editor = window.findChild(CodeEditor, "inputEditor")
    assert isinstance(editor, CodeEditor)
    return editor


def test_building_the_main_window_logs_no_stylesheet_parse_warning(qtbot) -> None:
    warnings: list[str] = []

    def handler(mode: QtMsgType, context: object, message: str) -> None:
        del context
        if mode == QtMsgType.QtWarningMsg and "Could not parse stylesheet" in message:
            warnings.append(message)

    previous = qInstallMessageHandler(handler)
    try:
        window = create_main_window()
        qtbot.addWidget(window)
        window.show()
        qtbot.waitExposed(window)
    finally:
        qInstallMessageHandler(previous)
    assert warnings == []


def test_replace_leading_tabs_is_one_undo(qtbot) -> None:
    editor = CodeEditor()
    qtbot.addWidget(editor)
    original = "a:\n\tb: 1\n\tc: 2\n"
    editor.setPlainText(original)
    replace_leading_tabs(editor)
    assert "\t" not in editor.toPlainText()
    assert "  b: 1" in editor.toPlainText()
    assert "  c: 2" in editor.toPlainText()
    editor.undo()
    assert editor.toPlainText() == original


def test_replace_button_stays_hidden_when_the_flag_is_off(qtbot) -> None:
    assert SHOW_REPLACE_TABS_BUTTON is False
    window = _show(qtbot)
    pane = _pane(window)
    pane.set_problems((display_problem(Problem("yaml_tab", 8)),))
    button = window.findChild(QPushButton, "replaceTabsButton")
    assert isinstance(button, QPushButton)
    assert button.text() == "Replace tabs with spaces"
    assert not button.isEnabled()
    assert not button.isVisibleTo(pane)


def test_gutter_paints_a_marker_on_the_error_line(qtbot) -> None:
    editor = CodeEditor()
    qtbot.addWidget(editor)
    editor.resize(420, 180)
    editor.show()
    qtbot.waitExposed(editor)
    editor.setPlainText("one\ntwo\nthree\n")
    editor.set_problem_lines({2})
    editor.reveal_line(2)
    assert editor.problem_lines() == {2}
    assert editor.textCursor().blockNumber() == 1
    image = editor.line_number_area().grab().toImage()
    assert not image.isNull()
    assert _has_error_pixel(image)


def test_missing_comma_pane_jumps_and_status_is_only_a_count(qtbot) -> None:
    window = _show(qtbot)
    status = _format(qtbot, window, _MISSING)
    _wait_status(qtbot, status, "1 problem")
    lowered = status.text().casefold()
    assert "column" not in lowered
    assert "(char" not in lowered
    assert "expecting" not in lowered
    pane = _pane(window)
    qtbot.waitUntil(lambda: pane.isVisible() and pane.width() >= 200, timeout=15000)
    assert pane.messages() == ["Missing comma at the end of line 2."]
    assert pane.lines() == [2]
    assert _editor(window).problem_lines() == {2}
    assert _action(window, "Show Problems").isChecked()
    row = pane.list_widget().itemWidget(pane.list_widget().item(0))
    assert row is not None
    assert row.accessibleName() == "Line 2: Missing comma at the end of line 2."
    qtbot.mouseClick(row, Qt.MouseButton.LeftButton)
    assert _editor(window).textCursor().blockNumber() == 1
    origin = status.mapTo(
        window.statusBar(),
        QPoint(status.contentsRect().left(), 0),
    )
    assert origin.x() >= 8
    assert status.contentsMargins().left() >= 8
    assert status.contentsMargins().right() >= 8
    image = _editor(window).line_number_area().grab().toImage()
    assert _has_error_pixel(image)
    minimize = window.findChild(QPushButton, "problemsMinimize")
    close = window.findChild(QPushButton, "problemsClose")
    assert isinstance(minimize, QPushButton)
    assert isinstance(close, QPushButton)
    assert minimize.size().width() == 24 and minimize.size().height() == 24
    assert close.size().width() == 24 and close.size().height() == 24
    assert minimize.accessibleName() == "Minimize"
    assert minimize.toolTip() == "Minimize"
    assert close.accessibleName() == "Close"
    assert close.toolTip() == "Close"


def test_yaml_tabs_list_every_line_and_the_repair_stays_hidden(qtbot) -> None:
    window = _show(qtbot)
    status = _format(qtbot, window, _TWO_TABS, source="YAML")
    _wait_status(qtbot, status, "2 problems")
    pane = _pane(window)
    qtbot.waitUntil(
        lambda: (
            pane.messages()
            == [
                "Line 2 starts with a tab. YAML needs spaces for indenting.",
                "Line 3 starts with a tab. YAML needs spaces for indenting.",
            ]
        ),
        timeout=15000,
    )
    assert "cannot start any token" not in " ".join(pane.messages())
    button = window.findChild(QPushButton, "replaceTabsButton")
    assert isinstance(button, QPushButton)
    assert not button.isVisibleTo(pane)
    problems = pane.list_widget()
    assert isinstance(problems, QListWidget)
    assert problems.accessibleName() == "Problems"
    assert problems.item(0) is not None
    assert problems.item(0).text() == (
        "Line 2: Starts with a tab. YAML needs spaces for indenting."
    )
    problems.setFocus()
    qtbot.keyClick(problems, Qt.Key.Key_Down)
    qtbot.keyClick(problems, Qt.Key.Key_Return)
    assert _editor(window).textCursor().blockNumber() == 2
    problems.setFocus()
    qtbot.keyClick(problems, Qt.Key.Key_Tab)
    assert _editor(window).hasFocus()


def test_close_stays_closed_and_the_count_or_menu_reopens_it(qtbot) -> None:
    window = _show(qtbot)
    status = _format(qtbot, window, _MISSING)
    _wait_status(qtbot, status, "1 problem")
    pane = _pane(window)
    qtbot.waitUntil(lambda: pane.isVisible(), timeout=15000)
    window.findChild(QPushButton, "problemsClose").click()
    assert not pane.isVisible()
    assert status.text() == "1 problem"
    assert not _action(window, "Show Problems").isChecked()
    editor = _editor(window)
    editor.setPlainText('{"a": "hi}')
    _action(window, "Format").trigger()
    qtbot.waitUntil(
        lambda: pane.messages() == ["Unclosed quote on line 1."],
        timeout=15000,
    )
    assert not pane.isVisible()
    assert status.text() == "1 problem"
    qtbot.mouseClick(status, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: pane.isVisible(), timeout=15000)
    assert _action(window, "Show Problems").isChecked()
    _action(window, "Show Problems").trigger()
    assert not pane.isVisible()
    assert not _action(window, "Show Problems").isChecked()
    qtbot.keySequence(window, QKeySequence("Ctrl+Shift+M"))
    qtbot.waitUntil(lambda: pane.isVisible(), timeout=15000)
    assert _action(window, "Show Problems").isChecked()


def test_minimize_expands_from_the_strip(qtbot) -> None:
    window = _show(qtbot)
    status = _format(qtbot, window, _MISSING)
    _wait_status(qtbot, status, "1 problem")
    pane = _pane(window)
    qtbot.waitUntil(lambda: pane.isVisible(), timeout=15000)
    window.findChild(QPushButton, "problemsMinimize").click()
    strip = window.findChild(QPushButton, "problemsStrip")
    assert isinstance(strip, QPushButton)
    qtbot.waitUntil(lambda: pane.width() == 28 and strip.isVisible(), timeout=15000)
    assert strip.text() == "1"
    assert "1 problem" in strip.accessibleName()
    assert _action(window, "Show Problems").isChecked()
    strip.click()
    qtbot.waitUntil(
        lambda: pane.width() >= 200 and not strip.isVisible(),
        timeout=15000,
    )


def test_closed_state_persists_across_a_new_window(qtbot) -> None:
    window = _show(qtbot)
    status = _format(qtbot, window, _MISSING)
    _wait_status(qtbot, status, "1 problem")
    pane = _pane(window)
    qtbot.waitUntil(lambda: pane.isVisible(), timeout=15000)
    splitter = window.findChild(QSplitter, "problemsSplitter")
    assert isinstance(splitter, QSplitter)
    splitter.setSizes([340, 800])
    qtbot.waitUntil(lambda: pane.width() >= 300, timeout=15000)
    saved = int(problems_settings().value("problems/width"))
    assert saved >= 300
    window.findChild(QPushButton, "problemsClose").click()
    assert problems_settings().value("problems/state") == "closed"
    window.close()

    again = _show(qtbot)
    status = _format(qtbot, again, "abc")
    _wait_status(qtbot, status, "1 problem")
    restored = _pane(again)
    assert not restored.isVisible()
    assert restored.messages() == ["Couldn't read this JSON on line 1."]
    assert not _action(again, "Show Problems").isChecked()
    _action(again, "Show Problems").trigger()
    qtbot.waitUntil(lambda: restored.isVisible(), timeout=15000)
    assert abs(restored.width() - saved) <= 40


def test_pane_hides_once_the_input_is_fixed(qtbot) -> None:
    window = _show(qtbot)
    status = _format(qtbot, window, _MISSING)
    _wait_status(qtbot, status, "1 problem")
    pane = _pane(window)
    qtbot.waitUntil(lambda: pane.isVisible(), timeout=15000)
    _editor(window).setPlainText('{"price": 4.5, "qty": 3}\n')
    _action(window, "Format").trigger()
    _wait_status(qtbot, status, "Valid JSON")
    assert not pane.isVisible()
    assert pane.count() == 0
    assert _editor(window).problem_lines() == set()
    output = window.findChild(CodeEditor, "outputEditor")
    assert isinstance(output, CodeEditor)
    assert '"price"' in output.toPlainText()


def _show_pane_alone(qtbot) -> ProblemsPane:
    pane = ProblemsPane()
    qtbot.addWidget(pane)
    pane.resize(320, 300)
    pane.set_problems(
        (
            display_problem(Problem("yaml_tab", 3)),
            display_problem(Problem("yaml_tab", 5)),
        )
    )
    pane.show()
    qtbot.waitExposed(pane)
    return pane


def test_list_items_paint_no_text_under_the_rows(qtbot) -> None:
    pane = _show_pane_alone(qtbot)
    problems = pane.list_widget()
    item = problems.item(0)
    assert item.isSelected()
    assert item.text() == "Line 3: Starts with a tab. YAML needs spaces for indenting."
    rect = problems.visualItemRect(item)
    viewport = problems.viewport()
    pixmap = QPixmap(viewport.size())
    pixmap.fill(QColor("#123456"))
    # Leave out the row widgets so only the list's own painting shows.
    viewport.render(pixmap, QPoint(), QRegion(), QWidget.RenderFlag(0))
    image = pixmap.toImage().copy(rect)
    assert _colors(image) == {QColor("#123456").rgb()}


def test_selection_bar_stays_on_the_row_not_its_labels(qtbot, scheme_palette) -> None:
    pane = _show_pane_alone(qtbot)
    window = scheme_palette.color(QPalette.ColorRole.Window)
    problems = pane.list_widget()
    selected = problems.itemWidget(problems.item(0))
    other = problems.itemWidget(problems.item(1))
    assert selected is not None and other is not None

    image = selected.grab().toImage()
    bar = image.pixelColor(0, image.height() // 2)
    assert _contrast(_seen(bar, window), window) >= 3
    other_image = other.grab().toImage()
    other_bar = other_image.pixelColor(0, other_image.height() // 2)
    assert _contrast(_seen(other_bar, window), window) < 3

    labels = selected.findChildren(QLabel)
    assert len(labels) == 3
    assert "problemMarker" in {label.objectName() for label in labels}
    for label in labels:
        assert label.contentsMargins().left() == 0
        assert not _has_pixel(image.copy(label.geometry()), bar, 4)


def test_the_selected_row_reads_in_light_and_dark(qtbot, scheme_palette) -> None:
    pane = _show_pane_alone(qtbot)
    window = scheme_palette.color(QPalette.ColorRole.Window)
    problems = pane.list_widget()
    row = problems.itemWidget(problems.item(0))
    assert row is not None
    problems.setFocus()
    qtbot.waitUntil(problems.hasFocus)

    # The row grab is what users see: alpha text already blended on the fill.
    image = row.grab().toImage()
    fill = image.pixelColor(image.width() - 4, image.height() - 4)
    tint = selected_problem_palette(row.palette()).color(QPalette.ColorRole.Highlight)
    assert _near(fill, tint)
    outline = image.pixelColor(image.width() // 2, 0)
    bar = image.pixelColor(0, image.height() // 2)
    for edge in (outline, bar):
        assert _contrast(edge, window) >= 3
        assert _contrast(edge, fill) >= 3
    texts = [
        label
        for label in row.findChildren(QLabel)
        if label.objectName() != "problemMarker"
    ]
    assert len(texts) == 2
    for label in texts:
        glyphs = image.copy(label.geometry())
        assert _strongest_contrast(glyphs, fill) >= 4.5


def test_unselected_rows_keep_the_muted_message_color(qtbot, scheme_palette) -> None:
    pane = _show_pane_alone(qtbot)
    problems = pane.list_widget()
    other = problems.itemWidget(problems.item(1))
    assert other is not None
    message = [label for label in other.findChildren(QLabel) if label.wordWrap()]
    assert len(message) == 1
    assert message[0].styleSheet() == muted_text_style()


def test_the_minimized_strip_stylesheet_parses(qtbot, qtlog) -> None:
    pane = _show_pane_alone(qtbot)
    pane.set_minimized(True)
    strip = pane.findChild(QPushButton, "problemsStrip")
    assert isinstance(strip, QPushButton)
    strip.resize(28, 200)
    qtbot.waitUntil(strip.isVisible)
    image = strip.grab().toImage()
    assert _near(image.pixelColor(2, 2), QColor(ERROR))
    assert not [
        record
        for record in qtlog.records
        if "Could not parse stylesheet" in record.message
    ]


def test_the_focused_list_outlines_the_current_row(qtbot, scheme_palette) -> None:
    pane = _show_pane_alone(qtbot)
    problems = pane.list_widget()
    first = problems.itemWidget(problems.item(0))
    second = problems.itemWidget(problems.item(1))
    assert first is not None and second is not None
    assert not _outlined(first)

    problems.setFocus()
    qtbot.waitUntil(problems.hasFocus)
    assert _outlined(first)
    assert not _outlined(second)

    qtbot.keyClick(problems, Qt.Key.Key_Down)
    assert not _outlined(first)
    assert _outlined(second)

    pane.minimize_button().setFocus()
    qtbot.waitUntil(lambda: not problems.hasFocus())
    assert not _outlined(first)
    assert not _outlined(second)


def test_the_list_paints_no_text_while_focused(qtbot) -> None:
    pane = _show_pane_alone(qtbot)
    problems = pane.list_widget()
    problems.setFocus()
    qtbot.waitUntil(problems.hasFocus)
    item = problems.currentItem()
    viewport = problems.viewport()
    pixmap = QPixmap(viewport.size())
    pixmap.fill(QColor("#123456"))
    viewport.render(pixmap, QPoint(), QRegion(), QWidget.RenderFlag(0))
    image = pixmap.toImage().copy(problems.visualItemRect(item))
    assert _colors(image) == {QColor("#123456").rgb()}


def test_a_rewrapped_row_has_no_gap_under_the_line_number(qtbot) -> None:
    message = (
        "Line 3 starts with a tab. YAML needs spaces for indenting, "
        "so replace the tab before formatting again."
    )
    pane = ProblemsPane()
    qtbot.addWidget(pane)
    pane.resize(320, 400)
    pane.set_problems((DisplayProblem(3, message, f"Line 3: {message}", "yaml_tab"),))
    pane.show()
    qtbot.waitExposed(pane)
    pane.resize(520, 400)
    problems = pane.list_widget()
    row = problems.itemWidget(problems.item(0))
    assert row is not None
    qtbot.waitUntil(lambda: row.width() > 400)

    line, text = (
        label
        for label in row.findChildren(QLabel)
        if label.objectName() != "problemMarker"
    )
    assert line.height() == line.sizeHint().height()
    assert text.geometry().top() == line.geometry().bottom() + 1
    assert row.height() == max(row.heightForWidth(row.width()), 56)


def _outlined(row: QWidget) -> bool:
    """The top edge stands out 3:1 from the window, as an outline must."""
    window = row.palette().color(QPalette.ColorRole.Window)
    image = row.grab().toImage()
    top = _seen(image.pixelColor(image.width() // 2, 0), window)
    return _contrast(top, window) >= 3


def _seen(color: QColor, background: QColor) -> QColor:
    """``color`` blended over ``background``, as it appears on screen."""
    alpha = color.alphaF()
    return QColor(
        round(color.red() * alpha + background.red() * (1 - alpha)),
        round(color.green() * alpha + background.green() * (1 - alpha)),
        round(color.blue() * alpha + background.blue() * (1 - alpha)),
    )


def _luminance(color: QColor) -> float:
    def channel(value: int) -> float:
        srgb = value / 255
        if srgb <= 0.04045:
            return srgb / 12.92
        return ((srgb + 0.055) / 1.055) ** 2.4

    return (
        0.2126 * channel(color.red())
        + 0.7152 * channel(color.green())
        + 0.0722 * channel(color.blue())
    )


def _contrast(first: QColor, second: QColor) -> float:
    lighter = max(_luminance(first), _luminance(second))
    darker = min(_luminance(first), _luminance(second))
    return (lighter + 0.05) / (darker + 0.05)


def _strongest_contrast(image: QImage, background: QColor) -> float:
    """Contrast of the glyph pixel that stands out most from the fill."""
    return max(
        _contrast(image.pixelColor(x, y), background)
        for y in range(image.height())
        for x in range(image.width())
    )


def _colors(image: QImage) -> set[int]:
    return {
        image.pixel(x, y) for y in range(image.height()) for x in range(image.width())
    }


def _has_pixel(image: QImage, target: QColor, tolerance: int = 16) -> bool:
    for y in range(image.height()):
        for x in range(image.width()):
            if _near(image.pixelColor(x, y), target, tolerance):
                return True
    return False


def _near(color: QColor, target: QColor, tolerance: int = 8) -> bool:
    return (
        abs(color.red() - target.red()) <= tolerance
        and abs(color.green() - target.green()) <= tolerance
        and abs(color.blue() - target.blue()) <= tolerance
    )


def _has_error_pixel(image: QImage) -> bool:
    return _has_pixel(image, QColor(ERROR))
