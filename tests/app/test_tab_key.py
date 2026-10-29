"""The Tab key types spaces up to the next indent stop (#91 item 6).

Over several lines, Tab indents them and Shift+Tab outdents them. The unit
is the indent size picker's: 2 spaces by default.
"""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QTextCursor
from PySide6.QtWidgets import QApplication, QComboBox

from galley.app.editor import DEFAULT_INDENT_UNIT, CodeEditor
from galley.app.main import create_main_window
from galley.app.problems_pane import ProblemsPane

_CONTROL = Qt.KeyboardModifier.ControlModifier
# The Control key on macOS, where Qt's Control is ⌘.
_META = Qt.KeyboardModifier.MetaModifier
_SHIFT = Qt.KeyboardModifier.ShiftModifier


def _editor(qtbot, text: str, mode: str) -> CodeEditor:
    editor = CodeEditor()
    qtbot.addWidget(editor)
    editor.set_syntax_mode(mode)
    editor.setPlainText(text)
    return editor


def _place(editor: CodeEditor, position: int, anchor: int | None = None) -> None:
    cursor = editor.textCursor()
    cursor.setPosition(position if anchor is None else anchor)
    if anchor is not None:
        cursor.setPosition(position, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)


def _show(qtbot):
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def _input(window) -> CodeEditor:
    editor = window.findChild(CodeEditor, "inputEditor")
    assert isinstance(editor, CodeEditor)
    return editor


def _focus(qtbot, editor: CodeEditor) -> None:
    editor.activateWindow()
    editor.setFocus()
    qtbot.waitUntil(lambda: QApplication.focusWidget() is editor)


def test_the_default_unit_is_two_spaces(qtbot) -> None:
    assert DEFAULT_INDENT_UNIT == "  "
    assert _editor(qtbot, "", "JSON").indent_unit() == "  "


@pytest.mark.parametrize("mode", ["JSON", "YAML"])
@pytest.mark.parametrize(
    ("column", "spaces"),
    [(0, 2), (1, 1), (2, 2), (3, 1), (4, 2), (5, 1)],
)
def test_tab_types_spaces_to_the_next_stop(qtbot, mode, column, spaces) -> None:
    line = "abcdef"
    editor = _editor(qtbot, f"x\n{line}\n", mode)
    _place(editor, 2 + column)
    qtbot.keyClick(editor, Qt.Key.Key_Tab)
    expected = line[:column] + " " * spaces + line[column:]
    assert editor.toPlainText() == f"x\n{expected}\n"
    assert "\t" not in editor.toPlainText()
    assert editor.textCursor().positionInBlock() == column + spaces


@pytest.mark.parametrize("mode", ["JSON", "YAML"])
def test_repeated_tabs_step_from_stop_to_stop(qtbot, mode) -> None:
    editor = _editor(qtbot, "a", mode)
    _place(editor, 1)
    for _ in range(3):
        qtbot.keyClick(editor, Qt.Key.Key_Tab)
    assert editor.toPlainText() == "a" + " " * 5
    assert editor.textCursor().positionInBlock() == 6


def test_tab_replaces_the_selection_with_spaces(qtbot) -> None:
    editor = _editor(qtbot, "key: value\n", "YAML")
    # Select "value" (columns 5-10). Column 5 is one space from the next stop.
    _place(editor, 10, anchor=5)
    qtbot.keyClick(editor, Qt.Key.Key_Tab)
    assert editor.toPlainText() == "key:  \n"
    assert not editor.textCursor().hasSelection()
    editor.undo()
    assert editor.toPlainText() == "key: value\n"


_LINES = "a:\n  b: 1\n  c: 2\n"


_MULTI_LINE = [
    # (position, anchor, lines Tab indents)
    (12, 5, (1, 2)),  # forward, from inside line 2 to inside line 3
    (5, 12, (1, 2)),  # backward
    (13, 1, (0, 1, 2)),  # forward, from inside line 1 to inside line 3
    (3, 0, (0,)),  # the whole first line, ending at the start of line 2
    (10, 0, (0, 1)),  # lines 1 and 2, ending at the start of line 3
    (0, 10, (0, 1)),  # the same, selected backward
    (len(_LINES), 0, (0, 1, 2)),  # the whole document, ending on the empty line
    (0, len(_LINES), (0, 1, 2)),  # the whole document, selected backward
]


