"""Own the find worker: the one child process that runs Regex and Whole word.

The worker starts once and is reused. It keeps the last document it was
sent, so a new query sends only the query. One job runs at a time. Each
owner (a find bar) has at most one job waiting: a new one replaces it. If
the owner's job is running, its result is then dropped, because it answers
an old query.

A job that runs past :attr:`RegexSession.limit_ms` is killed with its
process. ``timed_out`` reports it, unless a newer job was already waiting.
The poll never joins. A replacement starts once the killed child has
exited, or after :data:`REAP_DEADLINE_MS` if it has not. The clock starts
when a job is sent to a worker that is up, so starting a cold worker does
not count against a job.

The ``re`` engine runs only in the child. This module does not compile user
patterns.
"""

import multiprocessing
import time
from multiprocessing.process import BaseProcess
from multiprocessing.queues import Queue
from queue import Empty

from PySide6.QtCore import QObject, QTimer, Signal

from galley.app.parse_session import (
    _LIVE_WORKERS,
    REAP_DEADLINE_MS,
    _arm_exit_cleanup,
    _release_without_join,
    _RunningWorker,
)
from galley.core.regex_find import (
    FindJob,
    FindReady,
    StoreText,
    TextStored,
    run_find_worker,
)

# How long one Regex or Whole word job may run before its process is killed.
SEARCH_LIMIT_MS = 2000
_POLL_MS = 10


class _Killed:
    """A child killed past its limit, watched until ``exitcode`` is set."""

    def __init__(self, resource: _RunningWorker) -> None:
        self.resource = resource
        self.started_s = time.monotonic()


