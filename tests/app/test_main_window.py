"""Smoke tests for the Galley window, menus, and process start method."""

import ast
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence, QPalette
from PySide6.QtWidgets import QComboBox, QLabel, QMenu, QPushButton, QSplitter

from galley.app.editor import CodeEditor
from galley.app.main import create_main_window
from galley.app.main_window import NOT_YET_MESSAGE
from galley.app.theme import (
    ACCENT,
    ACCENT_DISABLED,
    EDITOR_BG,
    GUTTER_BG,
    TEXT_MUTED,
    TEXT_ON_ACCENT,
    WINDOW_BG,
)

APP_ROOT = Path(__file__).resolve().parents[2] / "src" / "galley" / "app"

# Portable text. Ctrl is Command on macOS.
CUSTOM_SHORTCUTS = {
    "Word Wrap": ["Ctrl+Alt+W"],
    "Close others": ["Ctrl+Alt+Shift+W"],
    "Escape as String": ["Ctrl+Shift+E"],
    "Unescape String": ["Ctrl+Alt+Shift+E"],
    "Show Whitespace": ["Ctrl+Shift+8"],
    "Actual Size": ["Ctrl+0"],
    "Format": ["Ctrl+Return"],
}

STANDARD_SHORTCUTS = {
    "Undo": QKeySequence.StandardKey.Undo,
    "Redo": QKeySequence.StandardKey.Redo,
    "Cut": QKeySequence.StandardKey.Cut,
    "Copy": QKeySequence.StandardKey.Copy,
    "Paste": QKeySequence.StandardKey.Paste,
    "Select All": QKeySequence.StandardKey.SelectAll,
    "Find…": QKeySequence.StandardKey.Find,
    "Find and Replace…": QKeySequence.StandardKey.Replace,
    "Open file…": QKeySequence.StandardKey.Open,
    "Close": QKeySequence.StandardKey.Close,
    "Bigger": QKeySequence.StandardKey.ZoomIn,
    "Smaller": QKeySequence.StandardKey.ZoomOut,
    "Enter Full Screen": QKeySequence.StandardKey.FullScreen,
}


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


def _menu(window, title: str) -> QMenu:
    for action in window.menuBar().actions():
        menu = action.menu()
        if menu is not None and menu.title() == title:
            return menu
    raise AssertionError(title)


def _labels(menu: QMenu) -> list[str]:
    labels: list[str] = []
    for action in menu.actions():
        labels.append("|" if action.isSeparator() else action.text())
    return labels


def _submenu(menu: QMenu, title: str) -> QMenu:
    for action in menu.actions():
        child = action.menu()
        if child is not None and child.title() == title:
            return child
    raise AssertionError(title)


def _portable(action: QAction) -> list[str]:
    return [
        sequence.toString(QKeySequence.SequenceFormat.PortableText)
        for sequence in action.shortcuts()
        if not sequence.isEmpty()
    ]


def _sequence_id(sequence: QKeySequence) -> tuple[int, ...]:
    parts: list[int] = []
    for index in range(sequence.count()):
        part = sequence[index]
        combined = part.toCombined() if hasattr(part, "toCombined") else part
        parts.append(int(combined))
    return tuple(parts)


def test_main_window_opens(qtbot) -> None:
    window = _show(qtbot)
    assert window.isVisible()
    assert window.windowTitle() == "Galley"
    assert window.centralWidget() is not None
    assert window.palette().color(QPalette.ColorRole.Window).name() == WINDOW_BG.lower()


def test_menu_bar_matches_spec(qtbot) -> None:
    window = _show(qtbot)
    titles = [action.text() for action in window.menuBar().actions()]
    assert titles == ["Galley", "File", "Edit", "View", "Window", "Help"]

    assert _labels(_menu(window, "Galley")) == [
        "About Galley",
        "Preferences…",
        "|",
        "Quit",
    ]
    assert _labels(_menu(window, "File")) == ["Open file…"]
    assert _labels(_menu(window, "Edit")) == [
        "Undo",
        "Redo",
        "|",
        "Cut",
        "Copy",
        "Paste",
        "Select All",
        "|",
        "Find…",
        "Find and Replace…",
        "|",
        "Escape as String",
        "Unescape String",
    ]
    view = _menu(window, "View")
    assert _labels(view) == [
        "Font",
        "Bigger",
        "Smaller",
        "Actual Size",
        "|",
        "Word Wrap",
        "Show Whitespace",
        "Indent Guides",
        "|",
        "Enter Full Screen",
    ]
    assert _submenu(view, "Font").actions() == []
    assert _labels(_submenu(view, "Indent Guides")) == [
        "Off",
        "Lines",
        "Coloured by depth",
    ]
    assert _labels(_menu(window, "Window")) == [
        "Pop out into window",
        "Show window",
        "Bring back",
    ]
    assert _labels(_menu(window, "Help")) == []
    assert _labels(window.findChild(QMenu, "tabMenu")) == [
        "Rename",
        "Duplicate",
        "Pop out into window",
        "|",
        "Close",
        "Close others",
        "Close tabs to the right",
    ]


