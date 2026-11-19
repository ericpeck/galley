"""Escape as String and Unescape String write Output and never change Input."""

import sys
from collections import Counter
from functools import cache

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import QComboBox, QLabel

import galley.app.main_window as main_window_module
from galley.app.actions import not_yet_failure
from galley.app.editor import CodeEditor
from galley.app.escape_string import (
    ESCAPED_MESSAGE,
    NOT_ESCAPED_JSON_MESSAGE,
    NOT_ESCAPED_STRING_MESSAGE,
    NOT_JSON_VALUE_MESSAGE,
    escape_too_large_message,
    unescape_too_large_message,
)
from galley.app.limits import MARKUP_FORMAT_LIMIT_BYTES
from galley.app.main import create_main_window
from galley.app.parse_session import ParseSession
from galley.app.problems_pane import ProblemsPane
from galley.app.shortcuts import ESCAPE_AS_STRING, UNESCAPE_STRING
from galley.app.style import muted_text_style
from galley.app.theme import contrast_ratio
from galley.core.format import format_document

_ESCAPE = "Escape as String"
_UNESCAPE = "Unescape String"


def _show(qtbot):
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def _action(window, text: str) -> QAction:
    matches = [item for item in window.findChildren(QAction) if item.text() == text]
    assert len(matches) == 1, text
    return matches[0]


def _child(window, kind, name: str):
    widget = window.findChild(kind, name)
    assert isinstance(widget, kind), name
    return widget


def _editors(window) -> tuple[CodeEditor, CodeEditor]:
    return (
        _child(window, CodeEditor, "inputEditor"),
        _child(window, CodeEditor, "outputEditor"),
    )


def _status(window) -> str:
    return _child(window, QLabel, "statusMessage").text()


def test_both_commands_are_built_and_follow_the_input(qtbot) -> None:
    window = _show(qtbot)
    source, _output = _editors(window)
    escape = _action(window, _ESCAPE)
    unescape = _action(window, _UNESCAPE)
    assert escape.toolTip() != "Coming soon"
    assert not escape.isEnabled()
    assert not unescape.isEnabled()
    source.setPlainText("{}")
    assert escape.isEnabled()
    assert unescape.isEnabled()
    source.setPlainText("")
    assert not escape.isEnabled()
    assert not unescape.isEnabled()

    actions = window._actions
    assert escape not in actions._not_yet_actions()
    assert unescape not in actions._not_yet_actions()
    assert all(item not in actions.stubs() for item in (escape, unescape))
    assert not_yet_failure() is None


def test_escape_writes_output_as_one_undo_step_and_keeps_input(qtbot) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    output.setPlainText("previous\n")
    text = '{\n  "a": "b",\n  "n": [1, 2]\n}\n'
    source.setPlainText(text)
    source.document().clearUndoRedoStacks()

    _action(window, _ESCAPE).trigger()

    assert output.toPlainText() == '"{\\"a\\":\\"b\\",\\"n\\":[1,2]}"\n'
    assert source.toPlainText() == text
    assert not source.document().isUndoAvailable()
    assert _status(window) == ESCAPED_MESSAGE
    output.undo()
    assert output.toPlainText() == "previous\n"
    output.redo()
    assert output.toPlainText() == '"{\\"a\\":\\"b\\",\\"n\\":[1,2]}"\n'


def test_escape_reads_the_input_picker(qtbot) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    _child(window, QComboBox, "inputFormat").setCurrentText("YAML")
    _child(window, QComboBox, "outputFormat").setCurrentText("YAML")
    source.setPlainText("a: é 😀\nq: 'say \"hi\"'\n")
    _action(window, _ESCAPE).trigger()
    assert (
        output.toPlainText()
        == '"{\\"a\\":\\"é 😀\\",\\"q\\":\\"say \\\\\\"hi\\\\\\"\\"}"\n'
    )


