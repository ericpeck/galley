"""Indent, Outdent and the indent size picker (release check, Chief's brief).

Settings live in the throwaway store from ``isolated_settings``.
"""

import sys

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import (
    QAction,
    QFontMetricsF,
    QKeySequence,
    QStandardItemModel,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QMenu,
    QPushButton,
    QToolButton,
)
from tests.core.mask_rows import pretty_json, text_of_size
from tests.perf import time_runs

import galley.app.main_window as main_window_module
from galley.app.editor import TAB_OUTDENT_SPACES, CodeEditor
from galley.app.main import create_main_window
from galley.app.main_window import INPUT_EMPTY
from galley.app.parse_session import ParseSession
from galley.app.problems_settings import load_indent_size, problems_settings
from galley.app.shortcuts import FOLD, INDENT, OUTDENT, UNFOLD
from galley.core.format import format_document

_PORTABLE = QKeySequence.SequenceFormat.PortableText
_NATIVE = QKeySequence.SequenceFormat.NativeText
_CONTROL = Qt.KeyboardModifier.ControlModifier
_WAIT_MS = 15_000
_WIDE_PX = 1600
_KEY = "format/indentSize"
_JSON = '{"name": "Ann", "tags": ["a", {"k": 1}]}'
_LINES = "a\n  b\n\tc\n c2\n    d\n"
_YAML_TAB_REASON = "YAML indents with spaces."


def _show(qtbot):
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def _editors(window) -> tuple[CodeEditor, CodeEditor]:
    source = window.findChild(CodeEditor, "inputEditor")
    output = window.findChild(CodeEditor, "outputEditor")
    assert isinstance(source, CodeEditor)
    assert isinstance(output, CodeEditor)
    return source, output


def _focus(qtbot, editor: CodeEditor) -> None:
    editor.activateWindow()
    editor.setFocus()
    qtbot.waitUntil(lambda: QApplication.focusWidget() is editor)


def _select(editor: CodeEditor, anchor: int, position: int | None = None) -> None:
    cursor = editor.textCursor()
    cursor.setPosition(anchor)
    if position is not None:
        cursor.setPosition(position, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)


def _line_start(editor: CodeEditor, line: int) -> int:
    return editor.document().findBlockByNumber(line).position()


def _picker(window) -> QComboBox:
    picker = window.findChild(QComboBox, "indentSize")
    assert isinstance(picker, QComboBox)
    return picker


def _format_picker(window, pane: str) -> QComboBox:
    combo = window.findChild(QComboBox, f"{pane}Format")
    assert isinstance(combo, QComboBox)
    return combo


def _choose(combo: QComboBox, text: str) -> None:
    index = combo.findText(text)
    assert index >= 0, text
    combo.setCurrentIndex(index)


def _button(qtbot, window, action: QAction) -> QToolButton:
    window.resize(_WIDE_PX, window.height())
    for button in window.findChildren(QToolButton):
        if button.defaultAction() is action:
            qtbot.waitUntil(button.isVisible, timeout=5000)
            return button
    raise AssertionError(f"no toolbar button for {action.text()}")


def _format(qtbot, window) -> None:
    window._actions.format.trigger()
    qtbot.waitUntil(lambda: not window._formatting, timeout=_WAIT_MS)


def _hold(monkeypatch, window) -> None:
    """Keep a Format job running without sending it to the worker."""
    monkeypatch.setattr(ParseSession, "active_job_id", property(lambda _self: 1))
    window._session.submit = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    monkeypatch.setattr(
        main_window_module, "_busy_progress_bytes", lambda source, text: (1, 10**9)
    )


def _one_undo_restores(editor: CodeEditor, before: str) -> None:
    editor.undo()
    assert editor.toPlainText() == before
    assert not editor.document().isUndoAvailable()


# --- Actions, keys and menu ----------------------------------------------------


def test_the_shortcuts_are_portable_ctrl_brackets(qtbot) -> None:
    actions = _show(qtbot)._actions
    assert INDENT == QKeySequence("Ctrl+]", _PORTABLE)
    assert OUTDENT == QKeySequence("Ctrl+[", _PORTABLE)
    assert actions.indent.shortcut() == INDENT
    assert actions.outdent.shortcut() == OUTDENT
    if sys.platform == "darwin":
        assert INDENT.toString(_NATIVE) == "⌘]"
        assert OUTDENT.toString(_NATIVE) == "⌘["
    else:
        assert INDENT.toString(_NATIVE) == "Ctrl+]"
        assert OUTDENT.toString(_NATIVE) == "Ctrl+["


