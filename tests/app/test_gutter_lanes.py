"""Gutter lanes: numbers, fold, and markers."""

from collections.abc import Callable

import pytest
from PySide6.QtCore import QRect
from PySide6.QtGui import QAction, QColor, QImage, QPainter, QPalette, QTextCursor
from PySide6.QtWidgets import QTextEdit

from galley.app.editor import (
    CHANGE_MARKERS,
    FOLD_MARKERS,
    GUTTER_LANES,
    GUTTER_MIN_WIDTH,
    PROBLEM_MARKER,
    PROBLEM_MARKERS,
    CodeEditor,
    LineMarker,
)
from galley.app.main import create_main_window
from galley.app.main_window import MainWindow
from galley.app.problems_pane import ProblemsPane
from galley.app.status_message import StatusMessage
from galley.app.theme import error_color, is_dark

_TEXT = "one\ntwo\nthree\nfour\nfive\n"


class _Box(LineMarker):
    """Fills its lane with the palette's Highlight color, which is opaque."""

    def __init__(self) -> None:
        self.painted: list[int] = []

    def paint(self, painter: QPainter, rect: QRect, palette: QPalette) -> None:
        self.painted.append(rect.top())
        painter.fillRect(rect, palette.color(QPalette.ColorRole.Highlight))


class _Tinted(_Box):
    """A box that also bands its line in Highlight."""

    def line_tint(self, palette: QPalette) -> QColor | None:
        return palette.color(QPalette.ColorRole.Highlight)


def _band_line(editor: CodeEditor, selection: QTextEdit.ExtraSelection) -> int:
    """The 1-based line a band covers. A tint band runs on to the start of
    the next line, so its line is where the selection starts."""
    start = selection.cursor.selectionStart()
    return editor.document().findBlock(start).blockNumber() + 1


def _tints(editor: CodeEditor) -> dict[int, int]:
    """Line bands other than the current-line band."""
    midlight = editor.palette().color(QPalette.ColorRole.Midlight)
    return {
        _band_line(editor, selection): selection.format.background().color().rgb()
        for selection in editor.extraSelections()
        if selection.format.background().color() != midlight
    }


def _editor(qtbot, text: str = _TEXT) -> CodeEditor:
    editor = CodeEditor()
    qtbot.addWidget(editor)
    editor.resize(420, 200)
    editor.show()
    qtbot.waitExposed(editor)
    editor.setPlainText(text)
    return editor


def _gutter(editor: CodeEditor) -> QImage:
    image = editor.line_number_area().grab().toImage()
    assert image.devicePixelRatio() == 1
    return image


def _line_top(editor: CodeEditor, lineno: int) -> int:
    block = editor.document().findBlockByNumber(lineno - 1)
    geometry = editor.blockBoundingGeometry(block).translated(editor.contentOffset())
    return int(geometry.top())


def _lane_pixels(editor: CodeEditor, image: QImage, lane: str, lineno: int) -> set[int]:
    left, width = editor.gutter_layout().lanes[lane]
    top = _line_top(editor, lineno)
    height = editor.fontMetrics().height()
    # Outside the image, pixelColor returns an invalid (black) color.
    assert 0 <= top and top + height <= image.height(), (
        f"line {lineno} (y {top}..{top + height}) is outside the "
        f"{image.height()} px gutter grab"
    )
    return {
        image.pixelColor(x, y).rgb()
        for x in range(left, left + width)
        for y in range(top, top + height)
    }


def _text_x(editor: CodeEditor) -> tuple[int, int, int]:
    """Gutter width, numbers-lane left edge, and the text viewport's x."""
    return (
        editor.line_number_area().width(),
        editor.gutter_layout().lanes["numbers"][0],
        editor.viewport().geometry().x(),
    )


