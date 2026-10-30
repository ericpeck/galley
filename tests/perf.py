"""Wall-clock budgets for tests marked ``perf``.

``uv run pytest`` deselects these tests and ``uv run pytest -m perf`` runs
them. Each one goes through the ``perf_budget`` fixture: one warmup run,
then the median of five timed runs against the test's budget.
``GALLEY_PERF_MODE`` sets how far over the budget a median may go:

- ``gate`` (the default, and the ``perf`` CI job): fail only past 3x the
  budget, so a shared runner's noise does not fail a pull request.
- ``strict`` (the nightly run on ``main``): fail past the budget itself.

A budget on a tail (``perf_budget.within(..., runs=200, percentile=95)``)
checks that percentile of many timed runs instead of the median of five.

A ratio budget (``perf_budget.ratio_within``) compares two operations on
the same machine instead. Their runs alternate, so both share the runner's
noise, and the check uses the median of the per-pair ratios. A ratio does
not depend on how fast the runner is, so neither mode multiplies its limit.
"""

import gc
import math
import os
import statistics
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import pytest

MODE_VARIABLE = "GALLEY_PERF_MODE"
GATE = "gate"
STRICT = "strict"
GATE_FACTOR = 3.0
WARMUP_RUNS = 1
TIMED_RUNS = 5


@dataclass(frozen=True)
class Timing:
    """Seconds for each timed run, warmup left out."""

    samples: tuple[float, ...]

    @property
    def median(self) -> float:
        return statistics.median(self.samples)

    def percentile(self, percent: float) -> float:
        """The nearest-rank ``percent`` percentile: the sample at or under
        which ``percent`` of the samples fall."""
        ranked = sorted(self.samples)
        return ranked[max(math.ceil(percent / 100 * len(ranked)), 1) - 1]

    def statistic(self, percentile: float | None) -> tuple[str, float]:
        """The value a budget checks, and its name: the median, or ``pNN``."""
        if percentile is None:
            return "median", self.median
        return f"p{percentile:g}", self.percentile(percentile)


def time_runs(
    operation: Callable[[], object],
    *,
    setup: Callable[[], object] | None = None,
    clock: Callable[[], float] = time.perf_counter,
    runs: int = TIMED_RUNS,
) -> Timing:
    """Run ``operation`` once to warm up, then time ``runs`` more runs.

    ``setup`` runs before every run, warmup included, outside the clock.
    """
    samples: list[float] = []
    for run in range(WARMUP_RUNS + runs):
        if setup is not None:
            setup()
        elapsed = _timed(operation, clock)
        if run >= WARMUP_RUNS:
            samples.append(elapsed)
    return Timing(tuple(samples))


@dataclass(frozen=True)
class RatioTiming:
    """Paired runs of a candidate and its baseline, warmup pair left out."""

    candidate: Timing
    baseline: Timing

    @property
    def ratios(self) -> tuple[float, ...]:
        return tuple(
            candidate / baseline
            for candidate, baseline in zip(
                self.candidate.samples, self.baseline.samples, strict=True
            )
        )

    @property
    def median(self) -> float:
        return statistics.median(self.ratios)


def time_ratio_runs(
    candidate: Callable[[], object],
    baseline: Callable[[], object],
    *,
    clock: Callable[[], float] = time.perf_counter,
) -> RatioTiming:
    """Time ``candidate`` and ``baseline`` in alternating pairs.

    One warmup pair, then ``TIMED_RUNS`` timed pairs. The pair order flips
    on every run, so neither side always runs second on a warm cache.
    """
    candidates: list[float] = []
    baselines: list[float] = []
    for run in range(WARMUP_RUNS + TIMED_RUNS):
        if run % 2 == 0:
            base_s = _timed(baseline, clock)
            candidate_s = _timed(candidate, clock)
        else:
            candidate_s = _timed(candidate, clock)
            base_s = _timed(baseline, clock)
        if run >= WARMUP_RUNS:
            candidates.append(candidate_s)
            baselines.append(base_s)
    return RatioTiming(Timing(tuple(candidates)), Timing(tuple(baselines)))


def _timed(operation: Callable[[], object], clock: Callable[[], float]) -> float:
    gc.collect()
    started = clock()
    operation()
    return clock() - started


def current_mode(environ: Mapping[str, str] = os.environ) -> str:
    """Return ``gate`` or ``strict`` from ``GALLEY_PERF_MODE``. Unset is gate."""
    mode = environ.get(MODE_VARIABLE, "").strip().lower() or GATE
    if mode not in (GATE, STRICT):
        raise pytest.UsageError(
            f"{MODE_VARIABLE} must be {GATE!r} or {STRICT!r}, not {mode!r}"
        )
    return mode


def mode_factor(mode: str) -> float:
    """How many budgets a median may reach before the test fails."""
    return GATE_FACTOR if mode == GATE else 1.0


def rerun_command(test: str, mode: str) -> str:
    """The command that reruns ``test`` in ``mode``. Gate needs no variable."""
    command = f"uv run pytest -m perf -k {test}"
    return command if mode == GATE else f"{MODE_VARIABLE}={mode} {command}"


