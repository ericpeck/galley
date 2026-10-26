"""Compare slice 3b: diff kind colors, change markers, line tints, spans (#65)."""

import json
import os
import subprocess
import sys
from collections.abc import Iterator

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPalette, QTextCursor
from PySide6.QtWidgets import QApplication, QLineEdit, QTextEdit

from galley.app.appearance import set_application_palette
from galley.app.editor import (
    CHANGE_GLYPHS,
    CHANGE_MARKERS,
    CHANGE_TINT_SIDES,
    PROBLEM_MARKERS,
    ChangeMarker,
    CodeEditor,
)
from galley.app.fonts import code_font
from galley.app.highlighter import DARK_SYNTAX, LIGHT_SYNTAX
from galley.app.theme import (
    ACCENT,
    ACCENT_TEXT_DARK,
    ADDED,
    ADDED_DARK,
    CHANGE_TINT_SHARE,
    CHANGED,
    CHANGED_DARK,
    EDGE_CONTRAST,
    REMOVED,
    REMOVED_DARK,
    TEXT_MIN_CONTRAST,
    change_color,
    change_tint,
    composite,
    contrast_ratio,
    error_color,
    find_highlight_color,
    find_match_colors,
    is_dark,
    mix,
    problem_line_tint,
)
from galley.core.diff import ChangeKind, Span

_KINDS: tuple[ChangeKind, ...] = ("added", "removed", "changed", "moved")
_TEXT = "".join(f'"key{i}": {i},\n' for i in range(1, 9))
# macOS dark Base's current-line band, as cocoa reports it (see
# test_highlighter). The offscreen dark test palette has no band of its own.
_MAC_DARK_BAND = "#343434"
# Two fills count as different when some channel is this far apart.
_DISTINCT = 12


def _editor(qtbot, text: str = _TEXT, *, size: tuple[int, int] = (420, 200)):
    editor = CodeEditor()
    qtbot.addWidget(editor)
    editor.resize(*size)
    editor.show()
    qtbot.waitExposed(editor)
    editor.setPlainText(text)
    return editor


@pytest.fixture(params=["light", "dark"])
def scheme(request, qapp, install_scheme) -> Iterator[QPalette]:
    """The app's light or dark palette. Dark gets the macOS line band."""
    palette = install_scheme(request.param)
    if request.param == "dark":
        palette = QPalette(palette)
        for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
            palette.setColor(group, QPalette.ColorRole.Midlight, QColor(_MAC_DARK_BAND))
        set_application_palette(qapp, palette)
        qapp.processEvents()
    assert is_dark(palette) == (request.param == "dark")
    return palette


def _bands(editor: CodeEditor) -> dict[int, int]:
    """Line bands other than the current-line band, by 1-based line."""
    midlight = editor.palette().color(QPalette.ColorRole.Midlight)
    return {
        selection.cursor.blockNumber() + 1: selection.format.background().color().rgb()
        for selection in editor.extraSelections()
        if selection.format.background().color() != midlight
    }


def _lane_rect(editor: CodeEditor, lineno: int) -> QRect:
    """The markers lane on the first row of ``lineno``, in the gutter."""
    left, width = editor.gutter_layout().lanes["markers"]
    block = editor.document().findBlockByNumber(lineno - 1)
    top = int(
        editor.blockBoundingGeometry(block).translated(editor.contentOffset()).top()
    )
    return QRect(left, top, width, editor.fontMetrics().height())


def _lane(editor: CodeEditor, lineno: int) -> QImage:
    image = editor.line_number_area().grab().toImage()
    return image.copy(_lane_rect(editor, lineno))


def _marker_point(editor: CodeEditor, lineno: int) -> QPoint:
    return _lane_rect(editor, lineno).center()


def _colors(image: QImage) -> set[int]:
    return {
        image.pixelColor(x, y).rgb()
        for x in range(image.width())
        for y in range(image.height())
    }


def _differ(one: QColor, two: QColor) -> bool:
    return (
        max(
            abs(one.red() - two.red()),
            abs(one.green() - two.green()),
            abs(one.blue() - two.blue()),
        )
        >= _DISTINCT
    )


# --- Colors ---------------------------------------------------------------


def test_the_kind_inks_are_bills_b1_values() -> None:
    assert (ADDED, ADDED_DARK) == ("#1A7F37", "#57AB5A")
    assert (REMOVED, REMOVED_DARK) == ("#B35900", "#E0823D")
    assert (CHANGED, CHANGED_DARK) == ("#7A5C00", "#C9A227")
    assert CHANGE_TINT_SHARE == 0.12