def test_lanes_are_listed_once_left_to_right() -> None:
    assert [lane.name for lane in GUTTER_LANES] == ["markers", "numbers", "fold"]
    kinds = [kind for lane in GUTTER_LANES for kind in lane.kinds]
    assert kinds == [PROBLEM_MARKERS, CHANGE_MARKERS, FOLD_MARKERS]
    assert [lane.name for lane in GUTTER_LANES if lane.reserve] == ["markers"]


def test_the_markers_lane_keeps_its_width_with_no_markers(qtbot) -> None:
    editor = _editor(qtbot)
    width = editor.line_number_area_width()
    layout = editor.gutter_layout()

    assert layout.lanes["markers"] == (0, 16)
    assert layout.lanes["numbers"] == (16, width - 16 - 8)
    assert layout.lanes["fold"][1] == 0


def test_the_text_does_not_move_when_the_first_problem_appears(qtbot) -> None:
    editor = _editor(qtbot)
    clean = _text_x(editor)
    # The digits and reserved column, not the 50 px minimum, set the width.
    assert clean[0] > GUTTER_MIN_WIDTH

    editor.set_problem_lines({1})
    assert _text_x(editor) == clean

    editor.set_problem_lines(set())
    assert _text_x(editor) == clean


def test_the_fold_lane_takes_no_width_until_used(qtbot) -> None:
    editor = _editor(qtbot)
    width = editor.line_number_area_width()
    editor.set_problem_lines({2})
    editor.set_line_markers(CHANGE_MARKERS, {3: _Box()})
    assert editor.gutter_layout().lanes["fold"][1] == 0
    assert editor.line_number_area_width() == width

    editor.set_line_markers(FOLD_MARKERS, {1: _Box()})
    assert editor.gutter_layout().lanes["fold"][1] == 16
    assert editor.line_number_area_width() == width + 16

    editor.clear_line_markers(FOLD_MARKERS)
    assert editor.gutter_layout().lanes["fold"][1] == 0
    assert editor.line_number_area_width() == width


def test_change_markers_share_the_column_and_draw_on_their_line(
    qtbot, scheme_palette: QPalette
) -> None:
    editor = _editor(qtbot)
    editor.set_problem_lines({2})
    before = _text_x(editor)
    before_image = _gutter(editor)

    box = _Box()
    editor.set_line_markers(CHANGE_MARKERS, {4: box})

    assert _text_x(editor) == before
    assert editor.viewportMargins().left() == before[0]
    image = _gutter(editor)
    fill = scheme_palette.color(QPalette.ColorRole.Highlight).rgb()
    gutter = editor.gutter_color().rgb()
    assert _lane_pixels(editor, image, "markers", 4) == {fill}
    assert _lane_pixels(editor, image, "markers", 3) == {gutter}
    assert len(box.painted) == 1

    editor.clear_line_markers(CHANGE_MARKERS)

    assert _text_x(editor) == before
    assert editor.line_markers(CHANGE_MARKERS) == {}
    assert _gutter(editor) == before_image


def test_problem_markers_draw_on_their_lines(qtbot, scheme_palette: QPalette) -> None:
    editor = _editor(qtbot)
    editor.set_problem_lines({2, 4})

    assert editor.problem_lines() == {2, 4}
    assert editor.line_markers(PROBLEM_MARKERS) == {
        2: PROBLEM_MARKER,
        4: PROBLEM_MARKER,
    }
    image = _gutter(editor)
    error = error_color(scheme_palette).rgb()
    for lineno in (2, 4):
        assert error in _lane_pixels(editor, image, "markers", lineno)
    for lineno in (1, 3, 5):
        assert error not in _lane_pixels(editor, image, "markers", lineno)

    editor.set_problem_lines(set())

    assert editor.problem_lines() == set()
    assert editor.gutter_layout().lanes["markers"] == (0, 16)


