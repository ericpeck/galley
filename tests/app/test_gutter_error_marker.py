"""Gutter error marker: the Problems pane's ⊗, its tooltip, and its click (#79)."""

from collections.abc import Callable

import pytest
from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QAccessible, QAction, QHelpEvent, QImage, QPalette
from PySide6.QtWidgets import QComboBox, QToolTip

from galley.app.editor import GUTTER_MIN_WIDTH, PROBLEM_MARKERS, CodeEditor
from galley.app.main import create_main_window
from galley.app.main_window import MainWindow
from galley.app.problems_pane import ProblemsPane
from galley.app.status_message import StatusMessage
from galley.app.theme import (
    EDGE_CONTRAST,
    composite,
    contrast_ratio,
    error_color,
    is_dark,
)

_TEXT = '{\n  "price": 4.5\n  "qty": 3\n}\n'
_MESSAGE = "Missing comma at the end of line 2."
_TWO_TABS = "a:\n\tb: 1\n\tc: 2\n"
_LANE = 16
_RING = 12


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


def _line_rect(editor: CodeEditor, lane: str, lineno: int, image: QImage) -> QRect:
    """``lane`` on the first row of ``lineno``, checked to be inside ``image``."""
    left, width = editor.gutter_layout().lanes[lane]
    block = editor.document().findBlockByNumber(lineno - 1)
    geometry = editor.blockBoundingGeometry(block).translated(editor.contentOffset())
    top = int(geometry.top())
    height = editor.fontMetrics().height()
    # Outside the image, pixelColor returns an invalid (black) color.
    assert 0 <= top and top + height <= image.height(), (
        f"line {lineno} (y {top}..{top + height}) is outside the "
        f"{image.height()} px gutter grab"
    )
    return QRect(left, top, width, height)


def _pixels(image: QImage, rect: QRect) -> dict[tuple[int, int], int]:
    return {
        (x, y): image.pixelColor(x, y).rgb()
        for x in range(rect.left(), rect.right() + 1)
        for y in range(rect.top(), rect.bottom() + 1)
    }


def _marker_point(editor: CodeEditor, lineno: int) -> QPoint:
    rect = _line_rect(editor, "markers", lineno, _gutter(editor))
    return rect.center()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_error_stroke_is_3_to_1_on_the_gutter(
    qtbot, install_scheme: Callable[[str], QPalette], scheme: str
) -> None:
    other = install_scheme("dark" if scheme == "light" else "light")
    palette = install_scheme(scheme)
    gutter_role = QPalette.ColorRole.AlternateBase
    assert palette.color(gutter_role).rgb() != other.color(gutter_role).rgb()
    assert is_dark(palette) == (scheme == "dark")
    editor = _editor(qtbot)
    assert editor.gutter_color().rgb() == palette.color(gutter_role).rgb()
    editor.set_problem_messages({2: _MESSAGE})

    gutter = composite(editor.gutter_color(), palette.color(QPalette.ColorRole.Base))
    stroke = composite(error_color(editor.palette()), gutter)
    assert contrast_ratio(stroke, gutter) >= EDGE_CONTRAST

    # The painted ring reaches the full stroke color, not just an
    # antialiased blend of it.
    image = _gutter(editor)
    lane = _pixels(image, _line_rect(editor, "markers", 2, image))
    assert stroke.rgb() in lane.values()
    assert gutter.rgb() in lane.values()


def test_the_ring_is_12_px_and_centred_in_the_16_px_lane(
    qtbot, scheme_palette: QPalette
) -> None:
    editor = _editor(qtbot)
    editor.set_problem_messages({2: _MESSAGE})
    image = _gutter(editor)
    rect = _line_rect(editor, "markers", 2, image)
    assert rect.left() == 0 and rect.width() == _LANE
    assert rect.height() >= _RING
    error = error_color(scheme_palette).rgb()
    gutter = editor.gutter_color().rgb()
    lane = _pixels(image, rect)

    solid = [point for point, rgb in lane.items() if rgb == error]
    xs = [x for x, _y in solid]
    ys = [y - rect.top() for _x, y in solid]
    # Strokes sit on pixel centers, so the ring's outer rows and columns
    # are solid: 12 px across, with 2 px of lane on each side.
    left = (_LANE - _RING) // 2
    top = (rect.height() - _RING) // 2
    assert (min(xs), max(xs)) == (left, left + _RING - 1)
    assert (min(ys), max(ys)) == (top, top + _RING - 1)
    # Antialiasing reaches at most one pixel past the solid ring, evenly.
    inked = [point for point, rgb in lane.items() if rgb != gutter]
    inked_xs = [x for x, _y in inked]
    assert min(inked_xs) + max(inked_xs) == _LANE - 1
    assert min(inked_xs) >= left - 1


def test_the_line_number_keeps_its_color(qtbot, scheme_palette: QPalette) -> None:
    editor = _editor(qtbot)
    image = _gutter(editor)
    rect = _line_rect(editor, "numbers", 2, image)
    clean = image.copy(rect)

    editor.set_problem_messages({2: _MESSAGE})

    marked = _gutter(editor)
    assert marked.copy(rect) == clean
    assert error_color(scheme_palette).rgb() not in _pixels(marked, rect).values()