def test_inks_and_tints_follow_the_scheme(scheme: QPalette) -> None:
    dark = is_dark(scheme)
    expected = {
        "added": ADDED_DARK if dark else ADDED,
        "removed": REMOVED_DARK if dark else REMOVED,
        "changed": CHANGED_DARK if dark else CHANGED,
        "moved": ACCENT_TEXT_DARK if dark else ACCENT,
    }
    base = scheme.color(QPalette.ColorRole.Base)
    for kind in _KINDS:
        ink = change_color(kind, scheme)
        assert ink.name().upper() == expected[kind]
        assert change_tint(kind, scheme).rgb() == mix(base, ink, 0.12).rgb()


def test_glyphs_are_3_to_1_on_every_surface_they_meet(scheme: QPalette) -> None:
    base = scheme.color(QPalette.ColorRole.Base)
    surfaces = {
        "Base": base,
        "gutter": composite(scheme.color(QPalette.ColorRole.AlternateBase), base),
        "current-line band": composite(scheme.color(QPalette.ColorRole.Midlight), base),
    }
    for kind in _KINDS:
        ink = change_color(kind, scheme)
        for name, surface in {**surfaces, "tint": change_tint(kind, scheme)}.items():
            ratio = contrast_ratio(ink, surface)
            assert ratio >= EDGE_CONTRAST, f"{kind} glyph on {name}: {ratio:.2f}"


def test_text_and_every_syntax_color_are_4_5_to_1_on_each_tint(
    scheme: QPalette,
) -> None:
    syntax = DARK_SYNTAX if is_dark(scheme) else LIGHT_SYNTAX
    for kind in _KINDS:
        tint = change_tint(kind, scheme)
        inks = {
            "text": composite(scheme.color(QPalette.ColorRole.Text), tint),
            **{role: QColor(color) for role, color in syntax._asdict().items()},
        }
        for name, ink in inks.items():
            ratio = contrast_ratio(ink, tint)
            assert ratio >= TEXT_MIN_CONTRAST, f"{name} on {kind} tint: {ratio:.2f}"


def test_each_tint_differs_from_selection_find_error_and_band(
    scheme: QPalette,
) -> None:
    base = scheme.color(QPalette.ColorRole.Base)
    others = {
        "selection": composite(scheme.color(QPalette.ColorRole.Highlight), base),
        "find": find_highlight_color(),
        "current find match": find_match_colors(scheme).current,
        "other find match": find_match_colors(scheme).other,
        "error": problem_line_tint(scheme),
        "current-line band": composite(scheme.color(QPalette.ColorRole.Midlight), base),
        "Base": base,
    }
    for kind in _KINDS:
        tint = change_tint(kind, scheme)
        for name, other in others.items():
            assert _differ(tint, other), f"{kind} tint {tint.name()} vs {name}"


# --- Markers --------------------------------------------------------------


def test_each_kind_has_its_own_glyph() -> None:
    assert CHANGE_GLYPHS == {
        "added": "+",
        "removed": "−",
        "changed": "~",
        "moved": "⇄",
    }
    for kind in _KINDS:
        assert ChangeMarker(kind).glyph == CHANGE_GLYPHS[kind]


def _painted(marker: ChangeMarker, palette: QPalette) -> QImage:
    """``marker`` in a 16 x 18 lane, without antialiasing, on Base."""
    image = QImage(16, 18, QImage.Format.Format_RGB32)
    image.fill(palette.color(QPalette.ColorRole.Base))
    painter = QPainter(image)
    font = code_font()
    font.setStyleStrategy(QFont.StyleStrategy.NoAntialias)
    painter.setFont(font)
    marker.paint(painter, QRect(0, 0, 16, 18), palette)
    painter.end()
    return image


def test_each_glyph_paints_in_its_kinds_ink(qapp, scheme: QPalette) -> None:
    del qapp
    base = scheme.color(QPalette.ColorRole.Base).rgb()
    shapes: dict[ChangeKind, QImage] = {}
    for kind in _KINDS:
        image = _painted(ChangeMarker(kind), scheme)
        assert _colors(image) == {base, change_color(kind, scheme).rgb()}, kind
        # The shape alone, so glyphs compare across inks.
        shape = image.convertToFormat(QImage.Format.Format_Mono)
        shapes[kind] = shape
    for kind in _KINDS:
        for other in _KINDS:
            if kind != other:
                assert shapes[kind] != shapes[other], (kind, other)