@pytest.mark.parametrize(
    "change_first", [False, True], ids=["problem-first", "change-first"]
)
def test_a_problem_outranks_a_change_on_the_same_line(
    qtbot, scheme_palette: QPalette, change_first: bool
) -> None:
    editor = _editor(qtbot)
    width = editor.line_number_area_width()
    change = _Tinted()
    if change_first:
        editor.set_line_markers(CHANGE_MARKERS, {3: change, 4: change})
        assert editor.line_number_area_width() == width
        editor.set_problem_lines({3})
    else:
        editor.set_problem_lines({3})
        assert editor.line_number_area_width() == width
        editor.set_line_markers(CHANGE_MARKERS, {3: change, 4: change})

    assert editor.line_number_area_width() == width
    assert editor.line_number_area().width() == width
    change.painted.clear()
    image = _gutter(editor)
    error = error_color(scheme_palette).rgb()
    fill = scheme_palette.color(QPalette.ColorRole.Highlight).rgb()
    on_both = _lane_pixels(editor, image, "markers", 3)
    assert error in on_both
    assert fill not in on_both
    assert _lane_pixels(editor, image, "markers", 4) == {fill}
    assert change.painted == [_line_top(editor, 4)]
    palette = editor.palette()
    assert _tints(editor) == {
        3: PROBLEM_MARKER.line_tint(palette).rgb(),
        4: fill,
    }


def test_set_line_markers_with_nothing_clears_the_kind(qtbot) -> None:
    editor = _editor(qtbot)
    width = editor.line_number_area_width()
    editor.set_line_markers(CHANGE_MARKERS, {1: _Box()})

    editor.set_line_markers(CHANGE_MARKERS, {})

    assert editor.line_number_area_width() == width


def test_an_unknown_marker_kind_is_refused(qtbot) -> None:
    editor = _editor(qtbot)
    with pytest.raises(ValueError, match="GUTTER_LANES"):
        editor.set_line_markers("bookmark", {1: _Box()})


