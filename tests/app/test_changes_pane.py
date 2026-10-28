"""The Changes pane for Compare (#65 slice 3c).

Windows CI draws every glyph as the same box (#145), so nothing here reads
glyph shapes or text ink. Rows are checked through the model and its
roles, colors through ``row_colors`` and contrast, and the painted state
only at row edges and padding, where no text is drawn.
"""

import json
import random
from collections.abc import Callable
from unittest import mock

import pytest
from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPalette,
    QTextLayout,
    QTextOption,
)
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from galley.app import changes_pane as changes_pane_module
from galley.app.changes_pane import (
    ADD_SECOND_DOCUMENT,
    CHANGES_NOUNS,
    COMPARE_AGAIN,
    COMPARISON_CANCELED,
    COMPARISON_STOPPED,
    DEPTH_ROLE,
    EXPANDED_ROLE,
    FIX_ERRORS,
    NO_DIFFERENCES,
    ROW_ROLE,
    ChangesPane,
    ChangesState,
    change_detail,
    difference_count_text,
    line_text,
    row_colors,
)
from galley.app.results_panel import ResultRow
from galley.app.theme import change_color, contrast_ratio
from galley.core import diff as diff_module
from galley.core.compare_report import (
    ChangeRow,
    CompareReport,
    GroupRow,
    InfoRow,
    SideMarks,
    build_report,
)
from galley.core.diff import Span, Summary, diff_with_pairs, load_document

# Rows: Changed name, the users info row, groups users[1–2] and users[4–6],
# then Changed zip. The groups hold 2 and 3 changes, so 7 in all.
_LEFT = {"name": "Ada", "users": [1, 2, 3, 4, 5, 6], "zip": 1}
_RIGHT = {"name": "Bo", "users": [1, 9, 9, 4, 9, 9, 7], "zip": 2}
_KINDS = ("added", "removed", "changed", "moved")


def _report(left: object, right: object, *, budget: int | None = None) -> CompareReport:
    left_doc = load_document(json.dumps(left, indent=2), "json")
    right_doc = load_document(json.dumps(right, indent=2), "json")
    if budget is None:
        result, pairs = diff_with_pairs(left_doc, right_doc)
    else:
        with mock.patch.object(diff_module, "ALIGN_BUDGET", budget):
            result, pairs = diff_with_pairs(left_doc, right_doc)
    return build_report(result, left_doc, right_doc, pairs)


def _grouped() -> CompareReport:
    return _report(_LEFT, _RIGHT, budget=0)


def _every_kind() -> CompareReport:
    left = {"role": "user", "old": 1, "from": {"x": {"id": 7}}, "to": {}}
    right = {"role": "admin", "from": {}, "to": {"x": {"id": 7}}, "new": True}
    return _report(left, right)


def _pane(qtbot, report: CompareReport | None = None) -> ChangesPane:
    pane = ChangesPane()
    qtbot.addWidget(pane)
    pane.resize(640, 320)
    if report is not None:
        pane.set_report(report)
    pane.show()
    qtbot.waitExposed(pane)
    return pane


def _rows(pane: ChangesPane) -> list[object]:
    model = pane.model()
    return [model.index(row).data(ROW_ROLE) for row in range(model.rowCount())]


def _kinds(pane: ChangesPane) -> list[str]:
    """Each shown row as a short tag, for order checks."""
    tags = []
    for row in _rows(pane):
        if isinstance(row, ChangeRow):
            tags.append(f"{row.kind}:{row.path_text}")
        elif isinstance(row, GroupRow):
            tags.append(f"group:{row.path_text}")
        else:
            tags.append(f"info:{row.path_text}")
    return tags


def _current(pane: ChangesPane) -> object:
    return pane.model().row_at(pane.list_view().current_row())


# --- Rows and the model ---------------------------------------------------