def test_unescape_formats_with_the_output_picker(qtbot) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    output.setPlainText("previous\n")
    text = '"{\\"a\\":[1,2],\\"b\\":\\"é\\"}"\n'
    source.setPlainText(text)

    _action(window, _UNESCAPE).trigger()
    assert output.toPlainText() == '{\n  "a": [\n    1,\n    2\n  ],\n  "b": "é"\n}\n'
    assert source.toPlainText() == text
    assert _status(window) == "Unescaped to JSON."
    output.undo()
    assert output.toPlainText() == "previous\n"

    _child(window, QComboBox, "outputFormat").setCurrentText("YAML")
    _action(window, _UNESCAPE).trigger()
    assert output.toPlainText() == "a:\n- 1\n- 2\nb: é\n"
    assert _status(window) == "Unescaped to YAML."
    assert source.toPlainText() == text


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('{"a": "b"}', NOT_ESCAPED_STRING_MESSAGE),
        ('"unclosed', NOT_ESCAPED_STRING_MESSAGE),
        ('"hello"', NOT_ESCAPED_JSON_MESSAGE),
        ('"{\\"a\\":}"', NOT_ESCAPED_JSON_MESSAGE),
        ('"{\\"a\\":1,\\"a\\":2}"', 'Remove the repeated key "a" on line 1.'),
        (
            '"{\\n  \\"a\\": 1,\\n  \\"a\\": 2\\n}"',
            'Remove the repeated key "a" on line 3.',
        ),
    ],
)
def test_unescape_errors_leave_output_and_input_alone(qtbot, text, message) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    output.setPlainText("previous\n")
    source.setPlainText(text)
    _action(window, _UNESCAPE).trigger()
    assert _status(window) == message
    assert output.toPlainText() == "previous\n"
    assert source.toPlainText() == text


def test_unescape_peels_one_layer(qtbot) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    twice = '"\\"{\\\\\\"a\\\\\\":1}\\""'
    source.setPlainText(twice)
    _action(window, _UNESCAPE).trigger()
    assert output.toPlainText() == '"{\\"a\\":1}"\n'
    assert _status(window) == "Unescaped to JSON."


def test_escape_lists_input_problems_and_leaves_output_alone(qtbot) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    output.setPlainText("previous\n")
    text = '{\n  "a": 1\n  "b": 2\n}\n'
    source.setPlainText(text)
    _action(window, _ESCAPE).trigger()

    pane = _child(window, ProblemsPane, "problemsPane")
    assert pane.messages() == ["Missing comma at the end of line 2."]
    assert _status(window) == "1 problem"
    assert output.toPlainText() == "previous\n"
    assert source.toPlainText() == text

    source.setPlainText('{"a": 1}')
    _action(window, _ESCAPE).trigger()
    assert pane.count() == 0
    assert output.toPlainText() == '"{\\"a\\":1}"\n'


def test_escape_reports_yaml_values_json_cannot_hold(qtbot) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    _child(window, QComboBox, "inputFormat").setCurrentText("YAML")
    output.setPlainText("previous\n")
    source.setPlainText("day: 2020-01-01\n")
    _action(window, _ESCAPE).trigger()
    assert _status(window) == NOT_JSON_VALUE_MESSAGE
    assert output.toPlainText() == "previous\n"


@pytest.mark.parametrize(
    "text",
    ["!!binary aGVsbG8=\n", "file:\n  data: !!binary aGVsbG8=\n", "day: 2020-01-01\n"],
)
def test_format_to_json_reports_yaml_values_json_cannot_hold(qtbot, text: str) -> None:
    """Format shows the sentence Escape shows, not the ``json`` error text."""
    window = _show(qtbot)
    source, output = _editors(window)
    _child(window, QComboBox, "inputFormat").setCurrentText("YAML")
    _child(window, QComboBox, "outputFormat").setCurrentText("JSON")
    output.setPlainText("previous\n")
    source.setPlainText(text)
    _action(window, "Format").trigger()
    qtbot.waitUntil(lambda: _status(window) == NOT_JSON_VALUE_MESSAGE, timeout=15000)
    assert NOT_JSON_VALUE_MESSAGE == (
        "This document has a value JSON can't hold, such as a date or binary data."
    )
    assert source.toPlainText() == text