def test_only_visible_lines_are_painted(qtbot) -> None:
    lines = 20000
    editor = _editor(qtbot, "\n".join(f'"k{i}": {i},' for i in range(lines)))
    box = _Box()
    editor.set_line_markers(CHANGE_MARKERS, dict.fromkeys(range(1, lines + 1), box))
    editor.reveal_line(lines // 2)
    box.painted.clear()

    _gutter(editor)

    visible = editor.viewport().height() // editor.fontMetrics().height() + 2
    assert 0 < len(box.painted) <= visible


def test_marker_tints_skip_lines_past_the_end(qtbot) -> None:
    editor = _editor(qtbot)
    editor.set_problem_lines({2, 99})

    assert set(_tints(editor)) == {2}


# Chief's regression check for #74: the first problem marker must not move
# or rewrap the text. Line 3 is long enough to soft-wrap, and 1,100 more
# lines give four-digit line numbers, so the gutter is wider than its
# 50 px minimum and an unreserved marker column would show up as a jump.
_LONG = " ".join(f"word{i}" for i in range(120))
_KEYS = "".join(f'  "k{i}": {i},\n' for i in range(1100))
_VALID = (
    '{\n  "name": "Galley",\n  "note": "'
    + _LONG
    + '",\n  "count": 3,\n'
    + _KEYS
    + '  "ok": true\n}\n'
)
# The same text with the comma after "name" removed: one problem, on line 2.
# It sits above the long line so it stays in view however far that wraps
# (a wider font on Windows pushes line 4 out of the gutter).
_MISSING_COMMA = _VALID.replace('"name": "Galley",', '"name": "Galley"', 1)
_ERROR_LINE = 2
_LONG_LINE = 3


def _window_action(window: MainWindow, text: str) -> QAction:
    found = [item for item in window.findChildren(QAction) if item.text() == text]
    assert len(found) == 1, text
    return found[0]


def _format_input(qtbot, window: MainWindow, text: str, status_text: str) -> None:
    editor = window.findChild(CodeEditor, "inputEditor")
    status = window.findChild(StatusMessage, "statusMessage")
    assert isinstance(editor, CodeEditor)
    assert isinstance(status, StatusMessage)
    editor.setPlainText(text)
    _window_action(window, "Format").trigger()
    qtbot.waitUntil(lambda: status.text() == status_text, timeout=15000)


def _gutter_geometry(editor: CodeEditor) -> dict[str, object]:
    return {
        "markers lane": editor.gutter_layout().lanes["markers"],
        "gutter width": editor.line_number_area().width(),
        "viewport margin": editor.viewportMargins().left(),
        "numbers left": editor.gutter_layout().lanes["numbers"][0],
        "text x": editor.viewport().geometry().x(),
    }


def _text_flow(editor: CodeEditor) -> dict[str, object]:
    block = editor.document().findBlockByNumber(_LONG_LINE - 1)
    cursor = QTextCursor(block)
    cursor.setPosition(block.position() + 300)
    return {
        "viewport width": editor.viewport().width(),
        "wrapped lines": block.layout().lineCount(),
        "cursor rect": editor.cursorRect(cursor).getRect(),
    }


def _formatted_window(
    qtbot, install_scheme: Callable[[str], QPalette], scheme: str
) -> tuple[MainWindow, CodeEditor]:
    window = create_main_window()
    qtbot.addWidget(window)
    window.resize(1100, 700)
    window.show()
    qtbot.waitExposed(window)
    install_scheme(scheme)
    editor = window.findChild(CodeEditor, "inputEditor")
    assert isinstance(editor, CodeEditor)
    assert is_dark(editor.palette()) == (scheme == "dark")
    return window, editor


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_first_problem_from_format_moves_and_rewraps_nothing(
    qtbot, install_scheme: Callable[[str], QPalette], scheme: str
) -> None:
    window, editor = _formatted_window(qtbot, install_scheme, scheme)
    # The Problems pane opens beside the editors and narrows them, which
    # rewraps text on its own. Close it, as a user can, so that only the
    # gutter could move the text here.
    show_problems = _window_action(window, "Show Problems")
    if show_problems.isChecked():
        show_problems.trigger()
    pane = window.findChild(ProblemsPane, "problemsPane")
    assert isinstance(pane, ProblemsPane)

    _format_input(qtbot, window, _VALID, "Valid JSON")
    assert editor.problem_lines() == set()
    assert editor.line_markers(PROBLEM_MARKERS) == {}
    assert editor.line_number_area().width() > GUTTER_MIN_WIDTH
    clean_gutter = _gutter_geometry(editor)
    clean_flow = _text_flow(editor)
    assert clean_gutter["markers lane"] == (0, 16)
    assert clean_flow["wrapped lines"] > 1

    _format_input(qtbot, window, _MISSING_COMMA, "1 problem")
    qtbot.waitUntil(lambda: editor.problem_lines() == {_ERROR_LINE}, timeout=15000)
    assert not pane.isVisible()

    assert _gutter_geometry(editor) == clean_gutter
    assert _text_flow(editor) == clean_flow
    image = _gutter(editor)
    error = error_color(editor.palette()).rgb()
    assert error in _lane_pixels(editor, image, "markers", _ERROR_LINE)
    assert error not in _lane_pixels(editor, image, "markers", _LONG_LINE)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_first_problem_with_the_problems_pane_keeps_the_gutter(
    qtbot, install_scheme: Callable[[str], QPalette], scheme: str
) -> None:
    window, editor = _formatted_window(qtbot, install_scheme, scheme)
    pane = window.findChild(ProblemsPane, "problemsPane")
    assert isinstance(pane, ProblemsPane)

    _format_input(qtbot, window, _VALID, "Valid JSON")
    assert editor.problem_lines() == set()
    clean_gutter = _gutter_geometry(editor)
    assert clean_gutter["markers lane"] == (0, 16)

    _format_input(qtbot, window, _MISSING_COMMA, "1 problem")
    qtbot.waitUntil(lambda: editor.problem_lines() == {_ERROR_LINE}, timeout=15000)
    qtbot.waitUntil(pane.isVisible, timeout=15000)

    assert _gutter_geometry(editor) == clean_gutter
