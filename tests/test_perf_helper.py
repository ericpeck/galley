"""The perf budget helper in tests/perf.py: sampling, modes, and messages."""

import pytest
from tests import perf
from tests.perf import (
    Budget,
    Timing,
    budget_for,
    check,
    current_mode,
    failure_message,
    time_runs,
)

_FAILED = pytest.fail.Exception


def _clock(durations: list[float]):
    """A fake clock whose consecutive start/stop pairs are ``durations`` apart."""
    readings: list[float] = []
    now = 0.0
    for duration in durations:
        readings.extend([now, now + duration])
        now += duration + 1.0
    ticks = iter(readings)
    return lambda: next(ticks)


def test_one_warmup_run_then_five_timed_runs() -> None:
    calls: list[str] = []
    timing = time_runs(
        lambda: calls.append("run"),
        clock=_clock([9.0, 0.5, 0.125, 0.375, 0.25, 0.75]),
    )
    assert calls == ["run"] * 6
    assert timing.samples == (0.5, 0.125, 0.375, 0.25, 0.75)


def test_the_median_ignores_the_warmup_and_one_slow_sample() -> None:
    timing = time_runs(lambda: None, clock=_clock([9.0, 0.125, 0.25, 7.0, 0.375, 0.25]))
    assert timing.median == 0.25


def test_setup_runs_before_every_run_outside_the_clock() -> None:
    events: list[str] = []
    time_runs(
        lambda: events.append("run"),
        setup=lambda: events.append("setup"),
        clock=_clock([1.0] * 6),
    )
    assert events == ["setup", "run"] * 6


def test_the_real_clock_measures_the_operation() -> None:
    timing = time_runs(lambda: sum(range(1000)))
    assert len(timing.samples) == perf.TIMED_RUNS
    assert all(sample >= 0 for sample in timing.samples)


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({}, "gate"),
        ({"GALLEY_PERF_MODE": ""}, "gate"),
        ({"GALLEY_PERF_MODE": "gate"}, "gate"),
        ({"GALLEY_PERF_MODE": " STRICT "}, "strict"),
    ],
)
def test_the_mode_comes_from_the_environment(environ, expected) -> None:
    assert current_mode(environ) == expected


def test_an_unknown_mode_is_a_usage_error() -> None:
    with pytest.raises(pytest.UsageError, match="GALLEY_PERF_MODE"):
        current_mode({"GALLEY_PERF_MODE": "fast"})


def _timing(median: float) -> Timing:
    return Timing((median, median, median, median, median))


def test_gate_passes_up_to_three_times_the_budget() -> None:
    check(_timing(0.3), 0.1, test="t", fixture="f", mode="gate")
    with pytest.raises(_FAILED):
        check(_timing(0.3001), 0.1, test="t", fixture="f", mode="gate")


def test_strict_fails_past_the_budget_itself() -> None:
    check(_timing(0.1), 0.1, test="t", fixture="f", mode="strict")
    with pytest.raises(_FAILED):
        check(_timing(0.1001), 0.1, test="t", fixture="f", mode="strict")
    check(_timing(0.2), 0.1, test="t", fixture="f", mode="gate")


def test_check_reads_the_mode_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("GALLEY_PERF_MODE", "strict")
    with pytest.raises(_FAILED):
        check(_timing(0.2), 0.1, test="t", fixture="f")
    monkeypatch.setenv("GALLEY_PERF_MODE", "gate")
    check(_timing(0.2), 0.1, test="t", fixture="f")


def test_the_failure_message_names_everything_needed_to_rerun() -> None:
    timing = Timing((0.41, 0.2, 0.35, 0.5, 0.33))
    message = failure_message(
        test="test_scan", fixture="2 MB JSON", timing=timing, budget_s=0.1, mode="gate"
    )
    assert message.splitlines() == [
        "perf budget missed: test_scan",
        "  fixture: 2 MB JSON",
        "  median: 350.0 ms (5 timed runs after 1 warmup)",
        "  samples: 410.0 ms, 200.0 ms, 350.0 ms, 500.0 ms, 330.0 ms",
        "  budget: 100.0 ms",
        "  mode: gate, factor 3x, fails above 300.0 ms",
        "  rerun: uv run pytest -m perf -k test_scan",
    ]


def test_a_failed_check_reports_that_message() -> None:
    timing = _timing(0.15)
    with pytest.raises(_FAILED) as failed:
        check(timing, 0.1, test="test_scan", fixture="2 MB JSON", mode="strict")
    assert str(failed.value) == failure_message(
        test="test_scan",
        fixture="2 MB JSON",
        timing=timing,
        budget_s=0.1,
        mode="strict",
    )
    assert "mode: strict, factor 1x, fails above 100.0 ms" in str(failed.value)
    assert str(failed.value).endswith(
        "  rerun: GALLEY_PERF_MODE=strict uv run pytest -m perf -k test_scan"
    )


def test_a_budget_checks_its_own_test_name(monkeypatch) -> None:
    monkeypatch.setenv("GALLEY_PERF_MODE", "strict")
    monkeypatch.setattr(perf, "time_runs", lambda _op, setup=None: _timing(1.0))
    with pytest.raises(_FAILED, match="-k test_named"):
        Budget("test_named").within(0.5, lambda: None, fixture="f")


@pytest.mark.perf
def test_the_fixture_names_the_test_it_serves(perf_budget) -> None:
    assert perf_budget.test == "test_the_fixture_names_the_test_it_serves"


class _Node:
    nodeid = "tests/test_x.py::test_x[a]"
    originalname = "test_x"

    def __init__(self, marked: bool) -> None:
        self._marked = marked

    def get_closest_marker(self, name: str) -> object | None:
        return object() if self._marked and name == "perf" else None


def test_a_marked_test_gets_a_budget_under_its_function_name() -> None:
    assert budget_for(_Node(marked=True)).test == "test_x"


def test_an_unmarked_test_cannot_use_the_fixture() -> None:
    with pytest.raises(_FAILED, match=r"test_x\[a\] uses perf_budget; mark it perf"):
        budget_for(_Node(marked=False))
