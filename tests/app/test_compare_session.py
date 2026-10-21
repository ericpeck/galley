"""Compare jobs on the parse session's spawn worker, and the size prompt rule."""

import time

from galley.app.main_window import (
    JSON_WARNING_BYTES,
    YAML_WARNING_BYTES,
    compare_needs_size_warning,
)
from galley.app.parse_session import CompareFailure, ParseSession
from galley.core.diff import DiffResult, diff, load_document
from galley.core.format import format_json
from galley.core.worker import CompareEvent, ProgressEvent

_SPAWN_TIMEOUT_MS = 20000
_HOLD_S = 30.0
_LEFT = '{"name": "Ada", "orders": [{"total": 40}]}'
_RIGHT = '{"name": "Ada", "orders": [{"total": 45}], "vip": true}'


def test_a_compare_round_trip_delivers_the_diff_result(qtbot) -> None:
    session = _started(qtbot)
    finished: list[tuple[int, DiffResult]] = []
    session.compare_finished.connect(lambda job, result: finished.append((job, result)))
    try:
        job_id = session.submit_compare(_LEFT, "json", _RIGHT, "json")
        assert job_id is not None
        assert session.active_job_id == job_id
        qtbot.waitUntil(lambda: bool(finished), timeout=_SPAWN_TIMEOUT_MS)
        assert finished == [(job_id, _expected(_LEFT, _RIGHT))]
        changes = finished[0][1].changes
        assert [(c.kind, c.path_text) for c in changes] == [
            ("changed", "orders[0].total"),
            ("added", "vip"),
        ]
        assert session.active_job_id is None
    finally:
        session.shutdown()


def test_a_failure_names_the_side_that_did_not_load(qtbot) -> None:
    session = _started(qtbot)
    failed: list[tuple[int, CompareFailure]] = []
    session.compare_failed.connect(lambda job, failure: failed.append((job, failure)))
    try:
        job_id = session.submit_compare(_LEFT, "json", '{"a": "\\uD800"}', "json")
        qtbot.waitUntil(lambda: bool(failed), timeout=_SPAWN_TIMEOUT_MS)
        [(got_id, failure)] = failed
        assert got_id == job_id
        assert failure.side == "right"
        assert [row.lineno for row in failure.problems] == [1]
        assert session.active_job_id is None
    finally:
        session.shutdown()


def test_cancel_mid_compare_drops_the_result_and_uses_a_fresh_worker(qtbot) -> None:
    session = _started(qtbot)
    finished: list[int] = []
    cancelled: list[tuple[int, float]] = []
    session.compare_finished.connect(lambda job, _result: finished.append(job))
    session.compare_cancelled.connect(
        lambda job: cancelled.append((job, time.perf_counter()))
    )
    reads = _spy_reads(session)
    try:
        old_process = session.process
        old_jobs = session.job_queue
        assert old_process is not None
        job_id = session.submit_compare(_LEFT, "json", _RIGHT, "json", hold_s=_HOLD_S)
        qtbot.waitUntil(lambda: job_id in reads, timeout=_SPAWN_TIMEOUT_MS)
        pressed = time.perf_counter()
        session.cancel()
        assert [job for job, _at in cancelled] == [job_id]
        assert (cancelled[0][1] - pressed) * 1000 < 100
        assert session.active_job_id is None
        qtbot.waitUntil(
            lambda: session.job_queue is not None and session.job_queue is not old_jobs,
            timeout=_SPAWN_TIMEOUT_MS,
        )
        assert session.reaped_pid == old_process.pid
        assert not old_process.is_alive()
        next_id = session.submit_compare(_LEFT, "json", _RIGHT, "json")
        qtbot.waitUntil(lambda: bool(finished), timeout=_SPAWN_TIMEOUT_MS)
        assert finished == [next_id]
        assert session.process is not None
        assert session.process.pid != old_process.pid
    finally:
        session.shutdown()


def test_cancel_a_compare_waiting_on_a_reap_drops_only_that_job(qtbot) -> None:
    session = _started(qtbot)
    cancelled: list[int] = []
    finished: list[int] = []
    session.compare_cancelled.connect(cancelled.append)
    session.compare_finished.connect(lambda job, _result: finished.append(job))
    reads = _spy_reads(session)
    try:
        first = session.submit_compare(_LEFT, "json", _RIGHT, "json", hold_s=_HOLD_S)
        qtbot.waitUntil(lambda: first in reads, timeout=_SPAWN_TIMEOUT_MS)
        session.cancel()
        second = session.submit_compare(_LEFT, "json", _RIGHT, "json")
        assert second is not None
        if session.process is None:
            assert session.has_pending_job
            retired = list(session._retired)
            session.cancel()
            assert session._retired == retired
            assert not session.has_pending_job
            assert cancelled == [first, second]
        else:
            # The killed child was reaped before the second submit.
            qtbot.waitUntil(lambda: bool(finished), timeout=_SPAWN_TIMEOUT_MS)
            assert finished == [second]
            assert cancelled == [first]
    finally:
        session.shutdown()


def test_a_stale_result_is_ignored(qtbot) -> None:
    session = _started(qtbot)
    finished: list[tuple[int, DiffResult]] = []
    session.compare_finished.connect(lambda job, result: finished.append((job, result)))
    reads = _spy_reads(session)
    try:
        first = session.submit_compare(_LEFT, "json", _LEFT, "json")
        qtbot.waitUntil(lambda: bool(finished), timeout=_SPAWN_TIMEOUT_MS)
        assert first is not None
        second = session.submit_compare(_LEFT, "json", _RIGHT, "json", hold_s=0.5)
        qtbot.waitUntil(lambda: second in reads, timeout=_SPAWN_TIMEOUT_MS)
        events = session.event_queue
        assert events is not None
        events.put(CompareEvent(first, DiffResult([])))
        qtbot.waitUntil(lambda: len(finished) == 2, timeout=_SPAWN_TIMEOUT_MS)
        qtbot.wait(200)
        assert [job for job, _result in finished] == [first, second]
        assert finished[1][1] == _expected(_LEFT, _RIGHT)
    finally:
        session.shutdown()


