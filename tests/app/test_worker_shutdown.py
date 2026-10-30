"""Closing or deleting a window never waits on, or leaves behind, a worker."""

import gc

from galley.app.main import create_main_window
from galley.app.parse_session import _LIVE_WORKERS, ParseSession
from galley.app.regex_session import RegexSession
from galley.core.regex_find import FindJob

_WAIT_MS = 15_000
_RUNAWAY_TEXT = "a" * 30 + "b"
_RUNAWAY = "(a+)+$"
# Far larger than a pipe's buffer, so the feeder is still writing it.
_LARGE_TEXT = "a" * (8 * 1024 * 1024)


def _runaway(session: RegexSession) -> FindJob:
    return FindJob(
        job_id=session.next_job_id(),
        version=1,
        query=_RUNAWAY,
        regex=True,
        whole_word=False,
        match_case=True,
    )


def test_no_worker_queue_joins_its_feeder_thread(qtbot) -> None:
    """A join on a feeder still writing to a killed child would never end."""
    parse = ParseSession()
    find = RegexSession()
    try:
        parse.start()
        find.start()
        qtbot.waitUntil(find.is_ready, timeout=_WAIT_MS)
        queues = [parse.job_queue, parse.event_queue, *find._resource.queues]
        assert len(queues) == 4
        assert all(queue._joincancelled for queue in queues)
    finally:
        parse.shutdown()
        find.shutdown()


def test_a_window_deleted_without_a_close_stops_a_busy_worker(qtbot) -> None:
    window = create_main_window()
    window.start_parse_worker()
    find = window._regex_session
    find.limit_ms = 60_000
    find.start()
    qtbot.waitUntil(find.is_ready, timeout=_WAIT_MS)
    find.submit(_runaway(find), _RUNAWAY_TEXT)
    # The worker is busy, so this text fills the pipe and stays half sent.
    find.store_text(2, _LARGE_TEXT)
    parse_worker = window._session.process
    find_worker = find.process
    resources = [window._session._resource, find._resource]
    assert parse_worker is not None and find_worker is not None

    # Deleted, never closed, so closeEvent's shutdown does not run.
    window.deleteLater()
    del window, find
    qtbot.waitUntil(lambda: parse_worker.exitcode is not None, timeout=_WAIT_MS)
    qtbot.waitUntil(lambda: find_worker.exitcode is not None, timeout=_WAIT_MS)
    # Collecting the window and its queues must not wait on anything.
    gc.collect()

    assert not any(resource in _LIVE_WORKERS for resource in resources)