def test_the_gutter_width_and_text_x_are_as_on_main(qtbot) -> None:
    editor = _editor(qtbot, "".join(f"{i}\n" for i in range(1500)))
    # main: 8 px padding, the digits, and the reserved 16 px markers lane.
    digits = editor.fontMetrics().horizontalAdvance("9") * 4
    width = max(GUTTER_MIN_WIDTH, 8 + digits + _LANE)
    expected = {
        "gutter width": width,
        "viewport margin": width,
        "markers lane": (0, _LANE),
        "numbers lane": (_LANE, width - _LANE - 8),
        "text x": width,
    }

    def geometry() -> dict[str, object]:
        return {
            "gutter width": editor.line_number_area().width(),
            "viewport margin": editor.viewportMargins().left(),
            "markers lane": editor.gutter_layout().lanes["markers"],
            "numbers lane": editor.gutter_layout().lanes["numbers"],
            "text x": editor.viewport().geometry().x(),
        }

    assert geometry() == expected
    editor.set_problem_messages({2: _MESSAGE})
    assert geometry() == expected


def test_the_marker_tooltip_and_accessible_text_are_its_message(qtbot) -> None:
    editor = _editor(qtbot)
    editor.set_problem_messages({2: _MESSAGE})
    gutter = editor.line_number_area()

    point = _marker_point(editor, 2)
    hit = editor.marker_at(point)
    assert hit is not None
    assert (hit.kind, hit.lineno, hit.marker.description()) == (
        PROBLEM_MARKERS,
        2,
        _MESSAGE,
    )
    help_event = QHelpEvent(QEvent.Type.ToolTip, point, gutter.mapToGlobal(point))
    gutter.event(help_event)
    qtbot.waitUntil(lambda: QToolTip.text() == _MESSAGE)

    assert gutter.accessibleDescription() == _MESSAGE
    accessible = QAccessible.queryAccessibleInterface(gutter)
    assert accessible is not None
    assert accessible.text(QAccessible.Text.Description) == _MESSAGE

    # No marker, no tooltip: the line number and a clean line.
    for elsewhere in (
        QPoint(_LANE + 4, point.y()),
        _marker_point(editor, 3),
    ):
        assert editor.marker_at(elsewhere) is None
        miss = QHelpEvent(QEvent.Type.ToolTip, elsewhere, gutter.mapToGlobal(elsewhere))
        gutter.event(miss)
        assert not miss.isAccepted()

    editor.set_problem_lines(set())
    assert gutter.accessibleDescription() == ""


def test_clicking_a_marker_reports_its_line(qtbot) -> None:
    editor = _editor(qtbot)
    editor.set_problem_messages({2: _MESSAGE, 4: "Another."})
    gutter = editor.line_number_area()
    clicked: list[int] = []
    editor.problem_marker_clicked.connect(clicked.append)

    qtbot.mouseClick(gutter, Qt.MouseButton.LeftButton, pos=_marker_point(editor, 4))
    qtbot.mouseClick(gutter, Qt.MouseButton.LeftButton, pos=_marker_point(editor, 2))
    qtbot.mouseClick(gutter, Qt.MouseButton.LeftButton, pos=_marker_point(editor, 3))
    number = QPoint(_LANE + 4, _marker_point(editor, 2).y())
    qtbot.mouseClick(gutter, Qt.MouseButton.LeftButton, pos=number)

    assert clicked == [4, 2]


def _window_action(window: MainWindow, text: str) -> QAction:
    found = [item for item in window.findChildren(QAction) if item.text() == text]
    assert len(found) == 1, text
    return found[0]


def _formatted(qtbot, text: str, source: str, status_text: str):
    window = create_main_window()
    qtbot.addWidget(window)
    window.resize(1100, 700)
    window.show()
    qtbot.waitExposed(window)
    editor = window.findChild(CodeEditor, "inputEditor")
    pane = window.findChild(ProblemsPane, "problemsPane")
    status = window.findChild(StatusMessage, "statusMessage")
    assert isinstance(editor, CodeEditor)
    assert isinstance(pane, ProblemsPane)
    assert isinstance(status, StatusMessage)
    if source != "JSON":
        picker = window.findChild(QComboBox, "inputFormat")
        assert isinstance(picker, QComboBox)
        picker.setCurrentText(source)
    editor.setPlainText(text)
    _window_action(window, "Format").trigger()
    qtbot.waitUntil(lambda: status.text() == status_text, timeout=15000)
    qtbot.waitUntil(pane.isVisible, timeout=15000)
    return window, editor, pane


def test_the_window_gives_each_marker_its_row_message(qtbot) -> None:
    _window, editor, pane = _formatted(qtbot, _TEXT, "JSON", "1 problem")

    assert pane.messages() == [_MESSAGE]
    hit = editor.marker_at(_marker_point(editor, 2))
    assert hit is not None
    assert hit.marker.description() == pane.messages()[0]
    assert editor.line_number_area().accessibleDescription() == _MESSAGE


def test_clicking_a_marker_selects_its_problems_row(qtbot) -> None:
    _window, editor, pane = _formatted(qtbot, _TWO_TABS, "YAML", "2 problems")
    problems = pane.list_widget()
    assert pane.lines() == [2, 3]
    assert problems.currentRow() == 0
    cursor = editor.textCursor().position()
    gutter = editor.line_number_area()

    qtbot.mouseClick(gutter, Qt.MouseButton.LeftButton, pos=_marker_point(editor, 3))

    assert problems.currentRow() == 1
    row = problems.itemWidget(problems.item(1))
    assert row is not None
    assert row.is_selected()
    hit = editor.marker_at(_marker_point(editor, 3))
    assert hit is not None
    assert hit.marker.description() == pane.messages()[1]
    # Selecting the row does not jump: the cursor stays where it was.
    assert editor.textCursor().position() == cursor

    qtbot.mouseClick(gutter, Qt.MouseButton.LeftButton, pos=_marker_point(editor, 2))

    assert problems.currentRow() == 0