def test_format_works_before_and_after_a_compare(qtbot) -> None:
    session = _started(qtbot)
    formatted: list[str] = []
    finished: list[int] = []
    session.succeeded.connect(formatted.append)
    session.compare_finished.connect(lambda job, _result: finished.append(job))
    try:
        pid = session.process.pid if session.process is not None else None
        session.submit('{"a": 1}', "json", "json")
        qtbot.waitUntil(lambda: len(formatted) == 1, timeout=_SPAWN_TIMEOUT_MS)
        session.submit_compare(_LEFT, "json", _RIGHT, "json")
        qtbot.waitUntil(lambda: bool(finished), timeout=_SPAWN_TIMEOUT_MS)
        session.submit('{"b": 2}', "json", "json")
        qtbot.waitUntil(lambda: len(formatted) == 2, timeout=_SPAWN_TIMEOUT_MS)
        assert formatted == [format_json('{"a": 1}'), format_json('{"b": 2}')]
        assert session.process is not None
        assert session.process.pid == pid
    finally:
        session.shutdown()


def test_one_job_at_a_time_rejects_the_second_submit(qtbot) -> None:
    session = _started(qtbot)
    formatted: list[str] = []
    finished: list[int] = []
    session.succeeded.connect(formatted.append)
    session.compare_finished.connect(lambda job, _result: finished.append(job))
    try:
        job_id = session.submit_compare(_LEFT, "json", _RIGHT, "json", hold_s=0.5)
        session.submit('{"a": 1}', "json", "json")
        assert session.submit_compare(_LEFT, "json", _LEFT, "json") is None
        assert session.active_job_id == job_id
        qtbot.waitUntil(lambda: bool(finished), timeout=_SPAWN_TIMEOUT_MS)
        qtbot.wait(200)
        assert finished == [job_id]
        assert formatted == []
        session.submit('{"a": 1}', "json", "json")
        format_id = session.active_job_id
        assert session.submit_compare(_LEFT, "json", _RIGHT, "json") is None
        assert session.active_job_id == format_id
        qtbot.waitUntil(lambda: bool(formatted), timeout=_SPAWN_TIMEOUT_MS)
        assert finished == [job_id]
    finally:
        session.shutdown()


def test_a_worker_that_exits_mid_compare_reports_stopped(qtbot) -> None:
    session = _started(qtbot)
    stopped: list[int] = []
    format_failures: list[str] = []
    session.compare_stopped.connect(stopped.append)
    session.failed.connect(format_failures.append)
    reads = _spy_reads(session)
    try:
        process = session.process
        assert process is not None
        job_id = session.submit_compare(_LEFT, "json", _RIGHT, "json", hold_s=_HOLD_S)
        qtbot.waitUntil(lambda: job_id in reads, timeout=_SPAWN_TIMEOUT_MS)
        process.kill()
        qtbot.waitUntil(lambda: bool(stopped), timeout=_SPAWN_TIMEOUT_MS)
        assert stopped == [job_id]
        assert format_failures == []
        assert session.active_job_id is None
        assert session.process is not None
        assert session.process is not process
    finally:
        session.shutdown()


def test_the_size_prompt_uses_the_larger_side() -> None:
    small = "x"
    assert not compare_needs_size_warning(small, "json", small, "json")
    at_limit = "x" * JSON_WARNING_BYTES
    assert compare_needs_size_warning(small, "json", at_limit, "json")
    assert compare_needs_size_warning(at_limit, "json", small, "json")
    just_under = "x" * (JSON_WARNING_BYTES - 1)
    assert not compare_needs_size_warning(just_under, "json", just_under, "json")


def test_the_size_prompt_keeps_the_yaml_and_json_limits() -> None:
    yaml_limit = "x" * YAML_WARNING_BYTES
    under = "x" * (YAML_WARNING_BYTES - 1)
    assert compare_needs_size_warning("a: 1", "yaml", yaml_limit, "yaml")
    assert not compare_needs_size_warning(under, "yaml", under, "yaml")
    assert not compare_needs_size_warning(yaml_limit, "json", yaml_limit, "json")
    # Mixed formats: the YAML side asks at its own limit beside a larger JSON side.
    bigger_json = "x" * (YAML_WARNING_BYTES * 2)
    assert compare_needs_size_warning(yaml_limit, "yaml", bigger_json, "json")
    assert not compare_needs_size_warning(under, "yaml", bigger_json, "json")


def test_the_size_prompt_counts_utf8_bytes() -> None:
    wide = "é" * (YAML_WARNING_BYTES // 2)
    assert compare_needs_size_warning("a: 1", "yaml", wide, "yaml")


def _expected(left: str, right: str) -> DiffResult:
    return diff(load_document(left, "json"), load_document(right, "json"))


def _spy_reads(session: ParseSession) -> list[int]:
    """Record each job id whose read event the session drained."""
    seen: list[int] = []
    original = session._handle_progress

    def _record(event: ProgressEvent) -> None:
        seen.append(event.job_id)
        original(event)

    session._handle_progress = _record  # type: ignore[method-assign]
    return seen


def _started(qtbot) -> ParseSession:
    session = ParseSession()
    session.start()
    qtbot.waitUntil(
        lambda: (
            session.job_queue is not None
            and session.process is not None
            and session.process.pid is not None
        ),
        timeout=_SPAWN_TIMEOUT_MS,
    )
    return session
