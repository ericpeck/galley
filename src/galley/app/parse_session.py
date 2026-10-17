"""Own the parse worker, its queues, and the file commit.

Cancel updates no widgets itself. The window changes the UI, then calls
:meth:`ParseSession.cancel`, which kills the child and does not join it.
The next event-loop turn reaps the child, deletes its temp file, and starts
a replacement with new queues.
"""

import atexit
import multiprocessing
import os
from multiprocessing.process import BaseProcess
from multiprocessing.queues import Queue
from queue import Empty

from PySide6.QtCore import QObject, QTimer, Signal

from galley.core.worker import (
    FailureEvent,
    FormatJob,
    ProgressEvent,
    SuccessEvent,
    allocate_temp,
    run_worker,
    unlink_temp,
)

_POLL_MS = 30
_SENTINEL_JOIN_S = 1.0
_KILL_JOIN_S = 5.0


class _RunningWorker:
    """A child and its queues. Closed on shutdown, and again at process exit."""

    def __init__(self, process: BaseProcess, queues: list[Queue]) -> None:
        self.process = process
        self.queues = queues
        self.closed = False

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
        if process is not None and process.pid is not None:
            process.join(timeout=_KILL_JOIN_S)
        for queue in self.queues:
            _dispose_queue(queue)
        self.queues = []
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
        self._reap_scheduled = False
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
        chunk_size: int = 65536,
        chunk_pause_s: float = 0.0,
        fault: str | None = None,
    ) -> None:
        """Send one job. A second submit while a job is active is ignored."""
        if self._shut_down or self._active is not None:
            return
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

        Drop the queues. The replacement created after the reap gets new ones.
        The temp file is removed once the child has exited, so Windows can
        delete a file the worker still had open. This method does not wait.
        """
        if self._shut_down or self._active is None:
            return
        if self._active.temp_path:
            self.last_temp_path = self._active.temp_path
            self._temps_to_delete.append(self._active.temp_path)
        self._active = None
        self._pending = None
        self._timer.stop()
        process = self._process
        self._process = None
        if process is not None:
            self._abandoned_job_queue = self._job_queue
            self._abandoned_event_queue = self._event_queue
            self._job_queue = None
            self._event_queue = None
            self._retired_process = process
            self._retired_resource = self._resource
            self._resource = None
            if process.pid is not None:
                try:
                    process.kill()
                except OSError:
                    pass
        if not self._reap_scheduled:
            self._reap_scheduled = True
            QTimer.singleShot(0, self._reap_and_replace)

    def shutdown(self) -> None:
        """Stop the timer, reap any child, and drop its queues."""
        if self._shut_down:
            return
        self._shut_down = True
        self._closing = True
        self._timer.stop()
        busy = self._active is not None
        if self._active is not None and self._active.temp_path:
            self._temps_to_delete.append(self._active.temp_path)
        self._active = None
        self._pending = None
        if self._process is not None:
            self._retired_process = self._process
            self._retired_resource = self._resource
            self._process = None
            self._resource = None
            self._abandoned_job_queue = self._job_queue
            self._abandoned_event_queue = self._event_queue
            self._job_queue = None
            self._event_queue = None
            self._stop_retired(busy)
        self._reap_retired()
        self._unlink_queued_temps()
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
            or self._retired_process is not None
            or self._active is None
            or self._active.job_id != job.job_id
        ):
            return
        self._pending = None
        queue.put(job)

    def _poll(self) -> None:
        self._drain()
        process = self._process
        if process is None or self._active is None:
            return
        if process.exitcode is None:
            return
        path = self._active.temp_path
        self.last_temp_path = path
        self._active = None
        self._pending = None
        unlink_temp(path)
        self.failed.emit("The formatter stopped.")
        resource = self._resource
        self._resource = None
        self._process = None
        self._job_queue = None
        self._event_queue = None
        if resource is not None:
            resource.close()
        if not self._closing:
            self._spawn()

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
        self.failed.emit(event.message)

    def _unlink_queued_temps(self) -> None:
        while self._temps_to_delete:
            unlink_temp(self._temps_to_delete.pop())

    def _reap_and_replace(self) -> None:
        self._reap_scheduled = False
        self._reap_retired()
        self._unlink_queued_temps()
        self._dispose_queue(self._abandoned_job_queue)
        self._dispose_queue(self._abandoned_event_queue)
        self._abandoned_job_queue = None
        self._abandoned_event_queue = None
        if self._closing or self._shut_down:
            return
        self._spawn()
        if not self._timer.isActive():
            self._timer.start()
        self._flush_pending()

    def _stop_retired(self, busy: bool) -> None:
        process = self._retired_process
        if process is None:
            return
        queue = self._abandoned_job_queue
        if not busy and queue is not None and process.pid is not None:
            try:
                queue.put_nowait(None)
            except (OSError, ValueError, AssertionError):
                pass
            process.join(timeout=_SENTINEL_JOIN_S)
        if process.exitcode is None and process.pid is not None:
            try:
                process.kill()
            except OSError:
                pass

    def _reap_retired(self) -> None:
        process = self._retired_process
        if process is None:
            return
        if process.pid is not None:
            process.join(timeout=_KILL_JOIN_S)
            if process.exitcode is None:
                try:
                    process.kill()
                except OSError:
                    pass
                process.join(timeout=_KILL_JOIN_S)
        self.reaped_pid = process.pid
        self.reaped_exitcode = process.exitcode
        self._retired_process = None
        self._reaped_process = process
        resource = self._retired_resource
        self._retired_resource = None
        if resource is not None:
            resource.close()
            self.reaped_exitcode = process.exitcode

    def _dispose_queue(self, queue: Queue | None) -> None:
        _dispose_queue(queue)