@pytest.mark.parametrize(
    "text",
    [
        '{"nested": {"list": [1, {"deep": [true, null]}]}, "empty": {}, "none": []}',
        '{"quote": "\\"", "slash": "\\\\", "lines": "a\\nb\\tc\\r\\n"}',
        '{"unicode": "café 日本語", "emoji": "😀 \\ud83d\\ude00 👨‍👩‍👧"}',
    ],
)
def test_unescape_of_escape_matches_format(qtbot, text) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    source.setPlainText(text)
    _action(window, _ESCAPE).trigger()
    escaped = output.toPlainText()
    source.setPlainText(escaped)
    _action(window, _UNESCAPE).trigger()
    assert output.toPlainText() == format_document(text, "json", "json")


def test_shortcuts_use_the_control_modifier(qtbot) -> None:
    window = _show(qtbot)
    escape = _action(window, _ESCAPE)
    unescape = _action(window, _UNESCAPE)
    control = Qt.KeyboardModifier.ControlModifier
    shift = Qt.KeyboardModifier.ShiftModifier
    assert escape.shortcut() == QKeySequence(control | shift | Qt.Key.Key_E)
    assert unescape.shortcut() == QKeySequence(
        control | Qt.Key.Key_K, control | Qt.Key.Key_E
    )
    assert escape.shortcut() == ESCAPE_AS_STRING
    assert unescape.shortcut() == UNESCAPE_STRING
    portable = QKeySequence.SequenceFormat.PortableText
    assert escape.shortcut().toString(portable) == "Ctrl+Shift+E"
    assert unescape.shortcut().toString(portable) == "Ctrl+K, Ctrl+E"
    native = QKeySequence.SequenceFormat.NativeText
    if sys.platform == "darwin":
        assert escape.shortcut().toString(native) == "⇧⌘E"
        assert unescape.shortcut().toString(native) == "⌘K, ⌘E"
    else:
        assert escape.shortcut().toString(native) == "Ctrl+Shift+E"
        assert unescape.shortcut().toString(native) == "Ctrl+K, Ctrl+E"


def test_shortcut_keys_run_both_commands(qtbot) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    source.setPlainText('{"a": 1}')
    source.setFocus()
    qtbot.waitUntil(source.hasFocus)
    control = Qt.KeyboardModifier.ControlModifier
    shift = Qt.KeyboardModifier.ShiftModifier
    qtbot.keyClick(source, Qt.Key.Key_E, control | shift)
    assert output.toPlainText() == '"{\\"a\\":1}"\n'
    assert source.toPlainText() == '{"a": 1}'

    source.setPlainText('"[1]"')
    source.setFocus()
    qtbot.keyClick(source, Qt.Key.Key_K, control)
    qtbot.keyClick(source, Qt.Key.Key_E, control)
    assert output.toPlainText() == "[\n  1\n]\n"
    assert source.toPlainText() == '"[1]"'


def test_large_input_is_refused_with_its_size(
    qtbot, monkeypatch, scheme_palette
) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    output.setPlainText("previous\n")
    monkeypatch.setattr(main_window_module, "JSON_BUSY_BYTES", 2000)
    text = '"' + "x" * 2500 + '"'
    source.setPlainText(text)

    _action(window, _ESCAPE).trigger()
    # 2,502 bytes. A too-large size rounds up (Bill, #148), so it reads 2.6.
    assert _status(window) == escape_too_large_message("2.6 KB", "2 KB")
    _action(window, _UNESCAPE).trigger()
    assert _status(window) == unescape_too_large_message("2.6 KB", "2 KB")
    assert output.toPlainText() == "previous\n"

    monkeypatch.setattr(main_window_module, "YAML_BUSY_BYTES", 100)
    _child(window, QComboBox, "outputFormat").setCurrentText("YAML")
    source.setPlainText('"[1]"' + " " * 200)
    _action(window, _UNESCAPE).trigger()
    assert _status(window) == unescape_too_large_message("205 B", "100 B")
    _child(window, QComboBox, "inputFormat").setCurrentText("YAML")
    _action(window, _ESCAPE).trigger()
    assert _status(window) == escape_too_large_message("205 B", "100 B")
    assert output.toPlainText() == "previous\n"


