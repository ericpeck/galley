"""Regex and Whole word on both find bars (release check, Chief's brief)."""

import io
import multiprocessing
import sys
import time
from contextlib import redirect_stdout

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAccessible, QAction, QKeySequence, QPalette, QTextCursor
from PySide6.QtWidgets import QApplication, QPushButton
from tests.core.order_lines import REPLACE_BYTES, order_lines
from tests.perf import time_runs

from galley.app.actions import _ACTION_ATTRS, UNBUILT_ACTIONS, main
from galley.app.editor import CodeEditor
from galley.app.find_bar import (
    FIND_DELAY_MS,
    INVALID_PATTERN,
    INVALID_REPLACEMENT,
    MISSING_GROUP,
    SEARCH_TIMED_OUT,
    SEARCH_TIMED_OUT_SPOKEN,
    FindBar,
    pattern_error_text,
    replaced_text,
)
from galley.app.main import create_main_window
from galley.app.regex_session import SEARCH_LIMIT_MS, RegexSession
from galley.app.shortcuts import TOGGLE_REGEX, TOGGLE_WHOLE_WORD
from galley.app.style import find_toggle_style, options_link_style
from galley.app.theme import composite, contrast_ratio, segment_colors

_R = QPalette.ColorRole
_PORTABLE = QKeySequence.SequenceFormat.PortableText
_NATIVE = QKeySequence.SequenceFormat.NativeText
_WAIT_MS = 15_000
# Most tests stop a runaway search this soon; one uses the real 2 s.
_SHORT_LIMIT_MS = 300
_EMOJI = "\U0001f600"
_RUNAWAY_TEXT = "a" * 30 + "b"
_RUNAWAY = "(a+)+$"
_ORDERS = "qty=1 qty=2\nQTY=3 qtys=4\n"
_COMING_SOON_OUTPUT = (
    'Release blocked: these actions still show "Coming soon": '
    "Open Right Document…, Link Scrolling.\n"
    "Build them or remove them from UNBUILT_ACTIONS in actions.py "
    "before tagging.\n"
)


def _window(qtbot, limit_ms: int = _SHORT_LIMIT_MS):
    window = create_main_window()
    qtbot.addWidget(window)
    # Wide, so the Windows runner's font shows every label.
    window.resize(1600, 900)
    window.show()
    qtbot.waitExposed(window)
    window._regex_session.limit_ms = limit_ms
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


def _input_with(qtbot, text: str, limit_ms: int = _SHORT_LIMIT_MS):
    window = _window(qtbot, limit_ms)
    editor = _editor(window, "input")
    editor.setPlainText(text)
    _focus(qtbot, editor)
    return window, editor, _bar(window, "input")


def _output_with(qtbot, text: str):
    window = _window(qtbot)
    output = _editor(window, "output")
    output.set_syntax_mode("JSON")
    output.setPlainText(text)
    _focus(qtbot, output)
    return window, output, _bar(window, "output")


def _settle(qtbot, bar: FindBar) -> None:
    qtbot.waitUntil(lambda: not bar.is_searching(), timeout=_WAIT_MS)


def _turn_on(qtbot, window, button: QPushButton) -> None:
    """Turn a toggle on and wait for the find worker to come up."""
    button.setChecked(True)
    qtbot.waitUntil(window._regex_session.is_ready, timeout=_WAIT_MS)


def _find(
    qtbot,
    window,
    bar: FindBar,
    query: str,
    *,
    regex: bool = True,
    match_case: bool = True,
) -> None:
    """Open ``bar`` with Regex (or Whole word) on and search for ``query``."""
    bar.match_case.setChecked(match_case)
    bar.open(fill_from_selection=False)
    _turn_on(qtbot, window, bar.regex_button if regex else bar.whole_word_button)
    bar.field.setText(query)
    _settle(qtbot, bar)


def _open_replace(qtbot, window, query: str, replacement: str) -> FindBar:
    bar = _bar(window, "input")
    _action(window, "Find and Replace…").trigger()
    qtbot.waitUntil(bar.field.hasFocus, timeout=2000)
    bar.match_case.setChecked(True)
    _turn_on(qtbot, window, bar.regex_button)
    qtbot.keyClicks(bar.field, query)
    bar.replace_field.setText(replacement)
    _settle(qtbot, bar)
    return bar


def _matched(editor: CodeEditor, bar: FindBar) -> list[str]:
    """The text of each highlighted match, read through UTF-16 offsets."""
    units = editor.toPlainText().encode("utf-16-le")
    return [
        units[start * 2 : end * 2].decode("utf-16-le")
        for start, end in editor.find_matches()
    ]


def _press(qtbot, widget, sequence: QKeySequence) -> None:
    combo = sequence[0]
    qtbot.keyClick(widget, combo.key(), combo.keyboardModifiers())


def _no_dialog() -> bool:
    return QApplication.activeModalWidget() is None and (
        QApplication.activePopupWidget() is None
    )


# The toggles -------------------------------------------------------------------


