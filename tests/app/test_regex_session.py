"""The find worker: one child process for Regex and Whole word."""

import multiprocessing

import pytest

from galley.app.regex_session import SEARCH_LIMIT_MS, RegexSession
from galley.core.regex_find import FindJob, Found, StoreText

# Most tests stop a runaway search this soon. One test per OS uses the real
# limit (test_find_regex.py).
_SHORT_LIMIT_MS = 300
_WAIT_MS = 15_000
_RUNAWAY_TEXT = "a" * 30 + "b"
_RUNAWAY = "(a+)+$"


@pytest.fixture
def session(qtbot):
    del qtbot
    session = RegexSession(limit_ms=_SHORT_LIMIT_MS)
    yield session
    session.shutdown()


def _ready(qtbot, session: RegexSession) -> None:
    session.start()
    qtbot.waitUntil(session.is_ready, timeout=_WAIT_MS)


def _job(session: RegexSession, query: str, version: int = 1) -> FindJob:
    return FindJob(
        job_id=session.next_job_id(),
        version=version,
        query=query,
        regex=True,
        whole_word=False,
        match_case=True,
    )


class _Results:
    def __init__(self, session: RegexSession) -> None:
        self.finished: list[tuple[int, object]] = []
        self.timed_out: list[int] = []
        session.finished.connect(
            lambda job_id, event: self.finished.append((job_id, event))
        )
        session.timed_out.connect(self.timed_out.append)

    def answered(self, job_id: int) -> bool:
        return any(answered == job_id for answered, _event in self.finished)


def test_the_limit_is_two_seconds_and_adjustable() -> None:
    assert SEARCH_LIMIT_MS == 2000
    session = RegexSession(limit_ms=250)
    assert session.limit_ms == 250
    session.limit_ms = 400
    assert session.limit_ms == 400


def test_the_worker_starts_once_and_is_reused(qtbot, session) -> None:
    results = _Results(session)
    _ready(qtbot, session)
    process = session.process
    assert process is not None
    for query in ("a", "b", "c"):
        job = _job(session, query)
        session.submit(job, "abc")
        qtbot.waitUntil(
            lambda job=job: results.answered(job.job_id),
            timeout=_WAIT_MS,
        )
    assert session.process is process
    assert process.is_alive()
    session.start()
    assert session.process is process


def test_the_text_is_sent_only_when_its_version_changes(qtbot, session) -> None:
    results = _Results(session)
    _ready(qtbot, session)
    sent: list[object] = []
    original = session._send

    def record(item: object) -> None:
        sent.append(item)
        original(item)

    session._send = record  # type: ignore[method-assign]
    for version, query in ((1, "a"), (1, "b"), (2, "c")):
        job = _job(session, query, version)
        session.submit(job, f"text {version}")
        qtbot.waitUntil(
            lambda job=job: results.answered(job.job_id),
            timeout=_WAIT_MS,
        )
    stored = [item.version for item in sent if isinstance(item, StoreText)]
    assert stored == [1, 2]


def test_a_new_worker_is_sent_the_text_again(qtbot, session) -> None:
    """A killed worker's cache is gone, so the same version goes out again."""
    results = _Results(session)
    _ready(qtbot, session)
    session.submit(_job(session, _RUNAWAY), _RUNAWAY_TEXT)
    qtbot.waitUntil(lambda: bool(results.timed_out), timeout=_WAIT_MS)
    qtbot.waitUntil(session.is_ready, timeout=_WAIT_MS)
    sent: list[object] = []
    original = session._send

    def record(item: object) -> None:
        sent.append(item)
        original(item)

    session._send = record  # type: ignore[method-assign]
    job = _job(session, "a+b")
    session.submit(job, _RUNAWAY_TEXT)
    qtbot.waitUntil(lambda: results.answered(job.job_id), timeout=_WAIT_MS)
    assert [type(item) for item in sent] == [StoreText, FindJob]
    event = results.finished[-1][1]
    assert isinstance(event, Found)
    assert list(event.ends) == [31]


def test_store_text_answers_once_the_worker_has_it(qtbot, session) -> None:
    _ready(qtbot, session)
    with qtbot.waitSignal(session.text_stored, timeout=_WAIT_MS) as blocker:
        session.store_text(7, "x" * 1000)
    assert blocker.args == [7]


