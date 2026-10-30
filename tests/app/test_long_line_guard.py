"""Very long lines switch off whitespace dots and colors until they go (#91).

QPlainTextEdit lays out a whole block on every edit to it. On a one-line
1.6 MB JSON document, Show Whitespace made a keystroke take 10 s and the
highlighter added up to half a second more. The editor keeps the user's
choices and applies them only while every line is under the gates in
``editor.py``. Fixtures are built in memory: nothing large is in the tree.
"""

import json
import random

import pytest
from PySide6.QtGui import QAction, QTextCursor, QTextOption
from tests.text_compare import assert_same_text

import galley.app.editor as editor_module
from galley.app.editor import (
    COLORS_LIMIT,
    COLORS_LONG_LINE_CHARS,
    WHITESPACE_LIMIT,
    WHITESPACE_LONG_LINE_CHARS,
    CodeEditor,
)
from galley.app.main import create_main_window

# Past both gates, well under the byte gate. The budget test uses the
# measured 1.6 MB document.
_LONG_CHARS = COLORS_LONG_LINE_CHARS + 50_000
_WHITESPACE_ONLY_CHARS = WHITESPACE_LONG_LINE_CHARS + 10_000
_BUDGET_CHARS = 1_600_000
# A keystroke on the 1.6 MB line with both options wanted. Local runs are
# 0.4-0.6 s. CI runners went over the first 1.5 s budget: Windows measured
# 1.78-1.81 s at 5d6e261 (#94) and macOS 1.55-1.92 s at 595f435 (#84), so
# Chief set 3 s on both. Without the guard a keystroke takes about 10 s, so
# 3 s still catches a regression.
_KEYSTROKE_BUDGET_S = 3.0
_SHOW_TABS_AND_SPACES = QTextOption.Flag.ShowTabsAndSpaces


def _one_line(chars: int) -> str:
    """Compact JSON of exactly ``chars`` characters on one line."""
    rng = random.Random(91)
    item_count = (chars - 40) // 80
    items = [
        {
            "id": f"{index:08d}",
            "name": f"item-{index:08d}",
            "ok": rng.random() > 0.5,
            "n": f"{rng.randrange(1_000_000):06d}",
        }
        for index in range(item_count)
    ]
    text = json.dumps({"items": items, "pad": ""})
    gap = chars - len(text)
    assert gap >= 0, (chars, len(text))
    text = json.dumps({"items": items, "pad": "x" * gap})
    assert len(text) == chars and "\n" not in text
    return text


def _pretty(text: str) -> str:
    return json.dumps(json.loads(text), indent=2) + "\n"


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


def _shows_whitespace(editor: CodeEditor) -> bool:
    flags = editor.document().defaultTextOption().flags()
    return bool(flags & _SHOW_TABS_AND_SPACES)


def _highlighter_attached(editor: CodeEditor) -> bool:
    return editor._highlighter.document() is editor.document()


def _first_block_is_colored(editor: CodeEditor) -> bool:
    block = editor.document().firstBlock()
    return block.isValid() and bool(block.layout().formats())


def _true_longest(editor: CodeEditor) -> int:
    return max(len(line) for line in editor.toPlainText().split("\n"))


def _assert_full_display(editor: CodeEditor) -> None:
    assert _shows_whitespace(editor)
    assert editor.whitespace_shown()
    assert _highlighter_attached(editor)
    assert editor.syntax_colors_enabled()
    assert editor.display_limits() == ()
    assert not editor.display_limited()


def _assert_both_limited(editor: CodeEditor) -> None:
    assert not _shows_whitespace(editor)
    assert not editor.whitespace_shown()
    assert editor._highlighter.document() is None
    assert not editor.syntax_colors_enabled()
    assert editor.syntax_colors_wanted()
    assert editor.whitespace_wanted()
    assert editor.display_limits() == (WHITESPACE_LIMIT, COLORS_LIMIT)
    assert editor.display_limited()


def test_long_line_hides_dots_and_colors_until_the_text_is_pretty(qtbot) -> None:
    """One giant line: no dots, highlighter detached, View action still checked."""
    window = _show(qtbot)
    editor = window.findChild(CodeEditor, "inputEditor")
    output = window.findChild(CodeEditor, "outputEditor")
    assert isinstance(editor, CodeEditor) and isinstance(output, CodeEditor)
    show_whitespace = _action(window, "Show Whitespace")
    assert not show_whitespace.isChecked()
    show_whitespace.trigger()
    assert show_whitespace.isChecked()
    _assert_full_display(editor)
    _assert_full_display(output)

    one_line = _one_line(_LONG_CHARS)
    editor.setPlainText(one_line)

    assert show_whitespace.isChecked()
    _assert_both_limited(editor)
    assert editor.longest_line_chars() == _LONG_CHARS
    # The other editor is its own document and keeps both.
    _assert_full_display(output)

    editor.setPlainText(_pretty(one_line))

    assert show_whitespace.isChecked()
    _assert_full_display(editor)
    assert editor.longest_line_chars() == _true_longest(editor)
    qtbot.waitUntil(lambda: _first_block_is_colored(editor))

    # Turning the View option off while a long line is shown is honored
    # once the line is gone, and the limit is not reported meanwhile.
    editor.setPlainText(one_line)
    show_whitespace.trigger()
    assert not show_whitespace.isChecked()
    assert editor.display_limits() == (COLORS_LIMIT,)
    editor.setPlainText("{}\n")
    assert not _shows_whitespace(editor)
    assert _highlighter_attached(editor)
    assert editor.display_limits() == ()


