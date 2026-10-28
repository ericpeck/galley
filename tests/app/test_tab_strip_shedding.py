"""Narrow tab strips shed parts instead of clipping them (#91 items 1 and N1)."""

import sys
from collections.abc import Iterator

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction, QFont, QFontMetrics, QPalette
from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QPushButton,
    QSplitter,
    QToolTip,
    QWidget,
)
from pytestqt.exceptions import TimeoutError as WaitTimeout

from galley.app import fonts
from galley.app.main import create_main_window
from galley.app.pane import PaneRole, PaneSpec, PaneWidget, StripShed
from galley.app.problem_text import display_problem
from galley.app.problems_pane import ProblemsPane
from galley.app.problems_settings import (
    DEFAULT_WIDTH,
    MIN_WIDTH,
    problems_settings,
    save_problems_preferences,
)
from galley.core.problems import Problem

_ROLES = (PaneRole.INPUT, PaneRole.OUTPUT)
# The Windows runner's platform fonts are drawn at the same pixel sizes as
# macOS's offscreen ones, but its fallback family is much wider: with Pop
# out still in the strip, its strips shed at 1280 px and the titles
# shortened at 900 px. The "wide font" variant scales the fonts until these
# labels are as wide as they are there (what 20 pt draws on macOS), so
# macOS runs the Windows case too. Where the platform fonts are already
# that wide, the variant would only repeat the platform one, so it is
# skipped. The UI sample is the text measured on that runner when the strip
# still had Pop out. It only sets the font scale.
_WIDE_UI_SAMPLE = ("Output 1 Pop out", 185)
_WIDE_APP_SAMPLE = ("Output as", 109)


def _scaled_points(font: QFont, sample: tuple[str, int]) -> int | None:
    """Point size that draws ``sample`` at its target width, if that is wider."""
    text, target = sample
    width = QFontMetrics(font).size(0, text).width()
    if width >= target:
        return None
    return max(font.pointSize() + 1, round(font.pointSize() * target / width))


@pytest.fixture(params=["platform font", "wide font"])
def strip_font(request, monkeypatch, qapp) -> Iterator[str]:
    """Run once with the platform fonts and once with Windows-wide ones.

    Widgets with a stylesheet (the caption) take the application font, and
    the rest take ``ui_font``, so both grow.
    """
    if request.param == "platform font":
        yield request.param
        return
    ui_points = _scaled_points(fonts.ui_font(), _WIDE_UI_SAMPLE)
    app_points = _scaled_points(qapp.font(), _WIDE_APP_SAMPLE)
    if ui_points is None and app_points is None:
        pytest.skip(
            "The platform fonts already draw the strip labels as wide as the "
            "Windows runner's, so the platform variant covers this."
        )
    previous = QFont(qapp.font())
    if ui_points is not None:
        monkeypatch.setattr(fonts.ui_font, "__defaults__", (ui_points,))
    if app_points is not None:
        wide = QFont(previous)
        wide.setPointSize(app_points)
        qapp.setFont(wide)
    yield request.param
    qapp.setFont(previous)


def _show(qtbot, width: int = 1280):
    window = create_main_window()
    qtbot.addWidget(window)
    window.resize(width, 800)
    window.show()
    qtbot.waitExposed(window)
    return window


def _action(window, text: str) -> QAction:
    found = [item for item in window.findChildren(QAction) if item.text() == text]
    assert len(found) == 1, text
    return found[0]


def _open_problems(qtbot, window) -> ProblemsPane:
    pane = window.findChild(ProblemsPane, "problemsPane")
    assert isinstance(pane, ProblemsPane)
    pane.set_problems((display_problem(Problem("missing_comma", 2)),))
    show = _action(window, "Show Problems")
    show.trigger()
    show.trigger()
    qtbot.waitUntil(lambda: pane.isVisible() and pane.width() >= MIN_WIDTH)
    return pane


def _panes(window) -> list[PaneWidget]:
    found = []
    for spec in _ROLES:
        pane = window.findChild(PaneWidget, f"{spec.name}Pane")
        assert isinstance(pane, PaneWidget)
        found.append(pane)
    return found


def _saved_width() -> int:
    return int(problems_settings().value("problems/width", DEFAULT_WIDTH))


def _strip_parts(pane: PaneWidget) -> list[QWidget]:
    row = pane.tab_strip.layout()
    parts = []
    for index in range(row.count()):
        widget = row.itemAt(index).widget()
        if widget is not None and widget.isVisibleTo(pane.tab_strip):
            parts.append(widget)
    return parts


