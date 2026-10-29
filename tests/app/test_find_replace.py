"""Find and Replace on Input (release check, Chief's brief; ui-spec 4b).

Regex and Whole word are in test_find_regex.py.
"""

import io
import sys
from contextlib import redirect_stdout

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence, QPalette, QTextCursor
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton
from tests.core.order_lines import QUERY, REPLACE_BYTES, order_lines
from tests.perf import time_runs

import galley.app.main_window as main_window_module
from galley.app.actions import main, not_yet_failure, release_failure
from galley.app.editor import CodeEditor
from galley.app.find_bar import (
    NO_RESULTS,
    REPLACED_MS,
    FindBar,
    replaced_announcement,
    replaced_text,
)
from galley.app.fonts import code_font
from galley.app.main import create_main_window
from galley.app.main_window import NOT_YET_MESSAGE
from galley.app.parse_session import ParseSession
from galley.app.shortcuts import FIND_AND_REPLACE
from galley.app.theme import TEXT_MIN_CONTRAST, composite, contrast_ratio

_R = QPalette.ColorRole
_PORTABLE = QKeySequence.SequenceFormat.PortableText
_NATIVE = QKeySequence.SequenceFormat.NativeText
_WAIT_MS = 15_000
_ORDERS = '{"qty": 1, "items": [{"qty": 2}, {"QTY": 3}]}'
_COMING_SOON_OUTPUT = (
    'Release blocked: these actions still show "Coming soon": '
    "Open Right Document…, Link Scrolling.\n"
    "Build them or remove them from UNBUILT_ACTIONS in actions.py "
    "before tagging.\n"
)


def _window(qtbot):
    window = create_main_window()
    qtbot.addWidget(window)
    window.resize(1200, 700)
    window.show()
    qtbot.waitExposed(window)
    return window


def _editor(window, name: str) -> CodeEditor:
    editor = window.findChild(CodeEditor, f"{name}Editor")
    assert isinstance(editor, CodeEditor)
    return editor


def _bar(window, name: str) -> FindBar:
    bar = window.findChild(FindBar, f"{name}FindBar")
    assert isinstance(bar, FindBar)
    return bar


def _action(window, text: str) -> QAction:
    found = [item for item in window.findChildren(QAction) if item.text() == text]
    assert len(found) == 1, text
    return found[0]


def _focus(qtbot, widget) -> None:
    widget.activateWindow()
    widget.setFocus()
    qtbot.waitUntil(widget.hasFocus, timeout=2000)


def _input_with(qtbot, text: str):
    window = _window(qtbot)
    editor = _editor(window, "input")
    editor.setPlainText(text)
    _focus(qtbot, editor)
    return window, editor, _bar(window, "input")


def _open_replace(qtbot, window, query: str, replacement: str) -> FindBar:
    """Find and Replace, then type ``query`` and ``replacement``."""
    bar = _bar(window, "input")
    _action(window, "Find and Replace…").trigger()
    qtbot.waitUntil(bar.field.hasFocus, timeout=2000)
    qtbot.keyClicks(bar.field, query)
    bar.replace_field.setText(replacement)
    return bar


def _select(editor: CodeEditor, start: int, end: int | None = None) -> None:
    cursor = editor.textCursor()
    cursor.setPosition(start)
    if end is not None:
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)


def _match_text(editor: CodeEditor, bar: FindBar) -> str:
    match = bar.current_match()
    assert match is not None
    return editor.toPlainText()[match[0] : match[1]]


def _hold(monkeypatch, window) -> None:
    """Keep a Format job running without sending it to the worker."""
    monkeypatch.setattr(ParseSession, "active_job_id", property(lambda _self: 1))
    window._session.submit = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    monkeypatch.setattr(
        main_window_module, "_busy_progress_bytes", lambda source, text: (1, 10**9)
    )


def _replace_buttons_enabled(bar: FindBar) -> tuple[bool, bool]:
    return bar.replace_button.isEnabled(), bar.replace_all_button.isEnabled()


