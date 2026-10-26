"""The UI thread keeps running while a masked Format job runs (#66, #107).

``ParseSession.submit`` doesn't take a mask yet: that hookup belongs to the
UI slice. So each test swaps the session module's ``FormatJob`` for one
with ``mask`` set, and ``submit`` sends a masked job to the real worker.
``parse_session.py`` itself is unchanged.

A ``QTimer`` heartbeat ticks on the UI thread from just before ``submit``
until ``succeeded`` arrives. The largest gap between ticks is how long the
UI thread was blocked. Isaac's budget is 0.1 s for each job. Every value is
fake (``tests/core/mask_rows.py``).
"""

import functools
import time

import pytest
from PySide6.QtCore import QTimer
from tests.core.mask_rows import (
    ONE_LINE_CHARS,
    ONE_MB,
    one_line,
    pretty_json,
    text_of_size,
    yaml_text,
)
from tests.perf import TIMED_RUNS, WARMUP_RUNS, Timing

import galley.app.parse_session as parse_session_module
from galley.app.limits import YAML_BUSY_BYTES
from galley.app.parse_session import ParseSession
from galley.core.mask import MASK, MaskRules
from galley.core.worker import FormatJob

_BLOCK_BUDGET_S = 0.1
_HEARTBEAT_MS = 5
_SPAWN_TIMEOUT_MS = 20_000
_JOB_TIMEOUT_MS = 60_000


@pytest.mark.perf
@pytest.mark.parametrize(
    ("label", "render", "size", "source"),
    [
        ("1 MB JSON", pretty_json, ONE_MB, "json"),
        ("192 KB YAML", yaml_text, YAML_BUSY_BYTES, "yaml"),
        ("1.6 MB minified JSON line", one_line, ONE_LINE_CHARS, "json"),
    ],
    ids=["json-1mb", "yaml-192kb", "json-one-line"],
)
def test_the_ui_thread_is_never_blocked_while_a_masked_job_runs(
    qtbot, monkeypatch, perf_budget, label: str, render, size: int, source: str
) -> None:
    text, _rows = text_of_size(size, render)
    masked_job = functools.partial(FormatJob, mask=MaskRules())
    monkeypatch.setattr(parse_session_module, "FormatJob", masked_job)
    session = ParseSession()
    try:
        session.start()
        qtbot.waitUntil(
            lambda: session.process is not None and session.process.pid is not None,
            timeout=_SPAWN_TIMEOUT_MS,
        )
        gaps = [
            _largest_gap(qtbot, session, text, source)
            for _ in range(WARMUP_RUNS + TIMED_RUNS)
        ]
    finally:
        session.shutdown()
    # Like ``perf_budget.within``: the warmup run is left out, and the median
    # of the timed runs' largest gaps is held to the budget.
    perf_budget.check(
        Timing(tuple(gaps[WARMUP_RUNS:])),
        _BLOCK_BUDGET_S,
        fixture=f"{label} to {source}, masked: largest UI-thread gap",
    )


def _largest_gap(qtbot, session: ParseSession, text: str, source: str) -> float:
    """Run one masked job and return the largest gap between heartbeat ticks."""
    ticks = [time.monotonic()]
    heartbeat = QTimer()
    heartbeat.setInterval(_HEARTBEAT_MS)
    heartbeat.timeout.connect(lambda: ticks.append(time.monotonic()))
    heartbeat.start()
    try:
        with qtbot.waitSignal(session.succeeded, timeout=_JOB_TIMEOUT_MS) as done:
            session.submit(text, source, source)
        ticks.append(time.monotonic())
    finally:
        heartbeat.stop()
    output = done.args[0]
    assert MASK in output
    assert "user0@example.com" not in output
    return max(later - earlier for earlier, later in zip(ticks, ticks[1:]))