def _assert_strip_fits(pane: PaneWidget) -> None:
    """Every shown strip part has its full width, inside the strip, apart."""
    strip = pane.tab_strip
    parts = _strip_parts(pane)
    assert len(parts) >= 2
    right = 0
    for widget in sorted(parts, key=lambda item: item.x()):
        geometry = widget.geometry()
        hint = max(
            widget.minimumWidth(), min(widget.maximumWidth(), widget.sizeHint().width())
        )
        assert geometry.width() >= hint, (widget.objectName(), geometry, hint)
        assert geometry.left() >= right, widget.objectName()
        assert geometry.right() < strip.width(), (widget.objectName(), strip.width())
        right = geometry.right() + 1
    title = _title(pane)
    assert (
        title.fontMetrics().horizontalAdvance(title.text())
        <= title.contentsRect().width()
    )


def _title(pane: PaneWidget) -> QLabel:
    title = pane.findChild(QLabel, f"{pane.spec.name}TabTitle")
    assert isinstance(title, QLabel)
    return title


def _caption(pane: PaneWidget) -> QLabel:
    caption = pane.findChild(QLabel, f"{pane.spec.name}FormatCaption")
    assert isinstance(caption, QLabel)
    return caption


def _assert_names_kept(pane: PaneWidget) -> None:
    spec = pane.spec
    assert _title(pane).accessibleName() == spec.title
    assert pane.format_combo.accessibleName() == spec.format_label


def _assert_shed_state(pane: PaneWidget, shed: StripShed) -> None:
    """The parts shown, and the tooltips that stand in for the hidden words."""
    spec = pane.spec
    caption = _caption(pane)
    title = _title(pane)
    assert pane.strip_shed() is shed
    assert caption.isVisibleTo(pane) is (shed < StripShed.CAPTION)
    if shed < StripShed.CAPTION:
        assert pane.format_combo.toolTip() == ""
    else:
        assert pane.format_combo.toolTip() == spec.format_label
    if shed < StripShed.TITLE:
        assert title.text() == spec.title
        assert title.toolTip() == ""
    else:
        assert title.text() != spec.title
        assert title.text().endswith("…")
        assert spec.title.startswith(title.text()[:-1])
        assert title.toolTip() == spec.title
    _assert_names_kept(pane)


def _alone(qtbot, spec: PaneSpec) -> PaneWidget:
    pane = PaneWidget(spec)
    qtbot.addWidget(pane)
    # The window keeps panes at 240 px or more. Alone, the pane may go
    # narrower, so every font reaches the last step before its floor.
    pane.setMinimumWidth(0)
    pane.resize(pane.full_strip_width() + 40, 400)
    pane.show()
    qtbot.waitExposed(pane)
    return pane


@pytest.mark.usefixtures("scheme_palette")
@pytest.mark.parametrize("spec", _ROLES, ids=lambda spec: spec.name)
def test_strip_sheds_in_order_as_the_pane_narrows(qtbot, spec: PaneSpec) -> None:
    pane = _alone(qtbot, spec)
    seen: list[StripShed] = []
    floor = pane.min_strip_width()
    assert floor < pane.full_strip_width()
    for width in [*range(pane.width(), floor, -3), floor]:
        pane.resize(width, 400)
        qtbot.waitUntil(lambda width=width: pane.tab_strip.width() == width - 2)
        shed = pane.strip_shed()
        assert not seen or shed >= seen[-1], width
        if not seen or shed != seen[-1]:
            seen.append(shed)
        _assert_shed_state(pane, shed)
        _assert_strip_fits(pane)
    assert seen == [StripShed.NONE, StripShed.CAPTION, StripShed.TITLE]
    pane.resize(pane.full_strip_width(), 400)
    qtbot.waitUntil(lambda: pane.strip_shed() is StripShed.NONE)
    _assert_shed_state(pane, StripShed.NONE)
    _assert_strip_fits(pane)


def test_strip_sheds_only_the_caption_then_the_title() -> None:
    """Release-check PR 2c removed Pop out, and its shed step with it."""
    assert [step.name for step in StripShed] == ["NONE", "CAPTION", "TITLE"]
    assert not hasattr(StripShed, "POP_OUT_TEXT")


@pytest.mark.parametrize("spec", _ROLES, ids=lambda spec: spec.name)
def test_strip_has_no_pop_out_button(qtbot, spec: PaneSpec) -> None:
    pane = _alone(qtbot, spec)
    assert pane.findChild(QPushButton, f"{spec.name}PopOut") is None
    assert not hasattr(pane, "pop_out_button")
    names = [part.objectName() for part in _strip_parts(pane)]
    assert not any(name.endswith("PopOut") for name in names), names


