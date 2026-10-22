"""Narrow tab strips shed parts instead of clipping them (#91 items 1 and N1)."""

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QAction, QPalette
from PySide6.QtWidgets import QComboBox, QLabel, QSplitter, QToolTip, QWidget

from galley.app.actions import COMING_SOON
from galley.app.format_picker import PopOutButton
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
    assert len(parts) >= 5
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


def _pop_out(pane: PaneWidget) -> PopOutButton:
    button = pane.findChild(PopOutButton, f"{pane.spec.name}PopOut")
    assert isinstance(button, PopOutButton)
    return button


def _assert_names_kept(pane: PaneWidget) -> None:
    spec = pane.spec
    assert _title(pane).accessibleName() == spec.title
    assert pane.format_combo.accessibleName() == spec.format_label
    assert _pop_out(pane).accessibleName() == "Pop out into window"
    assert _pop_out(pane).text() == "Pop out"


def _assert_shed_state(pane: PaneWidget, shed: StripShed) -> None:
    """The parts shown, and the tooltips that stand in for the hidden words."""
    spec = pane.spec
    caption = _caption(pane)
    title = _title(pane)
    pop_out = _pop_out(pane)
    assert pane.strip_shed() is shed
    assert caption.isVisibleTo(pane) is (shed < StripShed.CAPTION)
    if shed < StripShed.CAPTION:
        assert pane.format_combo.toolTip() == ""
    else:
        assert pane.format_combo.toolTip() == spec.format_label
    assert pop_out.is_icon_only() is (shed >= StripShed.POP_OUT_TEXT)
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
    pane.resize(pane.full_strip_width() + 40, 400)
    pane.show()
    qtbot.waitExposed(pane)
    return pane


@pytest.mark.usefixtures("scheme_palette")
@pytest.mark.parametrize("spec", _ROLES, ids=lambda spec: spec.name)
def test_strip_sheds_in_order_as_the_pane_narrows(qtbot, spec: PaneSpec) -> None:
    pane = _alone(qtbot, spec)
    seen: list[StripShed] = []
    for width in range(pane.width(), pane.minimumWidth() - 1, -3):
        pane.resize(width, 400)
        qtbot.waitUntil(lambda width=width: pane.tab_strip.width() == width - 2)
        shed = pane.strip_shed()
        assert not seen or shed >= seen[-1], width
        if not seen or shed != seen[-1]:
            seen.append(shed)
        _assert_shed_state(pane, shed)
        _assert_strip_fits(pane)
    assert seen == [
        StripShed.NONE,
        StripShed.CAPTION,
        StripShed.POP_OUT_TEXT,
        StripShed.TITLE,
    ]
    pane.resize(pane.full_strip_width(), 400)
    qtbot.waitUntil(lambda: pane.strip_shed() is StripShed.NONE)
    _assert_shed_state(pane, StripShed.NONE)
    _assert_strip_fits(pane)


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
        pop_out = _pop_out(pane)
        assert pop_out.toolTip() == "Pop out into window — coming soon"
        assert pop_out.accessibleDescription() == COMING_SOON
        for widget, tip in (
            (_title(pane), pane.spec.title),
            (pane.format_combo, pane.spec.format_label),
            (pop_out, "Pop out into window — coming soon"),
        ):
            QToolTip.hideText()
            qtbot.mouseMove(window, QPoint(4, 4))
            qtbot.mouseMove(widget, QPoint(widget.width() // 2, widget.height() // 2))
            qtbot.waitUntil(lambda tip=tip: QToolTip.text() == tip, timeout=2000)
        assert pane.fit_tab_strip() is StripShed.NONE
        assert pop_out.toolTip() == COMING_SOON


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_900_px_with_problems_open_clips_nothing(
    qtbot, install_scheme, scheme: str
) -> None:
    window = _show(qtbot, 900)
    # The window applies the saved appearance when it is made, so the
    # scheme goes on after.
    install_scheme(scheme)
    surface = window.palette().color(QPalette.ColorRole.Window)
    assert (surface.lightness() < 128) is (scheme == "dark")
    problems = _open_problems(qtbot, window)
    qtbot.waitUntil(lambda: window.width() == 900)
    qtbot.waitUntil(lambda: problems.width() == MIN_WIDTH)
    assert _saved_width() == DEFAULT_WIDTH
    for pane in _panes(window):
        assert pane.strip_shed() > StripShed.NONE
        assert pane.strip_shed() < StripShed.TITLE
        _assert_shed_state(pane, pane.strip_shed())
        _assert_strip_fits(pane)
        picker = pane.format_combo
        assert isinstance(picker, QComboBox)
        assert picker.width() >= picker.sizeHint().width()


def test_1280_px_with_problems_open_sheds_nothing(qtbot) -> None:
    window = _show(qtbot, 1280)
    problems = _open_problems(qtbot, window)
    qtbot.waitUntil(lambda: problems.width() == DEFAULT_WIDTH)
    for pane in _panes(window):
        _assert_shed_state(pane, StripShed.NONE)
        _assert_strip_fits(pane)


def test_stack_panes_at_900_px_fits_both_strips(qtbot) -> None:
    window = _show(qtbot, 900)
    problems = _open_problems(qtbot, window)
    qtbot.waitUntil(lambda: problems.width() == MIN_WIDTH)
    stack = _action(window, "Stack Panes")
    stack.trigger()
    splitter = window.findChild(QSplitter, "mainSplitter")
    assert isinstance(splitter, QSplitter)
    assert stack.isChecked()
    # Stacked panes take the full width, so Problems goes back to its width.
    qtbot.waitUntil(lambda: problems.width() == DEFAULT_WIDTH)
    for pane in _panes(window):
        _assert_shed_state(pane, StripShed.NONE)
        _assert_strip_fits(pane)
    stack.trigger()
    qtbot.waitUntil(lambda: problems.width() == MIN_WIDTH)
    for pane in _panes(window):
        _assert_strip_fits(pane)
    assert _saved_width() == DEFAULT_WIDTH


def test_problems_shrinks_first_and_keeps_its_saved_width(qtbot) -> None:
    save_problems_preferences("open", 340)
    window = _show(qtbot, 1280)
    problems = _open_problems(qtbot, window)
    qtbot.waitUntil(lambda: problems.width() == 340)
    for pane in _panes(window):
        assert pane.strip_shed() is StripShed.NONE

    window.resize(1100, 800)
    qtbot.waitUntil(lambda: window.width() == 1100)
    # Problems gives way before either strip sheds.
    qtbot.waitUntil(lambda: problems.width() < 340)
    assert problems.width() >= MIN_WIDTH
    if problems.width() > MIN_WIDTH:
        for pane in _panes(window):
            assert pane.strip_shed() is StripShed.NONE
    assert _saved_width() == 340

    window.resize(900, 800)
    qtbot.waitUntil(lambda: problems.width() == MIN_WIDTH)
    for pane in _panes(window):
        _assert_strip_fits(pane)
    assert _saved_width() == 340

    window.resize(1280, 800)
    qtbot.waitUntil(lambda: problems.width() == 340)
    assert _saved_width() == 340