# Shortcut and menu ----------------------------------------------------------


def test_the_shortcut_is_set_by_platform_not_the_standard_replace_key() -> None:
    expected = "Ctrl+Alt+F" if sys.platform == "darwin" else "Ctrl+H"
    assert FIND_AND_REPLACE.toString(_PORTABLE) == expected


def test_the_edit_menu_item_has_the_platform_shortcut(qtbot) -> None:
    window = _window(qtbot)
    action = _action(window, "Find and Replace…")
    shortcuts = [sequence.toString(_PORTABLE) for sequence in action.shortcuts()]
    if sys.platform == "darwin":
        # ⌘H is the system Hide on macOS; the standard Replace key gave it.
        assert shortcuts == ["Ctrl+Alt+F"]
        assert action.shortcut().toString(_NATIVE) == "⌥⌘F"
    else:
        assert shortcuts == ["Ctrl+H"]
    assert action.isEnabled()
    assert action.toolTip() != "Coming soon"


def test_the_shortcut_clashes_with_no_other_action(qtbot) -> None:
    window = _window(qtbot)
    action = _action(window, "Find and Replace…")
    others = [
        item.text() or item.objectName()
        for item in window.findChildren(QAction)
        if item is not action
        and any(sequence == FIND_AND_REPLACE for sequence in item.shortcuts())
    ]
    assert others == []
    if sys.platform != "darwin":
        modifiers = FIND_AND_REPLACE[0].keyboardModifiers()
        assert not modifiers & Qt.KeyboardModifier.AltModifier


def test_the_shortcut_opens_find_and_replace(qtbot) -> None:
    window, editor, bar = _input_with(qtbot, _ORDERS)
    combo = FIND_AND_REPLACE[0]
    qtbot.keyClick(editor, combo.key(), combo.keyboardModifiers())
    assert bar.is_open()
    assert bar.replace_row_shown()


# Opening -------------------------------------------------------------------


@pytest.mark.parametrize("focused", ["input", "output"])
def test_find_and_replace_opens_the_input_bar_with_the_replace_row(
    qtbot, focused
) -> None:
    window = _window(qtbot)
    _editor(window, "input").setPlainText(_ORDERS)
    _editor(window, "output").setPlainText(_ORDERS)
    _focus(qtbot, _editor(window, focused))
    _action(window, "Find and Replace…").trigger()
    bar = _bar(window, "input")
    assert bar.is_open()
    assert bar.replace_row_shown()
    assert bar.replace_field.isVisible()
    assert bar.replace_button.isVisible()
    assert bar.replace_all_button.isVisible()
    assert window.statusBar().currentMessage() != NOT_YET_MESSAGE
    output_bar = _bar(window, "output")
    assert not output_bar.replace_row_shown()
    assert not output_bar.replace_field.isVisible()


def test_output_find_stays_search_only(qtbot) -> None:
    window = _window(qtbot)
    output = _editor(window, "output")
    output.setPlainText(_ORDERS)
    _focus(qtbot, output)
    _action(window, "Find…").trigger()
    bar = _bar(window, "output")
    assert bar.is_open()
    assert not bar.replace_row_shown()
    bar.open(replace=True)
    assert not bar.replace_row_shown()
    assert not bar.replace_field.isVisible()
    assert not bar.replace_button.isVisible()


def test_find_opens_without_the_replace_row(qtbot) -> None:
    window, _editor_, bar = _input_with(qtbot, _ORDERS)
    _action(window, "Find…").trigger()
    assert bar.is_open()
    assert not bar.replace_row_shown()
    assert not bar.replace_field.isVisible()


def test_find_on_an_open_replace_bar_keeps_the_replace_row(qtbot) -> None:
    window, _editor_, bar = _input_with(qtbot, _ORDERS)
    _action(window, "Find and Replace…").trigger()
    _focus(qtbot, bar.replace_field)
    _action(window, "Find…").trigger()
    assert bar.replace_row_shown()
    qtbot.waitUntil(bar.field.hasFocus, timeout=2000)
    # Closing ends replace mode, so the next Find is search-only.
    qtbot.keyClick(bar.field, Qt.Key.Key_Escape)
    _action(window, "Find…").trigger()
    assert not bar.replace_row_shown()