class RegexSession(QObject):
    """One warm find worker, shared by both find bars."""

    # Job id, then the worker's event for it.
    finished = Signal(int, object)
    # Job id of a job killed at the limit.
    timed_out = Signal(int)
    # Version of the text the worker now holds.
    text_stored = Signal(int)

    def __init__(
        self, parent: QObject | None = None, *, limit_ms: int = SEARCH_LIMIT_MS
    ) -> None:
        super().__init__(parent)
        self.limit_ms = limit_ms
        self._ctx = multiprocessing.get_context("spawn")
        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self._poll)
        self._resource: _RunningWorker | None = None
        self._ready = False
        self._held_version: int | None = None
        self._running: FindJob | None = None
        self._running_owner: object = None
        self._running_stale = False
        self._sent_s = 0.0
        # Each owner's waiting job and its text, oldest first.
        self._pending: dict[object, tuple[FindJob, str]] = {}
        self._killed: list[_Killed] = []
        self._job_ids = 0
        self._shut_down = False
        # The last child killed at the limit, for tests.
        self.killed_process: BaseProcess | None = None

    @property
    def process(self) -> BaseProcess | None:
        return self._resource.process if self._resource is not None else None

    def is_ready(self) -> bool:
        """The worker is up and has said so."""
        return self._ready

    def is_busy(self) -> bool:
        """A job is running or waiting."""
        return self._running is not None or bool(self._pending)

    def next_job_id(self) -> int:
        self._job_ids += 1
        return self._job_ids

    def start(self) -> None:
        """Start the worker if there is none and no killed child is in the way."""
        if self._shut_down:
            return
        self._replace_dead_worker()
        if self._resource is None and not self._waiting_on_kill():
            self._spawn()
        if not self._timer.isActive():
            self._timer.start()

    def submit(self, job: FindJob, text: str, *, owner: object = None) -> None:
        """Run ``job`` on ``text`` once the worker is free.

        ``text`` is sent only when the worker does not hold ``job.version``.
        The job replaces ``owner``'s waiting job, and makes its running one
        stale.
        """
        if self._shut_down:
            return
        self._pending.pop(owner, None)
        self._pending[owner] = (job, text)
        if self._running is not None and self._running_owner is owner:
            self._running_stale = True
        self.start()
        self._flush()

    def store_text(self, version: int, text: str) -> None:
        """Send ``text`` now. ``text_stored`` answers once the worker has it."""
        if self._shut_down:
            return
        self.start()
        if self._resource is None or not self._ready:
            return
        self._send(StoreText(version, text))
        self._held_version = version

    def drop(self, owner: object = None) -> None:
        """Forget ``owner``'s waiting job, and the result of its running one."""
        self._pending.pop(owner, None)
        if self._running is not None and self._running_owner is owner:
            self._running_stale = True

    def shutdown(self) -> None:
        """Kill every child and wait briefly for each to exit."""
        if self._shut_down:
            return
        self._shut_down = True
        self._timer.stop()
        self._pending.clear()
        self._running = None
        if self._resource is not None:
            self._resource.close()
            self._resource = None
        for killed in self._killed:
            killed.resource.close()
        self._killed.clear()

    # Internals ----------------------------------------------------------

    def _spawn(self) -> None:
        job_queue: Queue = self._ctx.Queue()
        event_queue: Queue = self._ctx.Queue()
        process = self._ctx.Process(
            target=run_find_worker,
            name="galley-find",
            args=(job_queue, event_queue),
            daemon=True,
        )
        process.start()
        self._resource = _RunningWorker(process, [job_queue, event_queue])
        _LIVE_WORKERS.append(self._resource)
        _arm_exit_cleanup()
        self._ready = False
        self._held_version = None

    def _send(self, item: object) -> None:
        assert self._resource is not None
        self._resource.queues[0].put(item)

    def _flush(self) -> None:
        if (
            not self._pending
            or self._running is not None
            or self._resource is None
            or not self._ready
        ):
            return
        owner = next(iter(self._pending))
        job, text = self._pending.pop(owner)
        if self._held_version != job.version:
            self._send(StoreText(job.version, text))
            self._held_version = job.version
        self._send(job)
        self._running = job
        self._running_owner = owner
        self._running_stale = False
        self._sent_s = time.monotonic()

    def _poll(self) -> None:
        if self._shut_down:
            return
        self._reap_killed()
        self._drain()
        self._check_limit()
        self._replace_dead_worker()
        if self._resource is None and not self._waiting_on_kill():
            self._spawn()
        if self._idle():
            self._timer.stop()

    def _idle(self) -> bool:
        return (
            self._ready
            and self._running is None
            and not self._pending
            and not self._killed
        )

    def _drain(self) -> None:
        resource = self._resource
        if resource is None or not resource.queues:
            return
        queue = resource.queues[1]
        while True:
            try:
                event = queue.get_nowait()
            except Empty:
                return
            except (OSError, EOFError, ValueError):
                return
            if isinstance(event, FindReady):
                self._ready = True
                self._flush()
            elif isinstance(event, TextStored):
                self.text_stored.emit(event.version)
            else:
                self._finish(event)

    def _finish(self, event: object) -> None:
        running = self._running
        job_id = getattr(event, "job_id", None)
        if running is None or job_id != running.job_id:
            return
        stale = self._running_stale
        self._running = None
        self._running_stale = False
        if not stale:
            self.finished.emit(job_id, event)
        self._flush()

    def _check_limit(self) -> None:
        running = self._running
        if running is None:
            return
        if (time.monotonic() - self._sent_s) * 1000 < self.limit_ms:
            return
        stale = self._running_stale
        self._running = None
        self._running_stale = False
        self._kill_current()
        if not stale:
            self.timed_out.emit(running.job_id)

    def _kill_current(self) -> None:
        resource = self._resource
        if resource is None:
            return
        self._resource = None
        self._ready = False
        self._held_version = None
        process = resource.process
        self.killed_process = process
        if process.exitcode is None:
            try:
                process.kill()
            except OSError:
                pass
        self._killed.append(_Killed(resource))

    def _reap_killed(self) -> None:
        """Forget each killed child once it has exited. Never join."""
        for killed in list(self._killed):
            if killed.resource.process.exitcode is None:
                continue
            self._killed.remove(killed)
            _release_without_join(killed.resource)

    def _waiting_on_kill(self) -> bool:
        """A killed child is still alive and inside its reap deadline."""
        return any(
            killed.resource.process.exitcode is None
            and (time.monotonic() - killed.started_s) * 1000 < REAP_DEADLINE_MS
            for killed in self._killed
        )

    def _replace_dead_worker(self) -> None:
        """A worker that exited on its own counts as a job that ran too long."""
        resource = self._resource
        if resource is None or resource.process.exitcode is None:
            return
        running = self._running
        stale = self._running_stale
        self._running = None
        self._running_stale = False
        self._resource = None
        self._ready = False
        self._held_version = None
        _release_without_join(resource)
        if running is not None and not stale:
            self.timed_out.emit(running.job_id)
