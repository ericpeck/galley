"""Own the parse worker, its queues, and the file commit.

Cancel updates no widgets itself. The window changes the UI, then calls
:meth:`ParseSession.cancel`, which kills the child and does not join it.
The poll timer reads ``exitcode`` and does not join. Once the child has
exited, its temp file is deleted and a replacement starts with new queues.
If the child is still alive after :data:`REAP_DEADLINE_MS`, the poll starts
that replacement without changing the Cancelled status. A Format press that
had to wait for that deadline is answered with the crash banner, and that
job is then sent to the new worker. A Format press made after a deadline
has already passed runs on the replacement and stays quiet. Cancelling that
job retires the replacement beside any child still being reaped. Each
retired child keeps its own process, queues, reap clock, and temp paths.
The poll reads ``exitcode`` and does not join. When one child exits, only
that child's temps are deleted.

Compare runs on the same worker and queues as Format, one job at a time.
While either kind of job is active, a second submit of either kind is
ignored. Cancel kills a compare the same way it kills a Format, and the
compare's result is dropped with that child's queues. Copy As jobs for a
large Output share the worker the same way.
"""

import atexit
import multiprocessing
import os
import time
from dataclasses import dataclass
from multiprocessing.process import BaseProcess
from multiprocessing.queues import Queue
from queue import Empty

from PySide6.QtCore import QObject, QTimer, Signal

