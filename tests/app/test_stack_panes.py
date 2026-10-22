"""View › Stack Panes replaces the divider layout button."""

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction, QKeySequence, QTextCursor
from PySide6.QtWidgets import QLabel, QMenu, QSplitter, QToolButton, QWidget

from galley.app.editor import CodeEditor
from galley.app.main import create_main_window
from galley.app.problem_text import display_problem
from galley.app.problems_pane import ProblemsPane
from galley.app.problems_settings import load_stacked_panes, problems_settings
from galley.app.shortcuts import STACK_PANES, STACK_PANES_ALT
from galley.app.splitter import RESET_NAME
from galley.core.problems import Problem


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
    menu = window.findChild(QMenu, "menu" + title.replace(" ", ""))
    assert menu is not None, title
    return menu


def _labels(menu: QMenu) -> list[str]:
    return ["|" if action.isSeparator() else action.text() for action in menu.actions()]


def _splitter(window) -> QSplitter:
    splitter = window.findChild(QSplitter, "mainSplitter")
    assert isinstance(splitter, QSplitter)
    return splitter


def _rail(splitter: QSplitter) -> QWidget:
    handle = splitter.handle(1)
    assert isinstance(handle, QWidget)
    return handle


def _ratio(splitter: QSplitter) -> float:
    sizes = splitter.sizes()
    total = sum(sizes)
    assert total > 0
    return sizes[0] / total


def _wait_ready(qtbot, splitter: QSplitter) -> None:
    qtbot.waitUntil(lambda: sum(splitter.sizes()) > 600)


def _set_share(splitter: QSplitter, first: float) -> None:
    total = sum(splitter.sizes())
    leading = max(1, int(total * first))
    splitter.setSizes([leading, max(1, total - leading)])


def _editor(window, name: str) -> CodeEditor:
    editor = window.findChild(CodeEditor, name)
    assert isinstance(editor, CodeEditor)
    return editor


def _origin(widget: QWidget, ancestor: QWidget) -> QPoint:
    return widget.mapTo(ancestor, QPoint(0, 0))


def _open_problems(qtbot, window) -> ProblemsPane:
    pane = window.findChild(ProblemsPane, "problemsPane")
    assert isinstance(pane, ProblemsPane)
    pane.set_problems((display_problem(Problem("missing_comma", 2)),))
    show = _action(window, "Show Problems")
    show.trigger()
    show.trigger()
    qtbot.waitUntil(lambda: pane.isVisible() and pane.width() >= 200)
    return pane


def test_divider_keeps_the_even_split_and_drops_the_layout_button(qtbot) -> None:
    window = _show(qtbot)
    splitter = _splitter(window)
    _wait_ready(qtbot, splitter)
    rail = _rail(splitter)
    assert window.findChild(QToolButton, "layoutButton") is None
    assert rail.findChild(QToolButton, "layoutButton") is None
    assert window.findChild(QToolButton, "swapButton") is None
    names = [button.accessibleName() for button in window.findChildren(QToolButton)]
    assert "Layout" not in names
    assert "Swap" not in names

    reset = rail.findChild(QToolButton, "resetSplitButton")
    assert isinstance(reset, QToolButton)
    assert reset.accessibleName() == RESET_NAME
    assert reset.toolTip() == RESET_NAME
    assert reset.width() == 24 and reset.height() == 24

    ratio = rail.findChild(QLabel, "splitRatio")
    assert isinstance(ratio, QLabel)
    assert ratio.text().split() == ["50", "50"]
    assert not reset.isEnabled()
    assert ratio.font().pixelSize() == 11
    _set_share(splitter, 0.49)
    qtbot.waitUntil(lambda: ratio.text().split()[0] != "50")
    assert reset.isEnabled()
    qtbot.mouseClick(reset, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: abs(_ratio(splitter) - 0.5) < 0.05)
    assert not reset.isEnabled() or ratio.text().split() != ["50", "50"]
    _set_share(splitter, 0.8)
    qtbot.waitUntil(lambda: _ratio(splitter) > 0.7)
    qtbot.mouseDClick(ratio, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: abs(_ratio(splitter) - 0.5) < 0.05)

    _action(window, "Stack Panes").trigger()
    assert splitter.orientation() == Qt.Orientation.Vertical
    ratio = rail.findChild(QLabel, "splitRatio")
    assert isinstance(ratio, QLabel)
    _set_share(splitter, 0.25)
    qtbot.waitUntil(lambda: _ratio(splitter) < 0.35)
    qtbot.mouseDClick(ratio, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: abs(_ratio(splitter) - 0.5) < 0.05)


