"""Footer counts and Problems rows, said once and without a column.

#227: "1 character", thousands separators on every count, and row
sentences that name the line at most, never the column. #229: a row's
accessible name is the message alone when the message already names its
line or the error has no line, and ``Line N: message`` otherwise (Bill).
"""

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QComboBox

from galley.app.banner import banner_text
from galley.app.editor import CodeEditor
from galley.app.main import create_main_window
from galley.app.problem_text import display_input_error, display_problem
from galley.app.problems_pane import ProblemsPane
from galley.core.errors import (
    InputError,
    escape_error,
    nesting_error,
    utf8_input_error,
)
from galley.core.problems import Problem

_WAIT_MS = 5_000
# Fake text: 951 lines of 40 characters and 333 of 39, joined by 1,283
# newlines, so 1,284 lines and 52,310 characters.
_MANY_LINES = "\n".join(["x" * 40] * 951 + ["y" * 39] * 333)
# A bad byte on line 4, column 8, after valid UTF-8.
_BAD_ON_LINE_4 = b'{\n  "a": 1,\n  "b": 2,\n  "c": \xff}\n'
# A bad byte after an earlier bad byte, so only the offset is known.
_BAD_AFTER_BAD = b"\xffab\xfe"


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


def _editor(window, name: str) -> CodeEditor:
    editor = window.findChild(CodeEditor, name)
    assert isinstance(editor, CodeEditor), name
    return editor


def _footer(qtbot, label, shown: str) -> None:
    qtbot.waitUntil(lambda: label.text() == shown, timeout=_WAIT_MS)


def _row_names(qtbot, pane: ProblemsPane) -> list[str]:
    widget = pane.list_widget()
    names = []
    for index in range(widget.count()):
        row = widget.itemWidget(widget.item(index))
        assert row is not None
        assert widget.item(index).text() == row.accessibleName()
        names.append(row.accessibleName())
    return names


def _utf8_error(data: bytes) -> InputError:
    """The error for the last bad byte in ``data``."""
    return utf8_input_error(data, len(data.rstrip(b"}\n")) - 1)


# --- Footer counts (#227) ----------------------------------------------------


def test_the_input_footer_says_1_character_and_separates_thousands(
    qtbot, scheme_palette
) -> None:
    window = _show(qtbot)
    editor = _editor(window, "inputEditor")
    label = window._input_pane.count_label
    for text, shown in (
        ("a", "1 line · 1 character"),
        ("a" * 999, "1 line · 999 characters"),
        ("a" * 1_000, "1 line · 1,000 characters"),
        (_MANY_LINES, "1,284 lines · 52,310 characters"),
    ):
        editor.setPlainText(text)
        _footer(qtbot, label, shown)


def test_the_output_footer_separates_thousands(qtbot, scheme_palette) -> None:
    window = _show(qtbot)
    editor = _editor(window, "outputEditor")
    label = window._output_pane.count_label
    for text, shown in (("1", "1 line"), (_MANY_LINES, "1,284 lines")):
        editor.setPlainText(text)
        _footer(qtbot, label, shown)
        assert "character" not in label.text()


def test_the_compare_footer_separates_thousands(qtbot, scheme_palette) -> None:
    window = _show(qtbot)
    _editor(window, "inputEditor").setPlainText("[1]")
    _action(window, "Compare").trigger()
    assert _action(window, "Compare").isChecked()
    right = _editor(window, "rightEditor")
    label = window._output_pane.count_label
    for text, shown in (("[1]", "1 line"), (_MANY_LINES, "1,284 lines")):
        right.setPlainText(text)
        _footer(qtbot, label, shown)
        assert "character" not in label.text()
        qtbot.waitUntil(lambda: not window._compare.is_running(), timeout=_WAIT_MS)


# --- Input error rows (#227) -------------------------------------------------


