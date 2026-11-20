"""Format, Copy, Copy As, Open and Convert keep the text as typed (#221).

``QPlainTextEdit.toPlainText()`` turns a no-break space (U+00A0) into a
space and a line separator (U+2028) into "\\n". These paths read through
``editor.raw_text`` instead. All data here is fake.
"""

import ast
import json
from pathlib import Path

import pytest
from PySide6.QtGui import QAction, QTextCursor, QTextDocument
from PySide6.QtWidgets import QApplication, QComboBox, QMessageBox

import galley.app.editor as editor_module
import galley.app.main_window as main_window_module
from galley.app.editor import CodeEditor
from galley.app.main import create_main_window

_WAIT_MS = 10000
# One JSON string with both characters toPlainText() changes.
_MIXED = '{"name": "Pat\u00a0Example", "note": "one\u2028two"}'
_UNTOUCHED = "clipboard before the copy"


def _raw(source: QTextDocument | object) -> str:
    return editor_module.raw_text(source)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(
            '{\n  "name": "Pat\u00a0Example",\n  "note": "one\u2028two"\n}\n',
            id="json",
        ),
        pytest.param('name: "Pat\u00a0Example"\nnote: "one\u2028two"\n', id="yaml"),
        pytest.param("<p>Pat\u00a0Example</p>\n<p>one\u2028two</p>\n", id="html"),
    ],
)
def test_raw_text_keeps_no_break_spaces_and_line_separators(qapp, text: str) -> None:
    document = QTextDocument()
    document.setPlainText(text)
    assert _raw(document) == text
    cursor = QTextCursor(document)
    cursor.select(QTextCursor.SelectionType.Document)
    assert _raw(cursor.selection()) == text


def test_a_paragraph_separator_becomes_a_line_break(qapp) -> None:
    # A known v1 limitation (ui-spec §13): Qt makes U+2029 a block break.
    document = QTextDocument()
    document.setPlainText('{"note": "one\u2029two"}')
    assert document.blockCount() == 2
    assert _raw(document) == '{"note": "one\ntwo"}'


def _show(qtbot):
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def _editor(window, name: str) -> CodeEditor:
    editor = window.findChild(CodeEditor, name)
    assert isinstance(editor, CodeEditor), name
    return editor


def _action(window, text: str) -> QAction:
    matches = [item for item in window.findChildren(QAction) if item.text() == text]
    assert len(matches) == 1, text
    return matches[0]


def test_format_keeps_the_input_characters(qtbot) -> None:
    window = _show(qtbot)
    picker = window.findChild(QComboBox, "outputFormat")
    assert isinstance(picker, QComboBox)
    picker.setCurrentText("JSON")
    _editor(window, "inputEditor").setPlainText(_MIXED)
    window._actions.format.trigger()
    qtbot.waitUntil(lambda: not window._formatting, timeout=_WAIT_MS)
    written = _editor(window, "outputEditor").document().toRawText()
    assert json.loads(written.replace("\u2029", "\n")) == json.loads(_MIXED)
    assert "Pat\u00a0Example" in written
    assert "one\\u2028two" in written


def test_copy_keeps_the_output_characters(qtbot) -> None:
    window = _show(qtbot)
    _editor(window, "outputEditor").setPlainText(_MIXED)
    QApplication.clipboard().setText(_UNTOUCHED)
    copy_button = window._output_pane.copy_button
    assert copy_button is not None
    copy_button.click()
    assert QApplication.clipboard().text() == _MIXED


def test_copy_as_original_keeps_the_output_characters(qtbot) -> None:
    window = _show(qtbot)
    output = _editor(window, "outputEditor")
    output.setPlainText(_MIXED)
    QApplication.clipboard().setText(_UNTOUCHED)
    _action(window, "Original").trigger()
    assert QApplication.clipboard().text() == _MIXED
    cursor = output.textCursor()
    cursor.select(QTextCursor.SelectionType.Document)
    output.setTextCursor(cursor)
    QApplication.clipboard().setText(_UNTOUCHED)
    _action(window, "Original").trigger()
    assert QApplication.clipboard().text() == _MIXED


def _replace_box(window) -> QMessageBox | None:
    for box in window.findChildren(QMessageBox):
        if box.objectName() == "replaceInput" and box.isVisible():
            return box
    return None


def test_open_keeps_the_file_text_for_save(qtbot, tmp_path: Path) -> None:
    path = tmp_path / "mixed.json"
    path.write_bytes(_MIXED.encode("utf-8"))
    window = _show(qtbot)
    editor = _editor(window, "inputEditor")
    window.open_path(path)
    shown = editor.document()
    qtbot.waitUntil(
        lambda: window._loading_path is None and shown.toRawText() == _MIXED,
        timeout=_WAIT_MS,
    )
    document = window._input_pane.document
    assert document is not None
    # Save writes from this text; the unsaved-edits check compares with it.
    assert document.opened_text == _MIXED
    window.open_path(path)
    assert _replace_box(window) is None
    qtbot.waitUntil(lambda: window._loading_path is None, timeout=_WAIT_MS)


# Reads of toPlainText() that only count, test for blank, or compare like
# with like, keyed on the function that makes them. Everything else in
# main_window.py reads through raw_text. Compare (compare_mode.py) is outside
# this guard; ui-spec §13 lists what it treats alike.
_PLAIN_TEXT_READS = {
    "_open_size_warning._choose",  # blank check
    "_start_format",  # _output_before_format
    "_restore_output",  # _output_before_format
    "_sync_format_button",  # blank check
    "_validate_reason",  # blank check
    "_sync_input_state",  # footer counts
    "_sync_output_state",  # footer counts
}


def _plain_text_reads(tree: ast.AST) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []

    def visit(node: ast.AST, names: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, (*names, child.name))
                continue
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "toPlainText"
            ):
                found.append((".".join(names), child.lineno))
            visit(child, names)

    visit(tree, ())
    return found


def test_main_window_reads_plain_text_only_where_allowed() -> None:
    source = Path(main_window_module.__file__).read_text(encoding="utf-8")
    reads = _plain_text_reads(ast.parse(source))
    assert reads, "the guard found no toPlainText() calls at all"
    outside = [(name, line) for name, line in reads if name not in _PLAIN_TEXT_READS]
    assert outside == []
