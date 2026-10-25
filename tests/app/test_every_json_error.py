"""Every JSON error in the Problems pane, gutter, and status bar (#78)."""

import importlib
import sys
from collections.abc import Callable

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QImage, QKeySequence, QPalette
from PySide6.QtWidgets import QLabel, QMainWindow, QMenu, QMenuBar, QWidget

import galley.app.shortcuts as shortcuts
from galley.app.editor import CodeEditor
from galley.app.main import create_main_window
from galley.app.problem_text import (
    STOPPED_TEXT,
    UNCERTAIN_SUFFIX,
    display_problem,
)
from galley.app.problems_pane import PROBLEM_ROW_LIMIT, ProblemsPane
from galley.app.status_bar import StatusBar
from galley.app.status_message import StatusMessage
from galley.app.style import muted_text_style
from galley.core.json_recover import MAX_FINDINGS
from galley.core.problems import Problem

# A missing comma on line 2, an unclosed quote on line 4, a trailing
# comma and a repeated key on line 5. Fake data only.
_THREE_LINES = (
    '{\n  "name": "Galley"\n  "kind": "app",\n  "note": "hello\n  "kind": 2,\n}\n'
)
_TWO_ON_ONE_LINE = '{\n  "a": 1\n  "b": [1 2 3]\n}\n'


def _show(qtbot) -> QMainWindow:
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def _action(window: QMainWindow, text: str) -> QAction:
    found = [item for item in window.findChildren(QAction) if item.text() == text]
    assert len(found) == 1, text
    return found[0]


def _format(
    qtbot, text: str, count: str
) -> tuple[QMainWindow, CodeEditor, ProblemsPane, StatusMessage]:
    window = _show(qtbot)
    editor = window.findChild(CodeEditor, "inputEditor")
    status = window.findChild(StatusMessage, "statusMessage")
    pane = window.findChild(ProblemsPane, "problemsPane")
    assert isinstance(editor, CodeEditor)
    assert isinstance(status, StatusMessage)
    assert isinstance(pane, ProblemsPane)
    editor.setPlainText(text)
    _action(window, "Format").trigger()
    qtbot.waitUntil(lambda: status.text() == count, timeout=15000)
    qtbot.waitUntil(pane.isVisible, timeout=15000)
    return window, editor, pane, status


def _row(pane: ProblemsPane, index: int) -> QWidget:
    problems = pane.list_widget()
    row = problems.itemWidget(problems.item(index))
    assert row is not None
    return row


def _texts(row: QWidget) -> list[str]:
    return [label.text() for label in row.findChildren(QLabel)]


def test_every_error_gets_a_row_in_text_order(qtbot) -> None:
    _window, editor, pane, _status = _format(qtbot, _THREE_LINES, "4 problems")
    assert pane.lines() == [2, 4, 5, 5]
    assert pane.messages() == [
        "Missing comma at the end of line 2.",
        "Unclosed quote on line 4.",
        'Remove the repeated key "kind" on line 5.',
        "Trailing comma at the end of line 5.",
    ]
    assert _texts(_row(pane, 0)) == [
        "⊗",
        "Error · Line 2, Col 19",
        "Missing comma at the end of line 2.",
    ]
    assert _row(pane, 1).accessibleName() == "Line 4: Unclosed quote on line 4."
    assert pane.notes() == ()
    assert editor.problem_lines() == {2, 4, 5}


def test_each_specific_problem_keeps_its_own_row(qtbot) -> None:
    # Before #78 this showed only the unquoted key.
    text = '{\n  "name": "Galley",\n  version: 2,\n  "tags": ["json"\n}\n'
    _window, editor, pane, _status = _format(qtbot, text, "2 problems")
    assert pane.messages() == [
        "Put double quotes around the key on line 3.",
        "Missing ] for the [ opened on line 4.",
    ]
    assert editor.problem_lines() == {3, 4}


def test_the_count_shows_in_the_badge_and_status_bar(qtbot) -> None:
    window, _editor, pane, status = _format(qtbot, _THREE_LINES, "4 problems")
    badge = pane.findChild(QLabel, "problemsBadge")
    assert isinstance(badge, QLabel)
    assert badge.text() == "4"
    assert badge.accessibleName() == "4 problems"
    assert status.accessibleName() == "4 problems. Show problems."
    bar = window.findChild(StatusBar)
    assert isinstance(bar, StatusBar)
    assert bar.counts_text() == ""