def test_opening_fills_the_find_field_from_the_selection_and_focuses_it(
    qtbot,
) -> None:
    window, editor, bar = _input_with(qtbot, _ORDERS)
    _select(editor, 2, 5)
    _action(window, "Find and Replace…").trigger()
    assert bar.field.text() == "qty"
    qtbot.waitUntil(bar.field.hasFocus, timeout=2000)
    assert bar.match_count() == 3


def test_opening_with_an_empty_selection_focuses_the_empty_find_field(qtbot) -> None:
    window, _editor_, bar = _input_with(qtbot, _ORDERS)
    _action(window, "Find and Replace…").trigger()
    assert bar.field.text() == ""
    qtbot.waitUntil(bar.field.hasFocus, timeout=2000)


def test_opening_with_text_already_in_the_find_field_focuses_replace(qtbot) -> None:
    window, editor, bar = _input_with(qtbot, _ORDERS)
    _action(window, "Find…").trigger()
    qtbot.keyClicks(bar.field, "qty")
    qtbot.keyClick(bar.field, Qt.Key.Key_Escape)
    _focus(qtbot, editor)
    _select(editor, 0)
    _action(window, "Find and Replace…").trigger()
    assert bar.field.text() == "qty"
    qtbot.waitUntil(bar.replace_field.hasFocus, timeout=2000)


# Controls ------------------------------------------------------------------


def test_the_buttons_are_labeled_exactly_with_no_icons(qtbot) -> None:
    window, _editor_, bar = _input_with(qtbot, _ORDERS)
    _action(window, "Find and Replace…").trigger()
    assert isinstance(bar.replace_button, QPushButton)
    assert isinstance(bar.replace_all_button, QPushButton)
    assert bar.replace_button.text() == "Replace"
    assert bar.replace_all_button.text() == "Replace All"
    assert bar.replace_button.icon().isNull()
    assert bar.replace_all_button.icon().isNull()
    assert bar.replace_button.accessibleName() == "Replace"
    assert bar.replace_all_button.accessibleName() == "Replace All"
    assert isinstance(bar.replace_field, QLineEdit)
    assert bar.replace_field.placeholderText() == "Replace"
    assert bar.replace_field.accessibleName() == "Replace with"
    assert bar.replace_field.font().family() == code_font().family()


def test_tab_moves_find_replace_buttons_then_the_existing_controls(qtbot) -> None:
    window, _editor_, bar = _input_with(qtbot, _ORDERS)
    _action(window, "Find and Replace…").trigger()
    qtbot.keyClicks(bar.field, "qty")
    expected = [
        bar.field,
        bar.regex_button,
        bar.whole_word_button,
        bar.replace_field,
        bar.replace_button,
        bar.replace_all_button,
        bar.previous_button,
        bar.next_button,
        bar.match_case,
        bar.close_button,
    ]
    for widget in expected:
        assert widget.focusPolicy() & Qt.FocusPolicy.TabFocus
    qtbot.waitUntil(bar.field.hasFocus, timeout=2000)
    reached = [QApplication.focusWidget()]
    for _ in range(len(expected) - 1):
        qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab)
        reached.append(QApplication.focusWidget())
    assert reached == expected


def test_the_buttons_are_greyed_out_with_an_empty_query_or_no_matches(qtbot) -> None:
    window, _editor_, bar = _input_with(qtbot, _ORDERS)
    _action(window, "Find and Replace…").trigger()
    assert _replace_buttons_enabled(bar) == (False, False)
    qtbot.keyClicks(bar.field, "qty")
    assert _replace_buttons_enabled(bar) == (True, True)
    qtbot.keyClicks(bar.field, "zz")
    assert bar.count_label_text() == NO_RESULTS
    assert _replace_buttons_enabled(bar) == (False, False)
    bar.field.clear()
    assert _replace_buttons_enabled(bar) == (False, False)


