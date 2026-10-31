"""Compare mode (#65 slice 3d): View › Compare, Next Change, Previous Change.

Compare reuses the Input/Output splitter. Left is the Input editor, read-only.
Right is a read-only editor that takes the Output editor's place in the
Output pane, so Input's text and undo history and all of Output come back
exactly on leaving. Paste fills Right.

Most tests run the compare in-process through ``inline_compare``, which
stands in for the spawn worker behind the same ``ParseSession`` signals.
The worker itself is covered by ``test_compare_session.py`` and by the
end-to-end tests at the bottom of this file.
"""

import gc
import sys
import weakref

import pytest
import shiboken6
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QAction, QKeySequence, QTextDocument
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QToolButton,
    QWidget,
)
from tests.app.compare_inline import inline_compare as inline_compare

from galley.app.actions import (
    NOT_AVAILABLE_WHILE_COMPARING,
    ActionHold,
    not_available_reason,
    set_not_available,
)
from galley.app.changes_pane import (
    ADD_SECOND_DOCUMENT,
    COMPARISON_CANCELED,
    COMPARISON_STOPPED,
    FIX_ERRORS,
    NO_DIFFERENCES,
    ChangesPane,
    ChangesState,
)
from galley.app.compare_mode import (
    COMPARE_ANYWAY,
    COMPARING_ACCESSIBLE_NAME,
    COMPARING_MESSAGE,
    NO_DIFFERENCES_COUNT,
    SIZE_WARNING_TEXT,
)
from galley.app.editor import CHANGE_MARKERS, CodeEditor
from galley.app.find_bar import FindBar
from galley.app.main import create_main_window
from galley.app.main_window import SPINNER_REVEAL_MS
from galley.app.problems_pane import ProblemsPane
from galley.core.compare_report import ChangeRow

# Fake data only. Left to Right: total 40 → 45, region removed, role user →
# admin, currency added. Engine order is key order on the left, then adds.
_LEFT = (
    '{\n  "orders": [{"id": "A-1001", "total": 40}],\n'
    '  "region": "north",\n  "role": "user"\n}\n'
)
_RIGHT = (
    '{\n  "orders": [{"id": "A-1001", "total": 45}],\n'
    '  "role": "admin",\n  "currency": "EUR"\n}\n'
)
_ONE_CHANGE = (
    '{\n  "orders": [{"id": "A-1001", "total": 40}],\n'
    '  "region": "south",\n  "role": "user"\n}\n'
)
_BROKEN = '{\n  "orders": [\n'
_WAIT_MS = 5_000
_SPAWN_TIMEOUT_MS = 30_000

# Bill's 3d list, in the brief's order, by menu text. Format left the list
# in 3e: it formats both sides while comparing.
_GREYED = (
    "Minify",
    "Validate",
    "Sort keys",
    "Indent",
    "Outdent",
    "Undo",
    "Redo",
    "Collapse all",
    "Expand all",
    "Escape as String",
    "Unescape String",
    "Convert from Python",
    "Find and Replace…",
)


# --- Helpers ---------------------------------------------------------------


def _show(qtbot, width: int = 1400):
    window = create_main_window()
    qtbot.addWidget(window)
    window.resize(width, 800)
    window.show()
    qtbot.waitExposed(window)
    return window


def _action(window, text: str) -> QAction:
    matches = [item for item in window.findChildren(QAction) if item.text() == text]
    assert len(matches) == 1, text
    return matches[0]


def _editor(window, name: str) -> CodeEditor:
    editor = window.findChild(CodeEditor, name)
    assert isinstance(editor, CodeEditor), name
    return editor


def _left(window) -> CodeEditor:
    return _editor(window, "inputEditor")


def _right(window) -> CodeEditor:
    return _editor(window, "rightEditor")


def _changes(window) -> ChangesPane:
    pane = window.findChild(ChangesPane, "changesPage")
    assert isinstance(pane, ChangesPane)
    return pane


def _type_input(qtbot, window, text: str) -> None:
    editor = _left(window)
    editor.setFocus()
    editor.setPlainText("")
    editor.insertPlainText(text)


def _paste_right(window, text: str) -> None:
    QApplication.clipboard().setText(text)
    _action(window, "Paste").trigger()


def _compare(qtbot, window, left: str = _LEFT, right: str | None = _RIGHT) -> None:
    """Fill Input, enter Compare, paste ``right`` and wait for the result."""
    _type_input(qtbot, window, left)
    _action(window, "Compare").trigger()
    assert _action(window, "Compare").isChecked()
    if right is None:
        return
    _paste_right(window, right)
    qtbot.waitUntil(lambda: not window._compare.is_running(), timeout=_WAIT_MS)


def _status_state(window) -> str:
    return window._status.state_text()


def _undo_steps(document: QTextDocument) -> tuple[int, int, bool, bool]:
    return (
        document.availableUndoSteps(),
        document.availableRedoSteps(),
        document.isUndoAvailable(),
        document.isModified(),
    )


def _title(window, name: str) -> str:
    label = window.findChild(QLabel, f"{name}TabTitle")
    assert isinstance(label, QLabel)
    return label.text()


def _focus(qtbot, widget: QWidget) -> None:
    widget.setFocus()
    qtbot.waitUntil(widget.hasFocus, timeout=_WAIT_MS)


def _press_escape(qtbot, widget: QWidget) -> None:
    qtbot.keyClick(widget, Qt.Key.Key_Escape)


# --- Menu, shortcuts and the release check ---------------------------------