def test_screen_readers_hear_the_count_and_the_first_error(qtbot) -> None:
    window, _editor, _pane, _status = _format(qtbot, _THREE_LINES, "4 problems")
    bar = window.findChild(StatusBar)
    assert isinstance(bar, StatusBar)
    assert bar.last_announcement() == (
        "4 problems. First: Missing comma at the end of line 2, line 2"
    )


def test_one_marker_per_line_lists_every_message(qtbot) -> None:
    _window, editor, pane, _status = _format(qtbot, _TWO_ON_ONE_LINE, "3 problems")
    assert pane.lines() == [2, 3, 3]
    assert editor.problem_lines() == {2, 3}
    gutter = editor.line_number_area()
    assert gutter.accessibleDescription() == (
        "Missing comma at the end of line 2.\n"
        "Missing comma at the end of line 3.\n"
        "Missing comma at the end of line 3."
    )
    # Clicking line 3's marker selects that line's first row.
    pane.list_widget().setCurrentRow(0)
    editor.problem_marker_clicked.emit(3)
    assert pane.list_widget().currentRow() == 1


def test_a_row_after_a_guess_carries_the_muted_suffix(qtbot) -> None:
    text = '{\n  "a": [1, 2,\n  "b": 3\n}\n'
    _window, editor, pane, _status = _format(qtbot, text, "2 problems")
    first, second = _row(pane, 0), _row(pane, 1)
    assert pane.lines() == [2, 3]
    message = f"Missing ] for the [ opened on line 2. {UNCERTAIN_SUFFIX}"
    assert _texts(first)[1:] == ["Error · Line 2, Col 8", message]
    assert first.accessibleName() == f"Line 2: {message}"
    assert _texts(second)[2] == "Missing comma at the end of line 3."
    # The suffix is in the muted message label: no colour of its own.
    for selected, row in ((1, first), (0, second)):
        pane.list_widget().setCurrentRow(selected)
        label = next(
            item for item in row.findChildren(QLabel) if "Missing" in item.text()
        )
        assert label.styleSheet() == muted_text_style()
    assert UNCERTAIN_SUFFIX in editor.line_number_area().accessibleDescription()


def test_clicking_a_row_jumps_to_its_line_and_column(qtbot) -> None:
    _window, editor, pane, _status = _format(qtbot, _TWO_ON_ONE_LINE, "3 problems")
    qtbot.mouseClick(_row(pane, 2), Qt.MouseButton.LeftButton)
    assert editor.cursor_place() == (3, 12)


def test_f8_and_shift_f8_walk_the_problems_and_wrap(qtbot) -> None:
    window, editor, pane, _status = _format(qtbot, _TWO_ON_ONE_LINE, "3 problems")
    following = _action(window, "Next Problem")
    previous = _action(window, "Previous Problem")
    editor.reveal_line(1)
    places = []
    for _ in range(4):
        following.trigger()
        places.append((editor.cursor_place(), pane.list_widget().currentRow()))
    assert places == [((2, 9), 0), ((3, 10), 1), ((3, 12), 2), ((2, 9), 0)]
    previous.trigger()
    assert (editor.cursor_place(), pane.list_widget().currentRow()) == ((3, 12), 2)
    previous.trigger()
    assert (editor.cursor_place(), pane.list_widget().currentRow()) == ((3, 10), 1)


def test_f8_keys_reach_the_window_from_the_editor(qtbot) -> None:
    _window, editor, pane, _status = _format(qtbot, _TWO_ON_ONE_LINE, "3 problems")
    editor.reveal_line(1)
    qtbot.keyClick(editor, Qt.Key.Key_F8)
    assert editor.cursor_place() == (2, 9)
    qtbot.keyClick(editor, Qt.Key.Key_F8, Qt.KeyboardModifier.ShiftModifier)
    assert editor.cursor_place() == (3, 12)
    assert pane.list_widget().currentRow() == 2


def _portable(action: QAction) -> list[str]:
    return [
        key.toString(QKeySequence.SequenceFormat.PortableText)
        for key in action.shortcuts()
    ]


def _menu(window: QMainWindow, title: str) -> QMenu:
    bar = window.findChild(QMenuBar)
    assert isinstance(bar, QMenuBar)
    for action in bar.actions():
        menu = action.menu()
        if isinstance(menu, QMenu) and menu.title() == title:
            return menu
    raise AssertionError(title)