def test_a_job_past_the_limit_kills_the_worker_and_a_new_one_starts(
    qtbot, session
) -> None:
    results = _Results(session)
    _ready(qtbot, session)
    old = session.process
    assert old is not None
    job = _job(session, _RUNAWAY)
    session.submit(job, _RUNAWAY_TEXT)
    qtbot.waitUntil(lambda: results.timed_out == [job.job_id], timeout=_WAIT_MS)
    assert session.killed_process is old
    qtbot.waitUntil(lambda: old.exitcode is not None, timeout=_WAIT_MS)
    assert not old.is_alive()
    qtbot.waitUntil(session.is_ready, timeout=_WAIT_MS)
    assert session.process is not old
    after = _job(session, "a+b")
    session.submit(after, _RUNAWAY_TEXT)
    qtbot.waitUntil(lambda: bool(results.finished), timeout=_WAIT_MS)
    job_id, event = results.finished[-1]
    assert job_id == after.job_id
    assert isinstance(event, Found)
    assert (list(event.starts), list(event.ends)) == ([0], [31])


def test_a_newer_job_drops_the_older_result_and_a_stale_runaway_is_quiet(
    qtbot, session
) -> None:
    results = _Results(session)
    _ready(qtbot, session)
    owner = object()
    runaway = _job(session, _RUNAWAY)
    session.submit(runaway, _RUNAWAY_TEXT, owner=owner)
    first = _job(session, "a")
    newest = _job(session, "b")
    session.submit(first, _RUNAWAY_TEXT, owner=owner)
    session.submit(newest, _RUNAWAY_TEXT, owner=owner)
    qtbot.waitUntil(lambda: bool(results.finished), timeout=_WAIT_MS)
    assert [job_id for job_id, _event in results.finished] == [newest.job_id]
    assert results.timed_out == []


def test_each_owner_keeps_its_own_waiting_job(qtbot, session) -> None:
    """Input's newer job replaces its older one; Output's job still runs."""
    results = _Results(session)
    _ready(qtbot, session)
    older, newer, other = (_job(session, query) for query in ("a", "b", "c"))
    session.submit(older, "abc", owner="input")
    session.submit(newer, "abc", owner="input")
    session.submit(other, "abc", owner="output")
    qtbot.waitUntil(
        lambda: results.answered(newer.job_id) and results.answered(other.job_id),
        timeout=_WAIT_MS,
    )
    assert not results.answered(older.job_id)


def test_drop_forgets_only_that_owners_job(qtbot, session) -> None:
    results = _Results(session)
    _ready(qtbot, session)
    kept = _job(session, "b")
    session.submit(_job(session, _RUNAWAY), _RUNAWAY_TEXT, owner="input")
    session.submit(_job(session, "a"), "ab", owner="input")
    session.submit(kept, "ab", owner="output")
    session.drop("input")
    qtbot.waitUntil(lambda: bool(results.finished), timeout=_WAIT_MS)
    assert [job_id for job_id, _event in results.finished] == [kept.job_id]
    assert results.timed_out == []


def test_a_worker_that_exits_on_its_own_counts_as_timed_out(qtbot) -> None:
    session = RegexSession(limit_ms=60_000)
    try:
        results = _Results(session)
        _ready(qtbot, session)
        job = _job(session, _RUNAWAY)
        session.submit(job, _RUNAWAY_TEXT)
        process = session.process
        assert process is not None
        process.kill()
        qtbot.waitUntil(lambda: results.timed_out == [job.job_id], timeout=_WAIT_MS)
        qtbot.waitUntil(session.is_ready, timeout=_WAIT_MS)
        assert session.process is not process
    finally:
        session.shutdown()


def test_shutdown_leaves_no_worker_running(qtbot, session) -> None:
    _ready(qtbot, session)
    process = session.process
    assert process is not None
    session.submit(_job(session, _RUNAWAY), _RUNAWAY_TEXT)
    session.shutdown()
    assert not process.is_alive()
    assert session.process is None
    names = [child.name for child in multiprocessing.active_children()]
    assert "galley-find" not in names
    session.submit(_job(session, "a"), "a")
    assert session.process is None
