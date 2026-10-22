"""Collapsible JSON in the Output pane (#64)."""

import json
import random
import sys
import time
from collections.abc import Callable

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QAction, QImage, QKeySequence, QPalette
from PySide6.QtWidgets import QApplication, QMenu, QToolButton

from galley.app.actions import is_unbuilt
from galley.app.editor import (
    FOLD_MARKERS,
    GUTTER_MIN_WIDTH,
    CodeEditor,
    FoldMarker,
    fold_status_text,
)
from galley.app.main import create_main_window
from galley.app.main_window import MainWindow
from galley.app.shortcuts import (
    COLLAPSE_ALL,
    EXPAND_ALL,
    FOLD,
    UNFOLD,
    native_chord_text,
)
from galley.app.status_message import StatusMessage
from galley.app.theme import EDGE_CONTRAST, composite, contrast_ratio
from galley.core.folds import FoldKind, fold_ranges, line_count

_LANE = 16
# Bill's #64 spec and the budget from Erik and Chief.
_COLLAPSE_BUDGET_S = 0.150
_TEXT_CONTRAST = 4.5
_SOURCE = json.dumps(
    {"name": "galley", "owner": {"login": "x", "id": 7, "site": True}, "tags": [1, 2]}
)
# 0 {  1 "name": ...,  2 "owner": {  3..5 keys  6 },  7 "tags": [
# 8 1,  9 2  10 ]  11 }
_OUTPUT = json.dumps(json.loads(_SOURCE), indent=2) + "\n"


def _window(qtbot) -> MainWindow:
    window = create_main_window()
    qtbot.addWidget(window)
    window.resize(1100, 700)
    window.show()
    qtbot.waitExposed(window)
    return window


def _editors(window: MainWindow) -> tuple[CodeEditor, CodeEditor]:
    source = window.findChild(CodeEditor, "inputEditor")
    output = window.findChild(CodeEditor, "outputEditor")
    assert isinstance(source, CodeEditor)
    assert isinstance(output, CodeEditor)
    return source, output


def _status(window: MainWindow) -> StatusMessage:
    status = window.findChild(StatusMessage, "statusMessage")
    assert isinstance(status, StatusMessage)
    return status


def _action(window: MainWindow, text: str) -> QAction:
    found = [item for item in window.findChildren(QAction) if item.text() == text]
    assert len(found) == 1, text
    return found[0]


def _formatted(qtbot, text: str = _SOURCE) -> tuple[MainWindow, CodeEditor]:
    window = _window(qtbot)
    source, output = _editors(window)
    source.setPlainText(text)
    _action(window, "Format").trigger()
    qtbot.waitUntil(lambda: output.toPlainText() != "", timeout=15000)
    return window, output


def _editor(qtbot, text: str = _OUTPUT) -> CodeEditor:
    editor = CodeEditor()
    qtbot.addWidget(editor)
    editor.resize(520, 360)
    editor.set_folding_enabled(True)
    editor.show()
    qtbot.waitExposed(editor)
    editor.setPlainText(text)
    editor.rescan_folds()
    return editor


def _hidden(editor: CodeEditor) -> list[int]:
    """1-based lines that are hidden."""
    document = editor.document()
    return [
        line + 1
        for line in range(document.blockCount())
        if not document.findBlockByNumber(line).isVisible()
    ]


def _lane_rect(editor: CodeEditor, lane: str, lineno: int) -> QRect:
    left, width = editor.gutter_layout().lanes[lane]
    block = editor.document().findBlockByNumber(lineno - 1)
    top = editor.blockBoundingGeometry(block).translated(editor.contentOffset()).top()
    return QRect(left, int(top), width, editor.fontMetrics().height())


def _click_fold(qtbot, editor: CodeEditor, lineno: int, **kwargs) -> None:
    point = _lane_rect(editor, "fold", lineno).center()
    modifier = kwargs.get("modifier", Qt.KeyboardModifier.NoModifier)
    qtbot.mouseClick(
        editor.line_number_area(), Qt.MouseButton.LeftButton, modifier, point
    )


def _gutter(editor: CodeEditor) -> QImage:
    image = editor.line_number_area().grab().toImage()
    assert image.devicePixelRatio() == 1
    return image