def test_the_view_menu_lists_next_and_previous_problem(qtbot) -> None:
    window, editor, pane, _status = _format(qtbot, _TWO_ON_ONE_LINE, "3 problems")
    view = _menu(window, "View")
    labels = [action.text() for action in view.actions()]
    start = labels.index("Show Problems")
    assert labels[start : start + 3] == [
        "Show Problems",
        "Next Problem",
        "Previous Problem",
    ]
    following, previous = view.actions()[start + 1 : start + 3]
    assert following is _action(window, "Next Problem")
    editor.reveal_line(1)
    following.trigger()
    assert (editor.cursor_place(), pane.list_widget().currentRow()) == ((2, 9), 0)
    previous.trigger()
    assert (editor.cursor_place(), pane.list_widget().currentRow()) == ((3, 12), 2)


def test_the_menu_shows_the_platform_key_first(qtbot) -> None:
    window = _show(qtbot)
    following = _action(window, "Next Problem")
    previous = _action(window, "Previous Problem")
    if sys.platform == "darwin":
        assert _portable(following) == ["Ctrl+'", "F8"]
        assert _portable(previous) == ["Ctrl+Shift+'", "Shift+F8"]
        native = QKeySequence.SequenceFormat.NativeText
        assert following.shortcut().toString(native) == "⌘'"
        assert previous.shortcut().toString(native) == "⇧⌘'"
    else:
        assert _portable(following) == ["F8"]
        assert _portable(previous) == ["Shift+F8"]


@pytest.mark.parametrize(
    ("platform", "following", "previous"),
    [
        ("darwin", ["Ctrl+'", "F8"], ["Ctrl+Shift+'", "Shift+F8"]),
        ("win32", ["F8"], ["Shift+F8"]),
        ("linux", ["F8"], ["Shift+F8"]),
    ],
)
def test_problem_keys_are_built_per_platform(
    monkeypatch, platform: str, following: list[str], previous: list[str]
) -> None:
    portable = QKeySequence.SequenceFormat.PortableText
    monkeypatch.setattr(sys, "platform", platform)
    try:
        module = importlib.reload(shortcuts)
        assert [key.toString(portable) for key in module.NEXT_PROBLEM] == following
        assert [key.toString(portable) for key in module.PREVIOUS_PROBLEM] == previous
    finally:
        monkeypatch.undo()
        importlib.reload(shortcuts)


@pytest.mark.skipif(sys.platform != "darwin", reason="⌘' is a macOS key")
def test_command_quote_walks_the_problems_on_macos(qtbot) -> None:
    _window, editor, pane, _status = _format(qtbot, _TWO_ON_ONE_LINE, "3 problems")
    editor.reveal_line(1)
    command = Qt.KeyboardModifier.ControlModifier
    qtbot.keyClick(editor, Qt.Key.Key_Apostrophe, command)
    assert editor.cursor_place() == (2, 9)
    shifted = command | Qt.KeyboardModifier.ShiftModifier
    qtbot.keyClick(editor, Qt.Key.Key_Apostrophe, shifted)
    assert editor.cursor_place() == (3, 12)
    assert pane.list_widget().currentRow() == 2


def test_f8_with_no_problems_does_nothing(qtbot) -> None:
    window = _show(qtbot)
    editor = window.findChild(CodeEditor, "inputEditor")
    assert isinstance(editor, CodeEditor)
    editor.setPlainText('{\n  "a": 1\n}\n')
    editor.reveal_line(2)
    _action(window, "Next Problem").trigger()
    _action(window, "Previous Problem").trigger()
    assert editor.cursor_place() == (2, 1)


def test_the_list_stops_at_100_rows_and_says_how_many(qtbot) -> None:
    count = 240
    text = "[\n" + "\n".join(["  1"] * (count + 1)) + "\n]\n"
    _window, editor, pane, status = _format(qtbot, text, f"{count} problems")
    assert pane.count() == count
    assert len(pane.lines()) == PROBLEM_ROW_LIMIT
    assert pane.notes() == (f"Showing the first 100 of {count} problems.",)
    problems = pane.list_widget()
    assert problems.count() == PROBLEM_ROW_LIMIT + 1
    note = problems.item(PROBLEM_ROW_LIMIT)
    assert note.flags() == Qt.ItemFlag.NoItemFlags
    assert _texts(_row(pane, PROBLEM_ROW_LIMIT)) == [pane.notes()[0]]
    badge = pane.findChild(QLabel, "problemsBadge")
    assert isinstance(badge, QLabel)
    assert badge.text() == str(count)
    # Only listed rows have a marker, so each marker selects a row.
    assert editor.problem_lines() == set(pane.lines())
    assert status.text() == f"{count} problems"