@pytest.mark.parametrize("name", ["input", "output"])
def test_both_bars_have_the_toggles_right_of_the_find_field(qtbot, name) -> None:
    window = _window(qtbot)
    bar = _bar(window, name)
    bar.open()
    for button, text, accessible in (
        (bar.regex_button, "Regex", "Use regular expression"),
        (bar.whole_word_button, "Whole word", "Match whole word"),
    ):
        assert isinstance(button, QPushButton)
        assert button.text() == text
        assert button.icon().isNull()
        assert button.isCheckable()
        assert not button.isChecked()
        assert button.accessibleName() == accessible
        assert button.isVisible()
    field_right = bar.field.mapTo(bar, bar.field.rect().topRight()).x()
    regex_left = bar.regex_button.mapTo(bar, bar.regex_button.rect().topLeft()).x()
    word_left = bar.whole_word_button.mapTo(bar, bar.whole_word_button.rect().topLeft())
    count_left = bar.count_label.mapTo(bar, bar.count_label.rect().topLeft()).x()
    assert field_right < regex_left < word_left.x() < count_left


def test_the_tooltips_name_the_platform_shortcut(qtbot) -> None:
    window = _window(qtbot)
    for name in ("input", "output"):
        bar = _bar(window, name)
        if sys.platform == "darwin":
            assert bar.regex_button.toolTip() == "Use regular expression (⌥⌘R)"
            assert bar.whole_word_button.toolTip() == "Match whole word (⌥⌘W)"
        else:
            assert bar.regex_button.toolTip() == "Use regular expression (Alt+R)"
            assert bar.whole_word_button.toolTip() == "Match whole word (Alt+W)"


def test_the_shortcuts_are_set_by_platform() -> None:
    if sys.platform == "darwin":
        assert TOGGLE_REGEX.toString(_PORTABLE) == "Ctrl+Alt+R"
        assert TOGGLE_WHOLE_WORD.toString(_PORTABLE) == "Ctrl+Alt+W"
        assert TOGGLE_REGEX.toString(_NATIVE) == "⌥⌘R"
    else:
        assert TOGGLE_REGEX.toString(_PORTABLE) == "Alt+R"
        assert TOGGLE_WHOLE_WORD.toString(_PORTABLE) == "Alt+W"
        for sequence in (TOGGLE_REGEX, TOGGLE_WHOLE_WORD):
            # Ctrl+Alt is AltGr on Windows.
            modifiers = sequence[0].keyboardModifiers()
            assert not modifiers & Qt.KeyboardModifier.ControlModifier


def test_the_toggles_announce_on_and_off(qtbot) -> None:
    window = _window(qtbot)
    bar = _bar(window, "input")
    bar.open()
    for button in (bar.regex_button, bar.whole_word_button):
        interface = QAccessible.queryAccessibleInterface(button)
        assert interface is not None
        assert interface.role() == QAccessible.Role.CheckBox or (
            interface.state().checkable
        )
        assert interface.state().checkable
        assert not interface.state().checked
        button.setChecked(True)
        assert interface.state().checked
        button.setChecked(False)
        assert not interface.state().checked


def _checked_block(sheet: str) -> str:
    block = sheet[sheet.index(":checked") :]
    return block[block.index("{") + 1 : block.index("}")]


def test_a_toggle_that_is_on_looks_pressed_like_the_options_link(qtbot) -> None:
    window = _window(qtbot)
    for name in ("input", "output"):
        bar = _bar(window, name)
        for button in (bar.regex_button, bar.whole_word_button):
            assert button.styleSheet() == find_toggle_style()
    ours = _checked_block(find_toggle_style()).split()
    link = _checked_block(options_link_style()).split()
    for rule in (
        "background: palette(alternate-base);",
        "border: 1px solid palette(mid);",
    ):
        assert " ".join(rule.split()) in " ".join(ours)
        assert " ".join(rule.split()) in " ".join(link)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_toggle_contrast(qtbot, install_scheme, scheme) -> None:
    window = _window(qtbot)
    # After the window: creating it applies the saved appearance.
    palette = install_scheme(scheme)
    button = _bar(window, "input").regex_button
    # The scheme switch rebuilt the baked focus ring.
    assert button.styleSheet() == find_toggle_style()
    assert segment_colors(palette).focus.name() in button.styleSheet()
    window_bg = palette.color(_R.Window)
    pressed = composite(palette.color(_R.AlternateBase), window_bg)
    ink = composite(palette.color(_R.WindowText), window_bg)
    edge = composite(palette.color(_R.Mid), window_bg)
    ring = segment_colors(palette).focus
    assert contrast_ratio(ink, window_bg) >= 4.5
    assert contrast_ratio(ink, pressed) >= 4.5
    assert contrast_ratio(edge, pressed) >= 3.0
    assert contrast_ratio(ring, window_bg) >= 3.0
    assert contrast_ratio(ring, pressed) >= 3.0


# Shortcuts -------------------------------------------------------------------