def _indented(text: str, lines: tuple[int, ...], unit: str = "  ") -> str:
    rows = text.split("\n")
    return "\n".join(unit + row if i in lines else row for i, row in enumerate(rows))


def _assert_one_undo_step(editor: CodeEditor, before: str) -> None:
    """One undo restores ``before`` and leaves nothing more to undo."""
    editor.undo()
    assert editor.toPlainText() == before
    assert not editor.document().isUndoAvailable()


def _selected_lines(editor: CodeEditor) -> tuple[int, int]:
    cursor = editor.textCursor()
    document = editor.document()
    first = document.findBlock(cursor.selectionStart()).blockNumber()
    last = document.findBlock(cursor.selectionEnd())
    end = last.blockNumber()
    if end > first and cursor.selectionEnd() == last.position():
        end -= 1
    return first, end


@pytest.mark.parametrize(("position", "anchor", "lines"), _MULTI_LINE)
def test_tab_indents_a_multi_line_selection(qtbot, position, anchor, lines) -> None:
    window = _show(qtbot)
    editor = _input(window)
    editor.set_syntax_mode("YAML")
    editor.setPlainText(_LINES)
    editor.document().clearUndoRedoStacks()
    _focus(qtbot, editor)
    _place(editor, position, anchor=anchor)
    qtbot.keyClick(window.windowHandle(), Qt.Key.Key_Tab)
    assert editor.toPlainText() == _indented(_LINES, lines)
    assert _selected_lines(editor) == (min(lines), max(lines))
    assert QApplication.focusWidget() is editor
    _assert_one_undo_step(editor, _LINES)


@pytest.mark.parametrize(("position", "anchor", "lines"), _MULTI_LINE)
def test_shift_tab_outdents_a_multi_line_selection(
    qtbot, position, anchor, lines
) -> None:
    window = _show(qtbot)
    editor = _input(window)
    editor.set_syntax_mode("YAML")
    editor.setPlainText(_LINES)
    _focus(qtbot, editor)
    _place(editor, position, anchor=anchor)
    qtbot.keyClick(window.windowHandle(), Qt.Key.Key_Tab)
    editor.document().clearUndoRedoStacks()
    qtbot.keyClick(window.windowHandle(), Qt.Key.Key_Backtab, _SHIFT)
    assert editor.toPlainText() == _LINES
    assert _selected_lines(editor) == (min(lines), max(lines))
    assert QApplication.focusWidget() is editor
    _assert_one_undo_step(editor, _indented(_LINES, lines))


def test_select_all_then_tab_indents_every_line(qtbot) -> None:
    editor = _editor(qtbot, _LINES, "JSON")
    editor.document().clearUndoRedoStacks()
    editor.selectAll()
    qtbot.keyClick(editor, Qt.Key.Key_Tab)
    # The selection ends at the start of the empty last line, which stays.
    assert editor.toPlainText() == _indented(_LINES, (0, 1, 2))
    assert _selected_lines(editor) == (0, 2)
    _assert_one_undo_step(editor, _LINES)


@pytest.mark.parametrize(
    ("unit", "column", "typed"),
    [("    ", 0, "    "), ("    ", 1, "   "), ("    ", 5, "   "), ("\t", 2, "\t")],
)
def test_tab_steps_by_the_editors_unit(qtbot, unit, column, typed) -> None:
    editor = _editor(qtbot, "abcdef", "JSON")
    editor.set_indent_unit(unit)
    _place(editor, column)
    qtbot.keyClick(editor, Qt.Key.Key_Tab)
    assert editor.toPlainText() == "abcdef"[:column] + typed + "abcdef"[column:]


