"""Own the parse worker, its queues, and the file commit.

Cancel updates no widgets itself. The window changes the UI, then calls
:meth:`ParseSession.cancel`, which kills the child and does not join it.
The poll timer reads ``exitcode`` and does not join. Once the child has
exited, its temp file is deleted and a replacement starts with new queues.
If the child is still alive after :data:`REAP_DEADLINE_MS`, the poll starts
that replacement without changing the Cancelled status. A Format press that
is still waiting is answered with the crash banner, and that job is then
sent to the new worker. Cancelling that press before the deadline drops
the unsent job only: the temp path stays queued once, and no second
worker is started.
"""

import atexit
import multiprocessing
import os
import time
from multiprocessing.process import BaseProcess
from multiprocessing.queues import Queue
from queue import Empty

from PySide6.QtCore import QObject, QTimer, Signal

from galley.app.banner import FORMATTER_STOPPED, banner_text
from galley.core.worker import (
    WRITE_CHUNK_SIZE,
    FailureEvent,
    FormatJob,
    ProgressEvent,
    SuccessEvent,
    allocate_temp,
    run_worker,
    unlink_temp,
)

_POLL_MS = 30
_KILL_JOIN_S = 5.0

# How long a retired child may stay alive before the poll starts a
# replacement and leaves the temp for shutdown. The poll never joins.
REAP_DEADLINE_MS = 5000


class _RunningWorker:
    """A child and its queues. Closed on shutdown, and again at process exit."""

    def __init__(self, process: BaseProcess, queues: list[Queue]) -> None:
        self.process = process
        self.queues = queues
        self.closed = False

    def drop_queues(self) -> None:
        """Drop the queues without waiting for the child."""
        for queue in self.queues:
            _dispose_queue(queue)
        self.queues = []

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        process = self.process
        if process is not None and process.pid is not None and process.exitcode is None:
            try:
                process.kill()
            except OSError:
                pass
            process.join(timeout=_KILL_JOIN_S)
        self.drop_queues()
        try:
            _LIVE_WORKERS.remove(self)
        except ValueError:
            return


_LIVE_WORKERS: list[_RunningWorker] = []


def _close_live_workers() -> None:
    for worker in list(_LIVE_WORKERS):
        worker.close()


def _arm_exit_cleanup() -> None:
    """Run after queue finalizers are registered so exit closes them first."""
    atexit.unregister(_close_live_workers)
    atexit.register(_close_live_workers)


def _dispose_queue(queue: Queue | None) -> None:
    if queue is None:
        return
    try:
        queue.cancel_join_thread()
    except (AttributeError, OSError, ValueError):
        pass
    try:
        queue.close()
    except (AttributeError, OSError, ValueError):
        pass


def _release_without_join(resource: _RunningWorker) -> None:
    """Forget a child that has already exited. The caller read ``exitcode``."""
    resource.drop_queues()
    resource.closed = True
    try:
        _LIVE_WORKERS.remove(resource)
    except ValueError:
        return


class _Active:
    """Bookkeeping for the job the parent currently expects."""

    def __init__(self, job_id: int, destination: str | None) -> None:
        self.job_id = job_id
        self.destination = destination
        self.temp_path: str | None = None
        self.phase = "read"
        self.written = 0
        self.total = 0