@pytest.mark.parametrize("spec", _ROLES, ids=lambda spec: spec.name)
def test_strip_has_no_tab_buttons(qtbot, spec: PaneSpec) -> None:
    """Release-check PR 2d removed Tabs: the strip is title, caption, picker."""
    pane = _alone(qtbot, spec)
    for attr in ("close_tab_button", "new_tab_button"):
        assert not hasattr(pane, attr), attr
    names = [part.objectName() for part in _strip_parts(pane)]
    assert names == [
        f"{spec.name}TabTitle",
        f"{spec.name}FormatCaption",
        f"{spec.name}Format",
    ]
    assert pane.tab_strip.findChildren(QPushButton) == []
    for child in pane.tab_strip.findChildren(QWidget):
        for text in (child.accessibleName(), child.toolTip()):
            assert text not in ("Close", "New tab"), child.objectName()


def test_full_strip_width_is_where_shedding_starts(qtbot) -> None:
    pane = _alone(qtbot, PaneRole.OUTPUT)
    full = pane.full_strip_width()
    pane.resize(full, 400)
    qtbot.waitUntil(lambda: pane.width() == full)
    assert pane.strip_shed() is StripShed.NONE
    pane.resize(full - 1, 400)
    qtbot.waitUntil(lambda: pane.width() == full - 1)
    assert pane.strip_shed() is StripShed.CAPTION


def test_fit_tab_strip_takes_any_width(qtbot) -> None:
    """Compare's equal panes can fit a strip to a width they work out."""
    pane = _alone(qtbot, PaneRole.INPUT)
    assert pane.fit_tab_strip(10_000) is StripShed.NONE
    assert pane.fit_tab_strip(120) is StripShed.TITLE
    assert pane.fit_tab_strip() is StripShed.NONE


