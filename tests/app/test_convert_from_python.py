"""Convert from Python writes the output editor and shows notices."""

import json

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QComboBox, QLabel, QMenu, QPushButton, QToolButton

from galley.app.editor import CodeEditor
from galley.app.main import create_main_window
from galley.app.parse_session import ParseSession
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


def test_convert_is_in_the_edit_menu_beside_format_without_a_shortcut(qtbot) -> None:
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)

    convert = _action(window, "Convert from Python")
    assert convert.shortcut().isEmpty()
    assert all(sequence.isEmpty() for sequence in convert.shortcuts())
    buttons = [
        button
        for button in window.findChildren(QToolButton)
        if button.defaultAction() is convert
    ]
    assert len(buttons) == 1

    edit = _menu(window, "Edit")
    labels = [
        "|" if action.isSeparator() else action.text() for action in edit.actions()
    ]
    format_at = labels.index("Format")
    assert labels[format_at + 1] == "Convert from Python"


def test_convert_is_disabled_while_format_runs_and_returns_after(
    qtbot, monkeypatch
) -> None:
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)

    editor = window.findChild(CodeEditor, "inputEditor")
    button = window.findChild(QPushButton, "formatButton")
    assert isinstance(editor, CodeEditor)
    assert isinstance(button, QPushButton)
    editor.setPlainText('{"a": 1}')
    convert = _action(window, "Convert from Python")
    assert convert.isEnabled()
    assert button.isEnabled()

    monkeypatch.setattr(ParseSession, "active_job_id", property(lambda _self: 1))
    window._session.submit = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    _action(window, "Format").trigger()
    assert button.isEnabled()
    assert not _action(window, "Format").isEnabled()
    assert not convert.isEnabled()
    editor.insertPlainText(" ")
    assert not convert.isEnabled()

    window._session.finished_job_id = window._accepted_job_id
    window._on_format_succeeded('{\n  "a": 1\n}\n')
    assert button.isEnabled()
    assert convert.isEnabled()

    _action(window, "Format").trigger()
    assert not convert.isEnabled()
    assert button.isEnabled()
    assert not _action(window, "Format").isEnabled()
    window._cancel_format()
    assert convert.isEnabled()
    assert button.isEnabled()


def test_convert_success_names_the_output_format(qtbot) -> None:
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)

    editor = window.findChild(CodeEditor, "inputEditor")
    output = window.findChild(CodeEditor, "outputEditor")
    status = window.findChild(QLabel, "statusMessage")
    output_format = window.findChild(QComboBox, "outputFormat")
    assert isinstance(editor, CodeEditor)
    assert isinstance(output, CodeEditor)
    assert status is not None
    assert isinstance(output_format, QComboBox)

    source = "[1, 2]"
    editor.setPlainText(source)
    convert = _action(window, "Convert from Python")
    convert.trigger()
    json_result = convert_python(source, "JSON")
    assert isinstance(json_result, ConvertResult)
    assert json_result.notices == ()
    assert output.toPlainText() == json_result.text
    assert status.text() == "Converted to JSON."

    output_format.setCurrentText("YAML")
    convert.trigger()
    yaml_result = convert_python(source, "YAML")
    assert isinstance(yaml_result, ConvertResult)
    assert output.toPlainText() == yaml_result.text
    assert status.text() == "Converted to YAML."


def _menu(window, title: str) -> QMenu:
    # See tests/app/test_main_window.py:_menu. PySide 6.9 invalidates some
    # QAction.menu() wrappers after QMenuBar.actions() is released.
    menu = window.findChild(QMenu, "menu" + title.replace(" ", ""))
    assert menu is not None, title
    assert menu.title() == title
    assert menu.menuAction() in window.menuBar().actions()
    return menu