from galley.app.banner import FORMATTER_STOPPED, banner_text
from galley.app.problem_text import (
    DisplayProblem,
    display_input_error,
    display_problem,
)
from galley.core.mask import CopyAsMode
from galley.core.worker import (
    WRITE_CHUNK_SIZE,
    CompareEvent,
    CompareFailureEvent,
    CompareJob,
    CompareSide,
    CopyAsEvent,
    CopyAsFailureEvent,
    CopyAsJob,
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


def _release_without_join(resource: _RunningWorker | None) -> None:
    """Forget a child without joining it.

    The caller already read ``exitcode``, or the session is shutting down.
    """
    if resource is None or resource.closed:
        return
    resource.drop_queues()
    resource.closed = True
    try:
        _LIVE_WORKERS.remove(resource)
    except ValueError:
        return


class _Retired:
    """One killed child, tracked until ``exitcode`` is set or shutdown.

    The poll never joins. Temps on this entry belong to this child only.
    """

    def __init__(
        self,
        process: BaseProcess,
        resource: _RunningWorker | None,
        queues: list[Queue],
        started_s: float,
        temps: list[str],
    ) -> None:
        self.process = process
        self.resource = resource
        self.queues = queues
        self.started_s = started_s
        self.temps = temps


class _Active:
    """Bookkeeping for the job the parent currently expects."""

    def __init__(self, job_id: int, destination: str | None) -> None:
        self.job_id = job_id
        self.destination = destination
        self.temp_path: str | None = None
        self.phase = "read"
        self.written = 0
        self.total = 0


class _CompareActive(_Active):
    """Bookkeeping for a compare job. It has no destination and no temp."""

    def __init__(self, job_id: int) -> None:
        super().__init__(job_id, None)


class _CopyAsActive(_Active):
    """Bookkeeping for a Copy As job. It has no destination and no temp."""

    def __init__(self, job_id: int) -> None:
        super().__init__(job_id, None)


@dataclass(frozen=True)
class CompareFailure:
    """Why a compare did not finish, as ``compare_failed`` delivers it.

    ``side`` is the document that did not load, or ``None`` when both
    loaded and the comparison itself failed. ``problems`` are Problems-pane
    rows with lines in that side's document. ``message`` is the raw failure
    text, used only when ``problems`` is empty.
    """

    side: CompareSide | None
    problems: tuple[DisplayProblem, ...]
    message: str


class ParseSession(QObject):
    """One warm worker and the queues that belong to it."""

    write_progress = Signal(int, int)
    succeeded = Signal(str)
    failed = Signal(str)
    problems_found = Signal(object)
    # Compare: job id, then a ``DiffResult``, a ``CompareFailure``, or nothing.
    compare_finished = Signal(int, object)
    compare_failed = Signal(int, object)
    compare_cancelled = Signal(int)
    # The worker exited while comparing. A replacement has already started.
    compare_stopped = Signal(int)
    # Copy As: job id, then a ``CopyAsResult`` or a ``CopyAsFailureEvent``.
    # A worker that exits mid-copy is a failure too.
    copy_as_finished = Signal(int, object)
    copy_as_failed = Signal(int, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ctx = multiprocessing.get_context("spawn")
        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self._poll)
        self._process: BaseProcess | None = None
        self._job_queue: Queue | None = None
        self._event_queue: Queue | None = None
        self._retired: list[_Retired] = []
        self._reaped_process: BaseProcess | None = None
        self._resource: _RunningWorker | None = None
        self._active: _Active | None = None
        self._pending: FormatJob | CompareJob | CopyAsJob | None = None
        self._report_abandoned_reap = False
        self._job_ids = 0
        self._closing = False
        self._shut_down = False
        self.finished_job_id: int | None = None
        # Packed fold ranges of the last success, read in ``succeeded``.
        self.finished_folds = b""
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
    def active_job_id(self) -> int | None:
        """Job id of the format or compare run the session is waiting on."""
        if self._active is None:
            return None
        return self._active.job_id

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
        if self._active is None and not self._retired:
            self._replace_dead_worker()
        if self._process is None and not self._retired:
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
        if not self._retired:
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
        self._report_abandoned_reap = self._format_is_waiting_out_a_deadline()
        if self._process is None and not self._retired:
            self.start()
        self._flush_pending()

    def submit_compare(
        self,
        left_text: str,
        left_format: str,
        right_text: str,
        right_format: str,
        *,
        hold_s: float = 0.0,
    ) -> int | None:
        """Send one compare job and return its id.

        Returns ``None`` and sends nothing while a Format or a compare is
        active, the same way a second :meth:`submit` is ignored. A compare
        that has to wait out a reap deadline stays quiet and then runs on
        the replacement. ``hold_s`` is for tests (see ``CompareJob``).
        """
        if self._shut_down or self._active is not None:
            return None
        if not self._retired:
            self._replace_dead_worker()
        self._job_ids += 1
        job = CompareJob(
            job_id=self._job_ids,
            left_text=left_text,
            left_format=left_format,
            right_text=right_text,
            right_format=right_format,
            hold_s=hold_s,
        )
        self._active = _CompareActive(job.job_id)
        self._pending = job
        self._report_abandoned_reap = False
        if self._process is None and not self._retired:
            self.start()
        self._flush_pending()
        return job.job_id

    def submit_copy_as(self, text: str, fmt: str, mode: CopyAsMode) -> int | None:
        """Send one Copy As job and return its id.

        Returns ``None`` and sends nothing while another job is active, the
        same way :meth:`submit_compare` does.
        """
        if self._shut_down or self._active is not None:
            return None
        if not self._retired:
            self._replace_dead_worker()
        self._job_ids += 1
        job = CopyAsJob(job_id=self._job_ids, text=text, fmt=fmt, mode=mode)
        self._active = _CopyAsActive(job.job_id)
        self._pending = job
        self._report_abandoned_reap = False
        if self._process is None and not self._retired:
            self.start()
        self._flush_pending()
        return job.job_id

    def cancel(self) -> None:
        """Kill the current worker without joining it or reading its queues.

        Drop the queues. The replacement created after the child exits gets
        new ones. The temp file is removed once that child's ``exitcode``
        is set, so Windows can delete a file the worker still had open.
        This method does not wait, and it does not join. A second cancel
        appends another retired child. It does not drop a child that is
        already retired, and it does not delete that child's temps.
        Inside the deadline, cancelling a job that was never sent leaves
        the retired list unchanged. Past the deadline, the replacement is
        retired beside the first child. Each keeps its own temps.
        """
        if self._shut_down or self._active is None:
            return
        cancelled = self._active
        temp_path = self._active.temp_path
        if temp_path:
            self.last_temp_path = temp_path
        self._active = None
        self._pending = None
        self._report_abandoned_reap = False
        process = self._process
        if process is None:
            self._release_unsent_temp(temp_path)
            self._report_compare_cancelled(cancelled)
            return
        self._retire_current(temp_path)
        _kill(process)
        if not self._timer.isActive():
            self._timer.start()
        self._report_compare_cancelled(cancelled)

    def shutdown(self) -> None:
        """Stop the timer, kill any child, and drop its queues.

        This does not join. Every retired child is killed and its queues
        are dropped. Temp paths still queued on those children are deleted
        here, including ones held past :data:`REAP_DEADLINE_MS`.
        """
        if self._shut_down:
            return
        self._shut_down = True
        self._closing = True
        self._timer.stop()
        temp_path = self._active.temp_path if self._active is not None else None
        self._active = None
        self._pending = None
        self._report_abandoned_reap = False
        if self._process is not None:
            owned = temp_path if not self._temp_is_queued(temp_path) else None
            self._retire_current(owned)
        elif temp_path and not self._temp_is_queued(temp_path):
            unlink_temp(temp_path)
        for entry in list(self._retired):
            _kill(entry.process)
            self._forget_retired(entry, unlink=True)

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
            or self._waiting_on_reap()
            or self._active is None
            or self._active.job_id != job.job_id
        ):
            return
        self._pending = None
        queue.put(job)

    def _poll(self) -> None:
        if self._shut_down:
            return
        if self._retired:
            self._poll_retired()
        if self._shut_down:
            return
        self._drain()
        self._replace_dead_worker()

    def _poll_retired(self) -> None:
        """Watch every killed child. Never join.

        A child that exits loses only its own temps. A child that is still
        alive after :data:`REAP_DEADLINE_MS` keeps those temps, and one
        replacement starts if the session does not already have one. The
        Cancelled status is left alone unless Format had to wait out that
        deadline.
        """
        for entry in list(self._retired):
            if entry not in self._retired:
                continue
            process = entry.process
            if process.exitcode is not None:
                self._finish_retired(entry)
                continue
            if _elapsed_ms(entry.started_s) < REAP_DEADLINE_MS:
                continue
            self._replace_abandoned()

    def _replace_abandoned(self) -> None:
        """Start one replacement once any reap deadline has passed.

        Cancel is the only kill, so a quiet deadline leaves the Cancelled
        status alone. A Format press that had to wait out a deadline would
        otherwise get no response: report :data:`FORMATTER_STOPPED`, then
        send that job to the new worker. A press made when a child is
        already past :data:`REAP_DEADLINE_MS` did not wait, so it stays
        quiet even if another child was just cancelled. Retired children
        stay in the list until their own ``exitcode`` is set. This does
        not join.
        """
        if self._process is not None:
            return
        waiting = self._pending is not None
        if self._closing or self._shut_down:
            self._active = None
            self._pending = None
            return
        if waiting and self._report_abandoned_reap:
            self.failed.emit(FORMATTER_STOPPED)
        self._spawn()
        if waiting:
            self._flush_pending()

    def _finish_retired(self, entry: _Retired) -> None:
        """Drop one exited child and delete only that child's temps."""
        process = entry.process
        self.reaped_pid = process.pid
        self.reaped_exitcode = process.exitcode
        self._reaped_process = process
        already_replaced = self._process is not None
        self._forget_retired(entry, unlink=True)
        if self._closing or self._shut_down or already_replaced:
            return
        self._spawn()
        self._flush_pending()

    def _waiting_on_reap(self) -> bool:
        """True when a job must wait for a child still inside its deadline."""
        if self._process is not None:
            return False
        return any(
            entry.process.exitcode is None
            and _elapsed_ms(entry.started_s) < REAP_DEADLINE_MS
            for entry in self._retired
        )

    def _format_is_waiting_out_a_deadline(self) -> bool:
        """True when this Format must wait for a live child to pass its deadline.

        The crash banner answers that wait. A child already past
        :data:`REAP_DEADLINE_MS` is replaced on the next poll, so pressing
        Format then did not wait and stays quiet.
        """
        if self._process is not None:
            return False
        live = [entry for entry in self._retired if entry.process.exitcode is None]
        if not live:
            return False
        return all(_elapsed_ms(entry.started_s) < REAP_DEADLINE_MS for entry in live)

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
        if isinstance(active, _CompareActive) and not self._closing:
            self.compare_stopped.emit(active.job_id)
        elif isinstance(active, _CopyAsActive) and not self._closing:
            stopped = CopyAsFailureEvent(active.job_id, False)
            self.copy_as_failed.emit(active.job_id, stopped)
        elif had_job and not self._closing:
            self.failed.emit(FORMATTER_STOPPED)
        if not self._closing and not self._shut_down:
            self._spawn()
            self._flush_pending()

    def _retire_current(self, temp_path: str | None) -> None:
        """Append the current child. Leave every earlier retired child in place."""
        process = self._process
        if process is None:
            return
        temps: list[str] = []
        if temp_path and not self._temp_is_queued(temp_path):
            temps.append(temp_path)
        if self._resource is not None:
            queues = self._resource.queues
        else:
            queues = [
                queue
                for queue in (self._job_queue, self._event_queue)
                if queue is not None
            ]
        self._retired.append(
            _Retired(
                process=process,
                resource=self._resource,
                queues=queues,
                started_s=time.monotonic(),
                temps=temps,
            )
        )
        self._process = None
        self._resource = None
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
            elif isinstance(event, CompareEvent):
                self._handle_compare(event)
            elif isinstance(event, CompareFailureEvent):
                self._handle_compare_failure(event)
            elif isinstance(event, CopyAsEvent):
                self._handle_copy_as(event)
            elif isinstance(event, CopyAsFailureEvent):
                self._handle_copy_as_failure(event)

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
        self.finished_job_id = event.job_id
        self.finished_folds = event.folds
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
        rows = _displayed_problems(event)
        if rows:
            self.problems_found.emit(rows)
            return
        self.failed.emit(_failure_text(event))

    def _handle_compare(self, event: CompareEvent) -> None:
        if not self._expects_compare(event.job_id):
            return
        self._active = None
        self._pending = None
        self.compare_finished.emit(event.job_id, event.result)

    def _handle_compare_failure(self, event: CompareFailureEvent) -> None:
        if not self._expects_compare(event.job_id):
            return
        self._active = None
        self._pending = None
        failure = CompareFailure(event.side, _displayed_problems(event), event.message)
        self.compare_failed.emit(event.job_id, failure)

    def _handle_copy_as(self, event: CopyAsEvent) -> None:
        if not self._expects_copy_as(event.job_id):
            return
        self._active = None
        self._pending = None
        self.copy_as_finished.emit(event.job_id, event.result)

    def _handle_copy_as_failure(self, event: CopyAsFailureEvent) -> None:
        if not self._expects_copy_as(event.job_id):
            return
        self._active = None
        self._pending = None
        self.copy_as_failed.emit(event.job_id, event)

    def _expects_copy_as(self, job_id: int) -> bool:
        active = self._active
        return isinstance(active, _CopyAsActive) and active.job_id == job_id

    def _expects_compare(self, job_id: int) -> bool:
        """True for the compare the session is waiting on. Older results are dropped."""
        active = self._active
        return isinstance(active, _CompareActive) and active.job_id == job_id

    def _report_compare_cancelled(self, cancelled: _Active) -> None:
        """Emit after the kill, so a slot that submits waits for the new worker."""
        if isinstance(cancelled, _CompareActive):
            self.compare_cancelled.emit(cancelled.job_id)

    def _release_unsent_temp(self, temp_path: str | None) -> None:
        """Delete a temp that no worker has opened.

        Retired children keep the paths already queued for them. Those are
        removed once, when that child exits or when the UI process shuts
        down. This does not start a replacement and does not join.
        """
        if self._temp_is_queued(temp_path):
            return
        unlink_temp(temp_path)

    def _forget_retired(self, entry: _Retired, *, unlink: bool) -> None:
        """Drop one retired child without joining it."""
        try:
            self._retired.remove(entry)
        except ValueError:
            pass
        if unlink:
            while entry.temps:
                unlink_temp(entry.temps.pop())
        _release_without_join(entry.resource)
        for queue in list(entry.queues):
            _dispose_queue(queue)
        entry.queues.clear()

    def _temp_is_queued(self, path: str | None) -> bool:
        if path is None:
            return False
        return any(path in entry.temps for entry in self._retired)

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


def _displayed_problems(
    event: FailureEvent | CompareFailureEvent,
) -> tuple[DisplayProblem, ...]:
    """Pane rows for a located failure. Empty when the failure has no line."""
    if event.error is not None:
        return (display_input_error(event.error),)
    return tuple(display_problem(problem) for problem in event.problems)


def _failure_text(event: FailureEvent) -> str:
    """Banner sentence for a located error; other failures keep their message."""
    if event.error is None:
        return event.message
    return banner_text(event.error)
