"""Compare mode budgets and timings (#65 slices 3d and 3e).

Budgets (Isaac, PR gate at 3x): the diff of two 1 MB documents with about
10% of keys changed, a reordered 10k array, the aligned ~5,000-item case,
each 0.5 s or less, and an F7 jump on a 1 MB diff, 0.05 s or less.

The ~5 MB and ~8 MB tests have no budget. They print medians for the diff,
the report and the time until the panes and the Changes pane are usable,
plus the longest gap of a 16 ms UI-thread timer during the compare. At
8 MB, Cancel must stop the compare within about 1 s.

Link Scrolling (3e): the median time its handler takes per scroll step
with 1 MB on each side. It is marked ``nightly_budget``: the budget is
asserted only under GALLEY_PERF_MODE=strict and printed otherwise. The
4 ms budget is a proposal (#152): worst CI median 3.173 ms x 1.25.

All data is generated in memory from fixed seeds. Every test prints one
``perf:`` line per number.
"""

import json
import math
import random
import statistics
import time
from collections.abc import Callable

import pytest
from PySide6.QtCore import QElapsedTimer, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton
from tests.app.compare_inline import inline_compare as inline_compare
from tests.perf import report_or_check, time_runs

from galley.app.changes_pane import ChangesPane, ChangesState
from galley.app.compare_mode import COMPARE_ANYWAY
from galley.app.editor import CodeEditor
from galley.app.main import create_main_window
from galley.core.compare_report import build_report
from galley.core.diff import diff, diff_with_pairs, load_document

_BUDGET_S = 0.5
# Proposal pending sign-off (#152): worst CI median (3.173 ms, Windows)
# x 1.25 = 3.97 ms, rounded up.
_LINK_SCROLL_BUDGET_S = 0.004
_JUMP_BUDGET_S = 0.05
_SPAWN_TIMEOUT_MS = 120_000
_TIMING_RUNS = 3
_TICK_MS = 16
_CANCEL_LIMIT_S = 1.0
_SEED = 65
_SCROLL_STEPS = 200
_CITIES = ("Oslo", "Lima", "Pune", "Kyiv", "Perth")


# --- Generated fake data ---------------------------------------------------


def _record(rng: random.Random, index: int) -> dict[str, object]:
    return {
        "id": index,
        "name": f"user-{index}",
        "email": f"user{index}@example.com",
        "city": rng.choice(_CITIES),
        "score": rng.randint(0, 9_999),
        "active": rng.random() < 0.5,
        "note": f"note {rng.randint(0, 99_999)}",
    }