def test_deleting_the_long_line_brings_dots_and_colors_back(qtbot) -> None:
    editor = CodeEditor()
    qtbot.addWidget(editor)
    editor.set_whitespace_wanted(True)
    pretty = _pretty(_one_line(2_000))
    one_line = _one_line(_LONG_CHARS)
    editor.setPlainText(pretty + one_line + "\n" + pretty)
    _assert_both_limited(editor)
    long_block = editor.document().findBlockByNumber(pretty.count("\n"))
    assert_same_text(long_block.text(), one_line)

    # Select the line's text and delete it. The block stays, empty.
    cursor = QTextCursor(long_block)
    cursor.movePosition(
        QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor
    )
    cursor.removeSelectedText()

    _assert_full_display(editor)
    assert editor.longest_line_chars() == _true_longest(editor)

    # Paste it back, then delete it with its newline, which merges blocks.
    cursor.insertText(one_line)
    _assert_both_limited(editor)
    cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
    cursor.movePosition(
        QTextCursor.MoveOperation.NextBlock, QTextCursor.MoveMode.KeepAnchor
    )
    assert_same_text(cursor.selectedText().rstrip(" "), one_line)
    cursor.removeSelectedText()

    _assert_full_display(editor)
    assert_same_text(editor.toPlainText(), pretty + pretty)
    assert editor.longest_line_chars() == _true_longest(editor)
    qtbot.waitUntil(lambda: _first_block_is_colored(editor))


def test_whitespace_gate_comes_first_and_colors_stay(qtbot) -> None:
    editor = CodeEditor()
    qtbot.addWidget(editor)
    editor.set_whitespace_wanted(True)
    editor.setPlainText(_one_line(_WHITESPACE_ONLY_CHARS))

    assert not _shows_whitespace(editor)
    assert _highlighter_attached(editor)
    assert editor.display_limits() == (WHITESPACE_LIMIT,)
    assert editor.display_limited()


def test_options_the_user_turned_off_are_not_limits(qtbot) -> None:
    editor = CodeEditor()
    qtbot.addWidget(editor)
    editor.setPlainText(_one_line(_LONG_CHARS))
    assert editor.display_limits() == (COLORS_LIMIT,)

    editor.set_syntax_colors_wanted(False)
    assert editor.display_limits() == ()
    assert not editor.display_limited()
    assert not editor.syntax_colors_enabled()

    editor.set_syntax_colors_wanted(True)
    assert editor.display_limits() == (COLORS_LIMIT,)
    assert not editor.syntax_colors_enabled()


def test_set_syntax_colors_enabled_records_intent(qtbot) -> None:
    """Existing callers set intent. The gate still decides the highlighter."""
    editor = CodeEditor()
    qtbot.addWidget(editor)
    editor.set_syntax_colors_enabled(False)
    editor.setPlainText(_one_line(_LONG_CHARS))
    assert not editor.syntax_colors_wanted()
    assert not editor.syntax_colors_enabled()

    editor.set_syntax_colors_enabled(True)
    assert editor.syntax_colors_wanted()
    assert not editor.syntax_colors_enabled()

    editor.setPlainText("{}\n")
    assert editor.syntax_colors_enabled()


def test_documents_over_the_busy_size_turn_colors_off(qtbot, monkeypatch) -> None:
    """The byte gate uses the character count, which is free to read."""
    monkeypatch.setattr(editor_module, "JSON_BUSY_BYTES", 2_500)
    editor = CodeEditor()
    qtbot.addWidget(editor)
    editor.set_whitespace_wanted(True)
    editor.setPlainText(_pretty(_one_line(2_000)))
    assert len(editor.toPlainText()) > 2_500
    assert _shows_whitespace(editor)
    assert editor.display_limits() == (COLORS_LIMIT,)
    assert editor._highlighter.document() is None

    editor.setPlainText("{}\n")
    _assert_full_display(editor)


