"""Status-bar slots: state, counts, progress, and info."""

from collections import Counter

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QComboBox, QLabel, QProgressBar, QPushButton, QWidget

from galley.app.main import create_main_window
from galley.app.status_bar import INFO_RESERVE, Busy, StatusBar
from galley.app.status_message import StatusMessage
from galley.app.style import muted_text_style
from galley.app.theme import contrast_ratio


def _show(qtbot):
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def _bar(window) -> StatusBar:
    bar = window.statusBar()
    assert isinstance(bar, StatusBar)
    return bar


def _child(window, kind, name: str):
    widget = window.findChild(kind, name)
    assert isinstance(widget, kind), name
    return widget


def _left(widget: QWidget, bar: StatusBar) -> int:
    return widget.mapTo(bar, widget.rect().topLeft()).x()


def _right(widget: QWidget, bar: StatusBar) -> int:
    return widget.mapTo(bar, widget.rect().topRight()).x()


def _settle(qtbot, bar: StatusBar) -> None:
    layout = bar.layout()
    assert layout is not None
    layout.activate()
    qtbot.wait(0)


def test_counts_slot_is_empty_and_hidden_by_default(qtbot) -> None:
    window = _show(qtbot)
    bar = _bar(window)
    counts = _child(window, QLabel, "statusCounts")
    assert counts.isHidden()
    assert bar.counts_text() == ""


def test_setting_counts_shows_the_slot_and_clearing_hides_it(qtbot) -> None:
    window = _show(qtbot)
    bar = _bar(window)
    message = _child(window, QLabel, "statusMessage")
    counts = _child(window, QLabel, "statusCounts")
    _settle(qtbot, bar)
    empty_width = message.width()

    bar.set_counts("5 differences")
    _settle(qtbot, bar)
    assert counts.isVisible()
    assert counts.text() == "5 differences"
    assert counts.accessibleName() == "5 differences"
    assert message.width() < empty_width

    bar.set_counts("")
    _settle(qtbot, bar)
    assert counts.isHidden()
    assert counts.accessibleName() == ""
    assert message.width() == empty_width


def _edges(bar: StatusBar, *widgets: QWidget) -> list[tuple[int, int]]:
    return [(_left(widget, bar), _right(widget, bar)) for widget in widgets]


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_growing_counts_never_move_counts_or_progress(
    qtbot, install_scheme, scheme
) -> None:
    window = _show(qtbot)
    install_scheme(scheme)
    bar = _bar(window)
    counts = _child(window, QLabel, "statusCounts")
    progress = _child(window, QProgressBar, "writeProgress")
    cancel = _child(window, QPushButton, "cancelButton")
    bar.set_busy(Busy.PROGRESS)
    bar.set_counts("1 difference")
    _settle(qtbot, bar)
    edges = _edges(bar, counts, progress, cancel)
    for text in ("9 differences", "10 differences", "10000 differences"):
        bar.set_counts(text)
        _settle(qtbot, bar)
        assert counts.text() == text
        assert _edges(bar, counts, progress, cancel) == edges, text


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_info_changes_never_move_any_slot(qtbot, install_scheme, scheme) -> None:
    window = _show(qtbot)
    install_scheme(scheme)
    bar = _bar(window)
    counts = _child(window, QLabel, "statusCounts")
    progress = _child(window, QProgressBar, "writeProgress")
    cancel = _child(window, QPushButton, "cancelButton")
    direction = _child(window, QLabel, "directionLabel")
    assert bar.info_text() == "JSON → JSON"
    bar.set_busy(Busy.PROGRESS)
    bar.set_counts("5 differences")
    _settle(qtbot, bar)
    edges = _edges(bar, counts, progress, cancel, direction)
    for text in (*INFO_RESERVE, "JSON → JSON"):
        bar.set_info(text)
        _settle(qtbot, bar)
        assert bar.info_text() == text
        assert direction.alignment() & Qt.AlignmentFlag.AlignLeft
        assert _edges(bar, counts, progress, cancel, direction) == edges, text


def test_format_pickers_never_move_the_info_slot(qtbot) -> None:
    window = _show(qtbot)
    bar = _bar(window)
    direction = _child(window, QLabel, "directionLabel")
    pickers = [
        _child(window, QComboBox, name) for name in ("inputFormat", "outputFormat")
    ]
    _settle(qtbot, bar)
    edges = _edges(bar, direction)
    for left in ("YAML", "JSON"):
        for right in ("YAML", "JSON"):
            pickers[0].setCurrentText(left)
            pickers[1].setCurrentText(right)
            _settle(qtbot, bar)
            assert direction.text() == f"{left} → {right}"
            assert _edges(bar, direction) == edges


def test_a_tally_wider_than_the_reserve_widens_until_cleared(qtbot) -> None:
    window = _show(qtbot)
    bar = _bar(window)
    counts = _child(window, QLabel, "statusCounts")
    reserved = counts.reserved_width()
    bar.set_counts("1 difference")
    _settle(qtbot, bar)
    assert counts.width() == reserved

    bar.set_counts("123456789 differences, 12345 moved")
    _settle(qtbot, bar)
    wide = counts.width()
    assert wide > reserved
    bar.set_counts("1 difference")
    _settle(qtbot, bar)
    assert counts.width() == wide

    bar.set_counts("")
    bar.set_counts("1 difference")
    _settle(qtbot, bar)
    assert counts.width() == reserved