def test_a_row_per_change_with_its_kind_path_detail_and_line(qtbot) -> None:
    pane = _pane(qtbot, _every_kind())
    rows = _rows(pane)
    assert [(row.label, row.path_text) for row in rows] == [
        ("Changed", "role"),
        ("Removed", "old"),
        ("Moved", "from.x"),
        ("Added", "new"),
    ]
    # Each value sits in a direction isolate, U+2068 ... U+2069.
    assert [change_detail(row) for row in rows] == [
        '\u2068"user"\u2069 → \u2068"admin"\u2069',
        "\u20681\u2069",
        "→ \u2068to.x\u2069",
        "\u2068true\u2069",
    ]
    assert rows[0].left_line == 2 and rows[3].right_line == 9
    assert [line_text(row) for row in rows] == ["Line 2", "Line 3", "5 → 5", "Line 9"]


def test_a_group_shows_its_first_line_and_an_info_row_none(qtbot) -> None:
    pane = _pane(qtbot, _grouped())
    rows = _rows(pane)
    assert [line_text(row) for row in rows] == [
        "Line 2",
        "",
        f"Line {rows[2].left_line}",
        f"Line {rows[3].left_line}",
        "Line 11",
    ]


def test_display_and_accessible_text_are_the_spoken_name(qtbot) -> None:
    pane = _pane(qtbot, _grouped())
    model = pane.model()
    for row in range(model.rowCount()):
        index = model.index(row)
        spoken = index.data(Qt.ItemDataRole.AccessibleTextRole)
        assert index.data(Qt.ItemDataRole.DisplayRole) == spoken
        assert spoken.startswith(index.data(ROW_ROLE).accessible_name)
    assert [model.index(row).data() for row in range(model.rowCount())] == [
        'Changed, name, "Ada" to "Bo"',
        "users, items compared by position, too complex to align",
        "users, 1 to 2, 2 items differ, collapsed",
        "users, 4 to 6, 3 items differ, collapsed",
        "Changed, zip, 1 to 2",
    ]


def test_a_group_says_whether_it_is_collapsed_or_expanded(qtbot) -> None:
    pane = _pane(qtbot, _grouped())
    model = pane.model()
    changed: list[list[int]] = []
    model.dataChanged.connect(lambda _top, _bottom, roles: changed.append(roles))
    spoken = Qt.ItemDataRole.AccessibleTextRole
    model.set_expanded(2, True)
    assert model.index(2).data(spoken) == "users, 1 to 2, 2 items differ, expanded"
    assert spoken in changed[-1]
    model.set_expanded(2, False)
    assert model.index(2).data(spoken) == "users, 1 to 2, 2 items differ, collapsed"


def test_every_row_has_its_whole_text_as_a_tooltip(qtbot) -> None:
    pane = _pane(qtbot, _long_path_report())
    tip = pane.model().index(0).data(Qt.ItemDataRole.ToolTipRole)
    assert tip == f"Changed · {_LONG_PATH} · \u20687\u2069 → \u206810\u2069"
    pane.set_report(_grouped())
    model = pane.model()
    assert [
        model.index(row).data(Qt.ItemDataRole.ToolTipRole)
        for row in range(model.rowCount())
    ] == [
        'Changed · name · \u2068"Ada"\u2069 → \u2068"Bo"\u2069',
        "users: items compared by position (too complex to align)",
        "users[1–2]: 2 items differ",
        "users[4–6]: 3 items differ",
        "Changed · zip · \u20681\u2069 → \u20682\u2069",
    ]


_LONG_KEY = "estimated_delivery_window_days"
_LONG_PATH = f"shipping.regions.north_america.{_LONG_KEY}"


def _long_path_report() -> CompareReport:
    def nested(value: int) -> dict[str, object]:
        return {"shipping": {"regions": {"north_america": {_LONG_KEY: value}}}}

    return _report(nested(7), nested(10))


def _drawn_path(pane: ChangesPane) -> str:
    return next(text for text, _box, _needed in _drawn(pane) if "shipping" in text)