def test_stack_panes_follows_show_problems(qtbot) -> None:
    window = _show(qtbot)
    labels = _labels(_menu(window, "View"))
    show = labels.index("Show Problems")
    # The fold section (#64) sits between Show Problems and Stack Panes.
    assert labels[show + 1 : show + 8] == [
        "|",
        "Expand all",
        "Collapse all",
        "Fold",
        "Unfold",
        "|",
        "Stack Panes",
    ]
    action = _action(window, "Stack Panes")
    assert action.isCheckable()
    assert not action.isChecked()
    # Ctrl+\ comes first, so it is the one the menu shows.
    assert action.shortcuts() == [STACK_PANES, STACK_PANES_ALT]
    assert action.shortcut() == STACK_PANES
    portable = QKeySequence.SequenceFormat.PortableText
    assert [key.toString(portable) for key in action.shortcuts()] == [
        "Ctrl+\\",
        "Ctrl+Shift+L",
    ]


def test_stack_panes_toggles_orientation_and_the_shortcut(qtbot) -> None:
    window = _show(qtbot)
    splitter = _splitter(window)
    action = _action(window, "Stack Panes")
    input_pane = window.findChild(QWidget, "inputPane")
    output_pane = window.findChild(QWidget, "outputPane")
    assert isinstance(input_pane, QWidget)
    assert isinstance(output_pane, QWidget)
    assert splitter.orientation() == Qt.Orientation.Horizontal
    assert _origin(input_pane, window).x() < _origin(output_pane, window).x()

    action.trigger()
    assert action.isChecked()
    assert splitter.orientation() == Qt.Orientation.Vertical
    assert _origin(input_pane, window).y() < _origin(output_pane, window).y()

    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    qtbot.keySequence(window, QKeySequence("Ctrl+\\"))
    assert not action.isChecked()
    assert splitter.orientation() == Qt.Orientation.Horizontal
    assert _origin(input_pane, window).x() < _origin(output_pane, window).x()

    qtbot.keySequence(
        window,
        QKeySequence(Qt.ControlModifier | Qt.Key.Key_Backslash),
    )
    assert action.isChecked()
    assert splitter.orientation() == Qt.Orientation.Vertical

    control = Qt.KeyboardModifier.ControlModifier
    shift = Qt.KeyboardModifier.ShiftModifier
    qtbot.keyClick(window, Qt.Key.Key_L, control | shift)
    assert not action.isChecked()
    assert splitter.orientation() == Qt.Orientation.Horizontal


def test_toggle_keeps_text_cursor_scroll_and_ratio(qtbot) -> None:
    window = _show(qtbot)
    splitter = _splitter(window)
    _wait_ready(qtbot, splitter)
    _action(window, "Word Wrap").trigger()
    long_line = "m" * 600
    input_text = "\n".join([long_line, *[f"in {index:03d}" for index in range(400)]])
    output_text = "\n".join([long_line, *[f"out {index:03d}" for index in range(400)]])
    source = _editor(window, "inputEditor")
    result = _editor(window, "outputEditor")
    source.setPlainText(input_text)
    result.setPlainText(output_text)
    _select(source, 200, 220)
    _select(result, 250, 280)
    qtbot.waitUntil(
        lambda: (
            source.verticalScrollBar().maximum() > 200
            and source.horizontalScrollBar().maximum() > 80
            and result.verticalScrollBar().maximum() > 200
            and result.horizontalScrollBar().maximum() > 80
        )
    )
    _set_share(splitter, 0.6)
    qtbot.waitUntil(lambda: abs(_ratio(splitter) - 0.6) < 0.04)
    kept = _ratio(splitter)
    _scroll(source, 120, 40)
    _scroll(result, 80, 55)

    _action(window, "Stack Panes").trigger()
    qtbot.waitUntil(lambda: _views_match(source, result, kept, splitter))
    _assert_views(source, result, input_text, output_text, kept, splitter)
    _action(window, "Stack Panes").trigger()
    qtbot.waitUntil(lambda: _views_match(source, result, kept, splitter))
    _assert_views(source, result, input_text, output_text, kept, splitter)


def test_problems_pane_stays_on_the_left(qtbot) -> None:
    window = _show(qtbot)
    pane = _open_problems(qtbot, window)
    splitter = _splitter(window)
    problems = window.findChild(QSplitter, "problemsSplitter")
    assert isinstance(problems, QSplitter)
    input_pane = window.findChild(QWidget, "inputPane")
    output_pane = window.findChild(QWidget, "outputPane")
    assert isinstance(input_pane, QWidget)
    assert isinstance(output_pane, QWidget)

    def _left() -> None:
        assert problems.orientation() == Qt.Orientation.Horizontal
        assert problems.widget(0) is pane
        assert problems.widget(1) is splitter
        origin = _origin(pane, window).x()
        assert origin < _origin(splitter, window).x()
        assert origin < _origin(input_pane, window).x()
        assert origin < _origin(output_pane, window).x()

    _left()
    _action(window, "Stack Panes").trigger()
    assert splitter.orientation() == Qt.Orientation.Vertical
    _left()
    _action(window, "Stack Panes").trigger()
    assert splitter.orientation() == Qt.Orientation.Horizontal
    _left()