def _pair(target_bytes: int, seed: int = _SEED) -> tuple[str, str, int]:
    """Two JSON texts of about ``target_bytes`` each, ~10% of keys changed.

    Returns the texts and the number of changed keys.
    """
    sample = random.Random(seed)
    per_record = (
        len(json.dumps({"records": [_record(sample, i) for i in range(100)]}, indent=2))
        / 100
    )
    count = max(1, math.ceil(target_bytes / per_record))
    rng = random.Random(seed)
    records = [_record(rng, index) for index in range(count)]
    edited = [dict(record) for record in records]
    keys = [(i, key) for i in range(count) for key in edited[i] if key != "id"]
    changed = rng.sample(keys, len(keys) // 10)
    for index, key in changed:
        value = edited[index][key]
        if isinstance(value, bool):
            edited[index][key] = not value
        elif isinstance(value, int):
            edited[index][key] = value + 1
        else:
            edited[index][key] = f"{value}-changed"
    left = json.dumps({"records": records}, indent=2)
    right = json.dumps({"records": edited}, indent=2)
    return left, right, len(changed)


def _reordered(count: int) -> tuple[str, str]:
    rng = random.Random(_SEED)
    items = [_record(rng, index) for index in range(count)]
    shuffled = list(items)
    rng.shuffle(shuffled)
    return json.dumps(items, indent=2), json.dumps(shuffled, indent=2)


def _aligned(count: int) -> tuple[str, str]:
    """About ``count`` items, with inserts, deletes and edits to align."""
    rng = random.Random(_SEED)
    items = [_record(rng, index) for index in range(count)]
    edited = list(items)
    for position in sorted(rng.sample(range(count), 50), reverse=True):
        del edited[position]
    for position in sorted(rng.sample(range(len(edited)), 50)):
        edited.insert(position, _record(rng, count + position))
    for position in rng.sample(range(len(edited)), 50):
        edited[position] = {**edited[position], "score": -1}
    return json.dumps(items, indent=2), json.dumps(edited, indent=2)


def _mb(text: str) -> float:
    return len(text.encode("utf-8")) / 1_000_000


# --- Budgets ---------------------------------------------------------------


@pytest.mark.perf
def test_diffing_two_1_mb_documents_with_10_percent_changed(perf_budget) -> None:
    left_text, right_text, changed = _pair(1_000_000)
    left = load_document(left_text, "json")
    right = load_document(right_text, "json")
    assert len(diff(left, right).changes) == changed
    perf_budget.within(
        _BUDGET_S,
        lambda: diff(left, right),
        fixture=f"{_mb(left_text):.1f} MB JSON pair, {changed:,} keys changed",
    )


@pytest.mark.perf
def test_diffing_a_reordered_10k_array(perf_budget) -> None:
    left_text, right_text = _reordered(10_000)
    left = load_document(left_text, "json")
    right = load_document(right_text, "json")
    assert diff(left, right).changes
    perf_budget.within(
        _BUDGET_S,
        lambda: diff(left, right),
        fixture="10,000-item array, shuffled",
    )


@pytest.mark.perf
def test_diffing_the_aligned_5000_item_case(perf_budget) -> None:
    left_text, right_text = _aligned(5_000)
    left = load_document(left_text, "json")
    right = load_document(right_text, "json")
    result = diff(left, right)
    assert result.aligned is True
    perf_budget.within(
        _BUDGET_S,
        lambda: diff(left, right),
        fixture="5,000-item array, 50 inserts, 50 deletes, 50 edits",
    )


@pytest.mark.perf
def test_an_f7_jump_on_a_1_mb_diff(qtbot, perf_budget, inline_compare) -> None:
    del inline_compare
    left_text, right_text, changed = _pair(1_000_000)
    window = _window(qtbot)
    _left(window).setPlainText(left_text)
    _action(window, "Compare").trigger()
    QApplication.clipboard().setText(right_text)
    _action(window, "Paste").trigger()
    pane = _changes(window)
    qtbot.waitUntil(lambda: pane.count() == changed, timeout=_SPAWN_TIMEOUT_MS)
    middle = pane.model().rowCount() // 2
    next_change = _action(window, "Next Change")

    def _start_in_the_middle() -> None:
        pane.list_view().select_row(middle)

    perf_budget.within(
        _JUMP_BUDGET_S,
        next_change.trigger,
        setup=_start_in_the_middle,
        fixture=f"{_mb(left_text):.1f} MB JSON pair, {changed:,} changes, F7 "
        "from the middle",
    )
    assert pane.list_view().current_row() == middle + 1


# --- Timings, no budget ----------------------------------------------------


@pytest.mark.perf
@pytest.mark.nightly_budget
def test_linked_scrolling_is_timed(qtbot, inline_compare) -> None:
    """The linked-scroll handler, per scroll step, 1 MB per side.

    Budget 4 ms (proposal, #152), asserted only in strict mode.

    Each step puts Left at the next of 200 positions spread over the whole
    document, then times the handler that scrolls Right to match.
    """
    del inline_compare
    left_text, right_text, changed = _pair(1_000_000)
    window = _window(qtbot)
    _left(window).setPlainText(left_text)
    _action(window, "Compare").trigger()
    QApplication.clipboard().setText(right_text)
    _action(window, "Paste").trigger()
    pane = _changes(window)
    qtbot.waitUntil(lambda: pane.count() == changed, timeout=_SPAWN_TIMEOUT_MS)
    left = _left(window)
    right = window.findChild(CodeEditor, "rightEditor")
    assert isinstance(right, CodeEditor)
    bar = left.verticalScrollBar()
    steps = [round(bar.maximum() * i / _SCROLL_STEPS) for i in range(_SCROLL_STEPS)]
    upcoming = iter(steps * 2)

    def _next_step() -> None:
        bar.blockSignals(True)
        bar.setValue(next(upcoming))
        bar.blockSignals(False)

    timing = time_runs(
        lambda: window._compare.link_scroll(left),
        setup=_next_step,
        runs=_SCROLL_STEPS - 1,
    )
    assert right.verticalScrollBar().value() > 0
    label = (
        f"{_mb(left_text):.1f} MB per side, {changed:,} changes, {_SCROLL_STEPS} steps"
    )
    report_or_check(
        timing,
        _LINK_SCROLL_BUDGET_S,
        test="test_linked_scrolling_is_timed",
        fixture=f"compare linked scroll step, {label}",
    )


def _median_s(operation: Callable[[], object], runs: int = _TIMING_RUNS) -> float:
    samples: list[float] = []
    for _ in range(runs):
        started = time.perf_counter()
        operation()
        samples.append(time.perf_counter() - started)
    return statistics.median(samples)


class _TickGaps:
    """A 16 ms UI-thread timer. The longest gap is how long the thread was busy."""

    def __init__(self) -> None:
        self.longest_ms = 0.0
        self._clock = QElapsedTimer()
        self._timer = QTimer()
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._clock.start()
        self._timer.start()

    def stop(self) -> None:
        self._tick()
        self._timer.stop()

    def _tick(self) -> None:
        self.longest_ms = max(self.longest_ms, float(self._clock.restart()))


@pytest.mark.perf
@pytest.mark.parametrize("megabytes", [5, 8])
def test_large_compares_are_timed(qtbot, megabytes: int) -> None:
    left_text, right_text, changed = _pair(megabytes * 1_000_000)
    label = f"{_mb(left_text):.1f} MB per side, {changed:,} keys changed"
    left = load_document(left_text, "json")
    right = load_document(right_text, "json")
    diff_s = _median_s(lambda: diff(left, right))
    result, pairs = diff_with_pairs(left, right)
    report_s = _median_s(lambda: build_report(result, left, right, pairs))
    print(f"perf: compare diff {megabytes} MB [{label}] median {diff_s * 1000:.1f} ms")
    print(
        f"perf: compare report {megabytes} MB [{label}] median {report_s * 1000:.1f} ms"
    )

    window = _window(qtbot)
    try:
        _left(window).setPlainText(left_text)
        _action(window, "Compare").trigger()
        QApplication.clipboard().setText(right_text)
        # Paste fills Right on the UI thread, before the size sheet.
        started = time.perf_counter()
        _action(window, "Paste").trigger()
        paste_s = time.perf_counter() - started
        print(
            f"perf: compare paste into Right {megabytes} MB [{label}] "
            f"{paste_s * 1000:.1f} ms (one run, UI thread)"
        )
        pane = _changes(window)
        usable: list[float] = []
        gaps = _TickGaps()
        for _ in range(_TIMING_RUNS):
            box = _wait_for_size_sheet(qtbot, window)
            anyway = _button(box, COMPARE_ANYWAY)
            gaps.start()
            started = time.perf_counter()
            anyway.click()
            qtbot.waitUntil(
                lambda: not window._compare.is_running(), timeout=_SPAWN_TIMEOUT_MS
            )
            QApplication.processEvents()
            usable.append(time.perf_counter() - started)
            gaps.stop()
            assert pane.count() == changed
            window._compare.run()
        _dismiss_size_sheet(qtbot, window)
        usable_s = statistics.median(usable)
        print(
            f"perf: compare until usable {megabytes} MB [{label}] median "
            f"{usable_s * 1000:.1f} ms (worker load, diff and report, then panes "
            "and Changes pane)"
        )
        print(
            f"perf: compare longest UI-thread gap {megabytes} MB [{label}] "
            f"{gaps.longest_ms:.0f} ms ({_TICK_MS} ms timer)"
        )
    finally:
        window.shutdown_parse_worker()


@pytest.mark.perf
def test_cancel_stops_an_8_mb_compare(qtbot) -> None:
    left_text, right_text, _changed = _pair(8_000_000)
    window = _window(qtbot)
    try:
        _left(window).setPlainText(left_text)
        _action(window, "Compare").trigger()
        QApplication.clipboard().setText(right_text)
        _action(window, "Paste").trigger()
        box = _wait_for_size_sheet(qtbot, window)
        _button(box, COMPARE_ANYWAY).click()
        session = window._session
        qtbot.waitUntil(lambda: session.process is not None, timeout=_SPAWN_TIMEOUT_MS)
        process = session.process
        assert process is not None
        # Let the worker get into the load and the diff.
        qtbot.wait(1_500)
        assert window._compare.is_running()
        cancel = window.findChild(QPushButton, "cancelButton")
        assert isinstance(cancel, QPushButton)
        assert cancel.isVisible()
        started = time.perf_counter()
        cancel.click()
        qtbot.waitUntil(lambda: not process.is_alive(), timeout=5_000)
        stopped_s = time.perf_counter() - started
        print(
            f"perf: compare cancel 8 MB [{_mb(left_text):.1f} MB per side] "
            f"{stopped_s * 1000:.1f} ms until the worker exited"
        )
        assert stopped_s <= _CANCEL_LIMIT_S
        assert not window._compare.is_running()
        assert _action(window, "Compare").isChecked()
        assert _changes(window).state() == ChangesState.CANCELED
        assert not process.is_alive()
    finally:
        window.shutdown_parse_worker()


# --- Helpers ---------------------------------------------------------------


def _window(qtbot):
    window = create_main_window()
    qtbot.addWidget(window)
    window.resize(1400, 800)
    window.show()
    qtbot.waitExposed(window)
    return window


def _action(window, text: str) -> QAction:
    matches = [item for item in window.findChildren(QAction) if item.text() == text]
    assert len(matches) == 1, text
    return matches[0]


def _left(window) -> CodeEditor:
    editor = window.findChild(CodeEditor, "inputEditor")
    assert isinstance(editor, CodeEditor)
    return editor


def _changes(window) -> ChangesPane:
    pane = window.findChild(ChangesPane, "changesPage")
    assert isinstance(pane, ChangesPane)
    return pane


def _size_sheet(window) -> QMessageBox | None:
    box = window.findChild(QMessageBox, "compareSizeWarning")
    return box if isinstance(box, QMessageBox) and box.isVisible() else None


def _wait_for_size_sheet(qtbot, window) -> QMessageBox:
    qtbot.waitUntil(lambda: _size_sheet(window) is not None, timeout=10_000)
    box = _size_sheet(window)
    assert box is not None
    return box


def _dismiss_size_sheet(qtbot, window) -> None:
    box = _wait_for_size_sheet(qtbot, window)
    _button(box, "Cancel").click()
    qtbot.waitUntil(lambda: _size_sheet(window) is None, timeout=10_000)


def _button(box: QMessageBox, text: str) -> QPushButton:
    for button in box.buttons():
        if button.text() == text and isinstance(button, QPushButton):
            return button
    raise AssertionError(text)