def test_the_buttons_are_greyed_out_while_a_job_runs(qtbot, monkeypatch) -> None:
    window, editor, _bar_ = _input_with(qtbot, _ORDERS)
    _hold(monkeypatch, window)
    bar = _open_replace(qtbot, window, "qty", "quantity")
    assert _replace_buttons_enabled(bar) == (True, True)
    window._actions.format.trigger()
    assert window._formatting
    assert _replace_buttons_enabled(bar) == (False, False)
    bar.replace_current()
    bar.replace_all()
    assert editor.toPlainText() == _ORDERS
    window._cancel_format()
    qtbot.waitUntil(lambda: not window._formatting, timeout=_WAIT_MS)
    assert _replace_buttons_enabled(bar) == (True, True)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_replace_row_contrast(qtbot, install_scheme, scheme) -> None:
    window, _editor_, _bar_ = _input_with(qtbot, _ORDERS)
    install_scheme(scheme)
    bar = _open_replace(qtbot, window, "qty", "quantity")
    field = bar.replace_field.palette()
    base = field.color(_R.Base)
    assert contrast_ratio(composite(field.color(_R.Text), base), base) >= (
        TEXT_MIN_CONTRAST
    )
    placeholder = composite(field.color(_R.PlaceholderText), base)
    assert contrast_ratio(placeholder, base) >= TEXT_MIN_CONTRAST
    button = bar.replace_button.palette()
    face = button.color(_R.Button)
    label = composite(button.color(_R.ButtonText), face)
    assert contrast_ratio(label, face) >= TEXT_MIN_CONTRAST
    window_bg = bar.palette().color(_R.Window)
    text = composite(bar.palette().color(_R.WindowText), window_bg)
    assert contrast_ratio(text, window_bg) >= TEXT_MIN_CONTRAST


# Replace -------------------------------------------------------------------


def test_replace_replaces_the_current_match_and_selects_the_next(qtbot) -> None:
    window, editor, _bar_ = _input_with(qtbot, "qty qty qty")
    bar = _open_replace(qtbot, window, "qty", "quantity")
    assert bar.current_match() == (0, 3)
    bar.replace_button.click()
    assert editor.toPlainText() == "quantity qty qty"
    assert bar.current_match() == (9, 12)
    assert bar.count_label_text() == "1 of 2"
    bar.replace_button.click()
    assert editor.toPlainText() == "quantity quantity qty"
    assert bar.current_match() == (18, 21)


def test_return_in_the_replace_field_replaces_only_while_replace_is_enabled(
    qtbot,
) -> None:
    window, editor, _bar_ = _input_with(qtbot, "qty qty")
    bar = _open_replace(qtbot, window, "qty", "n")
    _focus(qtbot, bar.replace_field)
    qtbot.keyClick(bar.replace_field, Qt.Key.Key_Return)
    assert editor.toPlainText() == "n qty"
    assert bar.current_match() == (2, 5)
    qtbot.keyClick(
        bar.replace_field, Qt.Key.Key_Enter, Qt.KeyboardModifier.KeypadModifier
    )
    assert editor.toPlainText() == "n n"
    assert not bar.replace_button.isEnabled()
    qtbot.keyClick(bar.replace_field, Qt.Key.Key_Return)
    assert editor.toPlainText() == "n n"
    assert bar.replace_field.hasFocus()
    # Greyed out by a running job too: Return does nothing.
    editor.setPlainText("qty")
    bar.search()
    bar.set_replace_blocked(True)
    qtbot.keyClick(bar.replace_field, Qt.Key.Key_Return)
    assert editor.toPlainText() == "qty"