def test_a_long_path_is_cut_in_the_middle_and_keeps_its_last_key(qtbot) -> None:
    pane = _pane(qtbot, _long_path_report())
    metrics = QFontMetrics(pane.list_view().font())
    # Room for about two thirds of the path beside the value.
    pane.resize(metrics.horizontalAdvance(_LONG_PATH) + 120, 200)
    path = _drawn_path(pane)
    assert path != _LONG_PATH
    assert path.startswith("shipping.") and path.endswith(_LONG_KEY[-6:])
    assert "…" in path
    # The value is short, so it still shows in full.
    assert any("10" in text and "…" not in text for text, *_ in _drawn(pane))


def test_a_path_takes_the_room_a_short_value_does_not_need(qtbot) -> None:
    # Wide enough for the path and the value, but not for the path in half.
    pane = _pane(qtbot, _long_path_report())
    metrics = QFontMetrics(pane.list_view().font())
    path_width = metrics.horizontalAdvance(_LONG_PATH)
    pane.resize(path_width + 320, 200)
    assert _drawn_path(pane) == _LONG_PATH


def test_a_long_value_keeps_a_minimum_beside_a_long_path() -> None:
    metrics = QFontMetrics(QFont())
    floor = metrics.averageCharWidth() * 16
    long_value = "x" * 400
    assert changes_pane_module.path_width_for(metrics, 1000, long_value) == (
        1000 - floor - 8
    )
    short_value = "7 → 10"
    needed = metrics.horizontalAdvance(short_value)
    assert changes_pane_module.path_width_for(metrics, 1000, short_value) == (
        1000 - needed - 8
    )
    assert changes_pane_module.path_width_for(metrics, 1000, "") == 1000


def test_info_rows_are_muted_notes_that_are_not_counted(qtbot) -> None:
    pane = _pane(qtbot, _grouped())
    model = pane.model()
    info = model.index(1)
    assert isinstance(info.data(ROW_ROLE), InfoRow)
    assert model.flags(info) == Qt.ItemFlag.NoItemFlags
    assert model.flags(model.index(0)) == (
        Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
    )
    assert pane.count() == 7 == _grouped().count


def test_the_count_includes_grouped_items() -> None:
    report = _report([0, 1] * 3000, [1, 0] * 3000)
    assert report.count == 6000
    assert difference_count_text(report.count) == "6,000 differences"
    assert difference_count_text(1) == "1 difference"
    assert difference_count_text(12) == "12 differences"
    assert CHANGES_NOUNS == ("difference", "differences")


def test_groups_start_collapsed_and_children_are_indented(qtbot) -> None:
    pane = _pane(qtbot, _grouped())
    model = pane.model()
    assert model.rowCount() == 5
    assert model.index(2).data(EXPANDED_ROLE) is False
    assert model.index(0).data(EXPANDED_ROLE) is None
    model.set_expanded(2, True)
    assert model.rowCount() == 7
    assert [model.index(row).data(DEPTH_ROLE) for row in range(7)] == [
        0, 0, 0, 1, 1, 0, 0
    ]  # fmt: skip
    assert _kinds(pane)[2:5] == [
        "group:users[1–2]",
        "changed:users[1]",
        "changed:users[2]",
    ]


def _drawn(pane: ChangesPane) -> list[tuple[str, int, int]]:
    """Each text the delegate draws: the text, its box width, and the width
    the text needs in the font it is drawn with. Font metrics, not ink."""
    calls: list[tuple[str, int, int]] = []
    original = changes_pane_module._draw

    def _record(painter, x, rect, width, text, **kwargs) -> None:
        needed = QFontMetrics(painter.font()).horizontalAdvance(text)
        calls.append((text, width, needed))
        original(painter, x, rect, width, text, **kwargs)

    with mock.patch.object(changes_pane_module, "_draw", _record):
        pane.list_view().viewport().grab()
    return calls


def _far_report(line: int) -> CompareReport:
    """One Changed row on ``line`` of both sides, as a large file has."""
    span = Span(line, 3, line, 9)
    row = ChangeRow(
        kind="changed",
        glyph="~",
        label="Changed",
        path_text="z",
        old=Summary("number", "1"),
        new=Summary("number", "2"),
        moved_to_text=None,
        left_span=span,
        right_span=span,
        accessible_name="Changed, z, 1 to 2",
    )
    return CompareReport((row,), 1, SideMarks(), SideMarks())