@pytest.mark.parametrize("name", ["input", "output"])
def test_the_shortcuts_toggle_while_the_bar_has_focus(qtbot, name) -> None:
    window = _window(qtbot)
    bar = _bar(window, name)
    bar.open()
    qtbot.waitUntil(bar.field.hasFocus, timeout=2000)
    _press(qtbot, bar.field, TOGGLE_REGEX)
    assert bar.regex_button.isChecked()
    _press(qtbot, bar.field, TOGGLE_WHOLE_WORD)
    assert bar.whole_word_button.isChecked()
    _focus(qtbot, bar.match_case)
    _press(qtbot, bar.match_case, TOGGLE_REGEX)
    assert not bar.regex_button.isChecked()
    assert bar.field.text() == ""


def test_the_shortcuts_do_nothing_with_focus_in_an_editor(qtbot) -> None:
    window, editor, bar = _input_with(qtbot, "abc")
    _press(qtbot, editor, TOGGLE_REGEX)
    _press(qtbot, editor, TOGGLE_WHOLE_WORD)
    assert not bar.is_open()
    assert not bar.regex_button.isChecked()
    assert not bar.whole_word_button.isChecked()
    # An open bar without focus does not take them either.
    bar.open()
    _focus(qtbot, editor)
    _press(qtbot, editor, TOGGLE_REGEX)
    assert not bar.regex_button.isChecked()


def test_the_shortcuts_clash_with_no_action_or_menu(qtbot) -> None:
    window = _window(qtbot)
    for sequence in (TOGGLE_REGEX, TOGGLE_WHOLE_WORD):
        clashes = [
            item.text() or item.objectName()
            for item in window.findChildren(QAction)
            if any(
                shortcut.matches(sequence) != QKeySequence.SequenceMatch.NoMatch
                for shortcut in item.shortcuts()
            )
        ]
        assert clashes == [], sequence.toString(_PORTABLE)
    # No menu title has a mnemonic, so Alt+R and Alt+W open no menu.
    titles = [action.text() for action in window.menuBar().actions()]
    assert titles
    assert not any("&" in title.replace("&&", "") for title in titles)
    # Each bar owns its pair, for its own focus only.
    for name in ("input", "output"):
        shortcuts = _bar(window, name).toggle_shortcuts
        assert [shortcut.key() for shortcut in shortcuts] == [
            TOGGLE_REGEX,
            TOGGLE_WHOLE_WORD,
        ]
        for shortcut in shortcuts:
            assert shortcut.context() == Qt.ShortcutContext.WidgetWithChildrenShortcut


def test_tab_goes_find_regex_whole_word_then_replace(qtbot) -> None:
    window, _editor_, bar = _input_with(qtbot, _ORDERS)
    _action(window, "Find and Replace…").trigger()
    qtbot.keyClicks(bar.field, "zzz")
    bar.replace_field.setText("x")
    bar.field.setText("qty")
    expected = [
        bar.field,
        bar.regex_button,
        bar.whole_word_button,
        bar.replace_field,
        bar.replace_button,
        bar.replace_all_button,
    ]
    _focus(qtbot, bar.field)
    reached = [QApplication.focusWidget()]
    for _ in range(len(expected) - 1):
        qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab)
        reached.append(QApplication.focusWidget())
    assert reached == expected


# Shared state ----------------------------------------------------------------


def test_both_bars_share_the_toggles(qtbot) -> None:
    window = _window(qtbot)
    first, second = _bar(window, "input"), _bar(window, "output")
    first.regex_button.setChecked(True)
    assert second.regex_button.isChecked()
    second.whole_word_button.setChecked(True)
    assert first.whole_word_button.isChecked()
    second.regex_button.setChecked(False)
    assert not first.regex_button.isChecked()
    assert first.regex_session() is second.regex_session()
    assert first.regex_session() is window._regex_session


def test_the_toggles_start_off_in_a_new_window(qtbot) -> None:
    window = _window(qtbot)
    bar = _bar(window, "input")
    bar.regex_button.setChecked(True)
    bar.whole_word_button.setChecked(True)
    window.close()
    again = _bar(_window(qtbot), "input")
    assert not again.regex_button.isChecked()
    assert not again.whole_word_button.isChecked()


def test_turning_a_toggle_on_starts_the_worker_once(qtbot) -> None:
    window = _window(qtbot)
    session = window._regex_session
    assert session.process is None
    bar = _bar(window, "input")
    _turn_on(qtbot, window, bar.regex_button)
    process = session.process
    bar.whole_word_button.setChecked(True)
    bar.regex_button.setChecked(False)
    bar.regex_button.setChecked(True)
    assert session.process is process


# Searching -------------------------------------------------------------------


def test_a_regex_search_counts_and_highlights(qtbot) -> None:
    window, editor, bar = _input_with(qtbot, _ORDERS)
    _find(qtbot, window, bar, r"qty=\d")
    assert bar.match_count() == 2
    assert bar.count_label_text() == "1 of 2"
    assert _matched(editor, bar) == ["qty=1", "qty=2"]