def test_shed_parts_keep_their_tooltips_on_hover(qtbot) -> None:
    window = _show(qtbot, 1280)
    _open_problems(qtbot, window)
    for pane in _panes(window):
        pane.fit_tab_strip(200)
        _assert_shed_state(pane, StripShed.TITLE)
        for widget, tip in (
            (_title(pane), pane.spec.title),
            (pane.format_combo, pane.spec.format_label),
        ):
            QToolTip.hideText()
            qtbot.mouseMove(window, QPoint(4, 4))
            qtbot.mouseMove(widget, QPoint(widget.width() // 2, widget.height() // 2))
            qtbot.waitUntil(lambda tip=tip: QToolTip.text() == tip, timeout=2000)
        assert pane.fit_tab_strip(10_000) is StripShed.NONE
        pane.fit_tab_strip()


def _measured(window, problems: ProblemsPane) -> str:
    """Widths for a failure message, so a CI log shows what each font needs."""
    panes = ", ".join(
        f"{pane.spec.name} {pane.width()}/{pane.full_strip_width()} "
        f"{pane.strip_shed().name}"
        for pane in _panes(window)
    )
    return f"window {window.width()}, problems {problems.width()}, {panes}"


def _unshed_window_width(window, problems_width: int) -> int:
    """Window width at which Problems has ``problems_width`` and nothing sheds."""
    outer = window.findChild(QSplitter, "problemsSplitter")
    inner = window.findChild(QSplitter, "mainSplitter")
    assert isinstance(outer, QSplitter)
    assert isinstance(inner, QSplitter)
    full = max(pane.full_strip_width() for pane in _panes(window))
    if inner.orientation() == Qt.Orientation.Vertical:
        panes = full
    else:
        panes = 2 * full + inner.handleWidth()
    return window.width() - outer.width() + problems_width + outer.handleWidth() + panes


def _expected_problems_width(window, saved: int) -> int:
    """D2 from measured strips: the saved width, less what the strips need."""
    room = window.width() - _unshed_window_width(window, 0)
    return min(saved, max(MIN_WIDTH, room))


def _wait_problems(qtbot, window, problems: ProblemsPane, width: int) -> None:
    """Wait for Problems to reach ``width``; on timeout, say what each part measured."""
    try:
        qtbot.waitUntil(lambda: problems.width() == width)
    except WaitTimeout:
        pytest.fail(f"Problems never reached {width} px: {_measured(window, problems)}")


def _assert_problems_give_way_first(window, problems: ProblemsPane) -> None:
    """D2: Problems narrows toward 200 px before a strip sheds anything."""
    detail = _measured(window, problems)
    saved = _saved_width()
    assert MIN_WIDTH <= problems.width() <= saved, detail
    for pane in _panes(window):
        unshed = pane.width() >= pane.full_strip_width()
        assert (pane.strip_shed() is StripShed.NONE) is unshed, detail
        if not unshed:
            assert problems.width() == MIN_WIDTH, detail
        _assert_shed_state(pane, pane.strip_shed())
        _assert_strip_fits(pane)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_900_px_with_problems_open_clips_nothing(
    qtbot, install_scheme, strip_font: str, scheme: str
) -> None:
    del strip_font
    window = _show(qtbot, 900)
    # The window applies the saved appearance when it is made, so the
    # scheme goes on after.
    install_scheme(scheme)
    surface = window.palette().color(QPalette.ColorRole.Window)
    assert (surface.lightness() < 128) is (scheme == "dark")
    problems = _open_problems(qtbot, window)
    qtbot.waitUntil(lambda: window.width() == 900)
    # Without Pop out, the platform fonts can fit both unshed strips beside
    # Problems at 900 px. Wide fonts still push Problems to 200 px and shed.
    expected = _expected_problems_width(window, DEFAULT_WIDTH)
    _wait_problems(qtbot, window, problems, expected)
    assert _saved_width() == DEFAULT_WIDTH
    for pane in _panes(window):
        if expected == MIN_WIDTH:
            assert pane.strip_shed() > StripShed.NONE, _measured(window, problems)
        picker = pane.format_combo
        assert isinstance(picker, QComboBox)
        assert picker.width() >= picker.sizeHint().width()
    _assert_problems_give_way_first(window, problems)


def test_1280_px_with_problems_open(qtbot, strip_font: str) -> None:
    window = _show(qtbot, 1280)
    problems = _open_problems(qtbot, window)
    qtbot.waitUntil(lambda: window.width() == 1280)
    roomy = _unshed_window_width(window, DEFAULT_WIDTH) <= 1280
    if strip_font == "platform font" and sys.platform == "darwin":
        assert roomy, _measured(window, problems)
    if roomy:
        _wait_problems(qtbot, window, problems, DEFAULT_WIDTH)
        for pane in _panes(window):
            assert pane.strip_shed() is StripShed.NONE
    else:
        _wait_problems(
            qtbot, window, problems, _expected_problems_width(window, DEFAULT_WIDTH)
        )
        assert problems.width() < DEFAULT_WIDTH
    _assert_problems_give_way_first(window, problems)


def test_stack_panes_at_900_px_fits_both_strips(qtbot, strip_font: str) -> None:
    window = _show(qtbot, 900)
    problems = _open_problems(qtbot, window)
    side_by_side = _expected_problems_width(window, DEFAULT_WIDTH)
    _wait_problems(qtbot, window, problems, side_by_side)
    stack = _action(window, "Stack Panes")
    stack.trigger()
    assert stack.isChecked()
    # Stacked panes take the full width, so Problems gets back whatever the
    # wider strip leaves. With very wide fonts that can still be nothing.
    stacked = _expected_problems_width(window, DEFAULT_WIDTH)
    if strip_font == "platform font" and sys.platform == "darwin":
        assert stacked == DEFAULT_WIDTH, _measured(window, problems)
    _wait_problems(qtbot, window, problems, stacked)
    _assert_problems_give_way_first(window, problems)
    stack.trigger()
    assert not stack.isChecked()
    _wait_problems(qtbot, window, problems, side_by_side)
    _assert_problems_give_way_first(window, problems)
    assert _saved_width() == DEFAULT_WIDTH


def test_problems_shrinks_first_and_keeps_its_saved_width(
    qtbot, strip_font: str
) -> None:
    del strip_font
    save_problems_preferences("open", 340)
    window = _show(qtbot, 900)
    problems = _open_problems(qtbot, window)
    wide = max(1280, _unshed_window_width(window, 340))
    window.resize(wide, 800)
    qtbot.waitUntil(lambda: window.width() == wide)
    _wait_problems(qtbot, window, problems, 340)
    _assert_problems_give_way_first(window, problems)
    for pane in _panes(window):
        assert pane.strip_shed() is StripShed.NONE

    # Narrow enough that Problems must give up 60 px, but not reach 200 px.
    middle = _unshed_window_width(window, 280)
    window.resize(middle, 800)
    qtbot.waitUntil(lambda: window.width() == middle)
    _wait_problems(qtbot, window, problems, 280)
    for pane in _panes(window):
        assert pane.strip_shed() is StripShed.NONE, _measured(window, problems)
    _assert_problems_give_way_first(window, problems)
    assert _saved_width() == 340

    window.resize(900, 800)
    qtbot.waitUntil(lambda: window.width() == 900)
    _wait_problems(qtbot, window, problems, _expected_problems_width(window, 340))
    _assert_problems_give_way_first(window, problems)
    assert _saved_width() == 340

    window.resize(wide, 800)
    _wait_problems(qtbot, window, problems, 340)
    assert _saved_width() == 340