def test_stack_panes_persists_across_a_new_window(qtbot) -> None:
    window = _show(qtbot)
    _action(window, "Stack Panes").trigger()
    assert _splitter(window).orientation() == Qt.Orientation.Vertical
    assert problems_settings().contains("view/stackPanes")
    assert load_stacked_panes() is True
    window.close()

    again = _show(qtbot)
    assert _action(again, "Stack Panes").isChecked()
    assert _splitter(again).orientation() == Qt.Orientation.Vertical
    _action(again, "Stack Panes").trigger()
    assert not _action(again, "Stack Panes").isChecked()
    assert _splitter(again).orientation() == Qt.Orientation.Horizontal
    again.close()

    restored = _show(qtbot)
    assert not _action(restored, "Stack Panes").isChecked()
    assert _splitter(restored).orientation() == Qt.Orientation.Horizontal


def _scroll(editor: CodeEditor, vertical: int, horizontal: int) -> None:
    editor.verticalScrollBar().setValue(vertical)
    editor.horizontalScrollBar().setValue(horizontal)
    assert editor.verticalScrollBar().value() == vertical
    assert editor.horizontalScrollBar().value() == horizontal


def _select(editor: CodeEditor, anchor: int, position: int) -> None:
    cursor = editor.textCursor()
    cursor.setPosition(anchor)
    cursor.setPosition(position, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)


def _views_match(
    source: CodeEditor,
    result: CodeEditor,
    kept: float,
    splitter: QSplitter,
) -> bool:
    return (
        source.textCursor().anchor() == 200
        and source.textCursor().position() == 220
        and result.textCursor().anchor() == 250
        and result.textCursor().position() == 280
        and source.verticalScrollBar().value() == 120
        and source.horizontalScrollBar().value() == 40
        and result.verticalScrollBar().value() == 80
        and result.horizontalScrollBar().value() == 55
        and abs(_ratio(splitter) - kept) < 0.05
    )


def _assert_views(
    source: CodeEditor,
    result: CodeEditor,
    input_text: str,
    output_text: str,
    kept: float,
    splitter: QSplitter,
) -> None:
    assert source.toPlainText() == input_text
    assert result.toPlainText() == output_text
    assert source.textCursor().anchor() == 200
    assert source.textCursor().position() == 220
    assert result.textCursor().anchor() == 250
    assert result.textCursor().position() == 280
    assert source.verticalScrollBar().value() == 120
    assert source.horizontalScrollBar().value() == 40
    assert result.verticalScrollBar().value() == 80
    assert result.horizontalScrollBar().value() == 55
    assert abs(_ratio(splitter) - kept) < 0.05


def test_split_ratio_persists_across_a_new_window(qtbot) -> None:
    window = _show(qtbot)
    splitter = _splitter(window)
    _wait_ready(qtbot, splitter)
    assert abs(_ratio(splitter) - 0.5) < 0.05
    _set_share(splitter, 0.7)
    qtbot.waitUntil(lambda: _ratio(splitter) > 0.65)
    assert problems_settings().contains("view/splitSizes")
    window.close()

    again = _show(qtbot)
    splitter = _splitter(again)
    _wait_ready(qtbot, splitter)
    qtbot.wait(100)
    assert abs(_ratio(splitter) - 0.7) < 0.05
    ratio = _rail(splitter).findChild(QLabel, "splitRatio")
    reset = _rail(splitter).findChild(QToolButton, "resetSplitButton")
    assert isinstance(ratio, QLabel)
    assert isinstance(reset, QToolButton)
    assert ratio.text().split() == ["70", "30"]
    assert ratio.accessibleName() == "70 / 30"
    assert reset.isEnabled()

    qtbot.mouseClick(reset, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: abs(_ratio(splitter) - 0.5) < 0.05)
    again.close()

    third = _show(qtbot)
    splitter = _splitter(third)
    _wait_ready(qtbot, splitter)
    qtbot.wait(100)
    assert abs(_ratio(splitter) - 0.5) < 0.05
    ratio = _rail(splitter).findChild(QLabel, "splitRatio")
    assert isinstance(ratio, QLabel)
    assert ratio.text().split() == ["50", "50"]


def test_dragging_the_rail_saves_the_split(qtbot) -> None:
    window = _show(qtbot)
    splitter = _splitter(window)
    _wait_ready(qtbot, splitter)
    rail = _rail(splitter)
    start = QPoint(rail.width() // 2, 4)
    qtbot.mousePress(rail, Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(rail, QPoint(start.x() + 150, start.y()))
    qtbot.mouseRelease(rail, Qt.MouseButton.LeftButton, pos=QPoint(start.x() + 150, 4))
    qtbot.waitUntil(lambda: _ratio(splitter) > 0.55)
    saved = problems_settings().value("view/splitSizes")
    assert saved is not None
    parts = saved if isinstance(saved, list) else str(saved).split(",")
    first, second = (int(part) for part in parts)
    assert first > second