def test_a_change_marker_draws_in_the_markers_lane(qtbot, scheme: QPalette) -> None:
    editor = _editor(qtbot)
    plain = _lane(editor, 2)
    name = "Changed, key2, 2 to 3"

    editor.set_line_markers(CHANGE_MARKERS, {2: ChangeMarker("changed", name)})

    assert _lane(editor, 2) != plain
    hit = editor.marker_at(_marker_point(editor, 2))
    assert hit is not None
    assert hit.kind == CHANGE_MARKERS
    assert hit.marker.description() == name
    # The marker never recolors the line number.
    assert ChangeMarker("added").number_color(scheme) is None


def test_a_problem_outranks_a_change_on_the_same_line(qtbot, scheme: QPalette) -> None:
    problem_only = _editor(qtbot)
    problem_only.set_problem_messages({2: "Missing comma."})
    both = _editor(qtbot)
    both.set_change_tints("right", [(1, 3, "changed")])
    both.set_line_markers(
        CHANGE_MARKERS, {2: ChangeMarker("changed"), 3: ChangeMarker("added")}
    )
    both.set_problem_messages({2: "Missing comma."})

    assert _lane(both, 2) == _lane(problem_only, 2)
    assert error_color(scheme).rgb() in _colors(_lane(both, 2))
    assert change_color("changed", scheme).rgb() not in _colors(_lane(both, 2))
    assert _lane(both, 3) != _lane(problem_only, 3)
    palette = both.palette()
    changed = change_tint("changed", palette).rgb()
    assert _bands(both) == {1: changed, 2: problem_line_tint(palette).rgb(), 3: changed}
    hit = both.marker_at(_marker_point(both, 2))
    assert hit is not None and hit.kind == PROBLEM_MARKERS


# --- Layers ---------------------------------------------------------------
# Eric's order, bottom to top: change tint, problem tint, Find highlight.

_UNDER_FIND = ["problem", *_KINDS]
# Line 2's trailing spaces are the match, so no glyph sits where it is
# sampled and the tint shows on both sides of it.
_FIND_TEXT = "x\nx        \nx\n"
_FIND_START, _FIND_END = 3, 11


def _find_over(qtbot, under: str, *, current: bool) -> CodeEditor:
    editor = _editor(qtbot, _FIND_TEXT)
    editor.set_whitespace_wanted(False)
    if under == "problem":
        editor.set_problem_messages({2: "Missing comma."})
    else:
        side = sorted(CHANGE_TINT_SIDES[under])[0]
        editor.set_change_tints(side, [(2, 2, under)])
    editor.set_find_matches([_FIND_START], [_FIND_END], 0 if current else -1)
    return editor


def test_a_find_match_is_the_top_layer_in_the_selections(qtbot) -> None:
    editor = _editor(qtbot, _FIND_TEXT)
    editor.set_change_tints("right", [(1, 3, "changed")])
    editor.set_problem_messages({2: "Missing comma."})
    editor.set_find_matches([_FIND_START], [_FIND_END], 0)

    palette = editor.palette()
    backgrounds = [
        selection.format.background().color().rgb()
        for selection in editor.extraSelections()
    ]
    changed = change_tint("changed", palette).rgb()
    assert backgrounds == [
        changed,
        problem_line_tint(palette).rgb(),
        changed,
        find_match_colors(palette).current.rgb(),
    ]