def test_view_defaults_match_spec(qtbot) -> None:
    window = _show(qtbot)
    assert _action(window, "Word Wrap").isChecked()
    assert _action(window, "Coloured by depth").isChecked()
    assert not _action(window, "Off").isChecked()
    assert not _action(window, "Lines").isChecked()
    assert not _action(window, "Show Whitespace").isChecked()
    assert not _action(window, "Show window").isEnabled()
    assert not _action(window, "Bring back").isEnabled()


def test_shortcuts_match_spec_and_do_not_collide(qtbot) -> None:
    window = _show(qtbot)
    for text, expected in CUSTOM_SHORTCUTS.items():
        assert _portable(_action(window, text)) == expected
    for text, standard in STANDARD_SHORTCUTS.items():
        expected = {
            sequence.toString(QKeySequence.SequenceFormat.PortableText)
            for sequence in QKeySequence.keyBindings(standard)
            if not sequence.isEmpty()
        }
        assert set(_portable(_action(window, text))) == expected

    owners: dict[tuple[int, ...], str] = {}
    duplicates: list[str] = []
    for action in window.findChildren(QAction):
        for sequence in action.shortcuts():
            if sequence.isEmpty() or sequence.count() == 0:
                continue
            ident = _sequence_id(sequence)
            previous = owners.get(ident)
            label = action.text() or action.objectName()
            if previous is None:
                owners[ident] = label
            elif previous != label:
                rendered = sequence.toString(QKeySequence.SequenceFormat.PortableText)
                duplicates.append(f"{rendered}: {previous} and {label}")
    assert duplicates == []
    assert _portable(_action(window, "Word Wrap")) != _portable(
        _action(window, "Close others")
    )


def test_splitter_starts_even(qtbot) -> None:
    window = _show(qtbot)
    splitter = window.findChild(QSplitter, "mainSplitter")
    assert isinstance(splitter, QSplitter)
    assert splitter.orientation() == Qt.Orientation.Horizontal
    assert splitter.count() == 2

    def _even() -> bool:
        sizes = splitter.sizes()
        total = sum(sizes)
        if total <= 200:
            return False
        return abs(sizes[0] - sizes[1]) / total < 0.05

    qtbot.waitUntil(_even)
    ratio = window.findChild(QLabel, "splitRatio")
    assert ratio is not None
    parts = ratio.text().split()
    assert len(parts) == 2
    assert abs(int(parts[0]) - 50) <= 1
    assert abs(int(parts[1]) - 50) <= 1


def test_empty_state(qtbot) -> None:
    window = _show(qtbot)
    status = window.findChild(QLabel, "statusMessage")
    direction = window.findChild(QLabel, "directionLabel")
    caption = window.findChild(QLabel, "editingCaption")
    input_title = window.findChild(QLabel, "inputEmptyTitle")
    output_title = window.findChild(QLabel, "outputEmptyTitle")
    hint = window.findChild(QLabel, "inputEmptyHint")
    count = window.findChild(QLabel, "inputCount")
    clear = window.findChild(QPushButton, "clearButton")
    copy = window.findChild(QPushButton, "outputCopyButton")
    format_button = window.findChild(QPushButton, "formatButton")
    editor = window.findChild(CodeEditor, "inputEditor")
    output = window.findChild(CodeEditor, "outputEditor")
    assert status is not None and status.text() == "Ready"
    assert direction is not None and direction.text() == "JSON → JSON"
    assert caption is not None and "Input 1" in caption.text()
    assert input_title is not None
    assert input_title.text() == "Paste JSON or YAML, or drop a file here"
    assert input_title.isVisible()
    assert hint is not None and "Ctrl+V on Windows" in hint.text()
    assert output_title is not None
    assert output_title.text() == "Formatted output will appear here."
    assert output_title.isVisible()
    assert count is not None and count.text() == "Empty"
    assert clear is not None and not clear.isEnabled()
    assert copy is not None and not copy.isEnabled()
    assert format_button is not None and not format_button.isEnabled()
    assert not _action(window, "Format").isEnabled()
    assert "Format" in format_button.text()
    sheet = format_button.styleSheet()
    assert ACCENT in sheet
    assert ACCENT_DISABLED in sheet
    assert TEXT_ON_ACCENT in sheet
    assert isinstance(editor, CodeEditor)
    assert isinstance(output, CodeEditor)
    qtbot.waitUntil(lambda: editor.hasFocus())


def test_editors_have_line_number_gutters(qtbot) -> None:
    window = _show(qtbot)
    for name in ("inputEditor", "outputEditor"):
        editor = window.findChild(CodeEditor, name)
        assert isinstance(editor, CodeEditor)
        assert editor.line_number_color().name() == TEXT_MUTED.lower()
        assert editor.gutter_color().name() == GUTTER_BG.lower()
        assert editor.viewportMargins().left() >= 48
        base = editor.palette().color(QPalette.ColorRole.Base).name()
        assert base == EDITOR_BG.lower()


