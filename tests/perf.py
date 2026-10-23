"""Wall-clock budgets for tests marked ``perf``.

``uv run pytest`` deselects these tests and ``uv run pytest -m perf`` runs
them. Each one goes through the ``perf_budget`` fixture: one warmup run,
then the median of five timed runs against the test's budget.
``GALLEY_PERF_MODE`` sets how far over the budget a median may go:

- ``gate`` (the default, and the ``perf`` CI job): fail only past 3x the
  budget, so a shared runner's noise does not fail a pull request.
- ``strict`` (the nightly run on ``main``): fail past the budget itself.
"""

import gc
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


def time_runs(
    operation: Callable[[], object],
    *,
    setup: Callable[[], object] | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> Timing:
    """Run ``operation`` once to warm up, then time ``TIMED_RUNS`` more runs.

    ``setup`` runs before every run, warmup included, outside the clock.
    """
    samples: list[float] = []
    for run in range(WARMUP_RUNS + TIMED_RUNS):
        if setup is not None:
            setup()
        gc.collect()
        started = clock()
        operation()
        elapsed = clock() - started
        if run >= WARMUP_RUNS:
            samples.append(elapsed)
    return Timing(tuple(samples))


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
    *, test: str, fixture: str, timing: Timing, budget_s: float, mode: str
) -> str:
    factor = mode_factor(mode)
    samples = ", ".join(_ms(sample) for sample in timing.samples)
    return "\n".join(
        [
            f"perf budget missed: {test}",
            f"  fixture: {fixture}",
            f"  median: {_ms(timing.median)} "
            f"({TIMED_RUNS} timed runs after {WARMUP_RUNS} warmup)",
            f"  samples: {samples}",
            f"  budget: {_ms(budget_s)}",
            f"  mode: {mode}, factor {factor:g}x, fails above {_ms(budget_s * factor)}",
            f"  rerun: {rerun_command(test, mode)}",
        ]
    )


def check(
    timing: Timing,
    budget_s: float,
    *,
    test: str,
    fixture: str,
    mode: str | None = None,
) -> None:
    """Fail the test when the median is past the budget for this mode."""
    mode = current_mode() if mode is None else mode
    limit = budget_s * mode_factor(mode)
    print(
        f"perf: {test} [{fixture}] median {_ms(timing.median)}, "
        f"budget {_ms(budget_s)}, {mode} limit {_ms(limit)}",
        flush=True,
    )
    if timing.median > limit:
        pytest.fail(
            failure_message(
                test=test, fixture=fixture, timing=timing, budget_s=budget_s, mode=mode
            ),
            pytrace=False,
        )


class Budget:
    """The ``perf_budget`` fixture: time and check runs for one test."""

    def __init__(self, test: str) -> None:
        self.test = test

    def measure(
        self,
        operation: Callable[[], object],
        *,
        setup: Callable[[], object] | None = None,
    ) -> Timing:
        """Time ``operation`` without checking it, for a baseline."""
        return time_runs(operation, setup=setup)

    def check(self, timing: Timing, budget_s: float, *, fixture: str) -> None:
        check(timing, budget_s, test=self.test, fixture=fixture)

    def within(
        self,
        budget_s: float,
        operation: Callable[[], object],
        *,
        fixture: str,
        setup: Callable[[], object] | None = None,
    ) -> Timing:
        """Time ``operation`` and fail when its median is over ``budget_s``."""
        timing = self.measure(operation, setup=setup)
        self.check(timing, budget_s, fixture=fixture)
        return timing


def budget_for(node: pytest.Item) -> Budget:
    """Return the fixture's budget, or fail a test not marked ``perf``."""
    if node.get_closest_marker("perf") is None:
        pytest.fail(f"{node.nodeid} uses perf_budget; mark it perf")
    return Budget(node.originalname)


def _ms(seconds: float) -> str:
    return f"{seconds * 1000:.1f} ms"
