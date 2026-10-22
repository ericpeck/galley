"""Smoke tests for the Galley window, menus, and process start method."""

import ast
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QSplitter,
)

from galley.app.editor import CodeEditor
from galley.app.main import create_main_window
from galley.app.main_window import (
    FORMAT_BUSY_DESCRIPTION,
    FORMAT_BUSY_TOOLTIP,
    FORMATTING_MESSAGE,
    NOT_YET_MESSAGE,
    PROGRESS_BAR_REVEAL_MS,
    PROGRESS_WIDTH_PX,
    SPINNER_REVEAL_MS,
    SPINNER_WIDTH_PX,
    _shows_own_full_screen_item,
)
from galley.app.pane import PaneRole
from galley.app.parse_session import ParseSession
from galley.app.problems_pane import ProblemsPane
from galley.app.theme import ACCENT, ACCENT_DISABLED

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
    "Show Problems": ["Ctrl+Shift+M"],
    "Stack Panes": ["Ctrl+Alt+L"],
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
    # PySide 6.9 drops the wrapper from QAction.menu() for the Galley menu
    # once the temporary list from QMenuBar.actions() is released. About,
    # Preferences, and Quit use menu roles. The menu is still in the bar;
    # the object name set when it was created still finds it.
    menu = window.findChild(QMenu, "menu" + title.replace(" ", ""))
    assert menu is not None, title
    assert menu.title() == title
    assert menu.menuAction() in window.menuBar().actions()
    return menu


def _labels(menu: QMenu) -> list[str]:
    labels: list[str] = []
    for action in menu.actions():
        labels.append("|" if action.isSeparator() else action.text())
    return labels


def _submenu(menu: QMenu, title: str) -> QMenu:
    # Direct child only. PySide 6.9 invalidates QAction.menu() wrappers.
    name = "menu" + title.replace(" ", "")
    child = menu.findChild(QMenu, name, Qt.FindDirectChildrenOnly)
    assert child is not None, title
    assert child.title() == title
    assert child.menuAction() in menu.actions()
    return child


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
    window_color = window.palette().color(QPalette.ColorRole.Window)
    app_color = QApplication.palette().color(QPalette.ColorRole.Window)
    assert window_color.name() == app_color.name()


def test_menu_bar_matches_spec(qtbot) -> None:
    window = _show(qtbot)
    titles = [action.text() for action in window.menuBar().actions()]
    assert titles == ["Galley", "File", "Edit", "View", "Window", "Help"]

    assert _labels(_menu(window, "Galley")) == [
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
        "Copy As",
        "Paste",
        "Select All",
        "|",
        "Find…",
        "Find and Replace…",
        "|",
        "Escape as String",
        "Unescape String",
        "|",
        "Format",
        "Convert from Python",
    ]
    edit = _menu(window, "Edit")
    assert _labels(_submenu(edit, "Copy As")) == [
        "Original",
        "Masked",
        "Random fake",
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
        "Show Problems",
        "|",
        "Expand all",
        "Collapse all",
        "Fold",
        "Unfold",
        "|",
        "Stack Panes",
        "|",
        "Appearance",
        "|",
        "Enter Full Screen",
    ]
    assert _submenu(view, "Font").actions() == []
    assert _labels(_submenu(view, "Appearance")) == ["System", "Light", "Dark"]
    assert _labels(_submenu(view, "Indent Guides")) == [
        "Off",
        "Lines",
        "Colored by depth",
    ]
    assert _labels(_menu(window, "Window")) == [
        "Pop out into window",
        "Show window",
        "Bring back",
    ]
    assert _labels(_menu(window, "Help")) == ["About Galley"]
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
    # Guides are Coming soon (#91), so the ✓ sits on Off, the real state,
    # until they ship. The spec's default is Colored by depth.
    assert _action(window, "Off").isChecked()
    assert not _action(window, "Colored by depth").isChecked()
    assert not _action(window, "Lines").isChecked()
    assert not _action(window, "Show Whitespace").isChecked()
    assert not _action(window, "Stack Panes").isChecked()
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
    assert input_title.text().startswith("Paste JSON or YAML, or drop a file here.")
    assert input_title.isVisible()
    assert hint is not None
    assert hint.text() == "⌨ ⌘V to paste · Ctrl+V on Windows"
    assert output_title is not None
    assert output_title.text() == "Formatted output will appear here."
    assert output_title.isVisible()
    assert count is not None and count.text() == "Empty"
    assert clear is not None and not clear.isEnabled()
    assert copy is not None and not copy.isEnabled()
    assert format_button is not None and not format_button.isEnabled()
    assert not _action(window, "Format").isEnabled()
    assert format_button.text().startswith("{·} Format")
    assert "Format  " not in format_button.text()
    sheet = format_button.styleSheet()
    assert ACCENT in sheet
    assert "palette(highlighted-text)" in sheet
    assert "palette(light)" in sheet
    light = QApplication.palette().color(QPalette.ColorRole.Light).name()
    assert light == ACCENT_DISABLED.lower()
    assert isinstance(editor, CodeEditor)
    assert isinstance(output, CodeEditor)
    qtbot.waitUntil(lambda: editor.hasFocus())