@pytest.mark.parametrize("line", [9, 99_999, 120_004, 1_234_567])
def test_the_line_column_fits_the_largest_line_number(qtbot, line: int) -> None:
    pane = _pane(qtbot, _far_report(line))
    drawn = _drawn(pane)
    assert drawn, "nothing was drawn"
    shown = [entry for entry in drawn if str(line) in entry[0]]
    assert len(shown) == 1
    for text, box, needed in drawn:
        assert needed <= box, f"{text!r} needs {needed} px in a {box} px box"


def _tied_line_texts_report() -> CompareReport:
    """ "Line 18" and a Moved "22 → 24" have the same number of characters,
    and the Moved one is wider in most fonts. The Moved row is also a group
    child, which is not drawn until the group is expanded."""
    changed = ChangeRow(
        kind="changed",
        glyph="~",
        label="Changed",
        path_text="a",
        old=Summary("number", "1"),
        new=Summary("number", "2"),
        moved_to_text=None,
        left_span=Span(18, 3, 18, 9),
        right_span=Span(18, 3, 18, 9),
        accessible_name="Changed, a, 1 to 2",
    )
    moved = ChangeRow(
        kind="moved",
        glyph="⇄",
        label="Moved",
        path_text="b.x",
        old=Summary("number", "3"),
        new=Summary("number", "3"),
        moved_to_text="c.x",
        left_span=Span(22, 3, 22, 9),
        right_span=Span(24, 3, 24, 9),
        accessible_name="Moved, b, x, now at c, x",
    )
    group = GroupRow(
        path_text="d[0–1]",
        first=0,
        last=1,
        text="d[0–1]: 2 items differ",
        accessible_name="d, 0 to 1, 2 items differ",
        children=(moved, changed),
    )
    return CompareReport((changed, moved, group), 4, SideMarks(), SideMarks())


def test_every_line_text_gets_the_width_it_needs(qtbot) -> None:
    report = _tied_line_texts_report()
    pane = _pane(qtbot, report)
    pane.model().set_expanded(2, True)
    texts = [line_text(row) for row in report.rows]
    texts += [line_text(child) for child in report.rows[2].children]
    assert "Line 18" in texts and "22 → 24" in texts
    drawn = _drawn(pane)
    drawn_lines = [entry for entry in drawn if entry[0] in texts and entry[0]]
    assert {text for text, *_ in drawn_lines} >= {"Line 18", "22 → 24"}
    for text, box, needed in drawn:
        assert needed <= box, f"{text!r} needs {needed} px in a {box} px box"
    # Group children count whether or not they are shown.
    pane.model().set_expanded(2, False)
    for text, box, needed in _drawn(pane):
        if text in texts:
            metrics = QFontMetrics(pane.list_view().font())
            widest = max(metrics.horizontalAdvance(t) for t in texts)
            assert box >= widest, f"the column is {box} px; {widest} px is needed"
            assert needed <= box


def _changed(old: Summary, new: Summary) -> ChangeRow:
    span = Span(2, 3, 2, 9)
    return ChangeRow(
        kind="changed",
        glyph="~",
        label="Changed",
        path_text="greeting",
        old=old,
        new=new,
        moved_to_text=None,
        left_span=span,
        right_span=span,
        accessible_name="Changed, greeting",
    )


# Fake right-to-left values: Hebrew "shalom" and "olam", Arabic "marhaba".
_SHALOM = '"\u05e9\u05dc\u05d5\u05dd"'
_OLAM = '"\u05e2\u05d5\u05dc\u05dd"'
_MARHABA = '"\u0645\u0631\u062d\u0628\u0627"'