def test_match_case_off_ignores_case(qtbot) -> None:
    window, editor, bar = _input_with(qtbot, _ORDERS)
    _find(qtbot, window, bar, r"qty=\d")
    bar.match_case.setChecked(False)
    _settle(qtbot, bar)
    assert _matched(editor, bar) == ["qty=1", "qty=2", "QTY=3"]
    bar.match_case.setChecked(True)
    _settle(qtbot, bar)
    assert bar.match_count() == 2


def test_whole_word_finds_only_whole_words(qtbot) -> None:
    window, editor, bar = _input_with(qtbot, _ORDERS)
    _find(qtbot, window, bar, "qty", regex=False)
    assert _matched(editor, bar) == ["qty", "qty"]
    bar.field.setText("qty.")
    _settle(qtbot, bar)
    # Regex off: the dot is literal.
    assert bar.match_count() == 0


def test_regex_and_whole_word_together(qtbot) -> None:
    window, editor, bar = _input_with(qtbot, _ORDERS)
    _find(qtbot, window, bar, r"qty\w*")
    assert _matched(editor, bar) == ["qty", "qty", "qtys"]
    bar.whole_word_button.setChecked(True)
    _settle(qtbot, bar)
    assert _matched(editor, bar) == ["qty", "qty", "qtys"]
    bar.field.setText("qt")
    _settle(qtbot, bar)
    assert bar.match_count() == 0


def test_astral_characters_before_and_inside_a_match(qtbot) -> None:
    text = f"{_EMOJI} a{_EMOJI}b {_EMOJI}{_EMOJI} a{_EMOJI}b"
    window, editor, bar = _input_with(qtbot, text)
    _find(qtbot, window, bar, "a.b")
    assert _matched(editor, bar) == [f"a{_EMOJI}b"] * 2
    assert editor.find_matches() == [(3, 7), (13, 17)]
    bar.find_next()
    cursor = editor.textCursor()
    assert cursor.position() == 13