def test_the_shortcuts_clash_with_nothing(qtbot) -> None:
    window = _show(qtbot)
    seen: dict[str, list[str]] = {}
    for action in window.findChildren(QAction):
        for key in action.shortcuts():
            seen.setdefault(key.toString(_PORTABLE), []).append(action.text())
    assert seen["Ctrl+]"] == ["Indent"]
    assert seen["Ctrl+["] == ["Outdent"]
    for key in (FOLD, UNFOLD):
        assert key not in (INDENT, OUTDENT)


@pytest.mark.parametrize(
    ("attr", "name", "words", "keys"),
    [
        ("indent", "Indent", "Indent lines", INDENT),
        ("outdent", "Outdent", "Outdent lines", OUTDENT),
    ],
)
def test_the_tooltip_shows_the_shortcut(qtbot, attr, name, words, keys) -> None:
    window = _show(qtbot)
    action = getattr(window._actions, attr)
    tip = f"{words} ({keys.toString(_NATIVE)})"
    assert action.toolTip() == tip
    if sys.platform == "darwin":
        assert tip == f"{words} ({'⌘]' if attr == 'indent' else '⌘['})"
    button = _button(qtbot, window, action)
    assert button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
    assert button.toolTip() == tip
    assert button.accessibleName() == name
    assert action.text() == name


def test_the_edit_menu_has_its_own_indent_group_after_select_all(qtbot) -> None:
    window = _show(qtbot)
    edit = window.findChild(QMenu, "menuEdit")
    assert isinstance(edit, QMenu)
    assert edit.menuAction() in window.menuBar().actions()
    labels = ["|" if item.isSeparator() else item.text() for item in edit.actions()]
    at = labels.index("Select All")
    assert labels[at + 1 : at + 5] == ["|", "Indent", "Outdent", "|"]
    assert window._actions.indent in edit.actions()
    assert window._actions.outdent in edit.actions()


# --- Indent and Outdent on the focused pane -----------------------------------


@pytest.mark.parametrize("pane", ["input", "output"])
def test_the_shortcuts_act_on_the_focused_pane(qtbot, pane: str) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    source.setPlainText("a\nb\n")
    output.setPlainText("x\ny\n")
    target, other = (source, output) if pane == "input" else (output, source)
    before, untouched = target.toPlainText(), other.toPlainText()
    _focus(qtbot, target)
    _select(target, _line_start(target, 1))
    qtbot.keyClick(target, Qt.Key.Key_BracketRight, _CONTROL)
    assert target.toPlainText() == before.replace("\n", "\n  ", 1)
    assert other.toPlainText() == untouched
    qtbot.keyClick(target, Qt.Key.Key_BracketLeft, _CONTROL)
    assert target.toPlainText() == before
    assert other.toPlainText() == untouched


def test_indent_adds_one_unit_to_every_selected_line(qtbot) -> None:
    window = _show(qtbot)
    source, _output = _editors(window)
    source.setPlainText(_LINES)
    source.document().clearUndoRedoStacks()
    _focus(qtbot, source)
    _select(source, 1, _line_start(source, 2) + 1)
    window._actions.indent.trigger()
    assert source.toPlainText() == "  a\n    b\n  \tc\n c2\n    d\n"
    cursor = source.textCursor()
    assert source.document().findBlock(cursor.selectionStart()).blockNumber() == 0
    assert source.document().findBlock(cursor.selectionEnd()).blockNumber() == 2
    _one_undo_restores(source, _LINES)


def test_indent_without_a_selection_indents_the_carets_line(qtbot) -> None:
    window = _show(qtbot)
    source, _output = _editors(window)
    source.setPlainText(_LINES)
    _focus(qtbot, source)
    _select(source, _line_start(source, 3) + 2)
    window._actions.indent.trigger()
    assert source.toPlainText() == "a\n  b\n\tc\n   c2\n    d\n"
    assert source.textCursor().position() == _line_start(source, 3) + 4
    assert not source.textCursor().hasSelection()


def test_outdent_removes_one_tab_or_up_to_the_units_spaces(qtbot) -> None:
    window = _show(qtbot)
    source, _output = _editors(window)
    text = "a\n  b\n\t\tc\n c2\n    d\n \tm\n\t e\n"
    source.setPlainText(text)
    source.document().clearUndoRedoStacks()
    _focus(qtbot, source)
    source.selectAll()
    window._actions.outdent.trigger()
    # "a" has nothing to remove. A mixed line loses only the first unit.
    assert source.toPlainText() == "a\nb\n\tc\nc2\n  d\n\tm\n e\n"
    _one_undo_restores(source, text)