@pytest.mark.parametrize("current", [True, False], ids=["current", "other"])
@pytest.mark.parametrize("under", _UNDER_FIND)
def test_a_find_match_is_readable_on_every_tint(
    qtbot, scheme: QPalette, under: str, current: bool
) -> None:
    editor = _find_over(qtbot, under, current=current)
    palette = editor.palette()
    colors = find_match_colors(palette)
    tint = (
        problem_line_tint(palette)
        if under == "problem"
        else change_tint(under, palette)
    )
    image = editor.viewport().grab().toImage()
    cursor = QTextCursor(editor.document())
    cursor.setPosition((_FIND_START + _FIND_END) // 2)
    inside = editor.cursorRect(cursor).center() + QPoint(2, 0)
    cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
    past = editor.cursorRect(cursor).center() + QPoint(12, 0)

    fill = image.pixelColor(inside)
    assert fill.rgb() == (colors.current if current else colors.other).rgb()
    assert image.pixelColor(past).rgb() == tint.rgb()
    ratio = contrast_ratio(colors.ink, fill)
    assert ratio >= TEXT_MIN_CONTRAST, f"Find ink on {under}: {ratio:.2f}:1"


def _runs(image: QImage, y: int) -> str:
    """Row ``y`` as color runs, for a failure message."""
    runs: list[list] = []
    for x in range(image.width()):
        name = image.pixelColor(x, y).name()
        if runs and runs[-1][0] == name:
            runs[-1][1] += 1
        else:
            runs.append([name, 1])
    return " ".join(f"{name}×{count}" for name, count in runs)


def _caret_x(editor: CodeEditor, position: int) -> int:
    cursor = QTextCursor(editor.document())
    cursor.setPosition(position)
    return editor.cursorRect(cursor).center().x()


def _assert_outlined(
    editor: CodeEditor,
    image: QImage,
    match: tuple[int, int],
    fill: QColor,
    edge: QColor,
    beside: QColor,
) -> None:
    """A 1 px ``edge`` outline around the fill of ``match``, with ``beside``
    just past it to the left and right.

    The outline is found by its color and its neighbors, not by walking the
    fill: glyph ink can cross the fill on any row. Windows CI's offscreen
    font draws every character, spaces too, as a box.
    """
    first, last = match
    cursor = QTextCursor(editor.document())
    cursor.setPosition((first + last) // 2)
    rect = editor.cursorRect(cursor)
    # 2 px below the line's top, clear of the current match's underline.
    inside = QPoint(rect.center().x(), rect.top() + 2)
    y = inside.y()
    row = _runs(image, y)

    def color(x: int) -> int:
        return image.pixelColor(x, y).rgb()

    xs = range(1, image.width() - 1)
    lefts = [x for x in xs if color(x) == edge.rgb() and color(x - 1) == beside.rgb()]
    rights = [x for x in xs if color(x) == edge.rgb() and color(x + 1) == beside.rgb()]
    assert lefts and rights, row
    left, right = lefts[0], rights[-1]
    # 1 px wide: the pixel inside each outline is not the outline again.
    assert color(left + 1) not in (edge.rgb(), beside.rgb()), row
    assert color(right - 1) not in (edge.rgb(), beside.rgb()), row
    assert abs(left - _caret_x(editor, first)) <= 1, (left, row)
    assert abs(right + 1 - _caret_x(editor, last)) <= 1, (right, row)
    between = {color(x) for x in range(left + 1, right)}
    assert fill.rgb() in between, row
    assert beside.rgb() not in between, row
    ys = [
        y
        for y in range(image.height())
        if image.pixelColor(inside.x(), y).rgb() == edge.rgb()
    ]
    above = [y for y in ys if y < inside.y()]
    below = [y for y in ys if y > inside.y()]
    assert above and below, ys
    assert above[-1] == rect.top(), (above, rect)
    for y in (above[-1] - 1, below[0] + 1):
        assert image.pixelColor(inside.x(), y).rgb() != fill.rgb(), y


@pytest.mark.parametrize("current", [True, False], ids=["current", "other"])
@pytest.mark.parametrize("under", _UNDER_FIND)
def test_a_find_match_stands_out_3_to_1_from_every_tint(
    qtbot, scheme: QPalette, under: str, current: bool
) -> None:
    editor = _find_over(qtbot, under, current=current)
    palette = editor.palette()
    colors = find_match_colors(palette)
    tint = (
        problem_line_tint(palette)
        if under == "problem"
        else change_tint(under, palette)
    )
    fill = colors.current if current else colors.other
    # The fill alone can't reach 3:1 and keep its text 4.5:1, so the
    # outline carries the edge.
    assert contrast_ratio(fill, tint) < EDGE_CONTRAST
    ratio = contrast_ratio(colors.edge, tint)
    assert ratio >= EDGE_CONTRAST, f"Find outline on {under}: {ratio:.2f}:1"

    image = editor.viewport().grab().toImage()
    match = (_FIND_START, _FIND_END)
    _assert_outlined(editor, image, match, fill, colors.edge, tint)


def test_the_find_outline_does_not_depend_on_the_middle_row(qtbot) -> None:
    editor = _find_over(qtbot, "changed", current=False)
    editor.set_whitespace_wanted(True)
    palette = editor.palette()
    colors = find_match_colors(palette)

    image = editor.viewport().grab().toImage()
    tint = change_tint("changed", palette)
    match = (_FIND_START, _FIND_END)
    _assert_outlined(editor, image, match, colors.other, colors.edge, tint)


def test_the_find_outline_draws_with_a_fold_closed(qtbot, scheme: QPalette) -> None:
    # The match is line 5's trailing spaces, so the tint is past both ends.
    text = '{\n  "a": [\n    1\n  ],\n  "b": "x"        \n}\n'
    editor = _editor(qtbot, text)
    editor.set_folding_enabled(True)
    editor.set_change_tints("left", [(5, 5, "changed")])
    start = text.index('x" ') + 2
    editor.set_find_matches([start], [start + 8], 0)
    assert editor.fold_line(2)
    palette = editor.palette()
    colors = find_match_colors(palette)

    image = editor.viewport().grab().toImage()
    tint = change_tint("changed", palette)
    match = (start, start + 8)
    _assert_outlined(editor, image, match, colors.current, colors.edge, tint)


# A paint that never returns can't be stopped inside the test process, so
# these paint in a child process with a hard limit.
_PAINT_LIMIT_SECONDS = 10
_OUTLINE_CHILD = """
import sys
from PySide6.QtWidgets import QApplication
from galley.app.editor import CodeEditor
from galley.app.theme import find_match_colors

text, start, end = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
app = QApplication([])
editor = CodeEditor()
editor.resize(420, 200)
editor.show()
editor.setPlainText(text)
editor.set_change_tints("left", [(2, 2, "changed")])
editor.set_find_matches([start], [end], 0)
app.processEvents()
image = editor.viewport().grab().toImage()
edge = find_match_colors(editor.palette()).edge.rgb()
print(sum(
    image.pixelColor(x, y).rgb() == edge
    for x in range(image.width())
    for y in range(image.height())
))
"""


def _outline_pixels(text: str, start: int, end: int) -> int:
    """Outline pixels when one Find match is painted, in a child process."""
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    try:
        done = subprocess.run(
            [sys.executable, "-c", _OUTLINE_CHILD, text, str(start), str(end)],
            capture_output=True,
            text=True,
            env=env,
            timeout=_PAINT_LIMIT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"painting the Find outline hung past {_PAINT_LIMIT_SECONDS} s")
    assert done.returncode == 0, done.stderr
    return int(done.stdout.split()[-1])


def test_a_find_match_in_right_to_left_text_paints_its_outline() -> None:
    # Hebrew for "hello world"; the match is the second word.
    text = "x\n\u05e9\u05dc\u05d5\u05dd \u05e2\u05d5\u05dc\u05dd\nx\n"
    start = text.index("\u05e2")
    assert _outline_pixels(text, start, start + 4) > 0


def test_a_find_match_on_a_lone_combining_accent_paints() -> None:
    text = "x\n\u0301\nx\n"
    start = text.index("\u0301")
    assert _outline_pixels(text, start, start + 1) >= 0


def test_the_find_outline_follows_a_scheme_switch(qtbot, install_scheme) -> None:
    install_scheme("light")
    editor = _find_over(qtbot, "changed", current=True)
    assert find_match_colors(editor.palette()).edge == QColor(ACCENT)
    install_scheme("dark")
    assert find_match_colors(editor.palette()).edge == QColor(ACCENT_TEXT_DARK)


# --- Tints ----------------------------------------------------------------


def test_tints_follow_the_side_rule() -> None:
    assert CHANGE_TINT_SIDES == {
        "added": {"right"},
        "removed": {"left"},
        "changed": {"left", "right"},
        "moved": {"left", "right"},
    }


@pytest.mark.parametrize("side", ["left", "right"])
def test_each_side_tints_only_its_kinds(qtbot, scheme: QPalette, side: str) -> None:
    editor = _editor(qtbot)
    spans = [(1, 1, "added"), (2, 3, "removed"), (4, 4, "changed"), (5, 6, "moved")]

    editor.set_change_tints(side, spans)

    palette = editor.palette()
    tint = {kind: change_tint(kind, palette).rgb() for kind in _KINDS}
    both = {4: tint["changed"], 5: tint["moved"], 6: tint["moved"]}
    if side == "left":
        # Removed text is only on the left.
        assert _bands(editor) == {2: tint["removed"], 3: tint["removed"], **both}
    else:
        # Added text is only on the right.
        assert _bands(editor) == {1: tint["added"], **both}


def test_a_moved_change_tints_both_ends(qtbot, scheme: QPalette) -> None:
    left = _editor(qtbot)
    right = _editor(qtbot)
    # The item left lines 2-3 on the left and arrived at lines 6-7 on the right.
    left.set_change_tints("left", [(2, 3, "moved")])
    right.set_change_tints("right", [(6, 7, "moved")])

    moved = change_tint("moved", left.palette()).rgb()
    assert _bands(left) == {2: moved, 3: moved}
    assert _bands(right) == {6: moved, 7: moved}


def test_a_later_span_wins_where_spans_overlap(qtbot) -> None:
    editor = _editor(qtbot)
    editor.set_change_tints("left", [(1, 5, "moved"), (3, 3, "changed")])

    palette = editor.palette()
    moved = change_tint("moved", palette).rgb()
    assert _bands(editor) == {
        1: moved,
        2: moved,
        3: change_tint("changed", palette).rgb(),
        4: moved,
        5: moved,
    }


def test_an_empty_call_clears_the_tints(qtbot) -> None:
    editor = _editor(qtbot)
    editor.set_change_tints("right", [(1, 2, "added")])
    assert editor.change_tints() == ((1, 2, "added"),)

    editor.set_change_tints("right", [])

    assert editor.change_tints() == ()
    assert _bands(editor) == {}


def test_the_current_line_band_gives_way_to_a_tint(qtbot, scheme: QPalette) -> None:
    editor = _editor(qtbot)
    editor.set_change_tints("left", [(3, 3, "removed")])
    editor.reveal_line(3)

    backgrounds = [
        selection.format.background().color().rgb()
        for selection in editor.extraSelections()
    ]
    assert backgrounds == [change_tint("removed", editor.palette()).rgb()]

    editor.reveal_line(5)
    midlight = editor.palette().color(QPalette.ColorRole.Midlight).rgb()
    assert midlight in [
        selection.format.background().color().rgb()
        for selection in editor.extraSelections()
    ]


def test_tints_follow_a_scheme_switch(qtbot, install_scheme) -> None:
    install_scheme("light")
    editor = _editor(qtbot)
    editor.set_change_tints("left", [(2, 2, "removed")])
    light = _bands(editor)[2]

    palette = install_scheme("dark")
    qtbot.waitUntil(lambda: is_dark(editor.palette()))

    assert _bands(editor) == {2: change_tint("removed", palette).rgb()}
    assert _bands(editor)[2] != light


# --- Only the lines on screen ---------------------------------------------

_LINES = 2_000
_LONG = "".join(f'"key{i}": {i},\n' for i in range(_LINES))


def _shown_lines(editor: CodeEditor) -> set[int]:
    return {
        lineno for lineno, _top in editor._visible_line_tops(editor.viewport().rect())
    }


def test_only_the_lines_on_screen_get_a_band(qtbot) -> None:
    editor = _editor(qtbot, _LONG)
    editor.set_change_tints("left", [(1, _LINES, "changed")])

    shown = _shown_lines(editor)
    assert 0 < len(shown) < 40
    assert set(_bands(editor)) == shown


def test_the_bands_follow_a_scroll_a_resize_and_a_jump(qtbot) -> None:
    editor = _editor(qtbot, _LONG)
    editor.set_change_tints("left", [(1, _LINES, "changed")])
    editor.set_problem_lines({1_500})

    editor.verticalScrollBar().setValue(500)
    assert set(_bands(editor)) == _shown_lines(editor)
    assert min(_bands(editor)) >= 500

    editor.resize(420, 400)
    qtbot.waitUntil(lambda: set(_bands(editor)) == _shown_lines(editor))
    assert len(_bands(editor)) > 20

    editor.reveal_line(1_500)
    bands = _bands(editor)
    assert set(bands) == _shown_lines(editor)
    assert bands[1_500] == problem_line_tint(editor.palette()).rgb()


def test_the_bands_follow_a_fold(qtbot) -> None:
    text = (
        "{\n"
        + "".join(f'  "k{i}": [\n    {i}\n  ],\n' for i in range(60))
        + '  "z": 0\n}\n'
    )
    editor = _editor(qtbot, text)
    editor.set_folding_enabled(True)
    editor.set_change_tints("left", [(1, text.count("\n"), "changed")])
    before = set(_bands(editor))

    assert editor.collapse_all()

    after = set(_bands(editor))
    assert after == _shown_lines(editor)
    assert after != before
    assert max(after) > max(before)


def test_the_bands_follow_an_edit_that_brings_lines_on_screen(qtbot) -> None:
    # Line 3 wraps over many rows. Emptying it brings later lines on screen
    # with no scroll and no caret move.
    wrapped = " ".join(f"word{i}" for i in range(200))
    editor = _editor(qtbot, f"one\ntwo\n{wrapped}\n" + _LONG)
    editor.set_problem_lines(set(range(1, _LINES + 4)))
    last = max(_bands(editor))
    cursor = QTextCursor(editor.document().findBlockByNumber(2))
    cursor.movePosition(
        QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor
    )

    cursor.removeSelectedText()

    assert editor.textCursor().position() == 0
    assert max(_bands(editor)) > last + 5
    assert set(_bands(editor)) == _shown_lines(editor)


# --- Problem tints stay pixel-identical ------------------------------------


def _main_bands(editor: CodeEditor, lines: set[int]) -> None:
    """The bands as main drew them: one extra selection per problem line."""
    tint = problem_line_tint(editor.palette())
    selections: list[QTextEdit.ExtraSelection] = []
    current = editor.textCursor().blockNumber() + 1
    if current not in lines:
        band = editor.palette().color(QPalette.ColorRole.Midlight)
        selections.append(editor._line_band(editor.textCursor(), band))
    for lineno in sorted(lines):
        block = editor.document().findBlockByNumber(lineno - 1)
        if block.isValid():
            selections.append(editor._line_band(QTextCursor(block), tint))
    editor.setExtraSelections(selections)


_WRAPPED = " ".join(f"word{i}" for i in range(80))
_IDENTITY_TEXT = (
    "{\n"
    + "".join(f'  "key{i}": "value {i}",\n' for i in range(600))
    + f'  "long": "{_WRAPPED}",\n'
    + "".join(f'  "tail{i}": {i},\n' for i in range(40))
    + "}\n"
)


# Windows CI's code font is wider than macOS's, so the long line wraps over
# more rows there. The wide variant scales the code font up so macOS lays
# the text out the way Windows does.
_WIDE_CODE_POINTS = 18


@pytest.mark.parametrize("font", ["platform font", "wide font"])
@pytest.mark.parametrize(
    ("caret_line", "select_lines", "scroll_to"),
    [(2, 0, 0), (5, 3, 0), (601, 0, 600), (590, 4, 585)],
    ids=["caret-off-a-problem", "selection-over-problems", "wrapped", "scrolled"],
)
def test_problem_tints_are_pixel_identical_to_main(
    qtbot,
    scheme: QPalette,
    caret_line: int,
    select_lines: int,
    scroll_to: int,
    font: str,
) -> None:
    def place(editor: CodeEditor) -> None:
        if font == "wide font":
            editor.setFont(code_font(_WIDE_CODE_POINTS))
        editor.setFocus()
        cursor = QTextCursor(editor.document().findBlockByNumber(caret_line - 1))
        if select_lines:
            cursor.movePosition(
                QTextCursor.MoveOperation.Down,
                QTextCursor.MoveMode.KeepAnchor,
                select_lines,
            )
        editor.setTextCursor(cursor)
        if scroll_to:
            editor.verticalScrollBar().setValue(scroll_to)
        QApplication.processEvents()

    painted = _editor(qtbot, _IDENTITY_TEXT, size=(420, 300))
    place(painted)
    # Where a scroll value lands depends on how the font wraps, so every
    # other line on screen is a problem too, wherever the view ends up.
    shown = sorted(_shown_lines(painted))
    lines = {3, 4, 6, *range(580, 610), *shown[::2]}
    painted.set_problem_lines(lines)
    assert set(_bands(painted)) == lines & set(shown)
    reference = _editor(qtbot, _IDENTITY_TEXT, size=(420, 300))
    place(reference)
    _main_bands(reference, lines)
    # Neither editor blinks a caret into one grab and not the other.
    for editor in (painted, reference):
        editor.setCursorWidth(0)
    # The same lines on screen at the same offset. The scroll bar's value
    # can differ: it counts the wrapped rows of the blocks Qt has laid out,
    # and main's bands lay out every problem line, on screen or not.
    assert (
        painted.firstVisibleBlock().blockNumber()
        == reference.firstVisibleBlock().blockNumber()
    )
    assert painted.contentOffset() == reference.contentOffset()
    assert len(painted.extraSelections()) < len(reference.extraSelections())

    shot = painted.viewport().grab().toImage()
    expected = reference.viewport().grab().toImage()

    assert problem_line_tint(scheme).rgb() in _colors(shot)
    assert shot == expected


# --- Span selection -------------------------------------------------------


def test_select_span_uses_the_native_selection(qtbot, scheme: QPalette) -> None:
    text = '{\n  "a": 1,\n  "b": {\n    "c": "😀 x"\n  }\n}\n'
    editor = _editor(qtbot, text)
    other = QLineEdit()
    qtbot.addWidget(other)
    other.show()
    other.setFocus()
    qtbot.waitUntil(other.hasFocus)
    before = editor.viewport().grab().toImage()

    # "b" through the closing brace; columns are characters, end exclusive.
    editor.select_span(Span(line=3, column=3, end_line=5, end_column=4))

    assert editor.textCursor().selectedText() == '"b": {\u2029    "c": "😀 x"\u2029  }'
    assert editor.cursor_place() == (3, 3)
    assert other.hasFocus()
    after = editor.viewport().grab().toImage()
    highlight = editor.palette().color(
        QPalette.ColorGroup.Inactive, QPalette.ColorRole.Highlight
    )
    assert highlight.rgb() in _colors(after)
    assert highlight.rgb() not in _colors(before)


def test_select_span_counts_astral_characters_as_one_column(qtbot) -> None:
    editor = _editor(qtbot, '"😀😀": "after"\n')

    editor.select_span(Span(line=1, column=7, end_line=1, end_column=14))

    assert editor.textCursor().selectedText() == '"after"'


def test_select_span_scrolls_to_its_first_line(qtbot) -> None:
    editor = _editor(qtbot, _LONG)

    editor.select_span(Span(line=1_200, column=1, end_line=1_203, end_column=1))

    assert 1_200 in _shown_lines(editor)


def test_select_span_opens_a_fold_that_hides_it(qtbot) -> None:
    text = '{\n  "a": {\n    "b": 1,\n    "c": 2\n  }\n}\n'
    editor = _editor(qtbot, text)
    editor.set_folding_enabled(True)
    editor.fold_line(2)
    assert editor.folded_lines() == {2}

    editor.select_span(Span(line=4, column=5, end_line=4, end_column=11))

    assert editor.folded_lines() == set()
    assert editor.textCursor().selectedText() == '"c": 2'


# --- Perf (Isaac's 3b budgets, #65) ----------------------------------------

_CARET_LINES = 50_000
_CARET_MOVES = 200
_CARET_P95_S = 0.016
_TINT_2MB_S = 0.15


@pytest.mark.perf
def test_a_caret_move_with_50k_tinted_lines_is_within_budget(
    qtbot, perf_budget
) -> None:
    """16 ms at p95 over 200 caret moves, each one line down.

    The moves run off the bottom of the view, so the timed runs include the
    scrolls that pick the bands again.
    """
    lines = _CARET_LINES
    editor = _editor(qtbot, "".join(f'"key{i}": {i},\n' for i in range(lines)))
    editor.set_change_tints("right", [(1, lines, "changed")])
    editor.set_line_markers(
        CHANGE_MARKERS,
        {lineno: ChangeMarker("changed") for lineno in range(1, lines + 1)},
    )
    editor.reveal_line(lines // 2)
    first = editor.verticalScrollBar().value()

    timing = perf_budget.within(
        _CARET_P95_S,
        lambda: editor.moveCursor(QTextCursor.MoveOperation.Down),
        runs=_CARET_MOVES,
        percentile=95,
        fixture="50,000 tinted and marked lines, 200 moves",
    )

    assert len(timing.samples) == _CARET_MOVES
    assert editor.verticalScrollBar().value() > first
    assert set(_bands(editor)) == _shown_lines(editor)


def _two_mb_diff() -> tuple[str, list[tuple[int, int, ChangeKind]]]:
    """2 MB of JSON records, and a change span over every record.

    Nearly every line is tinted, and each record's first line has a marker.
    Fake data only.
    """
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
    spans: list[tuple[int, int, ChangeKind]] = []
    start = 0
    for lineno, line in enumerate(text.splitlines(), 1):
        if line == "    {":
            start = lineno
        elif line in ("    }", "    },"):
            spans.append((start, lineno, _KINDS[len(spans) % len(_KINDS)]))
    assert len(spans) == len(records)
    return text, spans


@pytest.mark.perf
def test_tinting_a_2_mb_diff_is_within_budget(qtbot, perf_budget) -> None:
    """0.15 s to apply the tints and markers of a 2 MB diff, not the diff."""
    text, spans = _two_mb_diff()
    editor = _editor(qtbot, text)
    markers = {
        first: ChangeMarker(kind, f"Changed {first}") for first, _, kind in spans
    }

    def clear() -> None:
        editor.set_change_tints("right", [])
        editor.clear_line_markers(CHANGE_MARKERS)

    def apply() -> None:
        editor.set_change_tints("right", spans)
        editor.set_line_markers(CHANGE_MARKERS, markers)

    perf_budget.within(
        _TINT_2MB_S, apply, setup=clear, fixture="2 MB JSON, 6100 changed records"
    )

    # The first record is Added, which the right side bands.
    assert spans[0][2] == "added"
    assert set(_bands(editor)) == _shown_lines(editor) - {1, 2}