def test_reserves_follow_a_font_change(qtbot) -> None:
    window = _show(qtbot)
    counts = _child(window, QLabel, "statusCounts")
    direction = _child(window, QLabel, "directionLabel")
    for label in (counts, direction):
        before = label.minimumWidth()
        font = QFont(label.font())
        font.setPixelSize(font.pixelSize() * 2)
        label.setFont(font)
        assert label.minimumWidth() > before
        assert label.minimumWidth() == label.reserved_width()


def test_counts_do_not_move_when_the_state_text_changes(qtbot) -> None:
    window = _show(qtbot)
    bar = _bar(window)
    counts = _child(window, QLabel, "statusCounts")
    bar.set_counts("5 differences")
    _settle(qtbot, bar)
    left = _left(counts, bar)
    bar.set_state("Both inputs are valid JSON, and this text is long")
    _settle(qtbot, bar)
    assert _left(counts, bar) == left


def test_long_state_text_is_elided_with_the_full_text_kept(qtbot) -> None:
    window = _show(qtbot)
    window.resize(window.minimumWidth(), window.height())
    bar = _bar(window)
    message = _child(window, StatusMessage, "statusMessage")
    direction = _child(window, QLabel, "directionLabel")
    _settle(qtbot, bar)
    edges = _edges(bar, direction)
    long_text = "Converted, but " + "this notice is very long. " * 20
    bar.set_state(long_text)
    _settle(qtbot, bar)
    assert message.text() != long_text
    assert message.text().endswith("…")
    assert message.fontMetrics().horizontalAdvance(message.text()) <= (
        message.contentsRect().width()
    )
    assert bar.state_text() == long_text
    assert message.toolTip() == long_text
    assert message.accessibleName() == long_text
    assert message.accessibleDescription() == long_text
    assert _edges(bar, direction) == edges

    bar.set_state("Valid JSON")
    _settle(qtbot, bar)
    assert message.text() == "Valid JSON"
    assert message.toolTip() == ""
    assert message.accessibleDescription() == ""


def test_busy_indicators_have_distinct_accessible_names(qtbot) -> None:
    window = _show(qtbot)
    spinner = _child(window, QProgressBar, "formatSpinner")
    progress = _child(window, QProgressBar, "writeProgress")
    assert spinner.accessibleName() == "Formatting"
    assert progress.accessibleName() == "Formatting progress"


def test_slot_order_is_fixed(qtbot) -> None:
    window = _show(qtbot)
    bar = _bar(window)
    spinner = _child(window, QProgressBar, "formatSpinner")
    message = _child(window, QLabel, "statusMessage")
    counts = _child(window, QLabel, "statusCounts")
    progress = _child(window, QProgressBar, "writeProgress")
    cancel = _child(window, QPushButton, "cancelButton")
    direction = _child(window, QLabel, "directionLabel")
    bar.set_counts("5 differences")

    bar.set_busy(Busy.SPINNER)
    _settle(qtbot, bar)
    assert spinner.isVisible()
    assert _right(spinner, bar) < _left(message, bar)
    assert _right(message, bar) < _left(counts, bar)
    assert _right(counts, bar) < _left(direction, bar)

    bar.set_busy(Busy.PROGRESS)
    _settle(qtbot, bar)
    assert not spinner.isVisible()
    assert progress.isVisible() and cancel.isVisible()
    order = (message, counts, progress, cancel, direction)
    for earlier, later in zip(order, order[1:], strict=False):
        assert _right(earlier, bar) < _left(later, bar)

    bar.set_busy(Busy.IDLE)
    _settle(qtbot, bar)
    assert spinner.isHidden() and progress.isHidden() and cancel.isHidden()


def test_a_temporary_message_hides_counts_until_it_clears(qtbot) -> None:
    window = _show(qtbot)
    bar = _bar(window)
    counts = _child(window, QLabel, "statusCounts")
    bar.set_counts("5 differences")
    bar.showMessage("Not yet available.")
    assert not counts.isVisible()
    bar.clearMessage()
    assert counts.isVisible()


def test_state_api_drives_the_message(qtbot) -> None:
    window = _show(qtbot)
    bar = _bar(window)
    message = _child(window, QLabel, "statusMessage")
    bar.set_state("Valid JSON")
    assert bar.state_text() == "Valid JSON"
    assert message.accessibleName() == "Valid JSON"
    bar.set_problem_count(2)
    assert bar.state_text() == "2 problems"
    assert message.accessibleName() == "2 problems. Show problems."
    with qtbot.waitSignal(bar.state_clicked, timeout=1000):
        qtbot.mouseClick(message, Qt.MouseButton.LeftButton)


def test_cancel_button_reports_through_the_bar(qtbot) -> None:
    window = _show(qtbot)
    bar = _bar(window)
    cancel = _child(window, QPushButton, "cancelButton")
    bar.set_busy(Busy.PROGRESS)
    with qtbot.waitSignal(bar.cancel_clicked, timeout=1000):
        cancel.click()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_counts_use_readable_muted_text(qtbot, install_scheme, scheme) -> None:
    window = _show(qtbot)
    palette = install_scheme(scheme)
    bar = _bar(window)
    counts = _child(window, QLabel, "statusCounts")
    message = _child(window, QLabel, "statusMessage")
    assert counts.styleSheet() == message.styleSheet() == muted_text_style()
    bar.set_counts("5 differences")
    _settle(qtbot, bar)
    image = counts.grab().toImage()
    pixels = [
        QColor(image.pixel(x, y))
        for x in range(image.width())
        for y in range(image.height())
    ]
    fill = Counter(color.rgb() for color in pixels).most_common(1)[0][0]
    assert QColor(fill).name() == palette.window().color().name()
    ink = max(contrast_ratio(color, QColor(fill)) for color in pixels)
    assert ink >= 4.5