def test_outdent_with_nothing_to_remove_changes_nothing(qtbot) -> None:
    window = _show(qtbot)
    source, _output = _editors(window)
    source.setPlainText("a\nb\n")
    source.document().clearUndoRedoStacks()
    _focus(qtbot, source)
    source.selectAll()
    window._actions.outdent.trigger()
    assert source.toPlainText() == "a\nb\n"
    assert not source.document().isUndoAvailable()


def test_a_selection_ending_at_column_zero_leaves_that_line_alone(qtbot) -> None:
    window = _show(qtbot)
    source, _output = _editors(window)
    source.setPlainText("a\nb\nc\n")
    _focus(qtbot, source)
    _select(source, 0, _line_start(source, 2))
    window._actions.indent.trigger()
    assert source.toPlainText() == "  a\n  b\nc\n"
    cursor = source.textCursor()
    assert (cursor.selectionStart(), cursor.selectionEnd()) == (
        0,
        _line_start(source, 2),
    )
    window._actions.outdent.trigger()
    assert source.toPlainText() == "a\nb\nc\n"
    assert source.textCursor().selectionEnd() == _line_start(source, 2)


def test_indent_on_output_is_an_edit_with_one_undo_step(qtbot) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    source.setPlainText(_JSON)
    _format(qtbot, window)
    formatted = output.toPlainText()
    _focus(qtbot, output)
    output.selectAll()
    window._actions.indent.trigger()
    assert output.toPlainText() != formatted
    assert all(
        line.startswith("  ") for line in output.toPlainText().splitlines() if line
    )
    # One undo step: the next undo is Format's own.
    output.undo()
    assert output.toPlainText() == formatted


def test_indent_and_outdent_use_the_pickers_unit(qtbot) -> None:
    window = _show(qtbot)
    source, _output = _editors(window)
    _choose(_picker(window), "4 spaces")
    source.setPlainText("a\n      b\n")
    _focus(qtbot, source)
    source.selectAll()
    window._actions.indent.trigger()
    assert source.toPlainText() == "    a\n          b\n"
    window._actions.outdent.trigger()
    window._actions.outdent.trigger()
    assert source.toPlainText() == "a\n  b\n"
    _choose(_picker(window), "Tab")
    window._actions.indent.trigger()
    assert source.toPlainText() == "\ta\n\t  b\n"


def test_with_tab_chosen_outdent_removes_up_to_the_tab_width_in_spaces(
    qtbot,
) -> None:
    """Galley sets no tab width, so it is TAB_OUTDENT_SPACES, 4 (Eric)."""
    window = _show(qtbot)
    source, _output = _editors(window)
    _choose(_picker(window), "Tab")
    assert TAB_OUTDENT_SPACES == 4
    source.setPlainText("      a\n\tb\n")
    _focus(qtbot, source)
    source.selectAll()
    window._actions.outdent.trigger()
    assert source.toPlainText() == "  a\nb\n"
    space = QFontMetricsF(source.font()).horizontalAdvance(" ")
    source.setTabStopDistance(space * 3)
    source.setPlainText("      a\n")
    source.selectAll()
    window._actions.outdent.trigger()
    assert source.toPlainText() == "   a\n"


# --- Greyed out ----------------------------------------------------------------


def test_greyed_out_while_the_focused_pane_is_empty(qtbot) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    actions = window._actions
    _focus(qtbot, source)
    assert not actions.indent.isEnabled()
    assert not actions.outdent.isEnabled()
    source.setPlainText("a\n")
    assert actions.indent.isEnabled()
    assert actions.outdent.isEnabled()
    _focus(qtbot, output)
    assert not actions.indent.isEnabled()
    assert not actions.outdent.isEnabled()
    qtbot.keyClick(output, Qt.Key.Key_BracketRight, _CONTROL)
    assert output.toPlainText() == ""
    output.setPlainText("x\n")
    assert actions.indent.isEnabled()
    _focus(qtbot, source)
    assert actions.indent.isEnabled()


def test_greyed_out_while_a_job_runs(qtbot, monkeypatch) -> None:
    window = _show(qtbot)
    _hold(monkeypatch, window)
    source, _output = _editors(window)
    source.setPlainText(_JSON)
    _focus(qtbot, source)
    assert window._actions.indent.isEnabled()
    window._actions.format.trigger()
    assert window._formatting
    assert not window._actions.indent.isEnabled()
    assert not window._actions.outdent.isEnabled()
    window._actions.indent.trigger()
    assert source.toPlainText() == _JSON
    window._cancel_format()
    qtbot.waitUntil(lambda: not window._formatting, timeout=_WAIT_MS)
    assert window._actions.indent.isEnabled()


