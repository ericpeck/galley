"""Status-bar slots: state, counts, progress, and info."""

from collections import Counter

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QProgressBar, QPushButton, QWidget

from galley.app.main import create_main_window
from galley.app.status_bar import Busy, StatusBar
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


def test_counts_keep_their_widest_width_until_cleared(qtbot) -> None:
    window = _show(qtbot)
    bar = _bar(window)
    counts = _child(window, QLabel, "statusCounts")
    bar.set_counts("12 differences")
    _settle(qtbot, bar)
    wide = counts.width()
    left = _left(counts, bar)

    bar.set_counts("1 difference")
    _settle(qtbot, bar)
    assert counts.width() == wide
    assert _left(counts, bar) == left

    bar.set_counts("")
    bar.set_counts("1 difference")
    _settle(qtbot, bar)
    assert counts.width() < wide


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


def test_info_text_updates_and_stays_at_the_right(qtbot) -> None:
    window = _show(qtbot)
    bar = _bar(window)
    direction = _child(window, QLabel, "directionLabel")
    assert bar.info_text() == "JSON → JSON"
    _settle(qtbot, bar)
    right = _right(direction, bar)

    bar.set_info("JSON → YAML")
    _settle(qtbot, bar)
    assert direction.text() == "JSON → YAML"
    assert bar.info_text() == "JSON → YAML"
    assert _right(direction, bar) == right


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