@pytest.mark.parametrize("mode", ["JSON", "YAML"])
def test_one_undo_removes_the_spaces(qtbot, mode) -> None:
    original = '{\n"a": 1\n}\n' if mode == "JSON" else "a:\nb: 1\n"
    editor = _editor(qtbot, original, mode)
    _place(editor, editor.document().findBlockByNumber(1).position())
    qtbot.keyClick(editor, Qt.Key.Key_Tab)
    assert editor.toPlainText() != original
    editor.undo()
    assert editor.toPlainText() == original
    assert not editor.document().isUndoAvailable()


def test_each_tab_is_its_own_undo_step(qtbot) -> None:
    editor = _editor(qtbot, "a", "YAML")
    _place(editor, 1)
    qtbot.keyClick(editor, Qt.Key.Key_Tab)
    qtbot.keyClick(editor, Qt.Key.Key_Tab)
    assert editor.toPlainText() == "a   "
    editor.undo()
    assert editor.toPlainText() == "a "
    editor.undo()
    assert editor.toPlainText() == "a"


def test_read_only_editor_types_nothing(qtbot) -> None:
    editor = _editor(qtbot, "a: 1\n", "YAML")
    editor.setReadOnly(True)
    _place(editor, 0)
    qtbot.keyClick(editor, Qt.Key.Key_Tab)
    assert editor.toPlainText() == "a: 1\n"


def test_shift_tab_without_a_multi_line_selection_moves_focus_back(qtbot) -> None:
    window = _show(qtbot)
    editor = _input(window)
    editor.setPlainText("a:\n  b: 1\n")
    _focus(qtbot, editor)
    _place(editor, editor.document().findBlockByNumber(1).position() + 2)
    qtbot.keyClick(window.windowHandle(), Qt.Key.Key_Backtab, _SHIFT)
    assert editor.toPlainText() == "a:\n  b: 1\n"
    assert QApplication.focusWidget() is not editor


@pytest.mark.parametrize("modifier", [_CONTROL, _META], ids=["control", "meta"])
def test_control_tab_moves_focus_out_of_the_editor(qtbot, modifier) -> None:
    window = _show(qtbot)
    editor = _input(window)
    editor.setPlainText("a: 1\n")
    _focus(qtbot, editor)
    qtbot.keyClick(window.windowHandle(), Qt.Key.Key_Tab, modifier)
    after = QApplication.focusWidget()
    assert after is not None
    assert after is not editor
    assert not editor.isAncestorOf(after)
    assert editor.toPlainText() == "a: 1\n"


@pytest.mark.parametrize("modifier", [_CONTROL, _META], ids=["control", "meta"])
def test_control_shift_tab_moves_focus_back(qtbot, modifier) -> None:
    window = _show(qtbot)
    editor = _input(window)
    editor.setPlainText("a: 1\n")
    _focus(qtbot, editor)
    qtbot.keyClick(window.windowHandle(), Qt.Key.Key_Backtab, modifier | _SHIFT)
    assert QApplication.focusWidget() is not editor
    assert editor.toPlainText() == "a: 1\n"


def test_a_pasted_tab_in_yaml_is_still_reported(qtbot) -> None:
    window = _show(qtbot)
    picker = window.findChild(QComboBox, "inputFormat")
    assert isinstance(picker, QComboBox)
    picker.setCurrentText("YAML")
    editor = _input(window)
    _focus(qtbot, editor)
    pasted = "a:\n\tb: 1\n"
    QApplication.clipboard().setText(pasted)
    editor.paste()
    assert editor.toPlainText() == pasted
    found = [item for item in window.findChildren(QAction) if item.text() == "Format"]
    assert len(found) == 1
    found[0].trigger()
    pane = window.findChild(ProblemsPane, "problemsPane")
    assert isinstance(pane, ProblemsPane)
    qtbot.waitUntil(
        lambda: (
            pane.messages()
            == ["Line 2 starts with a tab. YAML needs spaces for indenting."]
        ),
        timeout=15000,
    )
    assert "\t" in editor.toPlainText()