def test_the_count_and_replace_all_skip_a_no_break_space(qtbot) -> None:
    # "a b" twice: once with a space, once with a no-break space.
    original = "a b, a\u00a0b"
    window, editor, _bar_ = _input_with(qtbot, original)
    bar = _open_replace(qtbot, window, "a b", "x")
    assert bar.match_count() == 1
    assert bar.current_match() == (0, 3)
    assert bar.replace_all() == 1
    assert bar.count_label_text() == replaced_text(1)
    assert editor.document().toRawText() == "x, a\u00a0b"
    editor.undo()
    assert editor.document().toRawText() == original


def test_replace_wraps_to_the_first_match(qtbot) -> None:
    window, editor, _bar_ = _input_with(qtbot, "qty a qty b qty")
    _select(editor, 7)
    bar = _open_replace(qtbot, window, "qty", "n")
    assert bar.current_match() == (12, 15)
    bar.replace_current()
    assert editor.toPlainText() == "qty a qty b n"
    assert bar.current_match() == (0, 3)
    assert bar.count_label_text() == "1 of 2"


def test_each_replace_is_one_undo_step(qtbot) -> None:
    window, editor, _bar_ = _input_with(qtbot, "qty qty qty")
    bar = _open_replace(qtbot, window, "qty", "quantity")
    bar.replace_current()
    bar.replace_current()
    assert editor.toPlainText() == "quantity quantity qty"
    editor.undo()
    assert editor.toPlainText() == "quantity qty qty"
    editor.undo()
    assert editor.toPlainText() == "qty qty qty"
    assert not editor.document().isUndoAvailable()


def test_an_empty_replace_field_deletes_the_match(qtbot) -> None:
    window, editor, _bar_ = _input_with(qtbot, "a-b-c")
    bar = _open_replace(qtbot, window, "-", "")
    bar.replace_current()
    assert editor.toPlainText() == "ab-c"
    assert bar.current_match() == (2, 3)


def test_a_replacement_that_contains_the_query_does_not_match_in_place(
    qtbot,
) -> None:
    window, editor, _bar_ = _input_with(qtbot, "a-a")
    bar = _open_replace(qtbot, window, "a", "aa")
    bar.replace_current()
    assert editor.toPlainText() == "aa-a"
    # The search runs again from the end of "aa", so its own "a"s are skipped.
    assert bar.current_match() == (3, 4)


def test_replace_follows_match_case(qtbot) -> None:
    window, editor, _bar_ = _input_with(qtbot, _ORDERS)
    bar = _open_replace(qtbot, window, "qty", "Quantity")
    assert bar.match_count() == 3
    bar.replace_all()
    assert editor.toPlainText() == _ORDERS.replace("qty", "Quantity").replace(
        "QTY", "Quantity"
    )
    editor.undo()
    bar.match_case.click()
    assert bar.match_count() == 2
    bar.replace_all()
    assert "QTY" in editor.toPlainText()
    assert "qty" not in editor.toPlainText()


def test_when_no_matches_are_left_focus_stays_in_the_replace_field(qtbot) -> None:
    window, editor, _bar_ = _input_with(qtbot, "one qty")
    bar = _open_replace(qtbot, window, "qty", "quantity")
    _focus(qtbot, bar.replace_button)
    qtbot.keyClick(bar.replace_button, Qt.Key.Key_Space)
    assert editor.toPlainText() == "one quantity"
    assert bar.count_label_text() == NO_RESULTS
    assert _replace_buttons_enabled(bar) == (False, False)
    qtbot.waitUntil(bar.replace_field.hasFocus, timeout=2000)


def test_esc_closes_both_rows_and_selects_the_last_replacement(qtbot) -> None:
    window, editor, _bar_ = _input_with(qtbot, "qty qty")
    bar = _open_replace(qtbot, window, "qty", "quantity")
    bar.replace_current()
    _focus(qtbot, bar.replace_field)
    qtbot.keyClick(bar.replace_field, Qt.Key.Key_Escape)
    assert not bar.is_open()
    assert not bar.replace_row_shown()
    assert not bar.replace_field.isVisible()
    qtbot.waitUntil(editor.hasFocus, timeout=2000)
    cursor = editor.textCursor()
    assert (cursor.selectionStart(), cursor.selectionEnd()) == (0, 8)
    assert cursor.selectedText() == "quantity"