def test_the_greyed_out_tooltip_stays_the_shortcut_tip(qtbot) -> None:
    window = _show(qtbot)
    button = _button(qtbot, window, window._actions.indent)
    assert not button.isEnabled()
    assert button.toolTip() == f"Indent lines ({INDENT.toString(_NATIVE)})"


def test_formats_greyed_out_tooltip_says_input_is_empty(qtbot) -> None:
    window = _show(qtbot)
    source, _output = _editors(window)
    button = window.findChild(QPushButton, "formatButton")
    assert isinstance(button, QPushButton)
    assert INPUT_EMPTY == "Input is empty."
    assert not button.isEnabled()
    assert button.toolTip() == "Input is empty."
    source.setPlainText(_JSON)
    assert button.isEnabled()
    keys = window._actions.format.shortcut().toString(_NATIVE)
    assert button.toolTip() == f"Format ({keys})"
    source.setPlainText("   \n")
    assert button.toolTip() == "Input is empty."


# --- The picker ------------------------------------------------------------------


def test_the_picker_defaults_to_two_spaces(qtbot) -> None:
    window = _show(qtbot)
    assert _picker(window).currentText() == "2 spaces"
    assert load_indent_size() == "2 spaces"
    source, output = _editors(window)
    assert source.indent_unit() == "  "
    assert output.indent_unit() == "  "


def test_the_picker_is_remembered_across_launches(qtbot) -> None:
    first = _show(qtbot)
    _choose(_picker(first), "4 spaces")
    assert problems_settings().value(_KEY) == "4 spaces"
    first.close()
    second = _show(qtbot)
    assert _picker(second).currentText() == "4 spaces"
    source, _output = _editors(second)
    assert source.indent_unit() == "    "


def test_an_unknown_saved_choice_falls_back_to_two_spaces(qtbot) -> None:
    settings = QSettings(problems_settings().fileName(), QSettings.Format.IniFormat)
    settings.setValue(_KEY, "3 spaces")
    settings.sync()
    assert load_indent_size() == "2 spaces"
    assert _picker(_show(qtbot)).currentText() == "2 spaces"


@pytest.mark.parametrize(("choice", "unit"), [("4 spaces", "    "), ("Tab", "\t")])
def test_format_writes_the_pickers_indent(qtbot, choice, unit) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    source.setPlainText(_JSON)
    _choose(_picker(window), choice)
    _format(qtbot, window)
    assert output.toPlainText() == format_document(_JSON, "json", "json", indent=unit)


def test_a_new_choice_applies_from_the_next_format_on(qtbot) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    source.setPlainText(_JSON)
    _format(qtbot, window)
    two = output.toPlainText()
    _choose(_picker(window), "4 spaces")
    assert output.toPlainText() == two
    _format(qtbot, window)
    assert output.toPlainText() == format_document(_JSON, "json", "json", indent="    ")


def test_minify_ignores_the_picker(qtbot) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    source.setPlainText(_JSON)
    _choose(_picker(window), "Tab")
    window._actions.minify.trigger()
    qtbot.waitUntil(lambda: not window._formatting, timeout=_WAIT_MS)
    assert output.toPlainText() == format_document(_JSON, "json", "json", minify=True)


def test_the_tab_key_uses_the_pickers_unit(qtbot) -> None:
    window = _show(qtbot)
    source, _output = _editors(window)
    source.setPlainText("a")
    _focus(qtbot, source)
    _choose(_picker(window), "4 spaces")
    _select(source, 1)
    qtbot.keyClick(source, Qt.Key.Key_Tab)
    assert source.toPlainText() == "a   "
    _choose(_picker(window), "Tab")
    qtbot.keyClick(source, Qt.Key.Key_Tab)
    assert source.toPlainText() == "a   \t"


def test_the_overflow_submenu_is_the_same_setting(qtbot) -> None:
    window = _show(qtbot)
    submenu = window.findChild(QMenu, "indentSizeMenu")
    assert isinstance(submenu, QMenu)
    tab = next(item for item in submenu.actions() if item.text() == "Tab")
    tab.trigger()
    assert _picker(window).currentText() == "Tab"
    assert load_indent_size() == "Tab"


