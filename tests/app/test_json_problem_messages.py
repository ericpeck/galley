"""Specific JSON problems reach the Problems pane and the gutter."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QImage
from PySide6.QtWidgets import QMainWindow

from galley.app.editor import CodeEditor
from galley.app.main import create_main_window
from galley.app.problems_pane import ProblemsPane
from galley.app.status_message import StatusMessage
from galley.app.theme import error_color

_CASES = {
    "unquoted key": (
        '{\n  "a": 1,\n  b: 2\n}\n',
        3,
        "Put double quotes around the key on line 3.",
    ),
    "single quotes": (
        "{\n  'a': 1\n}\n",
        2,
        "Use double quotes instead of single quotes on line 2.",
    ),
    "missing square bracket": (
        '{\n  "tags": [1, 2\n}\n',
        2,
        "Missing ] for the [ opened on line 2.",
    ),
    "missing curly bracket": (
        '[\n  {"a": 1\n]\n',
        2,
        "Missing } for the { opened on line 2.",
    ),
    "missing ] at the end of the text": (
        '{\n  "a": [\n    1,\n    2\n',
        2,
        "Missing ] for the [ opened on line 2.",
    ),
    "missing } before a later ]": (
        '[\n  {\n    "a": 1,\n    "b": 2\n]\n',
        2,
        "Missing } for the { opened on line 2.",
    ),
    "missing comma between objects": (
        '[\n  {"a": 1}\n  {"b": 2}\n]\n',
        2,
        "Missing comma at the end of line 2.",
    ),
    "trailing comma before ]": (
        "[\n  1,\n  2,\n]\n",
        3,
        "Trailing comma at the end of line 3.",
    ),
    "trailing comma before }": (
        '{\n  "a": 1,\n}\n',
        2,
        "Trailing comma at the end of line 2.",
    ),
    "unclosed quote": (
        '{\n  "a": "hi\n}\n',
        2,
        "Unclosed quote on line 2.",
    ),
    "several could apply": (
        '{\n  "name": "Galley",\n  version: 2,\n  "tags": ["json"\n}\n',
        3,
        "Put double quotes around the key on line 3.",
    ),
}


_OPENER_CASES = [name for name, case in _CASES.items() if " opened on line " in case[2]]


@pytest.mark.parametrize("name", list(_CASES))
def test_json_problem_shows_a_row_and_a_gutter_marker(qtbot, name: str) -> None:
    text, line, message = _CASES[name]
    window, editor, pane = _format(qtbot, text)
    del window
    assert pane.messages() == [message]
    assert pane.lines() == [line]
    assert editor.problem_lines() == {line}
    assert _marker_rows(editor) == {line}


@pytest.mark.parametrize("name", _OPENER_CASES)
def test_missing_bracket_row_jumps_to_the_opener_line(qtbot, name: str) -> None:
    text, line, _message = _CASES[name]
    _window, editor, pane = _format(qtbot, text)
    last = editor.document().blockCount()
    editor.reveal_line(last)
    assert editor.textCursor().blockNumber() != line - 1
    row = pane.list_widget().itemWidget(pane.list_widget().item(0))
    assert row is not None
    assert row.accessibleName().startswith(f"Line {line}: Missing ")
    qtbot.mouseClick(row, Qt.MouseButton.LeftButton)
    assert editor.textCursor().blockNumber() == line - 1
    assert _marker_rows(editor) == {line}


def _format(qtbot, text: str) -> tuple[QMainWindow, CodeEditor, ProblemsPane]:
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    editor = window.findChild(CodeEditor, "inputEditor")
    assert isinstance(editor, CodeEditor)
    status = window.findChild(StatusMessage, "statusMessage")
    assert isinstance(status, StatusMessage)
    editor.setPlainText(text)
    _action(window, "Format").trigger()
    qtbot.waitUntil(lambda: status.text() == "1 problem", timeout=15000)
    pane = window.findChild(ProblemsPane, "problemsPane")
    assert isinstance(pane, ProblemsPane)
    qtbot.waitUntil(pane.isVisible, timeout=15000)
    return window, editor, pane


def _action(window, text: str) -> QAction:
    found = [item for item in window.findChildren(QAction) if item.text() == text]
    assert len(found) == 1, text
    return found[0]


def _marker_rows(editor: CodeEditor) -> set[int]:
    """1-based lines whose gutter band holds an error-colored pixel."""
    image = editor.line_number_area().grab().toImage()
    ratio = image.devicePixelRatio()
    target = error_color(editor.palette())
    offset = editor.contentOffset()
    found: set[int] = set()
    block = editor.document().firstBlock()
    while block.isValid():
        box = editor.blockBoundingGeometry(block).translated(offset)
        top = int(box.top() * ratio)
        bottom = int(box.bottom() * ratio)
        if _has_pixel(image, target, top, bottom):
            found.add(block.blockNumber() + 1)
        block = block.next()
    return found


def _has_pixel(image: QImage, target: QColor, top: int, bottom: int) -> bool:
    for y in range(max(top, 0), min(bottom, image.height())):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if (
                abs(color.red() - target.red()) <= 16
                and abs(color.green() - target.green()) <= 16
                and abs(color.blue() - target.blue()) <= 16
            ):
                return True
    return False
