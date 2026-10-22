"""Convert from Python writes the output editor and shows notices."""

import json
import statistics
import time

import pytest
from benchmarks.size_bench import render_python_literal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QComboBox, QLabel, QMenu, QPushButton, QToolButton

import galley.app.main_window as main_window_module
from galley.app.editor import CodeEditor
from galley.app.escape_string import convert_python_too_large_message
from galley.app.limits import PYTHON_TO_JSON_BYTES, PYTHON_TO_YAML_BYTES
from galley.app.main import create_main_window
from galley.app.main_window import si_size
from galley.app.parse_session import ParseSession
from galley.core.convert_python import ConvertResult, convert_python

_LIMITS = pytest.mark.parametrize(
    ("target", "limit"),
    [("JSON", PYTHON_TO_JSON_BYTES), ("YAML", PYTHON_TO_YAML_BYTES)],
)


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


def _convert_window(qtbot, target: str):
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
    output_format.setCurrentText(target)
    return window, editor, output, status


def _refuse_to_convert(*_args, **_kwargs):
    raise AssertionError("Convert from Python ran above its size limit.")


def test_size_limits_are_about_1_mb_to_json_and_192_kb_to_yaml() -> None:
    assert PYTHON_TO_JSON_BYTES == 1_000_000
    assert PYTHON_TO_YAML_BYTES == 192_000
    assert PYTHON_TO_YAML_BYTES == main_window_module.YAML_BUSY_BYTES


@_LIMITS
def test_convert_runs_at_the_limit_for_the_output_format(qtbot, target, limit) -> None:
    window, editor, output, status = _convert_window(qtbot, target)
    text = render_python_literal(limit)
    assert len(text.encode("utf-8")) == limit
    editor.setPlainText(text)
    _action(window, "Convert from Python").trigger()

    result = convert_python(text, target)
    assert isinstance(result, ConvertResult)
    assert output.toPlainText() == result.text
    assert status.text() == f"Converted to {target}."
    assert editor.toPlainText() == text


@_LIMITS
def test_convert_refuses_one_byte_over_the_limit(
    qtbot, monkeypatch, target, limit
) -> None:
    window, editor, output, status = _convert_window(qtbot, target)
    output.setPlainText("previous\n")
    text = render_python_literal(limit + 1)
    editor.setPlainText(text)
    monkeypatch.setattr(main_window_module, "convert_python", _refuse_to_convert)
    _action(window, "Convert from Python").trigger()

    assert output.toPlainText() == "previous\n"
    assert editor.toPlainText() == text
    assert status.text() == convert_python_too_large_message(
        si_size(limit + 1), si_size(limit)
    )


@pytest.mark.parametrize(
    ("target", "size", "message"),
    [
        (
            "JSON",
            3_300_000,
            "This document is too large to convert from Python (3.3 MB). "
            "The limit is 1 MB.",
        ),
        (
            "YAML",
            250_000,
            "This document is too large to convert from Python (250 KB). "
            "The limit is 192 KB.",
        ),
    ],
)
def test_size_message_names_the_size_and_the_limit_for_the_output_format(
    qtbot, monkeypatch, target, size, message
) -> None:
    window, editor, output, status = _convert_window(qtbot, target)
    output.setPlainText("previous\n")
    editor.setPlainText(render_python_literal(size))
    monkeypatch.setattr(main_window_module, "convert_python", _refuse_to_convert)
    _action(window, "Convert from Python").trigger()
    assert status.text() == message
    assert output.toPlainText() == "previous\n"


def test_size_is_utf8_bytes_of_input(qtbot, monkeypatch) -> None:
    window, editor, output, status = _convert_window(qtbot, "YAML")
    output.setPlainText("previous\n")
    row = "    '" + "é" * 50 + "',\n"
    text = "[\n" + row * 2000 + "]\n"
    assert len(text) < PYTHON_TO_YAML_BYTES < len(text.encode("utf-8"))
    editor.setPlainText(text)
    monkeypatch.setattr(main_window_module, "convert_python", _refuse_to_convert)
    _action(window, "Convert from Python").trigger()
    size = si_size(len(text.encode("utf-8")))
    assert status.text() == convert_python_too_large_message(size, "192 KB")
    assert output.toPlainText() == "previous\n"


@pytest.mark.perf
@_LIMITS
def test_convert_at_the_limit_stays_under_half_a_second(target, limit) -> None:
    # The same work as the size bench rows python_to_json and python_to_yaml.
    # Showing the result in Output costs the same as after a Format.
    text = render_python_literal(limit)
    convert_python(text, target)
    seconds = []
    for _run in range(3):
        started = time.perf_counter()
        result = convert_python(text, target)
        seconds.append(time.perf_counter() - started)
        assert isinstance(result, ConvertResult)
    assert statistics.median(seconds) < 0.5


def _menu(window, title: str) -> QMenu:
    # See tests/app/test_main_window.py:_menu. PySide 6.9 invalidates some
    # QAction.menu() wrappers after QMenuBar.actions() is released.
    menu = window.findChild(QMenu, "menu" + title.replace(" ", ""))
    assert menu is not None, title
    assert menu.title() == title
    assert menu.menuAction() in window.menuBar().actions()
    return menu