def test_a_keystroke_reads_only_its_block(qtbot, monkeypatch) -> None:
    """No full-document walk for an insert, a newline, or a backspace."""
    rescans = 0
    original = CodeEditor._rescan_longest_line

    def counted(self: CodeEditor) -> None:
        nonlocal rescans
        rescans += 1
        original(self)

    monkeypatch.setattr(CodeEditor, "_rescan_longest_line", counted)
    editor = CodeEditor()
    qtbot.addWidget(editor)
    editor.setPlainText(_pretty(_one_line(20_000)))
    # setPlainText clears, then sets: two whole-document changes.
    assert rescans == 2
    longest = editor.longest_line_chars()
    assert longest == _true_longest(editor)

    # Type at the end of the longest line, split it, and join it again.
    document = editor.document()
    block = document.firstBlock()
    while block.isValid() and block.length() - 1 != longest:
        block = block.next()
    assert block.isValid()
    cursor = QTextCursor(block)
    cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
    cursor.insertText("!")
    assert editor.longest_line_chars() == longest + 1
    cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
    cursor.movePosition(QTextCursor.MoveOperation.Right, n=5)
    cursor.insertText("\n")
    assert editor.longest_line_chars() >= _true_longest(editor)
    cursor.deletePreviousChar()
    assert editor.longest_line_chars() == longest + 1
    cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
    cursor.deletePreviousChar()
    assert editor.longest_line_chars() >= longest
    assert rescans == 2


def test_random_edits_keep_the_gates_right(qtbot, monkeypatch) -> None:
    """The tracked length may be an upper bound, but the gates never lie."""
    monkeypatch.setattr(editor_module, "WHITESPACE_LONG_LINE_CHARS", 12)
    monkeypatch.setattr(editor_module, "COLORS_LONG_LINE_CHARS", 24)
    editor = CodeEditor()
    qtbot.addWidget(editor)
    editor.set_whitespace_wanted(True)
    rng = random.Random(1)
    alphabet = "ab \n"
    editor.setPlainText("".join(rng.choice(alphabet) for _ in range(60)))
    for _ in range(400):
        document = editor.document()
        cursor = QTextCursor(document)
        length = document.characterCount() - 1
        cursor.setPosition(rng.randrange(length + 1))
        move = rng.randrange(4)
        if move == 0:
            cursor.insertText(
                "".join(rng.choice(alphabet) for _ in range(rng.randrange(1, 30)))
            )
        elif move == 1 and cursor.position() < length:
            cursor.movePosition(
                QTextCursor.MoveOperation.Right,
                QTextCursor.MoveMode.KeepAnchor,
                rng.randrange(1, 40),
            )
            cursor.removeSelectedText()
        elif move == 2 and cursor.position() > 0:
            cursor.deletePreviousChar()
        else:
            cursor.insertText("\n")
        true_longest = _true_longest(editor)
        tracked = editor.longest_line_chars()
        assert tracked >= true_longest
        assert (tracked > 12) == (true_longest > 12)
        assert (tracked > 24) == (true_longest > 24)
        assert _shows_whitespace(editor) == (true_longest <= 12)
        assert editor.syntax_colors_enabled() == (true_longest <= 24)
        assert editor.display_limited() == (true_longest > 12)


def _long_line_with_both_wanted(qtbot, qapp, text: str) -> CodeEditor:
    """The 1.6 MB line, both options wanted, the cursor in the middle."""
    editor = CodeEditor()
    qtbot.addWidget(editor)
    editor.resize(800, 600)
    editor.show()
    qtbot.waitExposed(editor)
    editor.set_whitespace_wanted(True)
    editor.set_syntax_colors_wanted(True)
    editor.set_syntax_mode("JSON")
    editor.setPlainText(text)
    qapp.processEvents()
    cursor = editor.textCursor()
    cursor.setPosition(len(text) // 2)
    editor.setTextCursor(cursor)
    return editor


def _type_a_space(editor: CodeEditor, qapp) -> None:
    editor.insertPlainText(" ")
    qapp.processEvents()


def test_a_keystroke_on_a_long_line_keeps_both_limited(qtbot, qapp) -> None:
    editor = _long_line_with_both_wanted(qtbot, qapp, _one_line(_BUDGET_CHARS))
    _assert_both_limited(editor)
    _type_a_space(editor, qapp)
    _assert_both_limited(editor)


@pytest.mark.perf
def test_a_keystroke_on_a_long_line_stays_under_budget(
    qtbot, qapp, perf_budget
) -> None:
    """1.6 MB on one line, both options wanted: a keystroke takes under 3 s."""
    editor = _long_line_with_both_wanted(qtbot, qapp, _one_line(_BUDGET_CHARS))
    perf_budget.within(
        _KEYSTROKE_BUDGET_S,
        lambda: _type_a_space(editor, qapp),
        fixture="1.6 MB JSON on one line, whitespace and colors wanted",
    )