def test_input_error_rows_name_the_line_never_the_column() -> None:
    utf8 = _utf8_error(_BAD_ON_LINE_4)
    assert (utf8.lineno, utf8.colno) == (4, 8)
    offset_only = _utf8_error(_BAD_AFTER_BAD)
    assert (offset_only.lineno, offset_only.colno) == (None, None)
    escape = escape_error(4, 9, "\\udc80")
    deep = nesting_error(4, 9, depth=513)
    rows = {
        "utf8": display_input_error(utf8),
        "offset": display_input_error(offset_only),
        "escape": display_input_error(escape),
        "deep": display_input_error(deep),
    }
    assert rows["utf8"].message == (
        "This file isn't valid UTF-8, so Galley can't read it. "
        "The first bad byte is on line 4. "
        "Save the file as UTF-8 and open it again."
    )
    assert rows["escape"].message == (
        "Line 4 has an invalid character escape (\\udc80) that can't be saved as UTF-8."
    )
    # These two banners name no column, so the row keeps them as they are.
    assert rows["offset"].message == banner_text(offset_only)
    assert "The first bad byte is at byte 3." in rows["offset"].message
    assert rows["deep"].message == banner_text(deep)
    assert rows["deep"].message.endswith("The limit was reached at line 4.")
    for row in rows.values():
        assert "column" not in row.message.casefold()
    # The place line keeps the column; the offset-only row's is unchanged.
    assert (rows["utf8"].lineno, rows["utf8"].colno) == (4, 8)
    assert (rows["escape"].lineno, rows["escape"].colno) == (4, 9)
    assert (rows["offset"].lineno, rows["offset"].colno) == (1, 0)
    # The banners don't change.
    assert banner_text(utf8) == (
        "This file isn't valid UTF-8, so Galley can't read it. "
        "The first bad byte is at line 4, column 8. "
        "Save the file as UTF-8 and open it again."
    )
    assert banner_text(escape) == (
        "Line 4, column 9 has an invalid character escape (\\udc80) "
        "that can't be saved as UTF-8."
    )


# --- Accessible names (#229) -------------------------------------------------


def test_a_problem_rows_name_says_its_line_once(qtbot, scheme_palette) -> None:
    named = Problem("missing_comma", 2, colno=9)
    unnamed = Problem("xml_no_root", 1)
    pane = ProblemsPane()
    qtbot.addWidget(pane)
    pane.set_problems((display_problem(named), display_problem(unnamed)))
    assert pane.messages() == [
        "Missing comma at the end of line 2.",
        "There's no root element.",
    ]
    assert _row_names(qtbot, pane) == [
        "Missing comma at the end of line 2.",
        "Line 1: There's no root element.",
    ]


def test_an_input_error_rows_name_is_its_message(qtbot, scheme_palette) -> None:
    errors = (
        _utf8_error(_BAD_ON_LINE_4),
        escape_error(4, 9, "\\udc80"),
        nesting_error(4, 9, depth=513),
        _utf8_error(_BAD_AFTER_BAD),
    )
    rows = tuple(display_input_error(error) for error in errors)
    pane = ProblemsPane()
    qtbot.addWidget(pane)
    pane.set_problems(rows)
    messages = [row.message for row in rows]
    assert pane.messages() == messages
    # Each names its line already, or (the last) has no line: never "Line 1:".
    assert _row_names(qtbot, pane) == messages
    assert not any(name.startswith("Line 1:") for name in messages)


def test_a_convert_rows_name_says_its_line_once(qtbot, scheme_palette) -> None:
    window = _show(qtbot)
    picker = window.findChild(QComboBox, "outputFormat")
    assert isinstance(picker, QComboBox)
    picker.setCurrentText("JSON")
    editor = _editor(window, "inputEditor")
    pane = window.findChild(ProblemsPane)
    assert isinstance(pane, ProblemsPane)
    for text, message, name in (
        (
            "x = 1",
            'Remove "x =" on line 1. Convert from Python reads a value, '
            "not an assignment.",
            None,
        ),
        (
            "print(1)",
            "Function calls can't be converted.",
            "Line 1: Function calls can't be converted.",
        ),
    ):
        editor.setPlainText(text)
        _action(window, "Convert from Python").trigger()
        qtbot.waitUntil(lambda m=message: pane.messages() == [m], timeout=_WAIT_MS)
        assert _row_names(qtbot, pane) == [name or message]