def _visual_span(text: str, start: int, end: int) -> tuple[float, float]:
    """Where ``text[start:end]`` lands, laid out the way the painter lays out
    a row: the paragraph direction comes from the text itself."""
    layout = QTextLayout(text, QFont())
    option = QTextOption()
    option.setTextDirection(Qt.LayoutDirection.LayoutDirectionAuto)
    layout.setTextOption(option)
    layout.beginLayout()
    line = layout.createLine()
    layout.endLayout()
    xs = []
    for index in range(start, end + 1):
        x = line.cursorToX(index)
        xs.append(x[0] if isinstance(x, tuple) else x)
    return min(xs), max(xs)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (Summary("string", _SHALOM), Summary("string", _OLAM)),
        (Summary("string", _SHALOM), Summary("number", "2")),
        (Summary("number", "1"), Summary("string", _MARHABA)),
    ],
)
def test_right_to_left_values_keep_old_before_new(old: Summary, new: Summary) -> None:
    detail = change_detail(_changed(old, new))
    old_at = detail.find(old.text)
    new_at = detail.rfind(new.text)
    assert 0 <= old_at < new_at
    old_left, old_right = _visual_span(detail, old_at, old_at + len(old.text))
    new_left, new_right = _visual_span(detail, new_at, new_at + len(new.text))
    assert old_right <= new_left, (detail, (old_left, old_right), (new_left, new_right))


# --- Keys -------------------------------------------------------------


def test_up_and_down_move_between_rows_and_skip_info_rows(qtbot) -> None:
    pane = _pane(qtbot, _grouped())
    view = pane.list_view()
    view.setFocus()
    qtbot.waitUntil(view.hasFocus)
    assert view.current_row() == 0
    qtbot.keyClick(view, Qt.Key.Key_Down)
    assert view.current_row() == 2
    qtbot.keyClick(view, Qt.Key.Key_Down)
    assert view.current_row() == 3
    qtbot.keyClick(view, Qt.Key.Key_Up)
    qtbot.keyClick(view, Qt.Key.Key_Up)
    assert view.current_row() == 0


@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_return_jumps_to_the_current_change(qtbot, key: Qt.Key) -> None:
    pane = _pane(qtbot, _grouped())
    view = pane.list_view()
    view.setFocus()
    jumped: list[object] = []
    pane.activated.connect(jumped.append)
    qtbot.keyClick(view, key)
    assert jumped == [_rows(pane)[0]]
    view.select_row(2)
    qtbot.keyClick(view, key)
    assert isinstance(jumped[-1], GroupRow)


def test_escape_asks_to_leave(qtbot) -> None:
    pane = _pane(qtbot, _grouped())
    view = pane.list_view()
    view.setFocus()
    with qtbot.waitSignal(pane.escape_pressed, timeout=1000):
        qtbot.keyClick(view, Qt.Key.Key_Escape)


def test_right_expands_and_left_collapses_a_group(qtbot) -> None:
    pane = _pane(qtbot, _grouped())
    view = pane.list_view()
    model = pane.model()
    view.setFocus()
    view.select_row(2)
    qtbot.keyClick(view, Qt.Key.Key_Right)
    assert model.is_expanded(2) and model.rowCount() == 7
    # Left on a child goes back to its group, then collapses it.
    qtbot.keyClick(view, Qt.Key.Key_Down)
    assert view.current_row() == 3
    qtbot.keyClick(view, Qt.Key.Key_Left)
    assert view.current_row() == 2 and model.is_expanded(2)
    qtbot.keyClick(view, Qt.Key.Key_Left)
    assert not model.is_expanded(2) and model.rowCount() == 5
    assert view.current_row() == 2


def test_a_click_toggles_a_group_and_jumps_to_a_change(qtbot) -> None:
    pane = _pane(qtbot, _grouped())
    view = pane.list_view()
    model = pane.model()
    jumped: list[object] = []
    pane.activated.connect(jumped.append)
    group = view.visualRect(model.index(2)).center()
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=group)
    assert model.is_expanded(2) and jumped == []
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=group)
    assert not model.is_expanded(2)
    change = view.visualRect(model.index(4)).center()
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=change)
    assert jumped == [_rows(pane)[4]]


# --- Next and previous ----------------------------------------------------


