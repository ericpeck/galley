"""Inline find bar (#52) and Keys only on Output (#62)."""

import json
import random
import sys

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import (
    QAccessible,
    QAction,
    QColor,
    QKeySequence,
    QPalette,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from galley.app.actions import UNBUILT_ACTIONS, not_yet_failure, release_failure
from galley.app.editor import CodeEditor
from galley.app.find_bar import (
    FIND_DELAY_MS,
    NO_RESULTS,
    FindBar,
    FindField,
    count_text,
)
from galley.app.fonts import code_font
from galley.app.main import create_main_window
from galley.app.theme import (
    ACCENT,
    EDGE_CONTRAST,
    FIND_CURRENT_OPACITY,
    TEXT_MIN_CONTRAST,
    composite,
    contrast_ratio,
    error_color,
    find_match_colors,
    is_dark,
    mix,
)

_R = QPalette.ColorRole
_OUTPUT = json.dumps(
    {
        "order": {
            "price": 12,
            "items": [{"sku": "A", "price": 3, "note": "price drop"}],
        },
        "price": "list price",
    },
    indent=2,
)
_YAML = """\
order:
  price: 12
  items:
  - sku: A
    price: 3
    note: price drop
price: list price
"""


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


def _press(qtbot, widget, action: QAction) -> None:
    """Type ``action``'s first shortcut into ``widget``."""
    combo = action.shortcuts()[0][0]
    qtbot.keyClick(widget, combo.key(), combo.keyboardModifiers())


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


def _output_with(qtbot, text: str = _OUTPUT, mode: str = "JSON"):
    window = _window(qtbot)
    output = _editor(window, "output")
    output.set_syntax_mode(mode)
    output.setPlainText(text)
    output.rescan_folds()
    _focus(qtbot, output)
    return window, output, _bar(window, "output")


def _match_text(editor: CodeEditor, bar: FindBar) -> str:
    match = bar.current_match()
    assert match is not None
    return editor.toPlainText()[match[0] : match[1]]


def _line(editor: CodeEditor, position: int) -> int:
    return editor.document().findBlock(position).blockNumber() + 1


# Opening, typing, and the count -------------------------------------------


def test_find_opens_the_bar_over_the_focused_editor(qtbot) -> None:
    window, editor, bar = _input_with(qtbot, "alpha beta alpha")
    assert not bar.isVisible()
    _press(qtbot, editor, _action(window, "Find…"))
    assert bar.isVisible()
    assert not _bar(window, "output").isVisible()
    qtbot.waitUntil(bar.field.hasFocus, timeout=2000)
    assert bar.field.placeholderText() == "Find"
    assert bar.count_label_text() == ""
    # The bar sits above its editor in the same pane.
    assert bar.parentWidget() is editor.parentWidget()
    assert bar.geometry().bottom() <= editor.geometry().top()


def test_the_toolbar_find_button_opens_the_bar_for_the_last_editor(qtbot) -> None:
    window, output, bar = _output_with(qtbot)
    buttons = [
        button
        for button in window.findChildren(QToolButton)
        if button.defaultAction() is _action(window, "Find…")
    ]
    assert len(buttons) == 1
    assert buttons[0].isEnabled()
    window.setFocus()
    buttons[0].click()
    assert bar.isVisible()
    assert not _bar(window, "input").isVisible()


def test_opening_fills_the_field_with_the_selection_and_selects_it(qtbot) -> None:
    window, editor, bar = _input_with(qtbot, "one two three two")
    cursor = editor.textCursor()
    cursor.setPosition(4)
    cursor.setPosition(7, cursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    _action(window, "Find…").trigger()
    assert bar.field.text() == "two"
    assert bar.field.selectedText() == "two"
    assert bar.count_label_text() == "1 of 2"
    assert bar.current_match() == (4, 7)


def test_a_multiline_selection_does_not_fill_the_field(qtbot) -> None:
    window, editor, bar = _input_with(qtbot, "one\ntwo")
    editor.selectAll()
    _action(window, "Find…").trigger()
    assert bar.field.text() == ""


def test_typing_highlights_matches_and_counts_them(qtbot) -> None:
    window, editor, bar = _input_with(qtbot, "ab ab ab ab")
    _action(window, "Find…").trigger()
    qtbot.keyClicks(bar.field, "ab")
    assert bar.match_count() == 4
    assert bar.count_label_text() == "1 of 4"
    assert editor.find_matches() == [(0, 2), (3, 5), (6, 8), (9, 11)]
    assert editor.current_find_match() == 0
    bar.field.clear()
    assert bar.count_label_text() == ""
    assert editor.find_matches() == []


def test_the_count_groups_thousands() -> None:
    assert count_text(2, 12) == "3 of 12"
    assert count_text(1233, 79504) == "1,234 of 79,504"
    assert count_text(-1, 0) == NO_RESULTS


def test_the_first_match_is_at_or_after_the_caret(qtbot) -> None:
    window, editor, bar = _input_with(qtbot, "x.x.x.x")
    cursor = editor.textCursor()
    cursor.setPosition(3)
    editor.setTextCursor(cursor)
    _action(window, "Find…").trigger()
    qtbot.keyClicks(bar.field, "x")
    assert bar.count_label_text() == "3 of 4"


def test_return_and_shift_return_step_and_wrap(qtbot) -> None:
    window, editor, bar = _input_with(qtbot, "a1 a2 a3")
    _action(window, "Find…").trigger()
    qtbot.keyClicks(bar.field, "a")
    seen = [bar.count_label_text()]
    for _ in range(3):
        qtbot.keyClick(bar.field, Qt.Key.Key_Return)
        seen.append(bar.count_label_text())
    assert seen == ["1 of 3", "2 of 3", "3 of 3", "1 of 3"]
    qtbot.keyClick(bar.field, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert bar.count_label_text() == "3 of 3"
    assert editor.textCursor().position() == 6
    qtbot.keyClick(bar.field, Qt.Key.Key_Enter, Qt.KeyboardModifier.KeypadModifier)
    assert bar.count_label_text() == "1 of 3"


def test_previous_and_next_buttons_step(qtbot) -> None:
    window, _editor_, bar = _input_with(qtbot, "q q q")
    _action(window, "Find…").trigger()
    qtbot.keyClicks(bar.field, "q")
    bar.next_button.click()
    assert bar.count_label_text() == "2 of 3"
    bar.previous_button.click()
    bar.previous_button.click()
    assert bar.count_label_text() == "3 of 3"


def test_match_case(qtbot) -> None:
    window, editor, bar = _input_with(qtbot, "Key key KEY")
    _action(window, "Find…").trigger()
    qtbot.keyClicks(bar.field, "key")
    assert bar.match_count() == 3
    bar.match_case.click()
    assert bar.match_case.isChecked()
    assert bar.match_count() == 1
    assert bar.current_match() == (4, 7)


def test_search_is_plain_text_not_regex(qtbot) -> None:
    window, _editor_, bar = _input_with(qtbot, "a.c abc a.c")
    _action(window, "Find…").trigger()
    qtbot.keyClicks(bar.field, "a.c")
    assert bar.match_count() == 2


def test_no_results_shows_words_and_an_error_border(qtbot) -> None:
    window, _editor_, bar = _input_with(qtbot, "alpha")
    _action(window, "Find…").trigger()
    qtbot.keyClicks(bar.field, "zz")
    assert bar.count_label_text() == NO_RESULTS
    assert bar.count_label.text() == NO_RESULTS
    assert "placeholder-text" in bar.count_label.styleSheet()
    assert bar.field.no_results()
    assert bar.field.accessibleDescription() == NO_RESULTS
    border = bar.field.grab().toImage().pixelColor(1, bar.field.height() // 2)
    assert border.name() == error_color(bar.field.palette()).name()
    bar.field.clear()
    assert not bar.field.no_results()
    assert bar.count_label.text() == ""


def test_astral_characters_keep_matches_in_place(qtbot) -> None:
    window, editor, bar = _input_with(qtbot, "😀 key 😀 key")
    _action(window, "Find…").trigger()
    qtbot.keyClicks(bar.field, "key")
    assert bar.match_count() == 2
    for start, end in editor.find_matches():
        cursor = QTextCursor(editor.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        assert cursor.selectedText() == "key"


# Esc, close, and focus ------------------------------------------------------


def test_esc_closes_and_selects_the_current_match_in_the_editor(qtbot) -> None:
    window, editor, bar = _input_with(qtbot, "aa bb aa bb")
    _action(window, "Find…").trigger()
    qtbot.keyClicks(bar.field, "bb")
    qtbot.keyClick(bar.field, Qt.Key.Key_Return)
    qtbot.keyClick(bar.field, Qt.Key.Key_Escape)
    assert not bar.isVisible()
    assert not bar.is_open()
    qtbot.waitUntil(editor.hasFocus, timeout=2000)
    assert editor.textCursor().selectedText() == "bb"
    assert editor.textCursor().selectionStart() == 9
    assert editor.find_matches() == []


def test_esc_from_an_option_closes_too(qtbot) -> None:
    window, editor, bar = _input_with(qtbot, "aa")
    _action(window, "Find…").trigger()
    _focus(qtbot, bar.match_case)
    qtbot.keyClick(bar.match_case, Qt.Key.Key_Escape)
    assert not bar.isVisible()
    qtbot.waitUntil(editor.hasFocus, timeout=2000)


def test_the_close_button_closes(qtbot) -> None:
    window, editor, bar = _input_with(qtbot, "aa")
    _action(window, "Find…").trigger()
    qtbot.keyClicks(bar.field, "a")
    bar.close_button.click()
    assert not bar.isVisible()
    qtbot.waitUntil(editor.hasFocus, timeout=2000)
    assert editor.textCursor().selectedText() == "a"


def test_find_again_while_open_selects_the_field(qtbot) -> None:
    window, editor, bar = _input_with(qtbot, "abc")
    _action(window, "Find…").trigger()
    qtbot.keyClicks(bar.field, "b")
    _focus(qtbot, editor)
    _press(qtbot, editor, _action(window, "Find…"))
    qtbot.waitUntil(bar.field.hasFocus, timeout=2000)
    assert bar.field.selectedText() == "b"


# Shortcuts ---------------------------------------------------------------


def test_find_next_and_previous_use_the_standard_keys(qtbot) -> None:
    window = _window(qtbot)
    for text, key in (
        ("Find…", QKeySequence.StandardKey.Find),
        ("Find Next", QKeySequence.StandardKey.FindNext),
        ("Find Previous", QKeySequence.StandardKey.FindPrevious),
    ):
        action = _action(window, text)
        bindings = [
            item for item in QKeySequence.keyBindings(key) if not item.isEmpty()
        ]
        assert action.shortcuts() == bindings
        portable = {
            item.toString(QKeySequence.SequenceFormat.PortableText)
            for item in action.shortcuts()
        }
        if sys.platform == "darwin":
            expected = {"Find…": "Ctrl+F", "Find Next": "Ctrl+G"}
            expected["Find Previous"] = "Ctrl+Shift+G"
            assert expected[text] in portable, portable
        elif sys.platform == "win32":
            expected = {
                "Find…": "Ctrl+F",
                "Find Next": "F3",
                "Find Previous": "Shift+F3",
            }
            assert expected[text] in portable, portable
        # No Ctrl+Alt: it is AltGr on some Windows layouts.
        for item in action.shortcuts():
            modifiers = item[0].keyboardModifiers()
            assert not (
                modifiers & Qt.KeyboardModifier.ControlModifier
                and modifiers & Qt.KeyboardModifier.AltModifier
            )


def test_find_next_shortcut_steps_from_the_editor(qtbot) -> None:
    window, editor, bar = _input_with(qtbot, "k k k")
    _action(window, "Find…").trigger()
    qtbot.keyClicks(bar.field, "k")
    _focus(qtbot, editor)
    _press(qtbot, editor, _action(window, "Find Next"))
    assert bar.count_label_text() == "2 of 3"
    _press(qtbot, editor, _action(window, "Find Previous"))
    assert bar.count_label_text() == "1 of 3"
    assert editor.hasFocus()


def test_find_next_with_the_bar_closed_reopens_it_with_the_last_query(qtbot) -> None:
    window, editor, bar = _input_with(qtbot, "k k k")
    _action(window, "Find…").trigger()
    qtbot.keyClicks(bar.field, "k")
    qtbot.keyClick(bar.field, Qt.Key.Key_Escape)
    assert editor.textCursor().selectionStart() == 0
    _action(window, "Find Next").trigger()
    assert bar.isVisible()
    assert bar.count_label_text() == "2 of 3"


def test_find_next_with_no_query_opens_the_field(qtbot) -> None:
    window, editor, bar = _input_with(qtbot, "k")
    _action(window, "Find Next").trigger()
    assert bar.isVisible()
    qtbot.waitUntil(bar.field.hasFocus, timeout=2000)


# Keys only ----------------------------------------------------------------


def test_only_output_offers_keys_only(qtbot) -> None:
    window = _window(qtbot)
    assert _bar(window, "input").keys_only is None
    keys_only = _bar(window, "output").keys_only
    assert isinstance(keys_only, QCheckBox)
    assert keys_only.text() == "Keys only"
    assert keys_only.accessibleName() == "Keys only"


def test_keys_only_tooltip_and_description(qtbot) -> None:
    """#122: Bill's wording. Screen readers hear the tooltip as the description."""
    window = _window(qtbot)
    keys_only = _bar(window, "output").keys_only
    assert keys_only.toolTip() == "Match only object keys, not values"
    assert keys_only.accessibleDescription() in ("", keys_only.toolTip())
    face = QAccessible.queryAccessibleInterface(keys_only)
    assert face is not None
    assert face.text(QAccessible.Text.Description) == keys_only.toolTip()


def test_keys_only_skips_values_with_the_same_text_in_json(qtbot) -> None:
    window, output, bar = _output_with(qtbot)
    _action(window, "Find…").trigger()
    qtbot.keyClicks(bar.field, "price")
    assert bar.match_count() == 5
    bar.keys_only.click()
    assert bar.match_count() == 3
    text = output.toPlainText()
    for start, end in output.find_matches():
        assert text[end] == '"' and text[end + 1] == ":"
    assert bar.count_label_text() == "1 of 3"
    lines = [_line(output, start) for start, _end in output.find_matches()]
    assert lines == [3, 7, 12]


def test_keys_only_in_yaml_output(qtbot) -> None:
    window, output, bar = _output_with(qtbot, _YAML, "YAML")
    _action(window, "Find…").trigger()
    bar.keys_only.click()
    qtbot.keyClicks(bar.field, "price")
    assert [_line(output, start) for start, _end in output.find_matches()] == [
        2,
        5,
        7,
    ]
    qtbot.keyClick(bar.field, Qt.Key.Key_Return)
    qtbot.keyClick(bar.field, Qt.Key.Key_Return)
    assert bar.count_label_text() == "3 of 3"
    qtbot.keyClick(bar.field, Qt.Key.Key_Return)
    assert bar.count_label_text() == "1 of 3"


def test_keys_only_follows_a_new_output(qtbot) -> None:
    window, output, bar = _output_with(qtbot)
    _action(window, "Find…").trigger()
    bar.keys_only.click()
    qtbot.keyClicks(bar.field, "sku")
    assert bar.match_count() == 1
    output.setPlainText('{"sku": 1, "b": {"sku": "sku"}}')
    qtbot.waitUntil(lambda: bar.match_count() == 2, timeout=2000)


def test_the_bar_does_not_change_output_text(qtbot) -> None:
    window, output, bar = _output_with(qtbot)
    _action(window, "Find…").trigger()
    qtbot.keyClicks(bar.field, "price")
    for _ in range(6):
        qtbot.keyClick(bar.field, Qt.Key.Key_Return)
    bar.keys_only.click()
    qtbot.keyClick(bar.field, Qt.Key.Key_Escape)
    assert output.toPlainText() == _OUTPUT
    assert not output.document().isUndoAvailable()


# Folding -------------------------------------------------------------------


def test_a_match_inside_a_fold_opens_it(qtbot) -> None:
    window, output, bar = _output_with(qtbot)
    assert output.folding_enabled()
    output.collapse_all()
    sku_line = _OUTPUT.splitlines().index('        "sku": "A",') + 1
    block = output.document().findBlockByNumber(sku_line - 1)
    assert not block.isVisible()
    _action(window, "Find…").trigger()
    qtbot.keyClicks(bar.field, '"sku"')
    assert bar.match_count() == 1
    assert block.isVisible()
    assert _line(output, bar.current_match()[0]) == sku_line


def test_next_into_a_fold_opens_it(qtbot) -> None:
    window, output, bar = _output_with(qtbot)
    _action(window, "Find…").trigger()
    qtbot.keyClicks(bar.field, "note")
    _focus(qtbot, output)
    output.moveCursor(output.textCursor().MoveOperation.Start)
    output.collapse_all()
    _action(window, "Find Next").trigger()
    start = bar.current_match()[0]
    assert output.document().findBlock(start).isVisible()


def test_matches_in_folds_are_not_painted_until_shown(qtbot) -> None:
    window, output, bar = _output_with(qtbot)
    _action(window, "Find…").trigger()
    qtbot.keyClicks(bar.field, "price")
    total = bar.match_count()
    assert total == 5
    # Keep the bar open. Escape would close it and clear every match, which
    # would make an empty result say nothing about folds.
    _focus(qtbot, output)
    assert bar.isVisible()
    output.collapse_all()
    shown = output._find_selections()
    # Only the root's "price": "list price" line stays shown: 2 of 5 matches.
    assert len(shown) == 2
    assert all(selection.cursor.block().isVisible() for selection in shown)
    output.expand_all()
    assert len(output._find_selections()) == total


# Edits and large documents ---------------------------------------------------


def test_editing_searches_again_without_moving_the_caret(qtbot) -> None:
    window, editor, bar = _input_with(qtbot, "a a")
    _action(window, "Find…").trigger()
    qtbot.keyClicks(bar.field, "a")
    _focus(qtbot, editor)
    editor.moveCursor(editor.textCursor().MoveOperation.End)
    qtbot.keyClicks(editor, " a")
    qtbot.waitUntil(lambda: bar.match_count() == 3, timeout=2000)
    assert editor.textCursor().position() == len("a a a")
    assert editor.toPlainText() == "a a a"


def test_matches_past_a_shorter_document_are_not_drawn(qtbot) -> None:
    window, editor, bar = _input_with(qtbot, "abc abc abc")
    _action(window, "Find…").trigger()
    qtbot.keyClicks(bar.field, "abc")
    editor.setPlainText("abc")
    assert len(editor.extraSelections()) <= 2
    qtbot.waitUntil(lambda: bar.match_count() == 1, timeout=2000)


def test_large_documents_wait_before_searching(qtbot, monkeypatch) -> None:
    monkeypatch.setattr("galley.app.find_bar.JSON_BUSY_BYTES", 10)
    monkeypatch.setattr("galley.app.find_bar.YAML_BUSY_BYTES", 10)
    window, _editor_, bar = _input_with(qtbot, "abc " * 10)
    _action(window, "Find…").trigger()
    assert bar.is_large()
    qtbot.keyClicks(bar.field, "abc")
    # Typing only restarts the wait; nothing is searched yet.
    assert bar.match_count() == 0
    assert bar._timer.isActive()
    assert bar._timer.interval() == FIND_DELAY_MS
    qtbot.waitUntil(lambda: bar.match_count() == 10, timeout=2000)
    assert bar.count_label_text() == "1 of 10"


def test_small_documents_search_on_every_keystroke(qtbot) -> None:
    window, _editor_, bar = _input_with(qtbot, "abc " * 10)
    _action(window, "Find…").trigger()
    assert not bar.is_large()
    qtbot.keyClicks(bar.field, "ab")
    assert bar.match_count() == 10


# Colors and contrast ---------------------------------------------------------


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_match_colors_and_bar_contrast(qtbot, install_scheme, scheme) -> None:
    palette = install_scheme(scheme)
    assert is_dark(palette) == (scheme == "dark")
    window, output, bar = _output_with(qtbot)
    _action(window, "Find…").trigger()
    qtbot.keyClicks(bar.field, "price")
    editor_palette = output.palette()
    base = editor_palette.color(_R.Base)
    colors = find_match_colors(editor_palette)
    assert colors.current == mix(base, QColor(ACCENT), FIND_CURRENT_OPACITY)
    for fill in (colors.current, colors.other):
        assert contrast_ratio(colors.ink, fill) >= TEXT_MIN_CONTRAST
    assert colors.current != colors.other
    # The current match is not told apart by color alone.
    current = [s for s in output.extraSelections() if s.format.fontUnderline()]
    assert len(current) == 1
    assert current[0].cursor.selectionStart() == bar.current_match()[0]
    assert current[0].format.background().color() == colors.current
    assert current[0].format.foreground().color() == colors.ink

    # The painted fill under the current match.
    start = bar.current_match()[0]
    cursor = output.textCursor()
    cursor.setPosition(start)
    rect = output.cursorRect(cursor)
    image = output.viewport().grab().toImage()
    ratio = output.viewport().devicePixelRatioF()
    point = QPoint(int((rect.left() + 3) * ratio), int((rect.top() + 2) * ratio))
    assert image.pixelColor(point).name() == colors.current.name()

    bar_palette = bar.palette()
    window_bg = bar_palette.color(_R.Window)
    field_bg = bar.field.palette().color(_R.Base)
    text = composite(bar_palette.color(_R.WindowText), window_bg)
    muted = composite(bar_palette.color(_R.PlaceholderText), window_bg)
    assert contrast_ratio(text, window_bg) >= TEXT_MIN_CONTRAST
    assert contrast_ratio(muted, window_bg) >= TEXT_MIN_CONTRAST
    border = error_color(bar.field.palette())
    assert contrast_ratio(border, window_bg) >= EDGE_CONTRAST
    assert contrast_ratio(border, field_bg) >= EDGE_CONTRAST
    # Icon ink on the bar.
    assert contrast_ratio(muted, window_bg) >= EDGE_CONTRAST


# Accessibility ------------------------------------------------------------


def test_every_control_has_a_name_and_tab_reaches_it(qtbot) -> None:
    window, _output_, bar = _output_with(qtbot)
    _action(window, "Find…").trigger()
    expected = [
        (bar.field, "Find"),
        (bar.regex_button, "Use regular expression"),
        (bar.whole_word_button, "Match whole word"),
        (bar.previous_button, "Previous match"),
        (bar.next_button, "Next match"),
        (bar.match_case, "Match case"),
        (bar.keys_only, "Keys only"),
        (bar.close_button, "Close find bar"),
    ]
    for widget, name in expected:
        assert widget.accessibleName() == name
        assert widget.focusPolicy() & Qt.FocusPolicy.TabFocus
    native = QKeySequence(Qt.Key.Key_Return).toString(
        QKeySequence.SequenceFormat.NativeText
    )
    assert bar.next_button.toolTip() == f"Next match ({native})"
    assert bar.close_button.toolTip() == "Close find bar (Esc)"
    qtbot.waitUntil(bar.field.hasFocus, timeout=2000)
    reached = [QApplication.focusWidget()]
    for _ in range(len(expected) - 1):
        qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab)
        reached.append(QApplication.focusWidget())
    assert reached == [widget for widget, _name in expected]


def test_the_count_is_announced(qtbot) -> None:
    window, _editor_, bar = _input_with(qtbot, "n n n")
    _action(window, "Find…").trigger()
    heard: list[str] = []
    bar.count_changed.connect(heard.append)
    qtbot.keyClicks(bar.field, "n")
    qtbot.keyClick(bar.field, Qt.Key.Key_Return)
    qtbot.keyClicks(bar.field, "x")
    assert heard == ["1 of 3", "2 of 3", NO_RESULTS]
    assert isinstance(bar.count_label, QLabel)
    assert bar.count_label.accessibleName() == ""
    assert bar.count_label.accessibleDescription() == "Match count"


def test_a_narrow_pane_moves_the_options_to_a_second_row(qtbot) -> None:
    # Hosted in a layout, as in a pane: a top-level bar would be held at its
    # layout's minimum width, which depends on the platform's fonts.
    host = QWidget()
    qtbot.addWidget(host)
    editor = CodeEditor()
    bar = FindBar(editor, "narrow", keys_option=True)
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(bar)
    layout.addWidget(editor)
    host.resize(900, 300)
    host.show()
    qtbot.waitExposed(host)
    bar.open()

    def top(widget) -> int:
        return widget.mapTo(bar, QPoint(0, 0)).y()

    def below_field(widget) -> bool:
        return top(widget) >= top(bar.field) + bar.field.height()

    qtbot.waitUntil(lambda: not below_field(bar.match_case))
    host.resize(260, 300)
    qtbot.waitUntil(lambda: bar.width() == 260)
    qtbot.waitUntil(lambda: below_field(bar.match_case))
    assert top(bar.keys_only) == top(bar.match_case)
    host.resize(900, 300)
    qtbot.waitUntil(lambda: not below_field(bar.match_case))


def test_selections_are_never_set_inside_another_set(qtbot, monkeypatch) -> None:
    """Laying out blocks inside setExtraSelections can scroll; no nested call.

    A nested call freed the selection list Qt was walking and crashed CI.
    """
    window, output, bar = _output_with(qtbot, _pretty_document(400_000))
    depth = [0]
    nested = []
    original = CodeEditor.setExtraSelections

    def counting(self, selections) -> None:
        depth[0] += 1
        if depth[0] > 1:
            nested.append(depth[0])
        try:
            original(self, selections)
        finally:
            depth[0] -= 1

    monkeypatch.setattr(CodeEditor, "setExtraSelections", counting)
    _action(window, "Find…").trigger()
    qtbot.keyClicks(bar.field, "e")
    bar.keys_only.click()
    scroll = output.verticalScrollBar()
    for _ in range(20):
        scroll.setValue(scroll.value() + 200)
        QApplication.processEvents()
    bar.find_next()
    QApplication.processEvents()
    assert nested == []
    assert output.extraSelections()


# Release check ------------------------------------------------------------


def test_find_and_find_and_replace_are_built(qtbot) -> None:
    window = _window(qtbot)
    assert "Find" not in UNBUILT_ACTIONS
    message = release_failure()
    assert message is None or "Find" not in message
    assert not_yet_failure() is None
    find = _action(window, "Find…")
    assert find.isEnabled()
    assert find.toolTip() != "Coming soon"


def test_find_field_uses_the_code_font() -> None:
    assert FindField().font().family() == code_font().family()


# Performance ----------------------------------------------------------------

# Per operation, under ``-m perf`` (one warmup, median of five, failing at
# 3x in the PR gate). Local medians on Apple silicon, offscreen: typing "e"
# in 2 MB of pretty JSON (about 79,000 matches) is about 30 ms, and on one
# 1.6 MB line about 20 ms; Find next on that line about 40 ms. A selection
# for every match took seconds, so the budget still catches that.
_BUDGET_S = 0.5


def _pretty_document(target: int) -> str:
    """About ``target`` characters of pretty JSON, non-ASCII and emoji included."""
    rng = random.Random(52)

    def item(index: int) -> dict[str, object]:
        return {
            "id": index,
            "title": f"item-{index}",
            "price": rng.randrange(1000),
            "tags": [rng.choice(("alpha", "beta", "gamma")) for _ in range(3)],
            "note": "The quick brown fox jumps over the lazy dog; café 😀",
        }

    def dump(items: list[dict[str, object]]) -> str:
        return json.dumps({"items": items}, indent=2, ensure_ascii=False)

    each = len(dump([item(0), item(1)])) - len(dump([item(0)]))
    return dump([item(i) for i in range(target // each)])


def _one_line_document(target: int) -> str:
    """About ``target`` characters of compact JSON on one line."""
    rng = random.Random(91)

    def item(index: int) -> dict[str, object]:
        return {
            "id": f"{index:08d}",
            "name": f"item-{index:08d}",
            "n": rng.randrange(9),
        }

    each = len(json.dumps([item(0), item(1)])) // 2
    return json.dumps({"items": [item(i) for i in range(target // each)]})


_LARGE = [
    pytest.param(_pretty_document, 2_000_000, id="2 MB"),
    pytest.param(_one_line_document, 1_600_000, id="1.6 MB line"),
]


def _large_output(qtbot, build, target: int):
    text = build(target)
    assert abs(len(text) - target) < target // 20
    window, output, bar = _output_with(qtbot, text)
    _action(window, "Find…").trigger()
    return output, bar


def _type_query(bar: FindBar, query: str = "e") -> None:
    bar.field.clear()
    bar.field.setText(query)
    QApplication.processEvents()


def _toggle_keys_only(bar: FindBar) -> None:
    bar.keys_only.setChecked(False)
    bar.keys_only.click()
    QApplication.processEvents()


def _scroll(output: CodeEditor) -> None:
    scroll = output.verticalScrollBar()
    scroll.setValue(scroll.value() + 200)
    QApplication.processEvents()


@pytest.mark.parametrize(("build", "target"), _LARGE)
def test_large_documents_draw_only_the_matches_on_screen(qtbot, build, target) -> None:
    output, bar = _large_output(qtbot, build, target)
    _type_query(bar)
    everywhere = bar.match_count()
    assert everywhere > 10_000
    bar.find_next()
    assert bar.current_index() == 1
    _toggle_keys_only(bar)
    assert 0 < bar.match_count() < everywhere
    _scroll(output)
    # Only what is on screen becomes an extra selection.
    assert len(output.extraSelections()) < 2_000


@pytest.mark.perf
@pytest.mark.parametrize(("build", "target"), _LARGE)
def test_find_stays_responsive_on_large_documents(
    qtbot, perf_budget, build, target
) -> None:
    output, bar = _large_output(qtbot, build, target)
    label = f"{len(output.toPlainText()):,}-character JSON"
    if "\n" not in output.toPlainText():
        label += " on one line"
    perf_budget.within(
        _BUDGET_S, lambda: _type_query(bar), fixture=f"{label}: type 'e'"
    )
    perf_budget.within(_BUDGET_S, bar.find_next, fixture=f"{label}: Find next")
    perf_budget.within(
        _BUDGET_S, lambda: _toggle_keys_only(bar), fixture=f"{label}: Keys only on"
    )
    perf_budget.within(
        _BUDGET_S, lambda: _scroll(output), fixture=f"{label}: scroll 200 px"
    )
