"""Compare jobs in the worker, run in-process and through a spawn child."""

import multiprocessing
import pickle
import queue
import time
from dataclasses import replace

import pytest

from galley.core.diff import diff, load_document
from galley.core.errors import InputError
from galley.core.worker import (
    CompareEvent,
    CompareFailureEvent,
    CompareJob,
    ProgressEvent,
    _execute_compare,
    run_compare,
    run_worker,
)

_LEFT = '{"name": "Ada", "orders": [{"total": 40}], "old": true}'
_RIGHT = "name: Ada\norders:\n  - total: 45\nnew: 1\n"
_BAD_ESCAPE = '{"a": "\\uD800"}'


def _job(
    left: str = _LEFT,
    left_format: str = "json",
    right: str = _RIGHT,
    right_format: str = "yaml",
) -> CompareJob:
    return CompareJob(
        job_id=7,
        left_text=left,
        left_format=left_format,
        right_text=right,
        right_format=right_format,
    )


def test_compare_job_pickles() -> None:
    job = _job()
    assert pickle.loads(pickle.dumps(job)) == job


def test_run_compare_matches_calling_diff_directly() -> None:
    expected = diff(load_document(_LEFT, "json"), load_document(_RIGHT, "yaml"))
    event = run_compare(_job())
    assert isinstance(event, CompareEvent)
    assert event.job_id == 7
    assert event.result == expected
    kinds = [change.kind for change in event.result.changes]
    assert sorted(kinds) == ["added", "changed", "removed"]


def test_compare_event_pickles_with_its_result() -> None:
    event = run_compare(_job())
    assert pickle.loads(pickle.dumps(event)) == event


def test_identical_sides_give_no_changes() -> None:
    event = run_compare(_job(right=_LEFT, right_format="json"))
    assert isinstance(event, CompareEvent)
    assert event.result.changes == []


def test_execute_compare_sends_a_read_event_then_the_result() -> None:
    events: queue.Queue = queue.Queue()
    _execute_compare(_job(), events)  # type: ignore[arg-type]
    read = events.get_nowait()
    assert read == ProgressEvent(7, "read", 0, 0, None)
    assert isinstance(events.get_nowait(), CompareEvent)
    assert events.empty()


@pytest.mark.parametrize("side", ["left", "right"])
def test_an_input_error_names_its_side(side: str) -> None:
    if side == "left":
        job = _job(left=_BAD_ESCAPE)
    else:
        job = _job(right=_BAD_ESCAPE, right_format="json")
    event = run_compare(job)
    assert isinstance(event, CompareFailureEvent)
    assert event.side == side
    error = event.error
    assert isinstance(error, InputError)
    assert (error.kind, error.lineno, error.colno) == ("bad_escape", 1, 8)
    assert pickle.loads(pickle.dumps(event)).error.details == error.details


def test_a_syntax_error_is_located_in_its_own_side() -> None:
    event = run_compare(_job(right='{\n  "a": 1,\n  "b" 2\n}', right_format="json"))
    assert isinstance(event, CompareFailureEvent)
    assert event.side == "right"
    assert event.error is None
    assert [problem.lineno for problem in event.problems] == [3]


def test_yaml_tabs_are_reported_before_loading() -> None:
    event = run_compare(_job(left="a:\n\tb: 1\n", left_format="yaml"))
    assert isinstance(event, CompareFailureEvent)
    assert event.side == "left"
    assert [problem.code for problem in event.problems] == ["yaml_tab"]


def test_the_left_side_is_checked_first() -> None:
    event = run_compare(_job(left=_BAD_ESCAPE, right=_BAD_ESCAPE, right_format="json"))
    assert isinstance(event, CompareFailureEvent)
    assert event.side == "left"


def test_a_spawned_worker_returns_the_diff_result() -> None:
    job = _job()
    events = _run_in_child(job)
    assert events[0] == ProgressEvent(7, "read", 0, 0, None)
    assert events[-1] == run_compare(job)


def test_a_spawned_worker_returns_the_report_and_formatted_texts() -> None:
    job = replace(_job(), reformat=True)
    events = _run_in_child(job)
    event = events[-1]
    assert isinstance(event, CompareEvent)
    assert event == run_compare(job)
    assert event.left_text is not None and event.right_text is not None
    assert event.report.count == len(event.result.changes) == 3


def _run_in_child(job: CompareJob, timeout: float = 30.0) -> list[object]:
    ctx = multiprocessing.get_context("spawn")
    jobs = ctx.Queue()
    events = ctx.Queue()
    process = ctx.Process(target=run_worker, args=(jobs, events))
    process.start()
    collected: list[object] = []
    try:
        jobs.put(job)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                event = events.get(timeout=0.2)
            except queue.Empty:
                continue
            collected.append(event)
            if isinstance(event, (CompareEvent, CompareFailureEvent)):
                break
        jobs.put(None)
        process.join(timeout=10)
        assert process.exitcode == 0
    finally:
        if process.exitcode is None and process.pid is not None:
            process.kill()
            process.join(timeout=5)
        for item in (jobs, events):
            item.cancel_join_thread()
            item.close()
    return collected