def _walk(pane: ChangesPane, step: Callable[[], bool]) -> list[str]:
    """The rows ``step`` lands on until it stops, at most 20 steps."""
    visited = []
    for _ in range(20):
        if not step():
            return visited
        visited.append(_kinds(pane)[pane.list_view().current_row()])
    pytest.fail(f"navigation never stopped: {visited}")


def test_next_and_previous_skip_info_rows_and_stop_at_the_ends(qtbot) -> None:
    pane = _pane(qtbot, _grouped())
    jumped: list[object] = []
    pane.activated.connect(jumped.append)
    assert _current(pane) is _rows(pane)[0]
    visited = _walk(pane, pane.next_change)
    # A collapsed group is one stop; the info row is never one.
    assert visited == [
        "group:users[1–2]",
        "group:users[4–6]",
        "changed:zip",
    ]
    assert len(jumped) == 3
    assert not pane.next_change()
    assert _kinds(pane)[pane.list_view().current_row()] == "changed:zip"
    back = _walk(pane, pane.previous_change)
    assert back == [
        "group:users[4–6]",
        "group:users[1–2]",
        "changed:name",
    ]
    assert not pane.previous_change()
    assert pane.list_view().current_row() == 0


def test_next_steps_through_an_expanded_group(qtbot) -> None:
    pane = _pane(qtbot, _grouped())
    pane.model().set_expanded(3, True)
    visited = _walk(pane, pane.next_change)
    assert visited == [
        "group:users[1–2]",
        "changed:users[4]",
        "changed:users[5]",
        "added:users[6]",
        "changed:zip",
    ]


def test_next_and_previous_do_nothing_without_rows(qtbot) -> None:
    pane = _pane(qtbot)
    assert not pane.next_change()
    assert not pane.previous_change()
    pane.set_report(_report({"a": 1}, {"a": 1}))
    assert not pane.next_change()


# --- States ---------------------------------------------------------------


def test_every_empty_state_reads_verbatim(qtbot) -> None:
    pane = _pane(qtbot)
    assert pane.state() is ChangesState.EMPTY_RIGHT
    assert pane.message() == "Add a second document to compare." == ADD_SECOND_DOCUMENT
    pane.set_report(_report({"a": 1, "b": 2}, {"b": 2, "a": 1}))
    assert pane.state() is ChangesState.NO_DIFFERENCES
    assert pane.message() == "No differences." == NO_DIFFERENCES
    assert pane.message_glyph() == "✓"
    pane.show_errors()
    assert pane.message() == "Fix errors to compare." == FIX_ERRORS
    pane.show_canceled()
    assert pane.message() == "Comparison canceled." == COMPARISON_CANCELED
    pane.show_stopped()
    assert pane.message() == (
        "The comparison stopped unexpectedly. Your documents weren't changed."
    )
    assert pane.message() == COMPARISON_STOPPED
    pane.show_empty_right()
    assert pane.message() == ADD_SECOND_DOCUMENT
    assert pane.count() == 0


def test_only_no_differences_shows_the_check(qtbot) -> None:
    pane = _pane(qtbot)
    for show in (pane.show_errors, pane.show_canceled, pane.show_stopped):
        show()
        assert pane.message_glyph() == ""
    pane.set_report(_report({"a": 1}, {"a": 1}))
    assert pane.message_glyph() == "✓"


def test_compare_again_shows_only_when_the_comparison_stopped(qtbot) -> None:
    pane = _pane(qtbot)
    button = pane.compare_again_button()
    assert button.text() == "Compare Again" == COMPARE_AGAIN
    assert button.accessibleName() == "Compare Again"
    for show in (pane.show_empty_right, pane.show_errors, pane.show_canceled):
        show()
        assert not button.isVisible()
    pane.show_stopped()
    assert button.isVisible()
    with qtbot.waitSignal(pane.compare_again_requested, timeout=1000):
        qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    pane.set_report(_grouped())
    assert pane.state() is ChangesState.ROWS
    assert not button.isVisible()
    assert pane.message() == ""


