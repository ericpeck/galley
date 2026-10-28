"""Format and Escape show plain sentences for YAML that JSON can't hold (#116).

A key JSON can't hold, such as a date, and an alias that contains itself
used to reach the status bar as Python's own error text. Every value is
fake.
"""

import pytest
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QComboBox, QLabel

import galley.app.escape_string as words
from galley.app.editor import CodeEditor
from galley.app.main import create_main_window

# The words Bill approves. The tests check the window shows exactly these.
_KEY = (
    "This document has a key JSON can't hold, such as a date. "
    "JSON keys must be text or numbers."
)
_SELF = "This document refers to itself, so it can't be written as JSON."
_VALUE = "This document has a value JSON can't hold, such as a date or binary data."

_CASES = pytest.mark.parametrize(
    ("text", "message"),
    [
        ("2020-01-01: fake-note\n", _KEY),
        ("events:\n  2020-01-01 12:30:00: fake-note\n", _KEY),
        ("a: &x [fake-note, *x]\n", _SELF),
        ("a: &x {b: *x}\n", _SELF),
    ],
    ids=["date-key", "datetime-key", "self-reference-list", "self-reference-map"],
)


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


def test_the_sentences_live_in_escape_string() -> None:
    assert getattr(words, "NOT_JSON_KEY_MESSAGE", None) == _KEY
    assert getattr(words, "SELF_REFERENCE_MESSAGE", None) == _SELF
    assert words.NOT_JSON_VALUE_MESSAGE == _VALUE


@_CASES
def test_format_to_json_shows_the_sentence(qtbot, text: str, message: str) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    _child(window, QComboBox, "inputFormat").setCurrentText("YAML")
    _child(window, QComboBox, "outputFormat").setCurrentText("JSON")
    output.setPlainText("previous\n")
    source.setPlainText(text)
    _action(window, "Format").trigger()
    qtbot.waitUntil(lambda: _status(window) == message, timeout=15000)
    assert source.toPlainText() == text
    assert "fake-note" not in _status(window)


@_CASES
def test_escape_as_string_shows_the_sentence(qtbot, text: str, message: str) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    _child(window, QComboBox, "inputFormat").setCurrentText("YAML")
    output.setPlainText("previous\n")
    source.setPlainText(text)
    _action(window, "Escape as String").trigger()
    assert _status(window) == message
    assert output.toPlainText() == "previous\n"