def test_format_marks_every_block_that_spans_lines(qtbot) -> None:
    window, output = _formatted(qtbot)
    assert output.toPlainText() == _OUTPUT
    markers = output.line_markers(FOLD_MARKERS)
    assert sorted(markers) == [1, 3, 8]
    assert all(isinstance(marker, FoldMarker) for marker in markers.values())
    assert [marker.kind for _line, marker in sorted(markers.items())] == [
        FoldKind.OBJECT,
        FoldKind.OBJECT,
        FoldKind.ARRAY,
    ]
    assert not any(marker.folded for marker in markers.values())
    assert output.gutter_layout().lanes["fold"][1] == _LANE
    assert _hidden(output) == []
    for text in ("Expand all", "Collapse all", "Fold", "Unfold"):
        action = _action(window, text)
        assert action.isEnabled()
        assert not is_unbuilt(action)


def test_input_does_not_fold(qtbot) -> None:
    window, _output = _formatted(qtbot)
    source, _ = _editors(window)
    assert not source.folding_enabled()
    assert source.line_markers(FOLD_MARKERS) == {}
    assert source.fold_ranges() == []
    assert source.gutter_layout().lanes["fold"][1] == 0
    assert source.gutter_layout().lanes["markers"][1] == _LANE
    menu = window.findChild(QMenu, "editorMenu")
    assert isinstance(menu, QMenu)
    assert not {"Fold", "Unfold", "Expand all", "Collapse all"} & {
        action.text() for action in menu.actions()
    }


def test_empty_output_has_no_fold_lane_and_nothing_to_fold(qtbot) -> None:
    window = _window(qtbot)
    _source, output = _editors(window)
    assert output.folding_enabled()
    assert output.gutter_layout().lanes["fold"][1] == 0
    collapse = _action(window, "Collapse all")
    expand = _action(window, "Expand all")
    for action in (
        collapse,
        expand,
        _action(window, "Fold"),
        _action(window, "Unfold"),
    ):
        assert not action.isEnabled()
    assert collapse.toolTip() == "Collapse all: nothing to fold"
    assert expand.toolTip() == "Expand all: nothing to fold"


def test_one_line_output_has_nothing_to_fold(qtbot) -> None:
    window, output = _formatted(qtbot, "42")
    assert output.toPlainText().strip() == "42"
    assert output.fold_ranges() == []
    assert output.gutter_layout().lanes["fold"][1] == 0
    assert not _action(window, "Collapse all").isEnabled()


def test_clicking_the_arrow_folds_and_unfolds_the_block(qtbot) -> None:
    window, output = _formatted(qtbot)
    status = _status(window)
    _click_fold(qtbot, output, 3)
    assert output.folded_lines() == {3}
    assert _hidden(output) == [4, 5, 6, 7]
    marker = output.line_markers(FOLD_MARKERS)[3]
    assert isinstance(marker, FoldMarker)
    assert marker.folded
    assert marker.description() == "Unfold object"
    assert output.fold_summary(3) == (" … },", "3 keys")
    assert status.text() == "Folded object at line 3, 3 keys"

    _click_fold(qtbot, output, 3)
    assert output.folded_lines() == set()
    assert _hidden(output) == []
    assert output.line_markers(FOLD_MARKERS)[3].description() == "Fold object"
    assert status.text() == "Unfolded object at line 3, 3 keys"


def test_hidden_lines_are_skipped_in_the_numbers(qtbot) -> None:
    editor = _editor(qtbot)
    editor.fold_line(3)
    numbers = [lineno for lineno, _top in editor._visible_line_tops(editor.rect())]
    assert numbers == [1, 2, 3, 8, 9, 10, 11, 12, 13]


def test_the_folded_line_paints_its_summary(qtbot) -> None:
    editor = _editor(qtbot)
    before = editor.viewport().grab().toImage()
    editor.fold_line(8)
    qtbot.waitUntil(lambda: editor._fold_summaries != [], timeout=2000)
    after = editor.viewport().grab().toImage()
    rect, start = editor._fold_summaries[0]
    assert start == 7
    assert editor.fold_summary(8) == (" … ]", "2 items")
    changed = [
        (x, y)
        for x in range(rect.left(), rect.right())
        for y in range(rect.top(), rect.bottom())
        if after.pixelColor(x, y) != before.pixelColor(x, y)
    ]
    assert changed
    # The summary is painted: the document text did not change.
    assert editor.toPlainText() == _OUTPUT