_TWO_MB = MARKUP_FORMAT_LIMIT_BYTES


@cache
def _two_megabytes_of_json() -> str:
    """One fake JSON string of exactly 2 MB, built once for the module.

    Short lines of spaces keep the editor fast. Escape reads it as a JSON
    string, and Unescape finds ``[1]`` inside it.
    """
    line = " " * 99 + "\n"
    head = '"[1]"'
    count = (_TWO_MB - len(head)) // len(line)
    text = head + line * count + " " * (_TWO_MB - len(head) - count * len(line))
    assert len(text.encode("utf-8")) == _TWO_MB
    return text


@pytest.mark.parametrize(
    ("command", "too_large"),
    [
        pytest.param(_ESCAPE, escape_too_large_message, id="escape"),
        pytest.param(_UNESCAPE, unescape_too_large_message, id="unescape"),
    ],
)
def test_two_megabytes_runs_and_one_byte_more_is_refused(
    qtbot, monkeypatch, command: str, too_large
) -> None:
    """The limit is the largest Input that runs, as for Format's markup guard.

    Format refuses XML and HTML only over 2 MB, and the status says "The
    limit is 2 MB", so Escape and Unescape use the same boundary (#201).
    Their own limit is Format's busy size for the format, so the test sets
    it to 2 MB to check the boundary at the same size.
    """
    window = _show(qtbot)
    source, output = _editors(window)
    text = _two_megabytes_of_json()
    monkeypatch.setattr(main_window_module, "JSON_BUSY_BYTES", _TWO_MB)

    output.setPlainText("previous\n")
    source.setPlainText(text)
    _action(window, command).trigger()
    assert "too large" not in _status(window)
    assert output.toPlainText() != "previous\n"

    output.setPlainText("previous\n")
    source.setPlainText(text + " ")
    _action(window, command).trigger()
    assert _status(window) == too_large("2.1 MB", "2 MB")
    assert output.toPlainText() == "previous\n"


def test_both_commands_are_disabled_while_format_runs(qtbot, monkeypatch) -> None:
    window = _show(qtbot)
    source, _output = _editors(window)
    source.setPlainText('{"a": 1}')
    monkeypatch.setattr(ParseSession, "active_job_id", property(lambda _self: 1))
    window._session.submit = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    _action(window, "Format").trigger()
    assert not _action(window, _ESCAPE).isEnabled()
    assert not _action(window, _UNESCAPE).isEnabled()
    window._cancel_format()
    assert _action(window, _ESCAPE).isEnabled()
    assert _action(window, _UNESCAPE).isEnabled()


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize(
    "message",
    [ESCAPED_MESSAGE, NOT_ESCAPED_STRING_MESSAGE, NOT_ESCAPED_JSON_MESSAGE],
)
def test_status_text_is_readable(qtbot, install_scheme, scheme, message) -> None:
    window = _show(qtbot)
    palette = install_scheme(scheme)
    source, _output = _editors(window)
    label = _child(window, QLabel, "statusMessage")
    assert label.styleSheet() == muted_text_style()
    source.setPlainText(
        {
            ESCAPED_MESSAGE: '{"a": 1}',
            NOT_ESCAPED_STRING_MESSAGE: "[1]",
            NOT_ESCAPED_JSON_MESSAGE: '"hello"',
        }[message]
    )
    command = _ESCAPE if message == ESCAPED_MESSAGE else _UNESCAPE
    _action(window, command).trigger()
    assert label.text() == message
    qtbot.wait(0)
    image = label.grab().toImage()
    pixels = [
        QColor(image.pixel(x, y))
        for x in range(image.width())
        for y in range(image.height())
    ]
    fill = Counter(color.rgb() for color in pixels).most_common(1)[0][0]
    assert QColor(fill).name() == palette.window().color().name()
    ink = max(contrast_ratio(color, QColor(fill)) for color in pixels)
    assert ink >= 4.5