def failure_message(
    *,
    test: str,
    fixture: str,
    timing: Timing,
    budget_s: float,
    mode: str,
    percentile: float | None = None,
) -> str:
    factor = mode_factor(mode)
    samples = ", ".join(_ms(sample) for sample in timing.samples)
    name, value = timing.statistic(percentile)
    return "\n".join(
        [
            f"perf budget missed: {test}",
            f"  fixture: {fixture}",
            f"  {name}: {_ms(value)} "
            f"({len(timing.samples)} timed runs after {WARMUP_RUNS} warmup)",
            f"  samples: {samples}",
            f"  budget: {_ms(budget_s)}",
            f"  mode: {mode}, factor {factor:g}x, fails above {_ms(budget_s * factor)}",
            f"  rerun: {rerun_command(test, mode)}",
        ]
    )


def ratio_failure_message(
    *, test: str, fixture: str, timing: RatioTiming, limit: float, mode: str
) -> str:
    ratios = ", ".join(f"{ratio:.2f}x" for ratio in timing.ratios)
    candidates = ", ".join(_ms(sample) for sample in timing.candidate.samples)
    baselines = ", ".join(_ms(sample) for sample in timing.baseline.samples)
    return "\n".join(
        [
            f"perf ratio budget missed: {test}",
            f"  fixture: {fixture}",
            f"  median ratio: {timing.median:.2f}x "
            f"({TIMED_RUNS} alternating pairs after {WARMUP_RUNS} warmup pair)",
            f"  ratios: {ratios}",
            f"  candidate samples: {candidates}",
            f"  baseline samples: {baselines}",
            f"  limit: {limit:g}x",
            f"  mode: {mode}, factor 1x (ratios are not multiplied), "
            f"fails above {limit:g}x",
            f"  rerun: {rerun_command(test, mode)}",
        ]
    )


def check_ratio(
    timing: RatioTiming,
    limit: float,
    *,
    test: str,
    fixture: str,
    mode: str | None = None,
) -> None:
    """Fail the test when the median ratio is past ``limit``, in either mode."""
    mode = current_mode() if mode is None else mode
    print(
        f"perf: {test} [{fixture}] median ratio {timing.median:.2f}x "
        f"(candidate {_ms(timing.candidate.median)}, baseline "
        f"{_ms(timing.baseline.median)}), limit {limit:g}x in {mode} mode",
        flush=True,
    )
    if timing.median > limit:
        pytest.fail(
            ratio_failure_message(
                test=test, fixture=fixture, timing=timing, limit=limit, mode=mode
            ),
            pytrace=False,
        )


def check(
    timing: Timing,
    budget_s: float,
    *,
    test: str,
    fixture: str,
    mode: str | None = None,
    percentile: float | None = None,
) -> None:
    """Fail the test when the median, or ``percentile``, is past the budget
    for this mode."""
    mode = current_mode() if mode is None else mode
    limit = budget_s * mode_factor(mode)
    name, value = timing.statistic(percentile)
    print(
        f"perf: {test} [{fixture}] {name} {_ms(value)}, "
        f"budget {_ms(budget_s)}, {mode} limit {_ms(limit)}",
        flush=True,
    )
    if value > limit:
        pytest.fail(
            failure_message(
                test=test,
                fixture=fixture,
                timing=timing,
                budget_s=budget_s,
                mode=mode,
                percentile=percentile,
            ),
            pytrace=False,
        )


def report_or_check(
    timing: Timing,
    budget_s: float,
    *,
    test: str,
    fixture: str,
    mode: str | None = None,
) -> None:
    """For ``nightly_budget`` tests: print the median, and only under
    ``strict`` fail when it is past ``budget_s`` (no 3x gate factor)."""
    mode = current_mode() if mode is None else mode
    if mode != STRICT:
        print(
            f"perf: {test} [{fixture}] median {timing.median * 1000:.3f} ms, "
            f"budget {_ms(budget_s)} (timing only in {mode} mode)",
            flush=True,
        )
        return
    check(timing, budget_s, test=test, fixture=fixture, mode=STRICT)


class Budget:
    """The ``perf_budget`` fixture: time and check runs for one test."""

    def __init__(self, test: str) -> None:
        self.test = test

    def check(
        self,
        timing: Timing,
        budget_s: float,
        *,
        fixture: str,
        percentile: float | None = None,
    ) -> None:
        check(timing, budget_s, test=self.test, fixture=fixture, percentile=percentile)

    def within(
        self,
        budget_s: float,
        operation: Callable[[], object],
        *,
        fixture: str,
        setup: Callable[[], object] | None = None,
        runs: int = TIMED_RUNS,
        percentile: float | None = None,
    ) -> Timing:
        """Time ``operation`` and fail when its median is over ``budget_s``.

        With ``percentile``, that percentile of the ``runs`` timed runs is
        checked instead, for a budget on the slow tail of many short runs.
        """
        timing = time_runs(operation, setup=setup, runs=runs)
        self.check(timing, budget_s, fixture=fixture, percentile=percentile)
        return timing

    def ratio_within(
        self,
        limit: float,
        candidate: Callable[[], object],
        baseline: Callable[[], object],
        *,
        fixture: str,
    ) -> RatioTiming:
        """Fail when ``candidate`` takes over ``limit`` times ``baseline``.

        The limit is the same in gate and strict mode.
        """
        timing = time_ratio_runs(candidate, baseline)
        check_ratio(timing, limit, test=self.test, fixture=fixture)
        return timing


def budget_for(node: pytest.Item) -> Budget:
    """Return the fixture's budget, or fail a test not marked ``perf``."""
    if node.get_closest_marker("perf") is None:
        pytest.fail(f"{node.nodeid} uses perf_budget; mark it perf")
    return Budget(node.originalname)


def _ms(seconds: float) -> str:
    return f"{seconds * 1000:.1f} ms"
