"""Convert from Python writes the output editor and shows notices."""

import json

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QLabel

from galley.app.editor import CodeEditor
from galley.app.main import create_main_window
from galley.core.convert_python import ConvertResult, convert_python


def _action(window, text: str) -> QAction:
    matches = [item for item in window.findChildren(QAction) if item.text() == text]
    assert len(matches) == 1, text
    return matches[0]


def test_convert_from_python_shows_the_notice_and_keeps_the_result(qtbot) -> None:
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)

    editor = window.findChild(CodeEditor, "inputEditor")
    output = window.findChild(CodeEditor, "outputEditor")
    status = window.findChild(QLabel, "statusMessage")
    convert = _action(window, "Convert from Python")
    assert isinstance(editor, CodeEditor)
    assert isinstance(output, CodeEditor)
    assert status is not None
    assert not convert.isEnabled()

    source = "{'nums': (1, 2), 3: 'x'}"
    editor.setPlainText(source)
    assert convert.isEnabled()
    convert.trigger()

    result = convert_python(source, "JSON")
    assert isinstance(result, ConvertResult)
    assert output.toPlainText() == result.text
    assert json.loads(output.toPlainText()) == {"nums": [1, 2], "3": "x"}
    assert status.text() == " ".join(notice.message for notice in result.notices)
    assert "tuple" in status.text()
    assert "key" in status.text()
    assert editor.toPlainText() == source

    output.setPlainText("previous\n")
    editor.setPlainText("__import__('os')")
    convert.trigger()
    assert output.toPlainText() == "previous\n"
    assert "line 1" in status.text()
    assert "column 1" in status.text()
    assert "Function calls" in status.text()