# --- YAML ------------------------------------------------------------------------


def _tab_item(window):
    picker = _picker(window)
    model = picker.model()
    assert isinstance(model, QStandardItemModel)
    return model.item(picker.findText("Tab"))


def _tab_menu_action(window) -> QAction:
    submenu = window.findChild(QMenu, "indentSizeMenu")
    assert isinstance(submenu, QMenu)
    return next(item for item in submenu.actions() if item.text() == "Tab")


def test_tab_is_greyed_out_while_output_is_yaml(qtbot) -> None:
    window = _show(qtbot)
    output_format = _format_picker(window, "output")
    assert _tab_item(window).isEnabled()
    _choose(output_format, "YAML")
    item = _tab_item(window)
    assert not item.isEnabled()
    assert item.toolTip() == _YAML_TAB_REASON
    action = _tab_menu_action(window)
    assert not action.isEnabled()
    assert action.toolTip() == _YAML_TAB_REASON
    _choose(output_format, "JSON")
    assert _tab_item(window).isEnabled()
    assert _tab_item(window).toolTip() == ""
    assert _tab_menu_action(window).isEnabled()


def test_a_chosen_tab_writes_yaml_at_two_spaces_and_keeps_showing(qtbot) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    source.setPlainText(_JSON)
    _choose(_picker(window), "Tab")
    _choose(_format_picker(window, "output"), "YAML")
    assert _picker(window).currentText() == "Tab"
    _format(qtbot, window)
    assert output.toPlainText() == format_document(_JSON, "json", "yaml")
    assert "\t" not in output.toPlainText()
    assert _picker(window).currentText() == "Tab"
    assert load_indent_size() == "Tab"


def test_a_yaml_pane_indents_with_two_spaces_when_tab_is_chosen(qtbot) -> None:
    window = _show(qtbot)
    source, output = _editors(window)
    _choose(_picker(window), "Tab")
    _choose(_format_picker(window, "output"), "YAML")
    assert source.indent_unit() == "\t"
    assert output.indent_unit() == "  "
    _choose(_format_picker(window, "input"), "YAML")
    assert source.indent_unit() == "  "
    output.setPlainText("a:\nb: 1\n")
    _focus(qtbot, output)
    output.selectAll()
    window._actions.indent.trigger()
    assert output.toPlainText() == "  a:\n  b: 1\n"
    _select(output, output.document().characterCount() - 1)
    qtbot.keyClick(output, Qt.Key.Key_Tab)
    assert "\t" not in output.toPlainText()


# --- Perf (timing only, no budget) ----------------------------------------------

_FIXTURE_BYTES = 2_400_000


@pytest.mark.perf
def test_indent_and_outdent_on_every_line_of_2_4_mb_are_timed(qtbot) -> None:
    """Time Indent and Outdent over every line of the 2.4 MB Input.

    Timing only: the medians are printed with no budget (Isaac). Each run
    is one undo step: one undo puts the text back.
    """
    text, _rows = text_of_size(_FIXTURE_BYTES, pretty_json)
    size = len(text.encode("utf-8"))
    assert _FIXTURE_BYTES <= size < 2_500_000
    window = _show(qtbot)
    source, _output = _editors(window)
    source.setPlainText(text)
    _focus(qtbot, source)

    def reset() -> None:
        source.document().clearUndoRedoStacks()
        source.selectAll()

    def check_one_step(command: QAction) -> None:
        before = source.toPlainText()
        reset()
        command.trigger()
        assert source.toPlainText() != before
        _one_undo_restores(source, before)

    indent = time_runs(window._actions.indent.trigger, setup=reset)
    check_one_step(window._actions.indent)
    source.setPlainText(text)
    reset()
    window._actions.indent.trigger()
    indented = source.toPlainText()

    def reindent() -> None:
        source.setPlainText(indented)
        reset()

    outdent = time_runs(window._actions.outdent.trigger, setup=reindent)
    assert source.toPlainText() == text
    _one_undo_restores(source, indented)
    reindent()
    check_one_step(window._actions.outdent)
    lines = [
        f"Indent and Outdent on {size:,} B pretty JSON, "
        f"{source.blockCount():,} lines ({sys.platform}; timing only, no budget)"
    ]
    for label, timing in (("Indent", indent), ("Outdent", outdent)):
        samples = ", ".join(f"{sample * 1000:.1f}" for sample in timing.samples)
        lines.append(f"  {label}: median {timing.median * 1000:.1f} ms ({samples})")
    print("\n".join(lines), flush=True)