def test_clicking_the_summary_unfolds(qtbot) -> None:
    editor = _editor(qtbot)
    editor.fold_line(3)
    qtbot.waitUntil(lambda: editor._fold_summaries != [], timeout=2000)
    rect, _start = editor._fold_summaries[0]
    qtbot.mouseClick(editor.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert editor.folded_lines() == set()
    assert _hidden(editor) == []


def test_alt_click_folds_the_block_and_the_blocks_inside(qtbot) -> None:
    editor = _editor(qtbot)
    _click_fold(qtbot, editor, 1, modifier=Qt.KeyboardModifier.AltModifier)
    assert editor.folded_lines() == {1, 3, 8}
    _click_fold(qtbot, editor, 1)
    assert editor.folded_lines() == {3, 8}
    assert _hidden(editor) == [4, 5, 6, 7, 9, 10, 11]
    _click_fold(qtbot, editor, 1, modifier=Qt.KeyboardModifier.AltModifier)
    _click_fold(qtbot, editor, 1, modifier=Qt.KeyboardModifier.AltModifier)
    assert editor.folded_lines() == set()


def test_collapse_all_keeps_the_root_open_and_expand_all_restores(qtbot) -> None:
    window, output = _formatted(qtbot)
    status = _status(window)
    _action(window, "Collapse all").trigger()
    assert output.folded_lines() == {3, 8}
    assert 1 not in output.folded_lines()
    assert output.document().firstBlock().isVisible()
    assert _hidden(output) == [4, 5, 6, 7, 9, 10, 11]
    assert status.text() == "Collapsed 2 blocks"

    _action(window, "Expand all").trigger()
    assert output.folded_lines() == set()
    assert _hidden(output) == []
    assert status.text() == "Expanded 2 blocks"
    assert output.toPlainText() == _OUTPUT


def test_collapse_all_folds_the_children_of_a_root_array(qtbot) -> None:
    editor = _editor(qtbot, json.dumps([{"a": [1]}, {"b": 2}], indent=2) + "\n")
    assert editor.collapse_all() == 3
    # The root stays open, its children fold, and so does what is inside.
    assert editor.folded_lines() == {2, 3, 7}
    assert _hidden(editor) == [3, 4, 5, 6, 8, 9]
    assert editor.expand_all() == 3


def test_fold_and_unfold_act_at_the_caret(qtbot) -> None:
    window, output = _formatted(qtbot)
    output.reveal_line(5)
    _action(window, "Fold").trigger()
    assert output.folded_lines() == {3}
    # The caret was inside the fold, so it moved to the fold's first line.
    assert output.textCursor().blockNumber() == 2
    assert _status(window).text() == "Folded object at line 3, 3 keys"
    _action(window, "Fold").trigger()
    assert output.folded_lines() == {1, 3}
    _action(window, "Unfold").trigger()
    assert output.folded_lines() == {3}
    output.reveal_line(3)
    _action(window, "Unfold").trigger()
    assert output.folded_lines() == set()


def test_revealing_a_hidden_line_unfolds_its_parents(qtbot) -> None:
    editor = _editor(qtbot)
    editor.collapse_all()
    editor.fold_line(1)
    assert editor.folded_lines() == {1, 3, 8}
    editor.reveal_line(9)
    assert editor.folded_lines() == {3}
    assert editor.textCursor().blockNumber() == 8
    assert editor.textCursor().block().isVisible()


def test_copy_while_folded_copies_the_full_text(qtbot) -> None:
    window, output = _formatted(qtbot)
    _action(window, "Collapse all").trigger()
    copy_button = window._output_pane.copy_button
    assert copy_button is not None
    copy_button.click()
    assert QApplication.clipboard().text() == _OUTPUT
    output.selectAll()
    output.copy()
    assert QApplication.clipboard().text() == _OUTPUT
    assert "…" not in QApplication.clipboard().text()


def test_folding_never_touches_the_undo_stack(qtbot) -> None:
    _window, output = _formatted(qtbot)
    document = output.document()
    depth = document.availableUndoSteps()
    revision = document.revision()
    modified = document.isModified()
    output.fold_line(3)
    output.collapse_all()
    output.expand_all()
    output.fold_line(1)
    output.unfold_line(1)
    assert document.availableUndoSteps() == depth
    assert document.revision() == revision
    assert document.isModified() == modified


def test_format_again_comes_back_expanded(qtbot) -> None:
    window, output = _formatted(qtbot)
    output.collapse_all()
    assert output.folded_lines()
    source, _ = _editors(window)
    source.setPlainText(_SOURCE.replace("galley", "galley 2"))
    _action(window, "Format").trigger()
    qtbot.waitUntil(lambda: "galley 2" in output.toPlainText(), timeout=15000)
    assert output.folded_lines() == set()
    assert _hidden(output) == []
    assert sorted(output.line_markers(FOLD_MARKERS)) == [1, 3, 8]


def test_an_edit_in_output_opens_folds_and_finds_ranges_again(qtbot) -> None:
    editor = _editor(qtbot)
    editor.collapse_all()
    cursor = editor.textCursor()
    cursor.setPosition(0)
    cursor.insertText('{\n  "new": [\n    1\n  ],\n  "old": ')
    assert editor.folded_lines() == set()
    assert _hidden(editor) == []
    qtbot.waitUntil(lambda: 2 in editor.line_markers(FOLD_MARKERS), timeout=2000)
    text = editor.toPlainText()
    assert editor.fold_ranges() == fold_ranges(text, "JSON")
    assert line_count(text) == editor.blockCount()


def test_ranges_for_other_lines_are_dropped(qtbot) -> None:
    editor = _editor(qtbot)
    ranges = editor.fold_ranges()
    editor.set_fold_ranges(ranges, editor.blockCount() + 1)
    assert editor.fold_ranges() == []
    assert editor.gutter_layout().lanes["fold"][1] == 0
    editor.set_fold_ranges(ranges, editor.blockCount())
    assert editor.fold_ranges() == ranges


def test_fold_lane_geometry_leaves_the_markers_lane_alone(qtbot) -> None:
    plain = CodeEditor()
    qtbot.addWidget(plain)
    plain.setPlainText(_OUTPUT)
    editor = _editor(qtbot)
    plain_lanes = plain.gutter_layout(plain.line_number_area_width()).lanes
    lanes = editor.gutter_layout().lanes
    assert lanes["markers"] == plain_lanes["markers"] == (0, _LANE)
    numbers_left, numbers_width = lanes["numbers"]
    assert numbers_left == _LANE
    assert lanes["fold"] == (numbers_left + numbers_width, _LANE)
    digits = 3 * editor.fontMetrics().horizontalAdvance("9")
    # Right padding, three digits, and the markers and fold lanes.
    assert editor.line_number_area_width() == max(
        GUTTER_MIN_WIDTH, 8 + digits + 2 * _LANE
    )
    assert editor.line_number_area().width() == editor.line_number_area_width()

    # A problem marker still draws in the markers lane and keeps its tint.
    editor.set_problem_messages({3: "Missing comma."})
    assert editor.marker_at(_lane_rect(editor, "markers", 3).center()).kind == (
        "problem"
    )
    assert editor.marker_at(_lane_rect(editor, "fold", 3).center()).kind == (
        FOLD_MARKERS
    )
    assert editor.gutter_layout().lanes == lanes
    tints = editor._line_tints()
    assert set(tints) == {3}


def test_the_text_does_not_move_when_a_problem_appears(qtbot) -> None:
    editor = _editor(qtbot)
    before = (editor.gutter_layout().lanes, editor.viewportMargins().left())
    editor.set_problem_messages({2: "Bad value."})
    assert (editor.gutter_layout().lanes, editor.viewportMargins().left()) == before


def test_hover_inks_the_arrow_and_shows_a_pointing_hand(qtbot) -> None:
    editor = _editor(qtbot)
    gutter = editor.line_number_area()
    point = _lane_rect(editor, "fold", 3).center()
    qtbot.mouseMove(gutter, point)
    qtbot.waitUntil(lambda: editor._fold_hover == 3, timeout=2000)
    marker = editor.line_markers(FOLD_MARKERS)[3]
    assert isinstance(marker, FoldMarker)
    assert marker.hovered
    assert gutter.cursor().shape() == Qt.CursorShape.PointingHandCursor
    away = _lane_rect(editor, "numbers", 3).center()
    qtbot.mouseMove(gutter, away)
    qtbot.waitUntil(lambda: editor._fold_hover == 0, timeout=2000)
    assert gutter.cursor().shape() != Qt.CursorShape.PointingHandCursor
    hit = editor.marker_at(point)
    assert hit is not None
    assert hit.marker.description() == "Fold object"
    assert editor.marker_at(
        _lane_rect(editor, "fold", 8).center()
    ).marker.description() == ("Fold array")


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_fold_inks_meet_contrast(
    qtbot, install_scheme: Callable[[str], QPalette], scheme: str
) -> None:
    other = install_scheme("dark" if scheme == "light" else "light")
    palette = install_scheme(scheme)
    assert palette.color(QPalette.ColorRole.Base) != other.color(
        QPalette.ColorRole.Base
    )
    editor = _editor(qtbot)
    editor.follow_application_palette()
    editor.fold_line(3)
    base = editor.palette().color(QPalette.ColorRole.Base)
    gutter = composite(editor.gutter_color(), base)
    summary = composite(editor.line_number_color(), base)
    assert contrast_ratio(summary, base) >= _TEXT_CONTRAST
    hover = composite(editor.palette().color(QPalette.ColorRole.Text), gutter)
    assert contrast_ratio(hover, gutter) >= EDGE_CONTRAST

    # The arrow as painted: a pixel inside each triangle, against the gutter.
    image = _gutter(editor)
    for lineno in (1, 3):
        rect = _lane_rect(editor, "fold", lineno)
        center = QPoint(rect.left() + rect.width() // 2, rect.center().y())
        ink = image.pixelColor(center)
        fill = image.pixelColor(rect.left() + 1, rect.top() + 1)
        assert fill.rgb() == gutter.rgb()
        assert ink.rgb() != fill.rgb()
        assert contrast_ratio(ink, fill) >= EDGE_CONTRAST, (scheme, lineno)


def test_actions_have_spec_shortcuts_and_tooltips(qtbot) -> None:
    window, _output = _formatted(qtbot)
    expected = {
        "Fold": ("Ctrl+Alt+[", FOLD),
        "Unfold": ("Ctrl+Alt+]", UNFOLD),
        "Collapse all": ("Ctrl+K, Ctrl+0", COLLAPSE_ALL),
        "Expand all": ("Ctrl+K, Ctrl+J", EXPAND_ALL),
    }
    for text, (portable, sequence) in expected.items():
        action = _action(window, text)
        assert action.shortcut() == sequence
        assert (
            action.shortcut().toString(QKeySequence.SequenceFormat.PortableText)
            == portable
        )
        assert action.shortcutContext() == Qt.ShortcutContext.WindowShortcut
    if sys.platform == "darwin":
        assert native_chord_text(COLLAPSE_ALL) == "⌘K ⌘0"
        assert native_chord_text(EXPAND_ALL) == "⌘K ⌘J"
        assert native_chord_text(FOLD) == "⌥⌘["
    else:
        assert native_chord_text(COLLAPSE_ALL) == "Ctrl+K Ctrl+0"
        assert native_chord_text(EXPAND_ALL) == "Ctrl+K Ctrl+J"
        assert native_chord_text(FOLD) == "Ctrl+Alt+["
    collapse = _action(window, "Collapse all")
    expand = _action(window, "Expand all")
    assert collapse.toolTip() == (
        f"Collapse all blocks in Output ({native_chord_text(COLLAPSE_ALL)})"
    )
    assert expand.toolTip() == (
        f"Expand all blocks in Output ({native_chord_text(EXPAND_ALL)})"
    )
    buttons = {
        button.defaultAction().text(): button
        for button in window.findChildren(QToolButton)
        if button.defaultAction() in (collapse, expand)
    }
    assert buttons["Collapse all"].toolTip() == collapse.toolTip()
    assert buttons["Expand all"].toolTip() == expand.toolTip()
    assert buttons["Collapse all"].accessibleName() == "Collapse all"
    _source, output = _editors(window)
    assert output.accessibleName().endswith(" editor")
    assert native_chord_text(FOLD) in output.accessibleDescription()


def test_the_collapse_all_shortcut_runs_it(qtbot) -> None:
    window, output = _formatted(qtbot)
    output.setFocus()
    qtbot.waitUntil(output.hasFocus, timeout=2000)
    control = Qt.KeyboardModifier.ControlModifier
    qtbot.keyClick(output, Qt.Key.Key_K, control)
    qtbot.keyClick(output, Qt.Key.Key_0, control)
    assert output.folded_lines() == {3, 8}
    qtbot.keyClick(output, Qt.Key.Key_K, control)
    qtbot.keyClick(output, Qt.Key.Key_J, control)
    assert output.folded_lines() == set()
    assert _status(window).text() == "Expanded 2 blocks"


def test_output_context_menu_has_the_fold_commands(qtbot) -> None:
    window, _output = _formatted(qtbot)
    menu = window.findChild(QMenu, "outputEditorMenu")
    assert isinstance(menu, QMenu)
    labels = [action.text() or "|" for action in menu.actions()]
    assert labels[-5:] == ["|", "Fold", "Unfold", "Expand all", "Collapse all"]


def test_fold_actions_are_off_while_formatting(qtbot) -> None:
    window, _output = _formatted(qtbot)
    window._formatting = True
    window._sync_format_button()
    assert not _action(window, "Collapse all").isEnabled()
    assert not _action(window, "Fold").isEnabled()
    window._formatting = False
    window._sync_format_button()
    assert _action(window, "Collapse all").isEnabled()


def test_status_text_uses_singular_counts() -> None:
    ranges = fold_ranges('{\n  "a": [\n    1\n  ]\n}\n', "JSON")
    assert fold_status_text(ranges[0], True) == "Folded object at line 1, 1 key"
    assert fold_status_text(ranges[1], False) == "Unfolded array at line 2, 1 item"


def test_yaml_output_folds_by_indentation(qtbot) -> None:
    editor = _editor(qtbot, "a:\n  b: 1\n  c:\n  - 1\n  - 2\nd: 2\n")
    assert editor.syntax_mode() == "YAML"
    assert sorted(editor.line_markers(FOLD_MARKERS)) == [1, 3]
    editor.fold_line(1)
    assert _hidden(editor) == [2, 3, 4, 5]
    assert editor.fold_summary(1) == (" …", "2 keys")


def test_collapse_all_on_2_mb_is_within_budget(qtbot) -> None:
    records = [
        {
            "id": index,
            "name": f"item {index}",
            "tags": ["a", "b", "c"],
            "owner": {"login": f"user{index}", "site_admin": False},
            "metrics": {"views": index * 3, "scores": [1.5, 2.25, index]},
        }
        for index in range(6100)
    ]
    text = json.dumps({"items": records}, indent=2) + "\n"
    assert len(text.encode("utf-8")) >= 2_000_000
    editor = _editor(qtbot, text)
    assert len(editor.fold_ranges()) > 20_000
    best = float("inf")
    for _ in range(3):
        editor.expand_all()
        editor.viewport().repaint()
        QApplication.processEvents()
        started = time.perf_counter()
        editor.collapse_all()
        editor.viewport().repaint()
        editor.line_number_area().repaint()
        best = min(best, time.perf_counter() - started)
    assert editor.folded_lines() == {2}.union(
        item.start_line + 1 for item in editor.fold_ranges() if item.depth > 1
    )
    assert best <= _COLLAPSE_BUDGET_S, f"Collapse all took {best * 1000:.1f} ms"


def test_lines_above_a_fold_paint_as_qt_paints_them(qtbot) -> None:
    """While folded, the editor paints shown lines itself. They must match."""
    editor = _editor(qtbot)
    editor.setReadOnly(True)
    cursor = editor.textCursor()
    cursor.setPosition(editor.document().findBlockByNumber(1).position() + 4)
    cursor.setPosition(
        editor.document().findBlockByNumber(3).position() + 6,
        cursor.MoveMode.KeepAnchor,
    )
    editor.setTextCursor(cursor)
    stock = editor.viewport().grab().toImage()
    editor.fold_line(8)
    folded = editor.viewport().grab().toImage()
    block = editor.document().findBlockByNumber(7)
    top = int(
        editor.blockBoundingGeometry(block).translated(editor.contentOffset()).top()
    )
    assert top > 0
    differing = [
        (x, y)
        for y in range(top)
        for x in range(stock.width())
        if stock.pixelColor(x, y) != folded.pixelColor(x, y)
    ]
    assert differing == []


def test_any_fold_sequence_hides_exactly_the_folded_lines(qtbot) -> None:
    value = {
        "a": [{"b": [1, {"c": 2}]}, [3, [4, 5]]],
        "d": {"e": {"f": [6]}, "g": 7},
    }
    editor = _editor(qtbot, json.dumps(value, indent=2) + "\n")
    starts = [item.start_line + 1 for item in editor.fold_ranges()]
    generator = random.Random(64)
    for _ in range(200):
        roll = generator.random()
        line = generator.choice(starts)
        if roll < 0.4:
            editor.fold_line(line, nested=roll < 0.1)
        elif roll < 0.8:
            editor.unfold_line(line, nested=roll > 0.7)
        elif roll < 0.9:
            editor.collapse_all()
        else:
            editor.expand_all()
        expected = sorted(
            {
                hidden + 1
                for item in editor.fold_ranges()
                if item.start_line + 1 in editor.folded_lines()
                for hidden in range(item.start_line + 1, item.end_line + 1)
            }
        )
        assert _hidden(editor) == expected
