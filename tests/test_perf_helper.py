"""The perf budget helper in tests/perf.py: sampling, modes, and messages."""

import pytest
from tests import perf
from tests.perf import (
    Budget,
    RatioTiming,
    Timing,
    budget_for,
    check,
    check_ratio,
    current_mode,
    failure_message,
    ratio_failure_message,
    time_ratio_runs,
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
    monkeypatch.setattr(perf, "time_runs", lambda _op, **_kwargs: _timing(1.0))
    with pytest.raises(_FAILED, match="-k test_named"):
        Budget("test_named").within(0.5, lambda: None, fixture="f")


def test_ratio_runs_alternate_and_drop_the_warmup_pair() -> None:
    order: list[str] = []
    # Pairs: warmup (base first), then candidate first, base first, and so on.
    clock = _clock(
        [9.0, 9.0]
        + [0.5, 0.25]
        + [0.25, 0.75]
        + [0.75, 0.25]
        + [0.25, 0.5]
        + [0.5, 0.5]
    )
    timing = time_ratio_runs(
        lambda: order.append("candidate"),
        lambda: order.append("baseline"),
        clock=clock,
    )
    assert order == [
        "baseline",
        "candidate",
        "candidate",
        "baseline",
        "baseline",
        "candidate",
        "candidate",
        "baseline",
        "baseline",
        "candidate",
        "candidate",
        "baseline",
    ]
    assert timing.candidate.samples == (0.5, 0.75, 0.75, 0.5, 0.5)
    assert timing.baseline.samples == (0.25, 0.25, 0.25, 0.25, 0.5)
    assert timing.ratios == (2.0, 3.0, 3.0, 2.0, 1.0)
    assert timing.median == 2.0


def _ratio(*ratios: float) -> RatioTiming:
    return RatioTiming(Timing(ratios), Timing((1.0,) * len(ratios)))


@pytest.mark.parametrize("mode", ["gate", "strict"])
def test_a_ratio_limit_is_not_multiplied_in_either_mode(mode: str) -> None:
    check_ratio(_ratio(1.5, 1.5, 1.5, 1.5, 1.5), 1.5, test="t", fixture="f", mode=mode)
    with pytest.raises(_FAILED):
        check_ratio(
            _ratio(1.4, 1.6, 1.6, 1.6, 1.0), 1.5, test="t", fixture="f", mode=mode
        )


def test_the_ratio_failure_message_shows_both_sides() -> None:
    timing = RatioTiming(
        Timing((0.2, 0.3, 0.2, 0.4, 0.2)), Timing((0.1, 0.1, 0.1, 0.2, 0.1))
    )
    message = ratio_failure_message(
        test="test_dup", fixture="2 MB", timing=timing, limit=1.5, mode="gate"
    )
    assert message.splitlines() == [
        "perf ratio budget missed: test_dup",
        "  fixture: 2 MB",
        "  median ratio: 2.00x (5 alternating pairs after 1 warmup pair)",
        "  ratios: 2.00x, 3.00x, 2.00x, 2.00x, 2.00x",
        "  candidate samples: 200.0 ms, 300.0 ms, 200.0 ms, 400.0 ms, 200.0 ms",
        "  baseline samples: 100.0 ms, 100.0 ms, 100.0 ms, 200.0 ms, 100.0 ms",
        "  limit: 1.5x",
        "  mode: gate, factor 1x (ratios are not multiplied), fails above 1.5x",
        "  rerun: uv run pytest -m perf -k test_dup",
    ]


def test_a_budget_checks_a_ratio_under_its_own_test_name(monkeypatch) -> None:
    monkeypatch.setenv("GALLEY_PERF_MODE", "gate")
    monkeypatch.setattr(
        perf, "time_ratio_runs", lambda _c, _b: _ratio(2.0, 2.0, 2.0, 2.0, 2.0)
    )
    with pytest.raises(_FAILED, match="-k test_named"):
        Budget("test_named").ratio_within(1.5, lambda: None, lambda: None, fixture="f")


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


def test_runs_sets_how_many_timed_runs_follow_the_warmup() -> None:
    calls: list[str] = []
    timing = time_runs(
        lambda: calls.append("run"), runs=3, clock=_clock([9.0, 1, 2, 3])
    )
    assert calls == ["run"] * 4
    assert timing.samples == (1, 2, 3)


def test_the_percentile_is_the_nearest_rank_sample() -> None:
    timing = Timing(tuple(float(n) for n in range(200, 0, -1)))
    assert timing.percentile(95) == 190.0
    assert timing.percentile(100) == 200.0
    assert timing.percentile(50) == 100.0
    assert Timing((0.5,)).percentile(95) == 0.5


def test_a_percentile_budget_checks_the_tail_not_the_median() -> None:
    # 189 fast runs and 11 slow ones: the median passes, p95 does not.
    timing = Timing((0.01,) * 189 + (0.5,) * 11)
    check(timing, 0.1, test="t", fixture="f", mode="strict")
    check(timing, 0.5, test="t", fixture="f", mode="strict", percentile=95)
    with pytest.raises(_FAILED) as failed:
        check(timing, 0.1, test="t", fixture="f", mode="gate", percentile=95)
    assert "  p95: 500.0 ms (200 timed runs after 1 warmup)" in str(failed.value)


def test_within_passes_runs_and_percentile_through() -> None:
    timing = Budget("t").within(10.0, lambda: None, fixture="f", runs=7, percentile=95)
    assert len(timing.samples) == 7


# --- report_or_check (nightly_budget) ---------------------------------------

_FAKE_SLOW = Timing((0.009, 0.010, 0.011))  # fake: 10 ms median, budget 4 ms


def test_report_or_check_only_prints_a_slow_timing_in_gate_mode(
    monkeypatch, capsys
) -> None:
    monkeypatch.setenv("GALLEY_PERF_MODE", "gate")
    perf.report_or_check(_FAKE_SLOW, 0.004, test="test_fake", fixture="fake")
    out = capsys.readouterr().out
    assert "median 10.000 ms, budget 4.0 ms (timing only in gate mode)" in out


def test_report_or_check_fails_a_slow_timing_in_strict_mode(monkeypatch) -> None:
    monkeypatch.setenv("GALLEY_PERF_MODE", "strict")
    with pytest.raises(_FAILED, match="perf budget missed: test_fake"):
        perf.report_or_check(_FAKE_SLOW, 0.004, test="test_fake", fixture="fake")


def test_report_or_check_passes_a_fast_timing_in_strict_mode(monkeypatch) -> None:
    monkeypatch.setenv("GALLEY_PERF_MODE", "strict")
    perf.report_or_check(
        Timing((0.001, 0.002, 0.003)), 0.004, test="test_fake", fixture="fake"
    )
