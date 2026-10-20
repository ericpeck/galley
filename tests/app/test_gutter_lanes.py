"""Gutter lanes: numbers, fold, and markers."""

import pytest
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage, QPainter, QPalette

from galley.app.editor import (
    CHANGE_MARKERS,
    FOLD_MARKERS,
    GUTTER_LANES,
    PROBLEM_MARKER,
    PROBLEM_MARKERS,
    CodeEditor,
    LineMarker,
)
from galley.app.theme import error_color

_TEXT = "one\ntwo\nthree\nfour\nfive\n"


class _Box(LineMarker):
    """Fills its column with the palette's Highlight color, which is opaque."""

    def __init__(self) -> None:
        self.painted: list[int] = []

    def paint(self, painter: QPainter, rect: QRect, palette: QPalette) -> None:
        self.painted.append(rect.top())
        painter.fillRect(rect, palette.color(QPalette.ColorRole.Highlight))


class _Tinted(_Box):
    """A box that also bands its line in Highlight."""

    def line_tint(self, palette: QPalette) -> QColor | None:
        return palette.color(QPalette.ColorRole.Highlight)


def _tints(editor: CodeEditor) -> dict[int, int]:
    """Line bands other than the current-line band."""
    midlight = editor.palette().color(QPalette.ColorRole.Midlight)
    return {
        selection.cursor.blockNumber() + 1: selection.format.background().color().rgb()
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


def _column_pixels(
    editor: CodeEditor, image: QImage, kind: str, lineno: int
) -> set[int]:
    left, width = editor.gutter_layout().columns[kind]
    top = _line_top(editor, lineno)
    height = editor.fontMetrics().height()
    return {
        image.pixelColor(x, y).rgb()
        for x in range(left, left + width)
        for y in range(top, top + height)
    }


def test_lanes_are_listed_once_left_to_right() -> None:
    assert [lane.name for lane in GUTTER_LANES] == ["markers", "numbers", "fold"]
    kinds = [kind for lane in GUTTER_LANES for kind in lane.kinds]
    assert kinds == [PROBLEM_MARKERS, CHANGE_MARKERS, FOLD_MARKERS]


def test_fold_and_marker_lanes_take_no_width_until_used(qtbot) -> None:
    editor = _editor(qtbot)
    width = editor.line_number_area_width()
    layout = editor.gutter_layout()

    assert layout.lanes["markers"] == (0, 0)
    assert layout.lanes["fold"][1] == 0
    assert layout.columns == {}
    assert layout.lanes["numbers"] == (0, width - 8)

    editor.set_problem_lines({2})

    assert editor.gutter_layout().lanes["fold"][1] == 0


def test_a_second_marker_kind_widens_the_gutter_and_clearing_restores_it(
    qtbot, scheme_palette: QPalette
) -> None:
    editor = _editor(qtbot)
    editor.set_problem_lines({2})
    before = editor.line_number_area_width()
    assert editor.line_number_area().width() == before
    before_image = _gutter(editor)

    box = _Box()
    editor.set_line_markers(CHANGE_MARKERS, {4: box})

    after = editor.line_number_area_width()
    assert after > before
    assert editor.line_number_area().width() == after
    assert editor.viewportMargins().left() == after
    layout = editor.gutter_layout()
    problem_left, problem_width = layout.columns[PROBLEM_MARKERS]
    change_left, change_width = layout.columns[CHANGE_MARKERS]
    assert problem_left == 0
    assert change_left == problem_left + problem_width
    assert layout.lanes["markers"] == (0, problem_width + change_width)
    assert layout.lanes["numbers"][0] == change_left + change_width

    image = _gutter(editor)
    fill = scheme_palette.color(QPalette.ColorRole.Highlight).rgb()
    gutter = editor.gutter_color().rgb()
    assert _column_pixels(editor, image, CHANGE_MARKERS, 4) == {fill}
    assert _column_pixels(editor, image, CHANGE_MARKERS, 3) == {gutter}
    assert len(box.painted) == 1

    editor.clear_line_markers(CHANGE_MARKERS)

    assert editor.line_number_area_width() == before
    assert editor.line_number_area().width() == before
    assert editor.line_markers(CHANGE_MARKERS) == {}
    assert CHANGE_MARKERS not in editor.gutter_layout().columns
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
        assert error in _column_pixels(editor, image, PROBLEM_MARKERS, lineno)
    for lineno in (1, 3, 5):
        assert error not in _column_pixels(editor, image, PROBLEM_MARKERS, lineno)

    editor.set_problem_lines(set())

    assert editor.problem_lines() == set()
    assert editor.gutter_layout().lanes["markers"] == (0, 0)


def test_the_first_kind_listed_wins_the_line_tint(qtbot) -> None:
    editor = _editor(qtbot)
    editor.set_problem_lines({3})
    problems_only = editor.line_number_area_width()
    editor.set_line_markers(CHANGE_MARKERS, {3: _Tinted(), 4: _Tinted()})

    assert editor.line_number_area_width() == problems_only + 16
    palette = editor.palette()
    assert _tints(editor) == {
        3: PROBLEM_MARKER.line_tint(palette).rgb(),
        4: palette.color(QPalette.ColorRole.Highlight).rgb(),
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