def test_find_next_steps_one_code_point_over_empty_matches(qtbot) -> None:
    window, editor, bar = _input_with(qtbot, f"a{_EMOJI}b")
    _find(qtbot, window, bar, "x*")
    assert editor.find_matches() == [(0, 0), (1, 1), (3, 3), (4, 4)]
    seen = [bar.current_match()]
    for _ in range(4):
        qtbot.keyClick(bar.field, Qt.Key.Key_Return)
        seen.append(bar.current_match())
    assert seen == [(0, 0), (1, 1), (3, 3), (4, 4), (0, 0)]
    qtbot.keyClick(bar.field, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert bar.current_match() == (4, 4)


def test_find_next_reaches_every_match_that_shares_a_start(qtbot) -> None:
    window, _editor_, bar = _input_with(qtbot, "ab")
    _find(qtbot, window, bar, "|a")
    seen = [bar.current_match()]
    for _ in range(bar.match_count()):
        bar.find_next()
        seen.append(bar.current_match())
    assert seen == [(0, 0), (0, 1), (1, 1), (2, 2), (0, 0)]


def test_line_anchors_match_each_line(qtbot) -> None:
    window, _editor_, bar = _input_with(qtbot, _ORDERS)
    _find(qtbot, window, bar, "^q")
    assert bar.match_count() == 1
    bar.match_case.setChecked(False)
    _settle(qtbot, bar)
    assert bar.match_count() == 2


def test_the_count_and_replace_all_search_the_same_raw_text(qtbot) -> None:
    text = "a b a b a b"
    window, editor, _bar_ = _input_with(qtbot, text)
    bar = _open_replace(qtbot, window, "a b", "X")
    assert bar.match_count() == 2
    bar.replace_all()
    qtbot.waitUntil(
        lambda: bar.count_label_text() == replaced_text(2), timeout=_WAIT_MS
    )
    assert editor.document().toRawText() == "X a b X"


def test_typing_while_a_search_runs_shows_only_the_newest_result(qtbot) -> None:
    window, _editor_, bar = _input_with(qtbot, _ORDERS)
    _find(qtbot, window, bar, "q")
    heard: list[str] = []
    bar.count_changed.connect(heard.append)
    for query in ("qt", "qty", "qty="):
        bar.field.setText(query)
    _settle(qtbot, bar)
    assert bar.match_count() == 2
    assert heard[-1] == "1 of 2"


def test_a_stale_runaway_never_shows_timed_out_and_the_newest_result_wins(
    qtbot,
) -> None:
    window, editor, bar = _input_with(qtbot, _RUNAWAY_TEXT)
    _find(qtbot, window, bar, "a")
    heard: list[str] = []
    bar.count_changed.connect(heard.append)
    bar.field.setText(_RUNAWAY)
    bar.field.setText("a+b")
    _settle(qtbot, bar)
    assert SEARCH_TIMED_OUT_SPOKEN not in heard
    assert bar.count_label_text() == "1 of 1"
    assert editor.find_matches() == [(0, 31)]
    assert bar.field.error() == ""


# Invalid pattern ---------------------------------------------------------------


def test_an_invalid_pattern_says_so_without_a_dialog(qtbot) -> None:
    window, editor, bar = _input_with(qtbot, _ORDERS)
    _action(window, "Find and Replace…").trigger()
    _find(qtbot, window, bar, "qty")
    assert editor.find_matches()
    heard: list[str] = []
    bar.count_changed.connect(heard.append)
    _focus(qtbot, bar.field)
    qtbot.keyClicks(bar.field, "(")
    _settle(qtbot, bar)
    reason = pattern_error_text("Missing ), unterminated subpattern", 4)
    assert reason == "Col 4: Missing ), unterminated subpattern"
    assert bar.count_label_text() == INVALID_PATTERN
    assert bar.field.error() == reason
    assert bar.field.accessibleDescription() == reason
    assert bar.field.toolTip() == reason
    assert editor.find_matches() == []
    assert not bar.replace_button.isEnabled()
    assert not bar.replace_all_button.isEnabled()
    assert heard == [f"{INVALID_PATTERN}. {reason}"]
    assert bar.field.hasFocus()
    assert _no_dialog()
    # The next keystroke checks again.
    qtbot.keyClicks(bar.field, ")")
    _settle(qtbot, bar)
    assert bar.count_label_text() == "1 of 3"
    assert bar.field.error() == ""
    assert bar.field.toolTip() == ""
    assert bar.replace_all_button.isEnabled()


def test_an_invalid_pattern_ignores_document_edits_until_the_query_changes(
    qtbot,
) -> None:
    window, editor, bar = _input_with(qtbot, _ORDERS)
    _find(qtbot, window, bar, "[")
    assert bar.count_label_text() == INVALID_PATTERN
    editor.insertPlainText("more ")
    qtbot.wait(FIND_DELAY_MS * 2)
    assert not bar.is_searching()
    assert bar.count_label_text() == INVALID_PATTERN
    bar.regex_button.setChecked(False)
    bar.whole_word_button.setChecked(False)
    assert bar.count_label_text() == "No results"


# The limit ---------------------------------------------------------------------


def test_a_search_past_the_limit_says_so_and_is_not_tried_again(qtbot) -> None:
    window, editor, bar = _input_with(qtbot, _RUNAWAY_TEXT)
    _action(window, "Find and Replace…").trigger()
    bar.replace_field.setText("x")
    _find(qtbot, window, bar, "a+b")
    assert bar.match_count() == 1
    heard: list[str] = []
    bar.count_changed.connect(heard.append)
    bar.field.setText(_RUNAWAY)
    _settle(qtbot, bar)
    assert bar.count_label_text() == SEARCH_TIMED_OUT
    assert heard == [SEARCH_TIMED_OUT_SPOKEN]
    assert bar.field.error() == SEARCH_TIMED_OUT_SPOKEN
    assert editor.find_matches() == []
    assert not bar.replace_button.isEnabled()
    assert not bar.replace_all_button.isEnabled()
    assert _no_dialog()
    # No retry on its own: not after a wait, an edit, or Find Next.
    session = window._regex_session
    qtbot.wait(FIND_DELAY_MS * 2)
    editor.moveCursor(QTextCursor.MoveOperation.End)
    editor.insertPlainText("c")
    qtbot.wait(FIND_DELAY_MS * 2)
    bar.find_next()
    assert not bar.is_searching()
    assert not session.is_busy()
    assert bar.count_label_text() == SEARCH_TIMED_OUT
    # A toggle starts a fresh search.
    bar.whole_word_button.setChecked(True)
    assert bar.is_searching()
    _settle(qtbot, bar)
    bar.whole_word_button.setChecked(False)
    _settle(qtbot, bar)
    # So does editing the pattern.
    bar.field.setText("a+bc")
    _settle(qtbot, bar)
    assert bar.count_label_text() == "1 of 1"
    assert bar.field.error() == ""


def test_a_toggle_turned_off_on_a_closed_bar_lets_find_next_search(qtbot) -> None:
    window, editor, bar = _input_with(qtbot, _RUNAWAY_TEXT)
    _find(qtbot, window, bar, _RUNAWAY)
    assert bar.count_label_text() == SEARCH_TIMED_OUT
    bar.close_bar()
    bar.regex_button.setChecked(False)
    bar.field.setText("ab")
    _focus(qtbot, editor)
    bar.find_next()
    assert bar.is_open()
    assert bar.current_match() == (29, 31)


def test_the_real_limit_stops_a_runaway_search_and_a_new_worker_takes_over(
    qtbot,
) -> None:
    """The one test per OS that waits out the real 2 s limit."""
    window, editor, bar = _input_with(qtbot, _RUNAWAY_TEXT, limit_ms=SEARCH_LIMIT_MS)
    session = window._regex_session
    assert session.limit_ms == SEARCH_LIMIT_MS == 2000
    bar.open(fill_from_selection=False)
    _turn_on(qtbot, window, bar.regex_button)
    old = session.process
    assert old is not None
    ticks: list[float] = []
    ticker = QTimer()
    ticker.timeout.connect(lambda: ticks.append(time.monotonic()))
    ticker.start(50)
    started = time.monotonic()
    bar.field.setText(_RUNAWAY)
    qtbot.waitUntil(
        lambda: bar.count_label_text() == SEARCH_TIMED_OUT, timeout=_WAIT_MS
    )
    elapsed = time.monotonic() - started
    ticker.stop()
    assert elapsed <= 2.5, elapsed
    # The UI kept running and the editor is not locked.
    assert len(ticks) >= 20
    assert not editor.isReadOnly()
    assert bar.field.hasFocus()
    # The old worker has fully exited and a new one is up.
    qtbot.waitUntil(lambda: old.exitcode is not None, timeout=_WAIT_MS)
    assert not old.is_alive()
    qtbot.waitUntil(session.is_ready, timeout=_WAIT_MS)
    new = session.process
    assert new is not None and new is not old and new.is_alive()
    # The next normal search is right.
    bar.field.setText("a+b$")
    _settle(qtbot, bar)
    assert bar.count_label_text() == "1 of 1"
    assert editor.find_matches() == [(0, 31)]
    # Typing still edits the document.
    _focus(qtbot, editor)
    qtbot.keyClicks(editor, "z")
    assert "z" in editor.toPlainText()
    # Closing the app leaves no worker running.
    window.close()
    assert not new.is_alive()
    names = [child.name for child in multiprocessing.active_children()]
    assert "galley-find" not in names


# Replace -----------------------------------------------------------------------


def test_regex_replace_all_with_groups_is_one_undo_step(qtbot) -> None:
    window, editor, _bar_ = _input_with(qtbot, _ORDERS)
    bar = _open_replace(qtbot, window, r"qty=(\d)", "n=$1$$")
    heard: list[str] = []
    bar.count_changed.connect(heard.append)
    assert bar.replace_all() == 0
    qtbot.waitUntil(
        lambda: bar.count_label_text() == replaced_text(2), timeout=_WAIT_MS
    )
    assert editor.toPlainText() == "n=1$ n=2$\nQTY=3 qtys=4\n"
    assert "Replaced 2 matches." in heard
    assert not editor.isReadOnly()
    editor.undo()
    assert editor.toPlainText() == _ORDERS


def test_regex_replace_keeps_backslashes_and_empties_a_missing_optional_group(
    qtbot,
) -> None:
    window, editor, _bar_ = _input_with(qtbot, "ab a")
    bar = _open_replace(qtbot, window, "a(b)?", r"[$1\n]")
    bar.replace_all()
    qtbot.waitUntil(
        lambda: bar.count_label_text() == replaced_text(2), timeout=_WAIT_MS
    )
    assert editor.toPlainText() == r"[b\n] [\n]"


def test_regex_replace_uses_that_matchs_groups_then_moves_on(qtbot) -> None:
    window, editor, _bar_ = _input_with(qtbot, _ORDERS)
    bar = _open_replace(qtbot, window, r"qty=(\d)", "n=$1")
    bar.replace_current()
    qtbot.waitUntil(lambda: editor.toPlainText().startswith("n=1 "), timeout=_WAIT_MS)
    _settle(qtbot, bar)
    assert bar.match_count() == 1
    assert bar.current_match() == (4, 9)
    editor.undo()
    assert editor.toPlainText() == _ORDERS


def test_regex_replace_all_of_an_astral_match(qtbot) -> None:
    window, editor, _bar_ = _input_with(qtbot, f"{_EMOJI}x{_EMOJI}x")
    bar = _open_replace(qtbot, window, "(.)x", "$1$1")
    bar.replace_all()
    qtbot.waitUntil(
        lambda: bar.count_label_text() == replaced_text(2), timeout=_WAIT_MS
    )
    assert editor.toPlainText() == _EMOJI * 4
    qtbot.keyClick(bar.field, Qt.Key.Key_Escape)
    assert editor.textCursor().selectedText() == _EMOJI * 2


def test_a_missing_group_marks_the_replacement_and_greys_out_replace(qtbot) -> None:
    window, _editor_, _bar_ = _input_with(qtbot, _ORDERS)
    bar = _open_replace(qtbot, window, r"qty=(\d)", "")
    heard: list[str] = []
    bar.count_changed.connect(heard.append)
    qtbot.keyClicks(bar.replace_field, "$2")
    reason = MISSING_GROUP.format(group=2)
    assert reason == "The pattern has no group 2."
    assert bar.replace_field.error() == reason
    assert bar.replace_field.toolTip() == reason
    assert bar.replace_field.accessibleDescription() == reason
    assert bar.count_label_text() == INVALID_REPLACEMENT
    assert heard[-1] == f"{INVALID_REPLACEMENT}. {reason}"
    assert len(bar.editor().find_matches()) == 2
    assert not bar.replace_button.isEnabled()
    assert not bar.replace_all_button.isEnabled()
    assert bar.match_count() == 2
    # The next keystroke in either field checks again.
    bar.field.setText(r"q(t)y=(\d)")
    _settle(qtbot, bar)
    assert bar.replace_field.error() == ""
    assert bar.count_label_text() == "1 of 2"
    bar.field.setText(r"qty=(\d)")
    _settle(qtbot, bar)
    assert bar.count_label_text() == INVALID_REPLACEMENT
    qtbot.keyClick(bar.replace_field, Qt.Key.Key_Backspace)
    qtbot.keyClicks(bar.replace_field, "1")
    assert bar.replace_field.error() == ""
    assert bar.count_label_text() == "1 of 2"
    assert bar.replace_button.isEnabled()
    # With Regex off, $2 is plain text.
    bar.replace_field.setText("$2")
    bar.regex_button.setChecked(False)
    bar.whole_word_button.setChecked(True)
    bar.field.setText("qty")
    _settle(qtbot, bar)
    assert bar.replace_field.error() == ""
    assert bar.replace_all_button.isEnabled()


def test_whole_word_alone_replaces_with_the_text_as_typed(qtbot) -> None:
    window, editor, _bar_ = _input_with(qtbot, _ORDERS)
    bar = _bar(window, "input")
    _action(window, "Find and Replace…").trigger()
    _find(qtbot, window, bar, "qty", regex=False)
    bar.replace_field.setText("$1")
    bar.replace_all()
    qtbot.waitUntil(
        lambda: bar.count_label_text() == replaced_text(2), timeout=_WAIT_MS
    )
    assert editor.toPlainText() == "$1=1 $1=2\nQTY=3 qtys=4\n"


def test_replace_all_past_the_limit_leaves_the_document_alone(qtbot) -> None:
    text = order_lines()
    window, editor, _bar_ = _input_with(qtbot, text, limit_ms=60_000)
    bar = _open_replace(qtbot, window, r"(q)ty", "$1")
    assert bar.match_count() > 90_000
    window._regex_session.limit_ms = 1
    bar.replace_all()
    qtbot.waitUntil(
        lambda: bar.count_label_text() == SEARCH_TIMED_OUT, timeout=_WAIT_MS
    )
    assert editor.toPlainText() == text
    assert not editor.document().isUndoAvailable()
    assert not bar.replace_all_button.isEnabled()


def test_replace_all_drops_its_result_when_the_document_changed_meanwhile(
    qtbot,
) -> None:
    window, editor, _bar_ = _input_with(qtbot, _ORDERS)
    bar = _open_replace(qtbot, window, r"qty=(\d)", "n=$1")
    bar.replace_all()
    # Before the worker answers: nothing runs between these two lines.
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.insertText("qty=5")
    edited = editor.toPlainText()
    qtbot.wait(FIND_DELAY_MS * 2)
    _settle(qtbot, bar)
    assert editor.toPlainText() == edited
    assert bar.count_label_text() != replaced_text(2)
    assert bar.match_count() == 3
    editor.undo()
    assert editor.toPlainText() == _ORDERS


# Keys only -----------------------------------------------------------------------


def test_keys_only_keeps_regex_matches_inside_one_key(qtbot) -> None:
    text = '{"price": 1, "note": "price drop", "items": [{"prices": 2}]}'
    window, output, bar = _output_with(qtbot, text)
    assert bar.keys_only is not None
    bar.keys_only.setChecked(True)
    _find(qtbot, window, bar, r"pr\w+")
    assert _matched(output, bar) == ["price", "prices"]
    # Lookarounds see the real text, not the mask.
    bar.field.setText('(?<=")price')
    _settle(qtbot, bar)
    assert _matched(output, bar) == ["price", "price"]
    # A match that runs out of a key does not count.
    bar.field.setText('price": 1')
    _settle(qtbot, bar)
    assert bar.match_count() == 0
    bar.keys_only.setChecked(False)
    _settle(qtbot, bar)
    assert bar.match_count() == 1


def test_keys_only_with_whole_word(qtbot) -> None:
    text = '{"id": "id", "ids": [{"id": 1}]}'
    window, output, bar = _output_with(qtbot, text)
    assert bar.keys_only is not None
    bar.keys_only.setChecked(True)
    _find(qtbot, window, bar, "id", regex=False)
    assert output.find_matches() == [(2, 4), (23, 25)]


# Release check ---------------------------------------------------------------


def test_the_toggles_are_not_menu_actions(qtbot) -> None:
    window = _window(qtbot)
    names = {"Regex", "Whole word", "Use regular expression", "Match whole word"}
    assert not names & set(UNBUILT_ACTIONS)
    assert not names & set(_ACTION_ATTRS)
    assert window._actions._not_yet_actions() == []
    texts = {action.text() for action in window.findChildren(QAction)}
    assert not names & texts


def test_the_release_check_output_is_unchanged() -> None:
    printed = io.StringIO()
    with redirect_stdout(printed):
        assert main() == 1
    assert printed.getvalue() == _COMING_SOON_OUTPUT
    assert "Not yet" not in printed.getvalue()


# Performance (timing only, no budget) ----------------------------------------


def _print_timing(test: str, fixture: str, timing) -> None:
    samples = ", ".join(f"{sample * 1000:.1f}" for sample in timing.samples)
    seconds = timing.median
    note = " - over 1 s" if seconds > 1.0 else ""
    print(
        f"perf: {test} [{fixture}] median {seconds * 1000:.1f} ms "
        f"({sys.platform}; timing only, no budget){note} ({samples})",
        flush=True,
    )


def _large(qtbot, limit_ms: int = 60_000):
    text = order_lines()
    size = len(text.encode("utf-8"))
    assert REPLACE_BYTES <= size < 2_500_000
    window, editor, bar = _input_with(qtbot, text, limit_ms=limit_ms)
    return window, editor, bar, text, size


def _search_and_wait(qtbot, bar: FindBar) -> None:
    bar.search()
    _settle(qtbot, bar)


@pytest.mark.perf
def test_cold_and_warm_regex_searches_are_timed(qtbot) -> None:
    window, _editor_, bar, _text, size = _large(qtbot)
    _find(qtbot, window, bar, r"qty_max")
    matches = bar.match_count()
    assert matches > 90_000 / 2

    def fresh_worker() -> None:
        old = bar.regex_session()
        session = RegexSession(window, limit_ms=old.limit_ms)
        for name in ("input", "output"):
            _bar(window, name).set_regex_session(session)
        window._regex_session = session
        old.shutdown()

    cold = time_runs(lambda: _search_and_wait(qtbot, bar), setup=fresh_worker)
    assert bar.match_count() == matches
    _print_timing(
        "test_cold_and_warm_regex_searches_are_timed",
        f"cold: start the worker, copy and search {size:,} B, {matches:,} matches",
        cold,
    )

    def edit_query() -> None:
        bar.field.blockSignals(True)
        bar.field.setText(r"qty_max")
        bar.field.blockSignals(False)

    warm = time_runs(lambda: _search_and_wait(qtbot, bar), setup=edit_query)
    _print_timing(
        "test_cold_and_warm_regex_searches_are_timed",
        f"warm: search {size:,} B, text already sent, {matches:,} matches",
        warm,
    )


@pytest.mark.perf
def test_a_regex_search_on_2_4_mb_is_timed(qtbot) -> None:
    window, _editor_, bar, _text, size = _large(qtbot)
    _find(qtbot, window, bar, r"\"qty\": (\d)")
    matches = bar.match_count()
    assert matches > 90_000 / 2
    timing = time_runs(lambda: _search_and_wait(qtbot, bar))
    _print_timing(
        "test_a_regex_search_on_2_4_mb_is_timed",
        f"Regex search, {size:,} B JSON, {matches:,} matches",
        timing,
    )


@pytest.mark.perf
def test_a_whole_word_search_on_2_4_mb_is_timed(qtbot) -> None:
    window, _editor_, bar, _text, size = _large(qtbot)
    _find(qtbot, window, bar, "qty", regex=False)
    matches = bar.match_count()
    assert matches > 90_000 / 2
    timing = time_runs(lambda: _search_and_wait(qtbot, bar))
    _print_timing(
        "test_a_whole_word_search_on_2_4_mb_is_timed",
        f"Whole word search, {size:,} B JSON, {matches:,} matches",
        timing,
    )


@pytest.mark.perf
def test_a_regex_replace_all_on_2_4_mb_is_timed(qtbot) -> None:
    window, editor, _bar_, text, size = _large(qtbot)
    bar = _open_replace(qtbot, window, r"\"(qty)\"", "$1")
    matches = bar.match_count()
    assert matches > 90_000 / 2

    def reset() -> None:
        if editor.toPlainText() != text:
            editor.setPlainText(text)
        _search_and_wait(qtbot, bar)

    def replace_all() -> None:
        bar.replace_all()
        qtbot.waitUntil(
            lambda: bar.count_label_text() == replaced_text(matches), timeout=_WAIT_MS
        )

    replace_all()
    assert editor.toPlainText() == text.replace('"qty"', "qty")
    editor.undo()
    assert editor.toPlainText() == text
    timing = time_runs(replace_all, setup=reset)
    assert not editor.isReadOnly()
    _print_timing(
        "test_a_regex_replace_all_on_2_4_mb_is_timed",
        f"Regex Replace All with $1, {size:,} B JSON, {matches:,} matches",
        timing,
    )


@pytest.mark.perf
def test_copying_the_text_to_the_worker_is_timed(qtbot) -> None:
    window, _editor_, bar, text, size = _large(qtbot)
    _turn_on(qtbot, window, bar.regex_button)
    session = window._regex_session
    versions = iter(range(10**6, 10**7))

    def copy() -> None:
        with qtbot.waitSignal(session.text_stored, timeout=_WAIT_MS):
            session.store_text(next(versions), text)

    timing = time_runs(copy)
    _print_timing(
        "test_copying_the_text_to_the_worker_is_timed",
        f"copy {size:,} B to the find worker",
        timing,
    )