def test_compare_is_a_checkable_view_item_with_its_keys(qtbot) -> None:
    window = _show(qtbot)
    compare = _action(window, "Compare")
    assert compare.isCheckable()
    assert compare.menuRole() == QAction.MenuRole.NoRole
    portable = QKeySequence.SequenceFormat.PortableText
    assert [s.toString(portable) for s in compare.shortcuts()] == ["Ctrl+Shift+D"]
    next_keys = [
        s.toString(portable) for s in _action(window, "Next Change").shortcuts()
    ]
    previous_keys = [
        s.toString(portable) for s in _action(window, "Previous Change").shortcuts()
    ]
    if sys.platform == "darwin":
        # The menu shows the first key: ⌥⌘↓ and ⌥⌘↑.
        assert next_keys == ["Ctrl+Alt+Down", "F7"]
        assert previous_keys == ["Ctrl+Alt+Up", "Shift+F7"]
    else:
        assert next_keys == ["F7"]
        assert previous_keys == ["Shift+F7"]


def test_compare_has_no_toolbar_button(qtbot) -> None:
    window = _show(qtbot)
    compare = _action(window, "Compare")
    buttons = [
        button
        for button in window.findChildren(QToolButton)
        if button.defaultAction() is compare
    ]
    assert buttons == []
    overflow = window.findChild(QToolButton, "toolbarOverflow")
    assert isinstance(overflow, QToolButton)
    menu = overflow.menu()
    assert menu is not None
    assert compare not in menu.actions()