def test_accessible_names(qtbot) -> None:
    pane = _pane(qtbot, _every_kind())
    assert pane.accessibleName() == "Changes"
    assert pane.list_view().accessibleName() == "Changes"
    model = pane.model()
    assert [
        model.index(row).data(Qt.ItemDataRole.AccessibleTextRole)
        for row in range(model.rowCount())
    ] == [
        'Changed, role, "user" to "admin"',
        "Removed, old, 1",
        "Moved, from, x, now at to, x",
        "Added, new, true",
    ]
    pane.show_stopped()
    message = pane.findChild(QWidget, "changesMessage")
    assert message is not None
    assert message.accessibleName() == COMPARISON_STOPPED


# --- Focus outline (#68) --------------------------------------------------


def _host(qtbot, report: CompareReport) -> tuple[QWidget, QPushButton, ChangesPane]:
    """A button before the pane, so Tab can move focus into the list.

    pytest-qt holds added widgets weakly, so the test keeps the host.
    """
    host = QWidget()
    qtbot.addWidget(host)
    before = QPushButton("Before")
    before.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    pane = ChangesPane()
    pane.set_report(report)
    layout = QVBoxLayout(host)
    layout.addWidget(before)
    layout.addWidget(pane)
    host.resize(640, 360)
    host.show()
    qtbot.waitExposed(host)
    return host, before, pane


def _tab_into(qtbot, before: QPushButton, pane: ChangesPane) -> None:
    before.setFocus()
    qtbot.waitUntil(before.hasFocus)
    qtbot.keyClick(before, Qt.Key.Key_Tab)
    qtbot.waitUntil(pane.list_view().hasFocus)


def _row_edge(pane: ChangesPane, row: int) -> QColor:
    """The painted color at the middle of a row's top edge, where no text is."""
    view = pane.list_view()
    rect = view.visualRect(view.model().index(row))
    image = view.viewport().grab().toImage()
    return image.pixelColor(rect.center().x(), rect.top())


def test_the_outline_shows_for_keyboard_focus_only(qtbot) -> None:
    _host_widget, before, pane = _host(qtbot, _grouped())
    view = pane.list_view()
    assert not view.outline_visible()
    _tab_into(qtbot, before, pane)
    assert view.outline_visible()
    qtbot.keyClick(view, Qt.Key.Key_Down)
    assert view.outline_visible()
    # A click hides it, as in the Problems list.
    point = view.visualRect(view.model().index(4)).center()
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert not view.outline_visible()
    # Focus leaving hides it; Tab back brings it back.
    _tab_into(qtbot, before, pane)
    assert view.outline_visible()
    before.setFocus()
    qtbot.waitUntil(lambda: not view.hasFocus())
    assert not view.outline_visible()