# Replace All ---------------------------------------------------------------


def test_replace_all_is_one_edit_and_one_undo_restores_the_original(qtbot) -> None:
    original = _ORDERS + "\nqty 😀 qty\n"
    window, editor, _bar_ = _input_with(qtbot, original)
    bar = _open_replace(qtbot, window, "qty", "quantity")
    edits: list[tuple[int, int, int]] = []
    editor.document().contentsChange.connect(
        lambda position, removed, added: edits.append((position, removed, added))
    )
    bar.replace_all_button.click()
    assert editor.toPlainText() == original.replace("qty", "quantity").replace(
        "QTY", "quantity"
    )
    # One insert over the whole document, never one per match.
    assert len(edits) == 1
    assert edits[0][0] == 0
    editor.undo()
    assert editor.toPlainText() == original
    assert not editor.document().isUndoAvailable()


def test_replace_all_counts_again_once(qtbot) -> None:
    window, editor, _bar_ = _input_with(qtbot, "a a a")
    bar = _open_replace(qtbot, window, "a", "aa")
    bar.replace_all()
    assert editor.toPlainText() == "aa aa aa"
    assert bar.match_count() == 6


def test_replace_all_shows_replaced_n_then_the_live_count(qtbot) -> None:
    window, editor, _bar_ = _input_with(qtbot, " ".join(["qty"] * 12))
    bar = _open_replace(qtbot, window, "qty", "quantity")
    heard: list[str] = []
    bar.count_changed.connect(heard.append)
    bar.replace_all()
    assert bar.count_label_text() == "Replaced 12"
    assert bar.count_label.text() == "Replaced 12"
    assert heard == ["Replaced 12 matches."]
    assert bar.replaced_timer().interval() == REPLACED_MS == 4000
    assert bar.replaced_timer().isActive()
    bar.replaced_timer().timeout.emit()
    assert bar.count_label_text() == NO_RESULTS
    assert heard[-1] == NO_RESULTS
    assert _replace_buttons_enabled(bar) == (False, False)
    assert editor.toPlainText() == " ".join(["quantity"] * 12)


def test_replaced_wording_for_one_and_many() -> None:
    assert replaced_text(1) == "Replaced 1"
    assert replaced_announcement(1) == "Replaced 1 match."
    assert replaced_text(12) == "Replaced 12"
    assert replaced_announcement(12) == "Replaced 12 matches."
    assert replaced_text(1200) == "Replaced 1,200"


def test_replace_all_of_one_match_says_replaced_1(qtbot) -> None:
    window, _editor_, _bar_ = _input_with(qtbot, "one qty")
    bar = _open_replace(qtbot, window, "qty", "quantity")
    heard: list[str] = []
    bar.count_changed.connect(heard.append)
    bar.replace_all()
    assert bar.count_label_text() == "Replaced 1"
    assert heard == ["Replaced 1 match."]


def test_typing_in_the_find_field_restores_the_count_right_away(qtbot) -> None:
    window, _editor_, _bar_ = _input_with(qtbot, "qty qty quantity")
    bar = _open_replace(qtbot, window, "qty", "quantity")
    bar.replace_all()
    assert bar.count_label_text() == "Replaced 2"
    bar.field.setText("quant")
    assert not bar.replaced_timer().isActive()
    assert bar.count_label_text() == "1 of 3"


def test_replace_all_leaves_the_editor_unlocked_and_focus_in_the_bar(qtbot) -> None:
    window, editor, _bar_ = _input_with(qtbot, "qty qty")
    bar = _open_replace(qtbot, window, "qty", "quantity")
    _focus(qtbot, bar.replace_field)
    bar.replace_all()
    assert not editor.isReadOnly()
    assert bar.is_open()
    qtbot.waitUntil(bar.replace_field.hasFocus, timeout=2000)