def test_next_and_previous_change_wait_for_compare_mode(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    next_change = _action(window, "Next Change")
    previous_change = _action(window, "Previous Change")
    assert not next_change.isEnabled()
    assert not previous_change.isEnabled()
    _compare(qtbot, window)
    assert next_change.isEnabled()
    assert previous_change.isEnabled()
    _action(window, "Compare").trigger()
    assert not next_change.isEnabled()
    assert not previous_change.isEnabled()


@pytest.mark.parametrize(("label", "reason"), [("XML", "XML"), ("HTML", "HTML")])
def test_compare_is_greyed_out_for_markup_input(qtbot, label, reason) -> None:
    window = _show(qtbot)
    compare = _action(window, "Compare")
    combo = window.findChild(QComboBox, "inputFormat")
    assert isinstance(combo, QComboBox)
    combo.setCurrentText(label)
    assert not compare.isEnabled()
    assert compare.toolTip() == f"Not available for {reason}"
    assert compare.statusTip() == f"Not available for {reason}"
    assert not_available_reason(compare) == f"Not available for {reason}"
    combo.setCurrentText("JSON")
    assert compare.isEnabled()
    assert not_available_reason(compare) == ""


# --- Entering and leaving --------------------------------------------------


def test_entering_relabels_the_panes_and_leaving_puts_them_back(
    qtbot, inline_compare
) -> None:
    window = _show(qtbot)
    assert (_title(window, "input"), _title(window, "output")) == ("Input", "Output")
    _compare(qtbot, window)
    assert (_title(window, "input"), _title(window, "output")) == ("Left", "Right")
    assert window._input_pane.title == "Left"
    assert window._output_pane.title == "Right"
    _action(window, "Compare").trigger()
    assert not _action(window, "Compare").isChecked()
    assert (_title(window, "input"), _title(window, "output")) == ("Input", "Output")


def test_compare_reuses_the_input_output_splitter(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    splitter = window.findChild(QSplitter, "mainSplitter")
    assert isinstance(splitter, QSplitter)
    _compare(qtbot, window)
    assert splitter.count() == 2
    assert splitter.widget(0) is window._input_pane
    assert splitter.widget(1) is window._output_pane
    right = _right(window)
    assert right.isVisible()
    assert _is_inside(right, window._output_pane)
    assert not _editor(window, "outputEditor").isVisible()


def test_both_editors_are_read_only_while_comparing(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    _compare(qtbot, window)
    left, right = _left(window), _right(window)
    assert left.isReadOnly()
    assert right.isReadOnly()
    before = (left.toPlainText(), right.toPlainText())
    left.setFocus()
    qtbot.keyClicks(left, "zz")
    right.setFocus()
    qtbot.keyClicks(right, "zz")
    assert (left.toPlainText(), right.toPlainText()) == before
    _action(window, "Compare").trigger()
    assert not left.isReadOnly()


def test_leaving_restores_input_text_and_undo_history(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    left = _left(window)
    left.setFocus()
    qtbot.keyClicks(left, '{"a": 1}')
    qtbot.keyClicks(left, " ")
    text = left.toPlainText()
    steps = _undo_steps(left.document())
    assert steps[0] > 0
    _action(window, "Compare").trigger()
    _paste_right(window, '{"a": 2}')
    qtbot.waitUntil(lambda: _changes(window).count() == 1, timeout=_WAIT_MS)
    _action(window, "Next Change").trigger()
    _action(window, "Compare").trigger()
    assert left.toPlainText() == text
    assert _undo_steps(left.document()) == steps
    # The history still works: undo and redo walk the typing as before.
    left.undo()
    assert left.toPlainText() != text
    left.redo()
    assert left.toPlainText() == text


def test_leaving_leaves_output_as_it_was(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    output = _editor(window, "outputEditor")
    output.setPlainText('{"kept": true}\n')
    output.setFocus()
    output.moveCursor(output.textCursor().MoveOperation.End)
    qtbot.keyClicks(output, "x")
    steps = _undo_steps(output.document())
    text = output.toPlainText()
    _compare(qtbot, window)
    assert output.toPlainText() == text
    _action(window, "Compare").trigger()
    assert output.isVisible()
    assert not _right(window).isVisible()
    assert output.toPlainText() == text
    assert _undo_steps(output.document()) == steps
    assert window._output_pane.editor is output


def _selections(editor: CodeEditor) -> list[tuple[int, int, str]]:
    return [
        (
            selection.cursor.position(),
            selection.cursor.anchor(),
            selection.format.background().color().name(),
        )
        for selection in editor.extraSelections()
    ]


def _editor_state(editor: CodeEditor) -> dict[str, object]:
    cursor = editor.textCursor()
    return {
        "text": editor.toPlainText(),
        "undo": _undo_steps(editor.document()),
        "read_only": editor.isReadOnly(),
        "flags": editor.textInteractionFlags(),
        "caret": (cursor.position(), cursor.anchor()),
        "scroll": (
            editor.verticalScrollBar().value(),
            editor.horizontalScrollBar().value(),
        ),
        "folds": list(editor.fold_ranges()),
        "folded": editor.folded_lines(),
        "markers": {
            kind: editor.line_markers(kind) for kind in ("problem", "change", "fold")
        },
        "tints": editor.change_tints(),
        "selections": _selections(editor),
    }


def test_leaving_puts_the_input_editor_back_exactly(qtbot, inline_compare) -> None:
    """Text, undo, isModified, read-only, caret, folds and extra selections.

    Compare's markers, tints, problems and span selections all go.
    """
    window = _show(qtbot)
    left = _left(window)
    _focus(qtbot, left)
    left.insertPlainText(_LEFT)
    qtbot.keyClicks(left, " ")
    cursor = left.textCursor()
    cursor.setPosition(5)
    left.setTextCursor(cursor)
    qtbot.wait(10)
    before = _editor_state(left)
    _action(window, "Compare").trigger()
    _paste_right(window, _RIGHT)
    qtbot.waitUntil(lambda: _changes(window).count() == 4, timeout=_WAIT_MS)
    _action(window, "Next Change").trigger()
    assert left.line_markers(CHANGE_MARKERS)
    _paste_right(window, _BROKEN)
    qtbot.waitUntil(
        lambda: _changes(window).state() == ChangesState.ERRORS, timeout=_WAIT_MS
    )
    _action(window, "Compare").trigger()
    qtbot.wait(10)
    assert _editor_state(left) == before


def test_leaving_puts_the_output_editor_back_exactly(qtbot, inline_compare) -> None:
    """Text, undo, folds, Keys only, caret, scroll and the Output picker."""
    window = _show(qtbot, width=1400)
    output = _editor(window, "outputEditor")
    lines = ",\n".join(f'  "key{index}": {{"value": {index}}}' for index in range(80))
    _replace_text(output, "{\n" + lines + "\n}\n")
    output.rescan_folds()
    assert output.fold_ranges()
    output.fold_line(3)
    bar = window._output_pane.find_bar
    assert bar.keys_only is not None
    bar.keys_only.setChecked(True)
    output.verticalScrollBar().setValue(40)
    cursor = output.textCursor()
    cursor.setPosition(30)
    output.setTextCursor(cursor)
    picker = window.findChild(QComboBox, "outputFormat")
    assert isinstance(picker, QComboBox)
    picker.setCurrentText("YAML")
    qtbot.wait(10)
    before = _editor_state(output)
    _compare(qtbot, window)
    # Right follows the pasted JSON, through the Output pane's picker.
    assert picker.currentText() == "JSON"
    _action(window, "Compare").trigger()
    qtbot.wait(10)
    assert _editor_state(output) == before
    assert bar.keys_only.isChecked()
    assert picker.currentText() == "YAML"


def _replace_text(editor: CodeEditor, text: str) -> None:
    cursor = editor.textCursor()
    cursor.beginEditBlock()
    cursor.select(cursor.SelectionType.Document)
    cursor.insertText(text)
    cursor.endEditBlock()


def test_right_keeps_its_text_for_the_session(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    _compare(qtbot, window)
    assert _right(window).toPlainText() == _RIGHT
    _action(window, "Compare").trigger()
    _action(window, "Compare").trigger()
    assert _right(window).toPlainText() == _RIGHT
    qtbot.waitUntil(lambda: not window._compare.is_running(), timeout=_WAIT_MS)
    assert _changes(window).count() == 4


def test_right_starts_empty_and_is_never_seeded_from_output(
    qtbot, inline_compare
) -> None:
    window = _show(qtbot)
    _editor(window, "outputEditor").setPlainText('{"from": "output"}\n')
    _compare(qtbot, window, right=None)
    assert _right(window).toPlainText() == ""
    assert _changes(window).state() == ChangesState.EMPTY_RIGHT
    assert _changes(window).message() == ADD_SECOND_DOCUMENT
    assert inline_compare.sent == []


def test_paste_replaces_all_of_right_from_either_side(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    _compare(qtbot, window)
    _left(window).setFocus()
    _paste_right(window, _ONE_CHANGE)
    qtbot.waitUntil(lambda: _changes(window).count() == 1, timeout=_WAIT_MS)
    assert _right(window).toPlainText() == _ONE_CHANGE
    assert _left(window).toPlainText() == _LEFT
    _right(window).setFocus()
    _paste_right(window, _LEFT)
    qtbot.waitUntil(
        lambda: _changes(window).state() == ChangesState.NO_DIFFERENCES,
        timeout=_WAIT_MS,
    )
    assert _right(window).toPlainText() == _LEFT


def test_paste_while_comparing_cancels_and_compares_again(
    qtbot, inline_compare
) -> None:
    window = _show(qtbot)
    inline_compare.hold = True
    _compare(qtbot, window, right=None)
    _paste_right(window, _RIGHT)
    assert window._compare.is_running()
    _paste_right(window, _ONE_CHANGE)
    assert len(inline_compare.sent) == 2
    assert inline_compare.sent[-1].right_text == _ONE_CHANGE
    inline_compare.release()
    qtbot.waitUntil(lambda: _changes(window).count() == 1, timeout=_WAIT_MS)


def test_changes_tab_is_added_on_entry_and_removed_on_leaving(
    qtbot, inline_compare
) -> None:
    window = _show(qtbot)
    panel = window.findChild(ProblemsPane)
    assert isinstance(panel, ProblemsPane)
    tabs = panel.tab_bar()
    assert _tab_titles(panel) == ["Problems"]
    _compare(qtbot, window)
    # Each tab reads its title and count, as Problems does.
    assert [tabs.tabText(i) for i in range(tabs.count())] == ["Problems 0", "Changes 4"]
    assert panel.current_page() is _changes(window)
    assert panel.isVisible()
    _action(window, "Compare").trigger()
    assert _tab_titles(panel) == ["Problems"]
    assert not panel.isVisible()


def _tab_titles(panel: ProblemsPane) -> list[str]:
    tabs = panel.tab_bar()
    return [tabs.tabText(i).rsplit(" ", 1)[0] for i in range(tabs.count())]


# --- Results and wording ---------------------------------------------------


def test_differences_fill_the_changes_pane_counts_and_markers(
    qtbot, inline_compare
) -> None:
    window = _show(qtbot)
    _compare(qtbot, window)
    pane = _changes(window)
    assert pane.state() == ChangesState.ROWS
    assert pane.count() == 4
    assert window._status.counts_text() == "4 differences"
    assert window._status.last_announcement() == "4 differences"
    assert _left(window).line_markers(CHANGE_MARKERS)
    assert _right(window).line_markers(CHANGE_MARKERS)
    assert _left(window).change_tints()
    assert _right(window).change_tints()
    # Markers keep out of the gutter's accessible text (#118).
    assert _right(window)._line_numbers.accessibleDescription() == ""


def test_one_difference_reads_singular(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    _compare(qtbot, window, right=_ONE_CHANGE)
    assert window._status.counts_text() == "1 difference"


def test_identical_documents_say_no_differences(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    _compare(qtbot, window, right=_LEFT)
    pane = _changes(window)
    assert pane.state() == ChangesState.NO_DIFFERENCES
    assert pane.message() == NO_DIFFERENCES == "No differences."
    assert window._status.counts_text() == NO_DIFFERENCES_COUNT == "No differences"
    assert not _left(window).line_markers(CHANGE_MARKERS)


def test_a_parse_error_in_right_goes_to_problems_on_right(
    qtbot, inline_compare
) -> None:
    window = _show(qtbot)
    _compare(qtbot, window, right=_BROKEN)
    pane = _changes(window)
    assert pane.state() == ChangesState.ERRORS
    assert pane.message() == FIX_ERRORS == "Fix errors to compare."
    problems = window.findChild(ProblemsPane)
    assert isinstance(problems, ProblemsPane)
    assert problems.count() > 0
    assert _right(window).problem_lines()
    assert not _left(window).problem_lines()
    assert window._status.counts_text() == ""
    # The row jumps in Right, the side the problem is in.
    line = problems.shown()[0].lineno
    problems.jump_requested.emit(line, 0)
    assert _right(window).textCursor().blockNumber() == line - 1
    _action(window, "Compare").trigger()
    assert not _right(window).problem_lines()
    assert problems.count() == 0


def test_a_new_compare_clears_the_last_problems(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    _compare(qtbot, window, right=_BROKEN)
    assert _right(window).problem_lines()
    inline_compare.hold = True
    _paste_right(window, _RIGHT)
    problems = window.findChild(ProblemsPane)
    assert isinstance(problems, ProblemsPane)
    assert problems.count() == 0
    assert not _right(window).problem_lines()


def test_the_caption_follows_the_headers(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    caption = window.findChild(QLabel, "editingCaption")
    assert isinstance(caption, QLabel)
    _type_input(qtbot, window, _LEFT)
    window.findChild(ProblemsPane).list_widget().setFocus()
    _action(window, "Compare").trigger()
    assert "Left" in caption.text()
    _paste_right(window, _RIGHT)
    _focus(qtbot, _right(window))
    assert "Right" in caption.text()
    _action(window, "Compare").trigger()
    assert "Input" in caption.text()


def test_a_parse_error_in_left_marks_left(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    _compare(qtbot, window, left=_BROKEN, right=_RIGHT)
    assert _changes(window).message() == FIX_ERRORS
    assert _left(window).problem_lines()
    assert not _right(window).problem_lines()


def test_problems_from_before_compare_come_back(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    problems = window.findChild(ProblemsPane)
    assert isinstance(problems, ProblemsPane)
    _type_input(qtbot, window, _BROKEN)
    _action(window, "Validate").trigger()
    qtbot.waitUntil(lambda: problems.count() > 0, timeout=_SPAWN_TIMEOUT_MS)
    before = problems.messages()
    lines = _left(window).problem_lines()
    _action(window, "Compare").trigger()
    _paste_right(window, _RIGHT)
    qtbot.waitUntil(lambda: not window._compare.is_running(), timeout=_WAIT_MS)
    _action(window, "Compare").trigger()
    assert problems.messages() == before
    assert _left(window).problem_lines() == lines
    window.shutdown_parse_worker()


# --- Busy, Cancel, stopped -------------------------------------------------


def test_comparing_shows_after_500_ms_with_cancel(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    inline_compare.hold = True
    _compare(qtbot, window, right=None)
    _paste_right(window, _RIGHT)
    spinner = window.findChild(QProgressBar, "formatSpinner")
    cancel = window.findChild(QPushButton, "cancelButton")
    assert isinstance(spinner, QProgressBar)
    assert isinstance(cancel, QPushButton)
    assert _status_state(window) != COMPARING_MESSAGE
    assert not spinner.isVisible()
    assert not cancel.isVisible()
    qtbot.wait(SPINNER_REVEAL_MS // 2)
    assert not cancel.isVisible()
    qtbot.waitUntil(cancel.isVisible, timeout=_WAIT_MS)
    assert _status_state(window) == COMPARING_MESSAGE == "Comparing…"
    assert spinner.isVisible()
    assert spinner.accessibleName() == COMPARING_ACCESSIBLE_NAME == "Comparing"
    # The panes stay scrollable and the result is not shown yet.
    assert _changes(window).count() == 0
    qtbot.mouseClick(cancel, Qt.MouseButton.LeftButton)
    assert not window._compare.is_running()
    assert _changes(window).state() == ChangesState.CANCELED
    assert _changes(window).message() == COMPARISON_CANCELED
    assert not cancel.isVisible()
    assert not _right(window).line_markers(CHANGE_MARKERS)
    assert _action(window, "Compare").isChecked()


def test_a_canceled_compare_runs_again_on_the_next_paste(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    inline_compare.hold = True
    _compare(qtbot, window, right=None)
    _paste_right(window, _RIGHT)
    window._compare.cancel()
    assert _changes(window).state() == ChangesState.CANCELED
    inline_compare.hold = False
    _paste_right(window, _ONE_CHANGE)
    qtbot.waitUntil(lambda: _changes(window).count() == 1, timeout=_WAIT_MS)


def test_a_stopped_worker_offers_compare_again(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    inline_compare.hold = True
    _compare(qtbot, window, right=None)
    _paste_right(window, _RIGHT)
    inline_compare.stop()
    pane = _changes(window)
    assert pane.state() == ChangesState.STOPPED
    assert pane.message() == COMPARISON_STOPPED
    assert _status_state(window) == COMPARISON_STOPPED
    assert pane.compare_again_button().isVisibleTo(pane)
    inline_compare.hold = False
    qtbot.mouseClick(pane.compare_again_button(), Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: pane.count() == 4, timeout=_WAIT_MS)
    assert len(inline_compare.sent) == 2


# --- Esc ---------------------------------------------------------------------


def test_esc_closes_the_find_bar_then_cancels(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    inline_compare.hold = True
    _compare(qtbot, window, right=None)
    _paste_right(window, _RIGHT)
    right = _right(window)
    _focus(qtbot, right)
    _action(window, "Find…").trigger()
    bar = window.findChild(FindBar, "rightFindBar")
    assert isinstance(bar, FindBar)
    assert bar.is_open()
    _press_escape(qtbot, bar.field)
    assert not bar.is_open()
    assert window._compare.is_running()
    _press_escape(qtbot, right)
    assert not window._compare.is_running()
    assert _changes(window).state() == ChangesState.CANCELED


def test_esc_when_idle_stays_in_compare(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    _compare(qtbot, window)
    _press_escape(qtbot, _right(window))
    _press_escape(qtbot, _changes(window).list_view())
    _press_escape(qtbot, _left(window))
    assert _action(window, "Compare").isChecked()
    assert _changes(window).count() == 4


# --- Size check ------------------------------------------------------------


def _big(size: int) -> str:
    """A JSON array of exactly ``size`` UTF-8 bytes, 100-byte lines."""
    line = '  "' + "x" * 94 + '",\n'
    head, tail = "[\n", '  "x"\n]\n'
    body = line * ((size - len(head) - len(tail)) // len(line))
    pad = size - len(head) - len(tail) - len(body)
    text = head + body + tail[:3] + "x" * pad + tail[3:]
    assert len(text.encode("utf-8")) == size
    return text


def _size_box(window) -> QMessageBox | None:
    box = window.findChild(QMessageBox, "compareSizeWarning")
    return box if isinstance(box, QMessageBox) and box.isVisible() else None


def _box_button(box: QMessageBox, text: str) -> QPushButton:
    for button in box.buttons():
        if button.text() == text:
            assert isinstance(button, QPushButton)
            return button
    raise AssertionError(text)


def test_over_2_mb_asks_first_and_cancel_starts_no_job(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    _compare(qtbot, window, right=None)
    _paste_right(window, _big(2_000_001))
    qtbot.waitUntil(lambda: _size_box(window) is not None, timeout=_WAIT_MS)
    box = _size_box(window)
    assert box is not None
    assert (
        box.text() == SIZE_WARNING_TEXT == "Comparing this much data may take a while."
    )
    assert box.informativeText() == ""
    assert sorted(button.text() for button in box.buttons()) == [
        "Cancel",
        COMPARE_ANYWAY,
    ]
    assert box.defaultButton() is _box_button(box, COMPARE_ANYWAY)
    assert box.escapeButton() is _box_button(box, "Cancel")
    assert box.windowModality() == Qt.WindowModality.WindowModal
    qtbot.mouseClick(_box_button(box, "Cancel"), Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: _size_box(window) is None, timeout=_WAIT_MS)
    assert inline_compare.sent == []
    assert not window._compare.is_running()
    assert _changes(window).state() == ChangesState.CANCELED


def test_each_answer_covers_only_that_compare(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    big = _big(2_000_001)
    _compare(qtbot, window, right=None)
    _paste_right(window, big)
    qtbot.waitUntil(lambda: _size_box(window) is not None, timeout=_WAIT_MS)
    box = _size_box(window)
    assert box is not None
    qtbot.mouseClick(_box_button(box, COMPARE_ANYWAY), Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: len(inline_compare.sent) == 1, timeout=_WAIT_MS)
    qtbot.waitUntil(lambda: not window._compare.is_running(), timeout=_WAIT_MS)
    _paste_right(window, big + " ")
    qtbot.waitUntil(lambda: _size_box(window) is not None, timeout=_WAIT_MS)
    assert len(inline_compare.sent) == 1


def test_exactly_2_mb_does_not_ask(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    _compare(qtbot, window, right=None)
    _paste_right(window, _big(2_000_000))
    qtbot.waitUntil(lambda: len(inline_compare.sent) == 1, timeout=_WAIT_MS)
    assert _size_box(window) is None


def test_the_size_check_measures_utf8_bytes(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    _compare(qtbot, window, right=None)
    # Two-byte characters: under 2 MB as characters, over as bytes.
    line = '  "' + "é" * 96 + '",\n'
    wide = "[\n" + line * 10_300 + '  "é"\n]\n'
    assert len(wide) < 2_000_000 < len(wide.encode("utf-8"))
    _paste_right(window, wide)
    qtbot.waitUntil(lambda: _size_box(window) is not None, timeout=_WAIT_MS)


# --- Greyed out while comparing --------------------------------------------


def _greyed_state(window) -> dict[str, tuple[bool, str]]:
    return {
        text: (_action(window, text).isEnabled(), _action(window, text).toolTip())
        for text in _GREYED
    }


def test_actions_are_greyed_out_with_the_reason_and_come_back(
    qtbot, inline_compare
) -> None:
    window = _show(qtbot)
    _type_input(qtbot, window, _LEFT)
    before = _greyed_state(window)
    picker = window.findChild(QComboBox, "indentSize")
    options = window.findChild(QPushButton, "outputOptionsButton")
    assert isinstance(picker, QComboBox)
    assert isinstance(options, QPushButton)
    widgets_before = [(w.isEnabled(), w.toolTip()) for w in (picker, options)]
    _action(window, "Compare").trigger()
    for text in _GREYED:
        action = _action(window, text)
        assert not action.isEnabled(), text
        assert action.toolTip() == NOT_AVAILABLE_WHILE_COMPARING, text
        assert action.statusTip() == NOT_AVAILABLE_WHILE_COMPARING, text
    assert not _action(window, "Output Options").isEnabled()
    for widget in (picker, options):
        assert not widget.isEnabled()
        assert widget.toolTip() == NOT_AVAILABLE_WHILE_COMPARING
    assert NOT_AVAILABLE_WHILE_COMPARING == "Not available while comparing"
    for text in ("Compare", "Next Change", "Previous Change"):
        assert _action(window, text).isEnabled(), text
    _action(window, "Compare").trigger()
    assert _greyed_state(window) == before
    assert [(w.isEnabled(), w.toolTip()) for w in (picker, options)] == widgets_before


def test_toolbar_buttons_show_the_reason(qtbot, inline_compare) -> None:
    window = _show(qtbot, width=1800)
    _compare(qtbot, window)
    for text in ("Sort keys", "Minify", "Validate", "Indent", "Expand all"):
        action = _action(window, text)
        buttons = [
            button
            for button in window.findChildren(QToolButton)
            if button.defaultAction() is action and button.isVisible()
        ]
        assert buttons, text
        for button in buttons:
            assert not button.isEnabled(), text
            assert button.toolTip() == NOT_AVAILABLE_WHILE_COMPARING, text
    format_button = window.findChild(QPushButton, "formatButton")
    assert isinstance(format_button, QPushButton)
    assert format_button.isEnabled()


def test_the_window_cannot_turn_a_held_action_back_on(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    _compare(qtbot, window)
    _left(window).setFocus()
    window._sync_format_button()
    window._sync_undo_actions()
    window._sync_fold_actions()
    for text in _GREYED:
        action = _action(window, text)
        assert not action.isEnabled(), text
        assert action.toolTip() == NOT_AVAILABLE_WHILE_COMPARING, text


def test_open_right_document_and_link_scrolling_are_left_alone(
    qtbot, inline_compare
) -> None:
    window = _show(qtbot)
    items = [_action(window, "Open Right Document…"), _action(window, "Link Scrolling")]
    before = [(item.isEnabled(), item.toolTip()) for item in items]
    _compare(qtbot, window)
    assert [(item.isEnabled(), item.toolTip()) for item in items] == before


def test_action_hold_saves_and_restores_each_state(qtbot) -> None:
    owner = QWidget()
    qtbot.addWidget(owner)
    on = QAction("On", owner)
    off = QAction("Off", owner)
    off.setEnabled(False)
    tipped = QAction("Tipped", owner)
    tipped.setToolTip("Tipped (⌘T)")
    reasoned = QAction("Reasoned", owner)
    reasoned.setEnabled(False)
    set_not_available(reasoned, "Not available for XML")
    hold = ActionHold([on, off, tipped, reasoned], parent=owner)
    hold.set_held(True)
    for action in (on, off, tipped, reasoned):
        assert not action.isEnabled()
        assert not_available_reason(action) == NOT_AVAILABLE_WHILE_COMPARING
        assert action.toolTip() == NOT_AVAILABLE_WHILE_COMPARING
    # A window sync can't turn a held action on.
    on.setEnabled(True)
    assert not on.isEnabled()
    hold.set_held(False)
    assert on.isEnabled()
    assert not off.isEnabled()
    assert tipped.isEnabled()
    assert tipped.toolTip() == "Tipped (⌘T)"
    assert not reasoned.isEnabled()
    assert not_available_reason(reasoned) == "Not available for XML"
    assert reasoned.toolTip() == "Not available for XML"


def test_compare_owns_its_action_hold_group_and_guard(qtbot, inline_compare) -> None:
    """Compare is the parent of the held actions' group and the widget guard.

    Without a parent they were left to Python's gc, which could run their Qt
    destructors mid-collection while the group still held the actions and
    the guard still watched the widgets.
    """
    window = _show(qtbot)
    compare = window._compare
    hold = compare._hold
    _compare(qtbot, window)
    assert hold.is_held()
    parents = {"group": hold._group.parent(), "guard": hold._guard.parent()}
    assert parents == {"group": compare, "guard": compare}


def test_a_held_group_and_guard_outlive_their_action_hold(qtbot) -> None:
    """Dropping the hold leaves its group and guard to its parent, at work."""
    owner = QWidget()
    qtbot.addWidget(owner)
    action = QAction("On", owner)
    picker = QWidget(owner)
    hold = ActionHold([action], [picker], parent=owner)
    hold.set_held(True)
    refs = {"group": weakref.ref(hold._group), "guard": weakref.ref(hold._guard)}
    del hold
    gc.collect()
    alive = {
        name: (made := ref()) is not None and shiboken6.isValid(made)
        for name, ref in refs.items()
    }
    assert alive == {"group": True, "guard": True}
    action.setEnabled(True)
    picker.setEnabled(True)
    assert not action.isEnabled()
    assert not picker.isEnabled()
    assert picker.toolTip() == NOT_AVAILABLE_WHILE_COMPARING


# --- Layout ----------------------------------------------------------------


def test_stack_panes_puts_left_above_right(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    _compare(qtbot, window)
    _action(window, "Stack Panes").trigger()
    splitter = window.findChild(QSplitter, "mainSplitter")
    assert isinstance(splitter, QSplitter)
    assert splitter.orientation() == Qt.Orientation.Vertical
    left_pane, right_pane = splitter.widget(0), splitter.widget(1)
    assert _title(window, "input") == "Left"
    assert _title(window, "output") == "Right"
    qtbot.waitUntil(
        lambda: (
            left_pane.mapTo(window, left_pane.rect().topLeft()).y()
            < right_pane.mapTo(window, right_pane.rect().topLeft()).y()
        ),
        timeout=_WAIT_MS,
    )
    _action(window, "Stack Panes").trigger()


def test_narrow_windows_shed_the_left_and_right_headers_as_today(
    qtbot, inline_compare
) -> None:
    window = _show(qtbot, width=900)
    _compare(qtbot, window)
    for pane, title in ((window._input_pane, "Left"), (window._output_pane, "Right")):
        # The same rules as Input and Output: the strip is measured with the
        # title it shows.
        full = pane.full_strip_width()
        pane.fit_tab_strip(full)
        assert pane._tab_title.text() == title
        tight = pane.min_strip_width()
        pane.fit_tab_strip(tight - 1)
        assert pane._tab_title.toolTip() == title
        pane.fit_tab_strip()


def test_tab_order_is_left_right_changes(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    _compare(qtbot, window)
    left, right = _left(window), _right(window)
    assert left.nextInFocusChain() is right or _next_focus(left) is right
    assert _next_focus(right) is _changes(window).list_view()
    assert left.accessibleName() == "Left editor"
    assert right.accessibleName() == "Right editor"
    _action(window, "Compare").trigger()
    assert left.accessibleName() == "Input editor"


def _next_focus(widget: QWidget) -> QWidget:
    candidate = widget.nextInFocusChain()
    while candidate is not widget and not (
        candidate.isVisible()
        and candidate.isEnabled()
        and candidate.focusPolicy() & Qt.FocusPolicy.TabFocus
    ):
        candidate = candidate.nextInFocusChain()
    return candidate


# --- Find ------------------------------------------------------------------


def test_find_opens_a_search_only_bar_on_the_focused_side(
    qtbot, inline_compare
) -> None:
    window = _show(qtbot)
    _compare(qtbot, window)
    right = _right(window)
    _focus(qtbot, right)
    _action(window, "Find…").trigger()
    bar = window.findChild(FindBar, "rightFindBar")
    assert isinstance(bar, FindBar)
    assert bar.is_open()
    assert bar.editor() is right
    assert not bar.replace_row_shown()
    assert (
        bar.findChild(QWidget, "rightReplaceRow") is None
        or not bar.findChild(QWidget, "rightReplaceRow").isVisible()
    )
    assert bar.keys_only is None
    assert bar.match_case.isVisible()
    assert bar.regex_button.isVisible()
    assert bar.whole_word_button.isVisible()
    # One regex worker for every bar (#170).
    assert bar.regex_session() is window._regex_session
    left_bar = window._input_pane.find_bar
    bar.regex_button.setChecked(True)
    assert left_bar.regex_button.isChecked()
    bar.regex_button.setChecked(False)

    left = _left(window)
    _focus(qtbot, left)
    _action(window, "Find…").trigger()
    assert left_bar.is_open()
    assert left_bar.editor() is left
    assert not left_bar.replace_row_shown()
    # Find and Replace is greyed out, so the replace row can't open.
    _action(window, "Find and Replace…").trigger()
    assert not left_bar.replace_row_shown()


def test_entering_closes_an_open_replace_row(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    _type_input(qtbot, window, _LEFT)
    _action(window, "Find and Replace…").trigger()
    bar = window._input_pane.find_bar
    assert bar.replace_row_shown()
    _action(window, "Compare").trigger()
    assert not bar.replace_row_shown()


def test_find_counts_matches_in_right(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    _compare(qtbot, window)
    _focus(qtbot, _right(window))
    _action(window, "Find…").trigger()
    bar = window.findChild(FindBar, "rightFindBar")
    assert isinstance(bar, FindBar)
    bar.field.setText("EUR")
    bar.search()
    assert bar.match_count() == 1


# --- Next and Previous Change ------------------------------------------------


def _selected(editor: CodeEditor) -> str:
    return editor.textCursor().selectedText()


def test_next_and_previous_change_select_both_sides(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    _compare(qtbot, window)
    pane = _changes(window)
    rows = [pane.model().row_at(r) for r in range(pane.model().rowCount())]
    assert all(isinstance(row, ChangeRow) for row in rows)
    labels = [row.label for row in rows if isinstance(row, ChangeRow)]
    assert labels == ["Changed", "Removed", "Changed", "Added"]
    # The first change is shown when the result arrives.
    assert pane.list_view().current_row() == 0
    assert _selected(_left(window)) == '"total": 40'
    assert _selected(_right(window)) == '"total": 45'
    focused = QApplication.focusWidget()

    _action(window, "Next Change").trigger()
    assert pane.list_view().current_row() == 1
    # Removed selects on Left only.
    assert '"north"' in _selected(_left(window))
    _action(window, "Next Change").trigger()
    _action(window, "Next Change").trigger()
    assert pane.list_view().current_row() == 3
    assert '"EUR"' in _selected(_right(window))
    # No wrap at either end.
    _action(window, "Next Change").trigger()
    assert pane.list_view().current_row() == 3
    _action(window, "Previous Change").trigger()
    assert pane.list_view().current_row() == 2
    for _ in range(4):
        _action(window, "Previous Change").trigger()
    assert pane.list_view().current_row() == 0
    assert QApplication.focusWidget() is focused


def test_f7_moves_between_changes_from_a_read_only_editor(
    qtbot, inline_compare
) -> None:
    window = _show(qtbot)
    _compare(qtbot, window)
    pane = _changes(window)
    right = _right(window)
    right.setFocus()
    qtbot.waitUntil(right.hasFocus, timeout=_WAIT_MS)
    qtbot.keyClick(right, Qt.Key.Key_F7)
    assert pane.list_view().current_row() == 1
    qtbot.keyClick(right, Qt.Key.Key_F7, Qt.KeyboardModifier.ShiftModifier)
    assert pane.list_view().current_row() == 0


@pytest.mark.skipif(sys.platform != "darwin", reason="⌥⌘↓ is macOS only")
def test_option_command_arrows_move_between_changes_on_macos(
    qtbot, inline_compare
) -> None:
    window = _show(qtbot)
    _compare(qtbot, window)
    pane = _changes(window)
    left = _left(window)
    left.setFocus()
    qtbot.waitUntil(left.hasFocus, timeout=_WAIT_MS)
    ctrl_alt = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
    qtbot.keyClick(left, Qt.Key.Key_Down, ctrl_alt)
    assert pane.list_view().current_row() == 1
    qtbot.keyClick(left, Qt.Key.Key_Up, ctrl_alt)
    assert pane.list_view().current_row() == 0


def test_compare_shortcut_toggles_compare_mode(qtbot, inline_compare) -> None:
    window = _show(qtbot)
    _type_input(qtbot, window, _LEFT)
    left = _left(window)
    qtbot.waitUntil(left.hasFocus, timeout=_WAIT_MS)
    ctrl_shift = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
    qtbot.keyClick(left, Qt.Key.Key_D, ctrl_shift)
    assert _action(window, "Compare").isChecked()
    qtbot.keyClick(_left(window), Qt.Key.Key_D, ctrl_shift)
    assert not _action(window, "Compare").isChecked()


# --- The window owns Compare's widgets --------------------------------------


def _compare_widgets(window) -> tuple[CodeEditor, FindBar, ChangesPane]:
    compare = window._compare
    return compare.right_editor(), compare._right_bar, compare.changes_pane()


def test_a_window_that_never_compares_deletes_its_compare_widgets(qtbot) -> None:
    """Deleting the window deletes Right, its find bar and Changes with it.

    Without a parent they outlived the window in C++, held only by Compare,
    and Python's gc ran their Qt destructors mid-collection, which could
    crash. Most windows never turn Compare on.
    """
    window = create_main_window()
    window.show()
    qtbot.waitExposed(window)
    _type_input(qtbot, window, _LEFT)
    refs = [weakref.ref(widget) for widget in _compare_widgets(window)]
    window.close()
    window.deleteLater()
    QApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete.value)
    del window

    def _alive() -> list[str]:
        return [
            widget.objectName()
            for ref in refs
            if (widget := ref()) is not None and shiboken6.isValid(widget)
        ]

    # Gone in C++ already, so gc has no Qt object of theirs to delete.
    assert _alive() == []
    gc.collect()
    assert _alive() == []


def test_compare_widgets_stay_hidden_and_out_of_tab_until_compare(qtbot) -> None:
    window = _show(qtbot)
    widgets = _compare_widgets(window)
    for widget in widgets:
        assert widget.parentWidget() is window, widget.objectName()
        assert widget.isHidden(), widget.objectName()
        assert not widget.isVisible(), widget.objectName()
    # Every stop Tab can reach, once round the window's focus chain.
    start = _left(window)
    stops: list[QWidget] = []
    stop = _next_focus(start)
    while stop is not start and stop not in stops:
        stops.append(stop)
        stop = _next_focus(stop)
    assert stops
    reached = [
        stop.objectName()
        for stop in stops
        if any(_is_inside(stop, widget) for widget in widgets)
    ]
    assert reached == []


def test_compare_places_its_widgets_and_leaving_puts_output_back(
    qtbot, inline_compare
) -> None:
    window = _show(qtbot)
    pane = window._output_pane
    layout = pane.layout()
    output_editor, output_bar = pane.editor, pane.find_bar
    editor_at = layout.indexOf(output_editor)
    bar_at = layout.indexOf(output_bar)
    panel = window._problems_pane
    right, bar, changes = _compare_widgets(window)

    _compare(qtbot, window)
    # Right and its find bar take Output's places in the Output pane.
    assert (layout.indexOf(right), layout.indexOf(bar)) == (editor_at, bar_at)
    assert layout.indexOf(output_editor) == layout.indexOf(output_bar) == -1
    assert right.parentWidget() is pane
    assert bar.parentWidget() is pane
    assert right.isVisible()
    _focus(qtbot, right)
    _action(window, "Find…").trigger()
    assert bar.is_open()
    assert bar.isVisible()
    # Changes is the results panel's second tab, current and shown.
    assert _tab_titles(panel) == ["Problems", "Changes"]
    assert panel.current_page() is changes
    assert _is_inside(changes, panel)
    assert changes.isVisible()

    _action(window, "Compare").trigger()
    assert not _action(window, "Compare").isChecked()
    put_back = (layout.indexOf(output_editor), layout.indexOf(output_bar))
    assert put_back == (editor_at, bar_at)
    assert layout.indexOf(right) == layout.indexOf(bar) == -1
    assert output_editor.isVisible()
    assert _tab_titles(panel) == ["Problems"]
    for widget in (right, bar, changes):
        assert widget.isHidden(), widget.objectName()
        assert _is_inside(widget, window), widget.objectName()


# --- The real worker -------------------------------------------------------


def test_a_compare_on_the_spawn_worker_fills_the_changes_pane(qtbot) -> None:
    window = _show(qtbot)
    try:
        _type_input(qtbot, window, _LEFT)
        _action(window, "Compare").trigger()
        _paste_right(window, _RIGHT)
        qtbot.waitUntil(
            lambda: _changes(window).count() == 4, timeout=_SPAWN_TIMEOUT_MS
        )
        assert window._status.counts_text() == "4 differences"
        _action(window, "Compare").trigger()
        assert _left(window).toPlainText() == _LEFT
    finally:
        window.shutdown_parse_worker()


def _is_inside(widget: QWidget | None, ancestor: QWidget) -> bool:
    while widget is not None:
        if widget is ancestor:
            return True
        widget = widget.parentWidget()
    return False
