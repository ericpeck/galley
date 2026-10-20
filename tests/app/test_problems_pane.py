"""Problems pane, gutter markers, and plain errors in the window."""

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import QComboBox, QListWidget, QPushButton, QSplitter

from galley.app.editor import CodeEditor
from galley.app.main import create_main_window
from galley.app.problem_text import display_problem
from galley.app.problems_pane import SHOW_REPLACE_TABS_BUTTON, ProblemsPane
from galley.app.problems_settings import problems_settings
from galley.app.status_message import StatusMessage
from galley.app.tab_repair import replace_leading_tabs
from galley.app.theme import ERROR
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


def _has_error_pixel(image) -> bool:
    target = QColor(ERROR)
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if (
                abs(color.red() - target.red()) < 16
                and abs(color.green() - target.green()) < 16
                and abs(color.blue() - target.blue()) < 16
            ):
                return True
    return False