def test_esc_after_replace_all_selects_the_last_replacement(qtbot) -> None:
    window, editor, _bar_ = _input_with(qtbot, "qty, qty.")
    bar = _open_replace(qtbot, window, "qty", "quantity")
    bar.replace_all()
    _focus(qtbot, bar.replace_field)
    qtbot.keyClick(bar.replace_field, Qt.Key.Key_Escape)
    qtbot.waitUntil(editor.hasFocus, timeout=2000)
    cursor = editor.textCursor()
    assert (cursor.selectionStart(), cursor.selectionEnd()) == (10, 18)


# Release check -------------------------------------------------------------


def test_find_and_replace_is_off_the_not_yet_list(qtbot) -> None:
    window = _window(qtbot)
    actions = window._actions
    assert actions.find_replace not in actions._not_yet_actions()
    assert actions.find_replace not in actions.stubs()
    assert actions._not_yet_actions() == []
    assert not_yet_failure() is None
    message = release_failure()
    assert message is not None
    assert "Find and Replace" not in message


def test_the_release_check_leaves_out_an_empty_not_yet_section() -> None:
    printed = io.StringIO()
    with redirect_stdout(printed):
        assert main() == 1
    assert printed.getvalue() == _COMING_SOON_OUTPUT
    assert "Not yet" not in printed.getvalue()
    assert "\n\n" not in printed.getvalue()


# Performance (timing only, no budget) ----------------------------------------


def _large_replace(qtbot):
    text = order_lines()
    assert REPLACE_BYTES <= len(text.encode("utf-8")) < 2_500_000
    window, editor, _bar_ = _input_with(qtbot, text)
    bar = _open_replace(qtbot, window, QUERY, "quantity")
    qtbot.waitUntil(lambda: bar.match_count() > 90_000, timeout=_WAIT_MS)
    return text, editor, bar


def _print_timing(test: str, fixture: str, timing) -> None:
    samples = ", ".join(f"{sample * 1000:.1f}" for sample in timing.samples)
    seconds = timing.median
    note = " - over 1 s" if seconds > 1.0 else ""
    print(
        f"perf: {test} [{fixture}] median {seconds * 1000:.1f} ms "
        f"({sys.platform}; timing only, no budget){note} ({samples})",
        flush=True,
    )


@pytest.mark.perf
def test_replace_all_on_2_4_mb_input_is_timed(qtbot) -> None:
    text, editor, bar = _large_replace(qtbot)
    matches = bar.match_count()
    size = len(text.encode("utf-8"))

    def reset() -> None:
        if editor.toPlainText() != text:
            editor.setPlainText(text)
        bar.search()

    bar.replace_all()
    assert bar.count_label_text() == replaced_text(matches)
    assert editor.toPlainText() == text.replace(QUERY, "quantity")
    assert not editor.isReadOnly()
    editor.undo()
    assert editor.toPlainText() == text
    timing = time_runs(bar.replace_all, setup=reset)
    assert not editor.isReadOnly()
    assert bar.count_label_text() == replaced_text(matches)
    _print_timing(
        "test_replace_all_on_2_4_mb_input_is_timed",
        f"Replace All, {size:,} B JSON, {matches:,} matches",
        timing,
    )


@pytest.mark.perf
def test_one_replace_on_2_4_mb_input_is_timed(qtbot) -> None:
    text, editor, bar = _large_replace(qtbot)
    matches = bar.match_count()
    size = len(text.encode("utf-8"))
    timing = time_runs(bar.replace_current)
    replaced = 1 + len(timing.samples)
    assert bar.match_count() == matches - replaced
    assert editor.toPlainText().count("quantity") == replaced
    assert not editor.isReadOnly()
    _print_timing(
        "test_one_replace_on_2_4_mb_input_is_timed",
        f"Replace, {size:,} B JSON, {matches:,} matches",
        timing,
    )