def test_editors_have_line_number_gutters(qtbot) -> None:
    window = _show(qtbot)
    for name in ("inputEditor", "outputEditor"):
        editor = window.findChild(CodeEditor, name)
        assert isinstance(editor, CodeEditor)
        assert (
            editor.line_number_color().name()
            == editor.palette().color(QPalette.ColorRole.PlaceholderText).name()
        )
        assert (
            editor.gutter_color().name()
            == editor.palette().color(QPalette.ColorRole.AlternateBase).name()
        )
        assert editor.viewportMargins().left() >= 48
        base = editor.palette().color(QPalette.ColorRole.Base)
        app_base = QApplication.palette().color(QPalette.ColorRole.Base)
        assert base.name() == app_base.name()


def test_stub_action_reports_not_yet(qtbot) -> None:
    window = _show(qtbot)
    _action(window, "Find and Replace…").trigger()
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
    status = window.findChild(QLabel, "statusMessage")
    assert status is not None
    qtbot.waitUntil(lambda: status.text() == "1 problem", timeout=15000)
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


def test_focus_caption_reads_the_pane_titles(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(PaneRole, "INPUT", replace(PaneRole.INPUT, title="Original 1"))
    monkeypatch.setattr(PaneRole, "OUTPUT", replace(PaneRole.OUTPUT, title="Changed 1"))
    window = _show(qtbot)
    caption = window.findChild(QLabel, "editingCaption")
    assert caption is not None
    assert caption.text() == "Editing <b>Original 1</b>"
    window.findChild(CodeEditor, "outputEditor").setFocus()
    qtbot.waitUntil(lambda: caption.text() == "Editing <b>Changed 1</b>")
    window.findChild(CodeEditor, "inputEditor").setFocus()
    qtbot.waitUntil(lambda: caption.text() == "Editing <b>Original 1</b>")


def test_format_uses_the_worker_without_a_progress_bar(qtbot) -> None:
    window = _show(qtbot)
    editor = window.findChild(CodeEditor, "inputEditor")
    output = window.findChild(CodeEditor, "outputEditor")
    status = window.findChild(QLabel, "statusMessage")
    bar = window.findChild(QProgressBar, "writeProgress")
    spinner = window.findChild(QProgressBar, "formatSpinner")
    cancel = window.findChild(QPushButton, "cancelButton")
    button = window.findChild(QPushButton, "formatButton")
    assert isinstance(editor, CodeEditor)
    assert isinstance(output, CodeEditor)
    assert status is not None
    assert isinstance(bar, QProgressBar)
    assert isinstance(spinner, QProgressBar)
    assert isinstance(cancel, QPushButton)
    assert isinstance(button, QPushButton)
    editor.setPlainText('{"a": 1}')
    idle_text = button.text()
    _action(window, "Format").trigger()
    assert not bar.isVisible()
    assert not spinner.isVisible()
    assert not cancel.isVisible()
    assert button.text() == FORMATTING_MESSAGE
    assert button.isEnabled()

    def _done() -> bool:
        assert not bar.isVisible()
        return output.toPlainText() != ""

    qtbot.waitUntil(_done, timeout=15000)
    assert output.toPlainText() == '{\n  "a": 1\n}\n'
    assert status.text() == "Valid JSON"
    assert not cancel.isVisible()
    assert not spinner.isVisible()
    assert button.text() == idle_text
    assert button.isEnabled()


def test_quick_format_hides_the_spinner_and_cancel(qtbot, monkeypatch) -> None:
    clock = _freeze_clock(monkeypatch)
    window = _show(qtbot)
    window.start_parse_worker()
    qtbot.waitUntil(
        lambda: (
            window._session.process is not None
            and window._session.process.pid is not None
        ),
        timeout=15000,
    )
    editor = window.findChild(CodeEditor, "inputEditor")
    output = window.findChild(CodeEditor, "outputEditor")
    status = window.findChild(QLabel, "statusMessage")
    bar = window.findChild(QProgressBar, "writeProgress")
    spinner = window.findChild(QProgressBar, "formatSpinner")
    cancel = window.findChild(QPushButton, "cancelButton")
    button = window.findChild(QPushButton, "formatButton")
    assert isinstance(editor, CodeEditor)
    assert isinstance(output, CodeEditor)
    assert status is not None
    assert isinstance(bar, QProgressBar)
    assert isinstance(spinner, QProgressBar)
    assert isinstance(cancel, QPushButton)
    assert isinstance(button, QPushButton)
    editor.setPlainText('{"a": 1}')
    idle_text = button.text()
    clock["now"] = 1_000.0
    _action(window, "Format").trigger()
    assert not spinner.isVisible()
    assert not cancel.isVisible()
    assert not bar.isVisible()
    assert button.text() == FORMATTING_MESSAGE
    assert button.isEnabled()

    def _done() -> bool:
        assert not spinner.isVisible()
        assert not cancel.isVisible()
        assert not bar.isVisible()
        assert status.text() != FORMATTING_MESSAGE
        assert not status.text().startswith(f"{FORMATTING_MESSAGE} ")
        return output.toPlainText() != ""

    qtbot.waitUntil(_done, timeout=15000)
    assert output.toPlainText() == '{\n  "a": 1\n}\n'
    assert status.text() == "Valid JSON"
    assert button.text() == idle_text
    assert button.isEnabled()


def test_busy_indicator_appears_at_the_spinner_delay(qtbot, monkeypatch) -> None:
    clock = _freeze_clock(monkeypatch)
    window = _show(qtbot)
    editor = window.findChild(CodeEditor, "inputEditor")
    status = window.findChild(QLabel, "statusMessage")
    bar = window.findChild(QProgressBar, "writeProgress")
    spinner = window.findChild(QProgressBar, "formatSpinner")
    cancel = window.findChild(QPushButton, "cancelButton")
    button = window.findChild(QPushButton, "formatButton")
    assert isinstance(editor, CodeEditor)
    assert status is not None
    assert isinstance(bar, QProgressBar)
    assert isinstance(spinner, QProgressBar)
    assert isinstance(cancel, QPushButton)
    assert isinstance(button, QPushButton)
    editor.setPlainText('{"a": 1}')
    monkeypatch.setattr(ParseSession, "active_job_id", property(lambda _self: 1))
    window._session.submit = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    started = 1_000.0
    clock["now"] = started
    _action(window, "Format").trigger()
    assert not spinner.isVisible()
    assert not cancel.isVisible()
    assert button.text() == FORMATTING_MESSAGE
    assert button.isEnabled()
    clock["now"] = started + (SPINNER_REVEAL_MS - 1) / 1000
    window._apply_deferred_reveal()
    assert not spinner.isVisible()
    assert not cancel.isVisible()
    assert status.text() != FORMATTING_MESSAGE
    clock["now"] = started + SPINNER_REVEAL_MS / 1000
    window._apply_deferred_reveal()
    assert spinner.isVisible()
    assert not cancel.isVisible()
    assert not bar.isVisible()
    assert spinner.width() == SPINNER_WIDTH_PX
    assert spinner.accessibleName() == "Formatting"
    assert spinner.minimum() == 0
    assert spinner.maximum() == 0
    assert not spinner.isTextVisible()
    assert status.text() == FORMATTING_MESSAGE
    assert button.text() == FORMATTING_MESSAGE
    assert button.isEnabled()
    assert not _action(window, "Format").isEnabled()
    assert editor.isReadOnly()


def test_write_bar_appears_after_the_progress_delay(
    qtbot, monkeypatch, tmp_path: Path
) -> None:
    clock = _freeze_clock(monkeypatch)
    window = _show(qtbot)
    window.start_parse_worker()
    session = window._session
    qtbot.waitUntil(
        lambda: session.process is not None and session.process.pid is not None,
        timeout=15000,
    )
    editor = window.findChild(CodeEditor, "inputEditor")
    status = window.findChild(QLabel, "statusMessage")
    bar = window.findChild(QProgressBar, "writeProgress")
    spinner = window.findChild(QProgressBar, "formatSpinner")
    cancel = window.findChild(QPushButton, "cancelButton")
    button = window.findChild(QPushButton, "formatButton")
    assert isinstance(editor, CodeEditor)
    assert status is not None
    assert isinstance(bar, QProgressBar)
    assert isinstance(spinner, QProgressBar)
    assert isinstance(cancel, QPushButton)
    assert isinstance(button, QPushButton)
    editor.setPlainText('{"a": 1}')
    assert _action(window, "Format").isEnabled()
    started = 2_000.0
    clock["now"] = started
    window._formatting = True
    window._format_started_s = started
    window._sync_format_button()
    destination = tmp_path / "out.json"
    destination.write_bytes(b"{}")
    try:
        text = json.dumps({"blob": "x" * 80000})
        session.submit(
            text,
            "json",
            "json",
            destination=str(destination),
            chunk_size=4096,
            chunk_pause_s=0.2,
        )
        qtbot.waitUntil(
            lambda: session.total > 0 and 0 < session.written < session.total,
            timeout=15000,
        )
        assert not spinner.isVisible()
        assert not cancel.isVisible()
        assert not bar.isVisible()
        clock["now"] = started + SPINNER_REVEAL_MS / 1000
        window._apply_deferred_reveal()
        assert spinner.isVisible()
        assert not cancel.isVisible()
        assert not bar.isVisible()
        assert status.text() == FORMATTING_MESSAGE
        clock["now"] = started + (PROGRESS_BAR_REVEAL_MS - 1) / 1000
        window._apply_deferred_reveal()
        assert not bar.isVisible()
        clock["now"] = started + PROGRESS_BAR_REVEAL_MS / 1000
        window._apply_deferred_reveal()
        assert bar.isVisible()
        assert not spinner.isVisible()
        assert cancel.isVisible()
        assert bar.width() == PROGRESS_WIDTH_PX
        assert bar.accessibleName() == "Formatting progress"
        assert bar.minimum() == 0
        assert bar.maximum() == 0
        assert not bar.isTextVisible()
        assert status.text() == FORMATTING_MESSAGE
        assert "%" not in status.text()
        assert button.text() == FORMATTING_MESSAGE
        assert button.isEnabled()
    finally:
        window.shutdown_parse_worker()


def _freeze_clock(monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(
        "galley.app.main_window._monotonic",
        lambda: clock["now"],
    )
    return clock


def test_configure_runtime_leaves_start_method_unset() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "\n".join(
                [
                    "import multiprocessing",
                    "from galley.app.runtime import configure_runtime",
                    "configure_runtime()",
                    "print(multiprocessing.get_start_method(allow_none=True))",
                ]
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "None"


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


def test_entry_point_uses_freeze_support_without_global_start_method() -> None:
    runtime_tree = ast.parse((APP_ROOT / "runtime.py").read_text(encoding="utf-8"))
    configure_calls = [
        node
        for node in ast.walk(_function(runtime_tree, "configure_runtime"))
        if isinstance(node, ast.Call)
    ]
    assert any(
        _call_name(node) == "multiprocessing.freeze_support" for node in configure_calls
    )
    assert not any(
        _call_name(node) == "multiprocessing.set_start_method"
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


def test_format_click_while_working_does_not_start_another_job(
    qtbot, monkeypatch
) -> None:
    window = _show(qtbot)
    editor = window.findChild(CodeEditor, "inputEditor")
    button = window.findChild(QPushButton, "formatButton")
    assert isinstance(editor, CodeEditor)
    assert isinstance(button, QPushButton)
    submitted: list[str] = []
    monkeypatch.setattr(ParseSession, "active_job_id", property(lambda _self: 1))
    window._session.submit = (  # type: ignore[method-assign]
        lambda text, *_args, **_kwargs: submitted.append(text)
    )
    editor.setPlainText('{"a": 1}')
    idle_text = button.text()
    button.click()
    assert submitted == ['{"a": 1}']
    assert button.isEnabled()
    assert button.text() == FORMATTING_MESSAGE
    button.click()
    _action(window, "Format").trigger()
    assert submitted == ['{"a": 1}']
    window._cancel_format()
    assert button.text() == idle_text
    assert button.isEnabled()


def _held_format(qtbot, monkeypatch):
    """A window whose Format job never finishes on its own."""
    window = _show(qtbot)
    editor = window.findChild(CodeEditor, "inputEditor")
    button = window.findChild(QPushButton, "formatButton")
    assert isinstance(editor, CodeEditor)
    assert isinstance(button, QPushButton)
    monkeypatch.setattr(ParseSession, "active_job_id", property(lambda _self: 1))
    window._session.submit = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    editor.setPlainText('{"a": 1}')
    return window, button


def _assert_format_idle(window, button, idle_text: str) -> None:
    hint = (
        _action(window, "Format")
        .shortcut()
        .toString(QKeySequence.SequenceFormat.NativeText)
    )
    assert hint
    assert button.text() == idle_text
    assert button.accessibleName() == "Format"
    assert button.accessibleDescription() == ""
    assert button.toolTip() == f"Format ({hint})"
    assert not button.testAttribute(Qt.WidgetAttribute.WA_SetCursor)
    assert button.cursor().shape() != Qt.CursorShape.BusyCursor


def _assert_format_busy(button) -> None:
    assert button.isEnabled()
    assert button.text() == FORMATTING_MESSAGE
    assert button.accessibleName() == FORMATTING_MESSAGE
    assert button.accessibleDescription() == FORMAT_BUSY_DESCRIPTION
    assert button.toolTip() == FORMAT_BUSY_TOOLTIP
    assert button.testAttribute(Qt.WidgetAttribute.WA_SetCursor)
    assert button.cursor().shape() == Qt.CursorShape.BusyCursor


def _succeed(window) -> None:
    window._session.finished_job_id = window._accepted_job_id
    window._on_format_succeeded('{\n  "a": 1\n}\n')


def _fail(window) -> None:
    window._on_format_failed("The formatter stopped.")


def _cancel(window) -> None:
    window._cancel_format()


@pytest.mark.parametrize("finish", [_succeed, _fail, _cancel])
def test_format_busy_state_and_its_reset(qtbot, monkeypatch, finish) -> None:
    window, button = _held_format(qtbot, monkeypatch)
    idle_text = button.text()
    _assert_format_idle(window, button, idle_text)
    _action(window, "Format").trigger()
    assert window._formatting
    _assert_format_busy(button)
    finish(window)
    assert not window._formatting
    _assert_format_idle(window, button, idle_text)
    assert button.isEnabled()


def test_format_keeps_its_width_while_working(qtbot, monkeypatch) -> None:
    window, button = _held_format(qtbot, monkeypatch)
    idle_width = button.sizeHint().width()
    _action(window, "Format").trigger()
    assert button.text() == FORMATTING_MESSAGE
    assert button.sizeHint().width() == idle_width
    window.resize(window.width() - 200, window.height())
    qtbot.wait(10)
    assert button.sizeHint().width() == idle_width
    window._cancel_format()
    assert button.sizeHint().width() == idle_width


def test_clear_resets_status_problems_and_counts(qtbot) -> None:
    window = _show(qtbot)
    editor = window.findChild(CodeEditor, "inputEditor")
    status = window.findChild(QLabel, "statusMessage")
    counts = window.findChild(QLabel, "statusCounts")
    pane = window.findChild(ProblemsPane, "problemsPane")
    assert isinstance(editor, CodeEditor)
    assert status is not None and counts is not None
    assert isinstance(pane, ProblemsPane)
    editor.setPlainText('{\n  "price": 4.5\n  "qty": 3\n}\n')
    _action(window, "Format").trigger()
    qtbot.waitUntil(lambda: status.text() == "1 problem", timeout=15000)
    qtbot.waitUntil(lambda: pane.isVisible() and pane.count() == 1, timeout=15000)
    assert editor.problem_lines() == {2}
    window.statusBar().set_counts("5 differences")
    assert counts.isVisible()

    clear = window.findChild(QPushButton, "clearButton")
    assert clear is not None
    clear.click()
    assert editor.toPlainText() == ""
    assert status.text() == "Ready"
    assert pane.count() == 0
    qtbot.waitUntil(lambda: not pane.isVisible())
    assert editor.problem_lines() == set()
    assert counts.text() == ""
    assert not counts.isVisible()


def test_whitespace_only_input_counts_as_empty(qtbot) -> None:
    window = _show(qtbot)
    editor = window.findChild(CodeEditor, "inputEditor")
    status = window.findChild(QLabel, "statusMessage")
    count = window.findChild(QLabel, "inputCount")
    title = window.findChild(QLabel, "inputEmptyTitle")
    format_button = window.findChild(QPushButton, "formatButton")
    assert isinstance(editor, CodeEditor)
    assert status is not None and count is not None and title is not None
    assert format_button is not None
    editor.setPlainText("abc")
    assert _action(window, "Format").isEnabled()
    assert _action(window, "Convert from Python").isEnabled()
    assert not title.isVisible()

    editor.setPlainText("  \n\t\n  ")
    assert count.text() == "Empty"
    assert title.isVisible()
    assert not format_button.isEnabled()
    assert not _action(window, "Format").isEnabled()
    assert not _action(window, "Convert from Python").isEnabled()
    _action(window, "Format").trigger()
    qtbot.wait(200)
    assert status.text() == "Ready"
    _action(window, "Convert from Python").trigger()
    assert status.text() == "Ready"


def test_view_menu_holds_one_enter_full_screen_item(qtbot) -> None:
    window = _show(qtbot)
    view = _menu(window, "View")
    ours = [action for action in view.actions() if action.text() == "Enter Full Screen"]
    assert len(ours) == 1
    assert ours[0] is window._actions.full_screen
    assert ours[0].shortcut() == QKeySequence(QKeySequence.StandardKey.FullScreen)
    # macOS adds its own item to any menu titled View, so cocoa gets none of ours.
    assert _shows_own_full_screen_item("cocoa") is False
    assert _shows_own_full_screen_item("offscreen") is True
    assert _shows_own_full_screen_item("windows") is True
    assert _shows_own_full_screen_item("xcb") is True


def test_cocoa_leaves_enter_full_screen_to_the_system_menu(monkeypatch, qtbot) -> None:
    monkeypatch.setattr("galley.app.main_window._platform_name", lambda: "cocoa")
    window = _show(qtbot)
    view = _menu(window, "View")
    labels = _labels(view)
    assert "Enter Full Screen" not in labels
    assert labels[-1] == "Appearance"
    # The action still tracks the window state for the system item.
    assert window._actions.full_screen.isCheckable()