class ParseSession(QObject):
    """One warm worker and the queues that belong to it."""

    write_progress = Signal(int, int)
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ctx = multiprocessing.get_context("spawn")
        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self._poll)
        self._process: BaseProcess | None = None
        self._job_queue: Queue | None = None
        self._event_queue: Queue | None = None
        self._retired_process: BaseProcess | None = None
        self._reaped_process: BaseProcess | None = None
        self._resource: _RunningWorker | None = None
        self._retired_resource: _RunningWorker | None = None
        self._abandoned_job_queue: Queue | None = None
        self._abandoned_event_queue: Queue | None = None
        self._active: _Active | None = None
        self._pending: FormatJob | None = None
        self._temps_to_delete: list[str] = []
        self._deferred_temps: list[str] = []
        self._temps_deferred = False
        self._reap_started_s = 0.0
        self._reap_abandoned = False
        self._job_ids = 0
        self._closing = False
        self._shut_down = False
        self.reaped_pid: int | None = None
        self.reaped_exitcode: int | None = None
        self.last_temp_path: str | None = None

    @property
    def job_queue(self) -> Queue | None:
        return self._job_queue

    @property
    def event_queue(self) -> Queue | None:
        return self._event_queue

    @property
    def process(self) -> BaseProcess | None:
        return self._process

    @property
    def reaped_process(self) -> BaseProcess | None:
        return self._reaped_process

    @property
    def has_pending_job(self) -> bool:
        """True when Format was pressed and the job has not been sent yet."""
        return self._pending is not None

    @property
    def phase(self) -> str:
        if self._active is None:
            return "idle"
        return self._active.phase

    @property
    def written(self) -> int:
        if self._active is None:
            return 0
        return self._active.written

    @property
    def total(self) -> int:
        if self._active is None:
            return 0
        return self._active.total

    def start(self) -> None:
        """Start the warm worker if this session does not already have one."""
        if self._shut_down:
            return
        if self._active is None and self._retired_process is None:
            self._replace_dead_worker()
        if self._process is None and self._retired_process is None:
            self._spawn()
        if not self._timer.isActive():
            self._timer.start()

    def submit(
        self,
        text: str,
        source: str,
        target: str,
        *,
        destination: str | None = None,
        chunk_size: int = WRITE_CHUNK_SIZE,
        chunk_pause_s: float = 0.0,
        fault: str | None = None,
    ) -> None:
        """Send one job. A second submit while a job is active is ignored."""
        if self._shut_down or self._active is not None:
            return
        if self._retired_process is None:
            self._replace_dead_worker()
        temp_path: str | None = None
        if destination is not None:
            try:
                temp_path = allocate_temp(destination)
            except OSError as exc:
                self.failed.emit(str(exc))
                return
        self._job_ids += 1
        job = FormatJob(
            job_id=self._job_ids,
            text=text,
            source=source,
            target=target,
            destination=destination,
            temp_path=temp_path,
            chunk_size=chunk_size,
            chunk_pause_s=chunk_pause_s,
            fault=fault,
        )
        active = _Active(job.job_id, destination)
        active.temp_path = temp_path
        self._active = active
        self.last_temp_path = temp_path
        self._pending = job
        if self._process is None and self._retired_process is None:
            self.start()
        self._flush_pending()

    def cancel(self) -> None:
        """Kill the current worker without joining it or reading its queues.

        Drop the queues. The replacement created after the child exits gets
        new ones. The temp file is removed once ``exitcode`` is set, so
        Windows can delete a file the worker still had open. This method
        does not wait. A child still alive after :data:`REAP_DEADLINE_MS`
        keeps that path for shutdown. A second cancel while that child is
        still inside the deadline drops the unsent job only. The queued
        temp stays put, and no second worker is started. The poll starts
        one replacement without joining and leaves the Cancelled status
        in place. A Format press that is still waiting when the deadline
        passes gets the crash banner, then runs on that replacement.
        """
        if self._shut_down or self._active is None:
            return
        temp_path = self._active.temp_path
        if temp_path:
            self.last_temp_path = temp_path
        self._active = None
        self._pending = None
        process = self._process
        if process is None:
            self._release_unsent_temp(temp_path)
            return
        if temp_path:
            self._queue_temp_once(temp_path)
        self._retire_current()
        _kill(process)
        self._reap_started_s = time.monotonic()
        self._temps_deferred = False
        self._reap_abandoned = False
        if not self._timer.isActive():
            self._timer.start()

    def shutdown(self) -> None:
        """Stop the timer, kill any child, and drop its queues.

        This does not join. A child that is still alive stays registered so
        process exit can reap it. Temp paths, including ones held past
        :data:`REAP_DEADLINE_MS`, are deleted here.
        """
        if self._shut_down:
            return
        self._shut_down = True
        self._closing = True
        self._timer.stop()
        if self._active is not None and self._active.temp_path:
            self._queue_temp_once(self._active.temp_path)
        self._active = None
        self._pending = None
        if self._process is not None:
            if self._retired_process is None:
                self._retire_current()
            else:
                _kill(self._process)
                if self._resource is not None:
                    self._resource.drop_queues()
                self._process = None
                self._resource = None
                self._dispose_queue(self._job_queue)
                self._dispose_queue(self._event_queue)
                self._job_queue = None
                self._event_queue = None
        _kill(self._retired_process)
        if self._retired_resource is not None:
            self._retired_resource.drop_queues()
        self._unlink_queued_temps()
        self._unlink_deferred_temps()
        self._dispose_queue(self._abandoned_job_queue)
        self._dispose_queue(self._abandoned_event_queue)
        self._abandoned_job_queue = None
        self._abandoned_event_queue = None

    def _spawn(self) -> None:
        job_queue: Queue = self._ctx.Queue()
        event_queue: Queue = self._ctx.Queue()
        process = self._ctx.Process(
            target=run_worker,
            name="galley-parse",
            args=(job_queue, event_queue),
        )
        process.start()
        self._job_queue = job_queue
        self._event_queue = event_queue
        self._process = process
        self._resource = _RunningWorker(process, [job_queue, event_queue])
        _LIVE_WORKERS.append(self._resource)
        _arm_exit_cleanup()

    def _flush_pending(self) -> None:
        job = self._pending
        queue = self._job_queue
        if (
            job is None
            or queue is None
            or (self._retired_process is not None and not self._reap_abandoned)
            or self._active is None
            or self._active.job_id != job.job_id
        ):
            return
        self._pending = None
        queue.put(job)

    def _poll(self) -> None:
        if self._shut_down:
            return
        if self._retired_process is not None:
            self._poll_retired()
        if self._shut_down:
            return
        self._drain()
        self._replace_dead_worker()

    def _poll_retired(self) -> None:
        """Watch the killed child. Never join.

        A child that is still alive after :data:`REAP_DEADLINE_MS` keeps
        its temp for shutdown, and a replacement starts on that same tick.
        The Cancelled status is left alone unless Format is still waiting.
        """
        process = self._retired_process
        if process is None:
            return
        if process.exitcode is None:
            if _elapsed_ms(self._reap_started_s) < REAP_DEADLINE_MS:
                return
            self._defer_temps_if_due()
            self._abandon_live_worker()
            return
        already_replaced = self._reap_abandoned and self._process is not None
        self._finish_retired()
        if self._closing or self._shut_down or already_replaced:
            return
        self._spawn()
        self._flush_pending()

    def _abandon_live_worker(self) -> None:
        """Start a replacement once the reap deadline passes.

        Cancel is the only kill, so this stays quiet and leaves the
        Cancelled status alone. A Format press that arrived during the
        wait would otherwise get no response: report
        :data:`FORMATTER_STOPPED`, then send that job to the new worker.
        The child stays retired until ``exitcode`` is set, and its temp
        stays queued. This does not join.
        """
        if self._reap_abandoned:
            return
        self._reap_abandoned = True
        waiting = self._pending is not None
        if self._closing or self._shut_down:
            self._active = None
            self._pending = None
            return
        if waiting:
            self.failed.emit(FORMATTER_STOPPED)
        if self._process is None:
            self._spawn()
        if waiting:
            self._flush_pending()

    def _defer_temps_if_due(self) -> None:
        if self._temps_deferred:
            return
        if _elapsed_ms(self._reap_started_s) < REAP_DEADLINE_MS:
            return
        self._deferred_temps.extend(self._temps_to_delete)
        self._temps_to_delete.clear()
        self._temps_deferred = True

    def _finish_retired(self) -> None:
        process = self._retired_process
        if process is None:
            return
        self.reaped_pid = process.pid
        self.reaped_exitcode = process.exitcode
        self._retired_process = None
        self._reaped_process = process
        resource = self._retired_resource
        self._retired_resource = None
        if resource is not None:
            _release_without_join(resource)
        self._unlink_queued_temps()
        self._unlink_deferred_temps()
        self._temps_deferred = False
        self._dispose_queue(self._abandoned_job_queue)
        self._dispose_queue(self._abandoned_event_queue)
        self._abandoned_job_queue = None
        self._abandoned_event_queue = None
        self._reap_abandoned = False

    def _replace_dead_worker(self) -> None:
        """Respawn when the current child has exited, including while idle.

        A job that was in flight reports :data:`FORMATTER_STOPPED` with no
        location. The signal runs before the replacement is started, and the
        replacement is started before this method returns, so the next Format
        uses the new worker.
        """
        process = self._process
        if process is None or process.exitcode is None:
            return
        active = self._active
        path = active.temp_path if active is not None else None
        if path is not None:
            self.last_temp_path = path
        self._active = None
        self._pending = None
        unlink_temp(path)
        had_job = active is not None
        resource = self._resource
        self._resource = None
        self._process = None
        self._job_queue = None
        self._event_queue = None
        if resource is not None:
            _release_without_join(resource)
        if had_job and not self._closing:
            self.failed.emit(FORMATTER_STOPPED)
        if not self._closing and not self._shut_down:
            self._spawn()
            self._flush_pending()

    def _retire_current(self) -> None:
        self._retired_process = self._process
        self._retired_resource = self._resource
        self._process = None
        self._resource = None
        self._abandoned_job_queue = self._job_queue
        self._abandoned_event_queue = self._event_queue
        self._job_queue = None
        self._event_queue = None

    def _drain(self) -> None:
        queue = self._event_queue
        if queue is None:
            return
        while True:
            try:
                event = queue.get_nowait()
            except Empty:
                return
            except (OSError, EOFError, ValueError):
                return
            if isinstance(event, ProgressEvent):
                self._handle_progress(event)
            elif isinstance(event, SuccessEvent):
                self._handle_success(event)
            elif isinstance(event, FailureEvent):
                self._handle_failure(event)

    def _handle_progress(self, event: ProgressEvent) -> None:
        active = self._active
        if active is None or event.job_id != active.job_id:
            return
        if event.temp_path:
            active.temp_path = event.temp_path
            self.last_temp_path = event.temp_path
        if event.phase != "write":
            active.phase = "read"
            return
        active.phase = "write"
        active.written = event.written
        active.total = event.total
        self.write_progress.emit(event.written, event.total)

    def _handle_success(self, event: SuccessEvent) -> None:
        active = self._active
        if active is None or event.job_id != active.job_id:
            unlink_temp(event.temp_path)
            return
        self._active = None
        self._pending = None
        if event.temp_path is not None and active.destination is not None:
            try:
                os.replace(event.temp_path, active.destination)
            except OSError as exc:
                unlink_temp(event.temp_path)
                self.failed.emit(str(exc))
                return
        self.succeeded.emit(event.text)

    def _handle_failure(self, event: FailureEvent) -> None:
        active = self._active
        if active is None or event.job_id != active.job_id:
            unlink_temp(event.temp_path)
            return
        path = event.temp_path or active.temp_path
        self.last_temp_path = path
        self._active = None
        self._pending = None
        unlink_temp(path)
        self.failed.emit(_failure_text(event))

    def _release_unsent_temp(self, temp_path: str | None) -> None:
        """Delete a temp that no worker has opened.

        A child that is still being reaped keeps the paths already queued
        for it. Those are removed once, when that child exits or when the
        UI process shuts down. This does not start a replacement.
        """
        if self._retired_process is not None:
            if not self._temp_is_queued(temp_path):
                unlink_temp(temp_path)
            return
        if temp_path:
            self._queue_temp_once(temp_path)
        self._unlink_queued_temps()

    def _queue_temp_once(self, path: str) -> None:
        if self._temp_is_queued(path):
            return
        self._temps_to_delete.append(path)

    def _temp_is_queued(self, path: str | None) -> bool:
        if path is None:
            return False
        return path in self._temps_to_delete or path in self._deferred_temps

    def _unlink_queued_temps(self) -> None:
        while self._temps_to_delete:
            unlink_temp(self._temps_to_delete.pop())

    def _unlink_deferred_temps(self) -> None:
        while self._deferred_temps:
            unlink_temp(self._deferred_temps.pop())

    def _dispose_queue(self, queue: Queue | None) -> None:
        _dispose_queue(queue)


def _kill(process: BaseProcess | None) -> None:
    if process is None or process.pid is None or process.exitcode is not None:
        return
    try:
        process.kill()
    except OSError:
        return


def _elapsed_ms(started_s: float) -> float:
    return (time.monotonic() - started_s) * 1000


def _failure_text(event: FailureEvent) -> str:
    """Banner sentence for a located error; other failures keep their message."""
    if event.error is None:
        return event.message
    return banner_text(event.error)