def test_the_outline_matches_the_problems_rows(qtbot, scheme_palette) -> None:
    _host_widget, before, pane = _host(qtbot, _grouped())
    _tab_into(qtbot, before, pane)
    changes_edge = _row_edge(pane, 0)

    problem = ResultRow("Error")
    qtbot.addWidget(problem)
    problem.resize(640, 56)
    problem.set_state(selected=True, focused=True)
    image = problem.grab().toImage()
    problems_edge = image.pixelColor(image.width() // 2, 0)
    assert changes_edge.rgb() == problems_edge.rgb()
    window = scheme_palette.color(QPalette.ColorRole.Window)
    assert contrast_ratio(changes_edge, window) >= 3

    # Without keyboard focus the current row has its fill but no outline.
    before.setFocus()
    qtbot.waitUntil(lambda: not pane.list_view().hasFocus())
    fill = row_colors(pane.palette(), selected=True).background
    assert _row_edge(pane, 0).rgb() == fill.rgb()


def test_the_selected_row_paints_the_shared_fill(qtbot, scheme_palette) -> None:
    del scheme_palette
    pane = _pane(qtbot, _grouped())
    view = pane.list_view()
    rect = view.visualRect(view.model().index(0))
    image = view.viewport().grab().toImage()
    # Bottom padding, right of the 3 px bar: fill only, no text.
    padding = image.pixelColor(rect.left() + 6, rect.bottom() - 1)
    assert padding.rgb() == row_colors(pane.palette(), selected=True).background.rgb()
    other = view.visualRect(view.model().index(4))
    unselected = image.pixelColor(other.left() + 6, other.bottom() - 1)
    assert unselected.rgb() != padding.rgb()


# --- Colors and contrast --------------------------------------------------


@pytest.mark.parametrize("selected", [False, True])
def test_rows_read_in_light_and_dark(scheme_palette, selected: bool) -> None:
    for kind in (*_KINDS, None):
        colors = row_colors(scheme_palette, selected=selected, kind=kind)
        assert contrast_ratio(colors.text, colors.background) >= 4.5
        assert contrast_ratio(colors.muted, colors.background) >= 4.5
        assert contrast_ratio(colors.glyph, colors.background) >= 3


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("selected", [False, True])
def test_rows_read_with_the_app_palettes(install_scheme, scheme, selected) -> None:
    palette = install_scheme(scheme)
    for kind in _KINDS:
        colors = row_colors(palette, selected=selected, kind=kind)
        assert contrast_ratio(colors.text, colors.background) >= 4.5
        assert contrast_ratio(colors.muted, colors.background) >= 4.5
        assert contrast_ratio(colors.glyph, colors.background) >= 3


def test_glyphs_use_the_3b_kind_inks_where_they_reach_3_to_1(scheme_palette) -> None:
    for kind in _KINDS:
        ink = change_color(kind, scheme_palette)
        resting = row_colors(scheme_palette, selected=False, kind=kind)
        assert resting.glyph.rgb() == ink.rgb()
        selected = row_colors(scheme_palette, selected=True, kind=kind)
        if contrast_ratio(ink, selected.background) >= 3:
            assert selected.glyph.rgb() == ink.rgb()


def test_every_row_kind_paints_without_error(qtbot, scheme_palette) -> None:
    del scheme_palette
    pane = _pane(qtbot, _grouped())
    pane.model().set_expanded(2, True)
    pane.list_view().viewport().grab()
    pane.set_report(_every_kind())
    pane.list_view().viewport().grab()
    assert pane.model().rowCount() == 4


def test_a_new_report_resets_groups_and_selects_the_first_change(qtbot) -> None:
    pane = _pane(qtbot, _grouped())
    pane.model().set_expanded(2, True)
    pane.list_view().select_row(4)
    pane.set_report(_grouped())
    assert pane.model().rowCount() == 5
    assert pane.list_view().current_row() == 0
    assert pane.model().index(-1, 0) == QModelIndex()


# --- Performance ----------------------------------------------------------

# Isaac's #65 budget for 3b–3e: a jump to the next difference on a 1 MB
# diff, model and selection only (drawing counts against #117).
_NEXT_CHANGE_BUDGET_S = 0.05


def _one_megabyte_report() -> CompareReport:
    rng = random.Random(65)
    records = [
        {
            "id": index,
            "name": f"user-{index}",
            "email": f"u{index}@example.com",
            "city": rng.choice(["Oslo", "Lima", "Pune"]),
            "score": rng.randint(0, 999),
        }
        for index in range(7_500)
    ]
    changed = [dict(record) for record in records]
    for record in rng.sample(changed, 1_500):
        record["score"] += 1
    return _report({"records": records}, {"records": changed})


@pytest.mark.perf
def test_next_change_on_a_1_mb_diff_is_within_budget(qtbot, perf_budget) -> None:
    report = _one_megabyte_report()
    assert report.count == 1_500
    pane = _pane(qtbot, report)
    middle = pane.model().rowCount() // 2

    def _start_in_the_middle() -> None:
        pane.list_view().select_row(middle)

    perf_budget.within(
        _NEXT_CHANGE_BUDGET_S,
        pane.next_change,
        setup=_start_in_the_middle,
        fixture="1 MB JSON pair, 1,500 changes, next change from the middle",
    )
    assert pane.list_view().current_row() == middle + 1