def test_stub_action_reports_not_yet(qtbot) -> None:
    window = _show(qtbot)
    _action(window, "Open file…").trigger()
    assert NOT_YET_MESSAGE.lower().startswith("not yet")
    assert window.statusBar().currentMessage() == NOT_YET_MESSAGE
    output = window.findChild(CodeEditor, "outputEditor")
    assert isinstance(output, CodeEditor)
    assert output.toPlainText() == ""


def test_typing_enables_format_without_writing_output(qtbot) -> None:
    window = _show(qtbot)
    editor = window.findChild(CodeEditor, "inputEditor")
    output = window.findChild(CodeEditor, "outputEditor")
    assert isinstance(editor, CodeEditor)
    assert isinstance(output, CodeEditor)
    qtbot.waitUntil(lambda: editor.hasFocus())
    qtbot.keyClicks(editor, "abc")
    assert _action(window, "Format").isEnabled()
    count = window.findChild(QLabel, "inputCount")
    assert count is not None
    assert count.text() == "1 line · 3 characters"
    title = window.findChild(QLabel, "inputEmptyTitle")
    assert title is not None and not title.isVisible()
    _action(window, "Format").trigger()
    assert window.statusBar().currentMessage() == NOT_YET_MESSAGE
    assert output.toPlainText() == ""
    clear = window.findChild(QPushButton, "clearButton")
    assert clear is not None
    clear.click()
    assert editor.toPlainText() == ""
    assert count.text() == "Empty"
    assert not _action(window, "Format").isEnabled()


def test_direction_follows_format_pickers(qtbot) -> None:
    window = _show(qtbot)
    output_format = window.findChild(QComboBox, "outputFormat")
    direction = window.findChild(QLabel, "directionLabel")
    assert output_format is not None
    assert direction is not None
    output_format.setCurrentText("YAML")
    assert direction.text() == "JSON → YAML"


def test_word_wrap_toggles_both_editors(qtbot) -> None:
    window = _show(qtbot)
    action = _action(window, "Word Wrap")
    editors = [
        window.findChild(CodeEditor, "inputEditor"),
        window.findChild(CodeEditor, "outputEditor"),
    ]
    assert all(isinstance(editor, CodeEditor) for editor in editors)
    assert action.isChecked()
    for editor in editors:
        assert isinstance(editor, CodeEditor)
        assert editor.lineWrapMode() == CodeEditor.LineWrapMode.WidgetWidth
    action.trigger()
    assert not action.isChecked()
    for editor in editors:
        assert isinstance(editor, CodeEditor)
        assert editor.lineWrapMode() == CodeEditor.LineWrapMode.NoWrap


def test_focus_caption_follows_the_editor(qtbot) -> None:
    window = _show(qtbot)
    output = window.findChild(CodeEditor, "outputEditor")
    caption = window.findChild(QLabel, "editingCaption")
    assert isinstance(output, CodeEditor)
    assert caption is not None
    output.setFocus()
    qtbot.waitUntil(lambda: "Output 1" in caption.text())
    window.findChild(CodeEditor, "inputEditor").setFocus()
    qtbot.waitUntil(lambda: "Input 1" in caption.text())


def test_configure_runtime_sets_spawn() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "\n".join(
                [
                    "from galley.app.runtime import configure_runtime",
                    "import multiprocessing",
                    "configure_runtime()",
                    "print(multiprocessing.get_start_method())",
                ]
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "spawn"


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return f"{func.value.id}.{func.attr}"
    return None


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def test_entry_point_uses_freeze_support_and_spawn() -> None:
    runtime_tree = ast.parse((APP_ROOT / "runtime.py").read_text(encoding="utf-8"))
    configure_calls = [
        node
        for node in ast.walk(_function(runtime_tree, "configure_runtime"))
        if isinstance(node, ast.Call)
    ]
    assert any(
        _call_name(node) == "multiprocessing.freeze_support" for node in configure_calls
    )
    assert any(
        _call_name(node) == "multiprocessing.set_start_method"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "spawn"
        for node in configure_calls
    )

    main_tree = ast.parse((APP_ROOT / "main.py").read_text(encoding="utf-8"))
    main_body = _function(main_tree, "main").body
    if (
        main_body
        and isinstance(main_body[0], ast.Expr)
        and isinstance(main_body[0].value, ast.Constant)
        and isinstance(main_body[0].value.value, str)
    ):
        main_body = main_body[1:]
    first_statement = main_body[0]
    assert isinstance(first_statement, ast.Expr)
    assert isinstance(first_statement.value, ast.Call)
    assert _call_name(first_statement.value) == "configure_runtime"