def test_the_note_rows_cannot_be_selected_or_activated(qtbot) -> None:
    count = PROBLEM_ROW_LIMIT + 5
    text = "[\n" + "\n".join(["  1"] * (count + 1)) + "\n]\n"
    _window, editor, pane, _status = _format(qtbot, text, f"{count} problems")
    problems = pane.list_widget()
    problems.setFocus()
    problems.setCurrentRow(PROBLEM_ROW_LIMIT - 1)
    qtbot.keyClick(problems, Qt.Key.Key_Down)
    assert problems.currentRow() == PROBLEM_ROW_LIMIT - 1
    jumps: list[tuple[int, int]] = []
    pane.jump_requested.connect(lambda line, column: jumps.append((line, column)))
    qtbot.mouseClick(_row(pane, PROBLEM_ROW_LIMIT), Qt.MouseButton.LeftButton)
    assert jumps == []
    del editor


def test_a_stopped_check_ends_with_its_note(qtbot) -> None:
    text = "[" + " ".join(["1"] * (MAX_FINDINGS + 20)) + "]"
    _window, editor, pane, status = _format(qtbot, text, f"{MAX_FINDINGS} problems")
    assert pane.count() == MAX_FINDINGS
    assert pane.notes() == (
        f"Showing the first 100 of {MAX_FINDINGS} problems.",
        STOPPED_TEXT,
    )
    assert editor.problem_lines() == {1}
    assert status.text() == f"{MAX_FINDINGS} problems"


def test_a_stopped_short_list_has_only_the_stop_note(qtbot) -> None:
    pane = ProblemsPane()
    qtbot.addWidget(pane)
    pane.set_problems(
        (
            display_problem(Problem("missing_comma", 2, colno=9)),
            display_problem(Problem("json_stopped", 3, colno=4)),
        )
    )
    assert pane.count() == 1
    assert pane.lines() == [2]
    assert pane.notes() == (STOPPED_TEXT,)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_notes_and_suffix_read_in_light_and_dark(
    qtbot, install_scheme: Callable[[str], QPalette], scheme: str
) -> None:
    other = install_scheme("dark" if scheme == "light" else "light")
    palette = install_scheme(scheme)
    window_role = QPalette.ColorRole.Window
    assert palette.color(window_role).rgb() != other.color(window_role).rgb()
    pane = ProblemsPane()
    qtbot.addWidget(pane)
    pane.resize(360, 400)
    problems = [
        display_problem(Problem("missing_comma", line, colno=9))
        for line in range(1, PROBLEM_ROW_LIMIT + 3)
    ]
    problems[1] = display_problem(Problem("trailing_comma", 2, colno=9, uncertain=True))
    problems.append(display_problem(Problem("json_stopped", 200, colno=1)))
    pane.set_problems(problems)
    pane.show()
    qtbot.waitExposed(pane)

    background = palette.color(window_role)
    suffixed = _row(pane, 1)
    plain = _row(pane, 2)
    pane.list_widget().setCurrentRow(0)
    for row in (suffixed, plain):
        label = next(
            item for item in row.findChildren(QLabel) if "comma" in item.text()
        )
        assert label.styleSheet() == muted_text_style()
        assert _label_contrast(label, background) >= 4.5
    assert UNCERTAIN_SUFFIX in _texts(suffixed)[2]
    for index in (PROBLEM_ROW_LIMIT, PROBLEM_ROW_LIMIT + 1):
        problems_list = pane.list_widget()
        problems_list.scrollToItem(problems_list.item(index))
        (label,) = _row(pane, index).findChildren(QLabel)
        assert label.styleSheet() == muted_text_style()
        assert _label_contrast(label, background) >= 4.5, label.text()


def _label_contrast(label: QLabel, background: QColor) -> float:
    image: QImage = label.grab().toImage()
    return max(
        _contrast(_seen(image.pixelColor(x, y), background), background)
        for y in range(image.height())
        for x in range(image.width())
    )


def _seen(color: QColor, background: QColor) -> QColor:
    alpha = color.alphaF()
    return QColor(
        round(color.red() * alpha + background.red() * (1 - alpha)),
        round(color.green() * alpha + background.green() * (1 - alpha)),
        round(color.blue() * alpha + background.blue() * (1 - alpha)),
    )


def _contrast(first: QColor, second: QColor) -> float:
    def luminance(color: QColor) -> float:
        def channel(value: int) -> float:
            srgb = value / 255
            if srgb <= 0.04045:
                return srgb / 12.92
            return ((srgb + 0.055) / 1.055) ** 2.4

        return (
            0.2126 * channel(color.red())
            + 0.7152 * channel(color.green())
            + 0.0722 * channel(color.blue())
        )

    lighter = max(luminance(first), luminance(second))
    darker = min(luminance(first), luminance(second))
    return (lighter + 0.05) / (darker + 0.05)
