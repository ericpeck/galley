"""Parse and format documents in a child process.

``run_worker`` is a module-level function so a spawn child can import it by
name. This module does not import Qt. The child writes a temp file beside
the destination and never replaces that destination; the UI process commits.
A ``CompareJob`` loads two documents and diffs them. It has no output file,
so it writes no temp.
"""

import os
import tempfile
import time
from dataclasses import dataclass
from multiprocessing.queues import Queue
from typing import BinaryIO, Literal

from galley.core.diff import DiffResult, diff, load_document
from galley.core.errors import InputError
from galley.core.format import format_document
from galley.core.problems import Problem, collect_problems

TEMP_PREFIX = ".galley-"
TEMP_SUFFIX = ".tmp"
FAULT_AFTER_TEMP = "after-temp"
_KNOWN_FAULTS = frozenset({None, FAULT_AFTER_TEMP})

# Placeholders until Isaac's benchmark (ADR 0001, UI spec §10). Not tuned.
# Chunk size and how often a write-progress event is sent can change separately.
WRITE_CHUNK_SIZE = 65_536
WRITE_PROGRESS_EVERY_CHUNKS = 1


@dataclass(frozen=True)
class FormatJob:
    """One document to format. The body is a copy; the worker does not share it."""

    job_id: int
    text: str
    source: str
    target: str
    destination: str | None = None
    temp_path: str | None = None
    chunk_size: int = WRITE_CHUNK_SIZE
    chunk_pause_s: float = 0.0
    fault: str | None = None


@dataclass(frozen=True)
class ProgressEvent:
    """Read phase has ``written == 0`` and ``total == 0``. Write carries counts."""

    job_id: int
    phase: str
    written: int
    total: int
    temp_path: str | None = None


@dataclass(frozen=True)
class SuccessEvent:
    """Formatting finished. ``temp_path`` is closed and not yet committed."""

    job_id: int
    text: str
    temp_path: str | None = None


@dataclass(frozen=True)
class FailureEvent:
    """Formatting failed. ``temp_path`` is set when a temp file was created.

    ``error`` is set when the failure is an ``InputError``. Its ``kind``,
    ``lineno``, ``colno``, and ``details`` are the structured fields. The
    banner sentence is built in the app, not here.
    """

    job_id: int
    message: str
    temp_path: str | None = None
    error: InputError | None = None
    problems: tuple[Problem, ...] = ()


@dataclass(frozen=True)
class CompareJob:
    """Two documents to compare. The bodies are copies; the worker does not share them.

    ``hold_s`` pauses after both sides load and before ``diff`` runs, so a
    test can cancel mid-compare. The app leaves it at 0.
    """

    job_id: int
    left_text: str
    left_format: str
    right_text: str
    right_format: str
    hold_s: float = 0.0


CompareSide = Literal["left", "right"]


@dataclass(frozen=True)
class CompareEvent:
    """Comparing finished. ``result`` is the engine's ``DiffResult``."""

    job_id: int
    result: DiffResult


@dataclass(frozen=True)
class CompareFailureEvent:
    """Comparing failed.

    ``side`` names the document that did not load. It is ``None`` when both
    loaded and ``diff`` itself failed. ``error`` and ``problems`` are filled
    the way :class:`FailureEvent` fills them, with lines in that side's
    document. The words are chosen in the app.
    """

    job_id: int
    side: CompareSide | None
    message: str
    error: InputError | None = None
    problems: tuple[Problem, ...] = ()


def run_worker(job_queue: Queue, event_queue: Queue) -> None:
    """Format jobs until the queue yields ``None``.

    The spawn parent imports this function by module path. A ``None`` job is
    a clean shutdown. Cancel kills the process instead of using this sentinel.
    """
    while True:
        job = job_queue.get()
        if job is None:
            return
        if isinstance(job, CompareJob):
            _execute_compare(job, event_queue)
            continue
        if not isinstance(job, FormatJob):
            continue
        _execute(job, event_queue)


def allocate_temp(destination: str) -> str:
    """Create an empty temp file in ``destination``'s directory and close it.

    The parent keeps this path so Cancel can delete it without reading a
    queue. The worker opens it only to write formatted bytes.
    """
    directory = os.path.dirname(os.path.abspath(destination))
    descriptor, name = tempfile.mkstemp(
        prefix=TEMP_PREFIX,
        suffix=TEMP_SUFFIX,
        dir=directory,
    )
    os.close(descriptor)
    return name


def unlink_temp(path: str | None) -> None:
    """Delete a Galley temp file. Other paths are left alone."""
    if path is None or not _is_temp_name(path):
        return
    try:
        os.unlink(path)
    except OSError:
        return


def _execute(job: FormatJob, event_queue: Queue) -> None:
    if job.fault not in _KNOWN_FAULTS:
        event_queue.put(FailureEvent(job.job_id, "Unknown fault.", None))
        return
    event_queue.put(ProgressEvent(job.job_id, "read", 0, 0, None))
    early = collect_problems(job.text, job.source)
    if early:
        event_queue.put(FailureEvent(job.job_id, "", None, None, early))
        return
    try:
        text = format_document(job.text, job.source, job.target)
    except Exception as exc:
        event_queue.put(_failure_event(job, exc, None))
        return
    payload = text.encode("utf-8")
    if job.destination is None and job.temp_path is None:
        event_queue.put(SuccessEvent(job.job_id, text, None))
        return
    temp_path: str | None = None
    try:
        temp_path = _write_output(job, payload, event_queue)
    except Exception as exc:
        event_queue.put(_failure_event(job, exc, temp_path))
        return
    if job.fault == FAULT_AFTER_TEMP:
        event_queue.put(FailureEvent(job.job_id, "forced failure", temp_path))
        return
    event_queue.put(SuccessEvent(job.job_id, text, temp_path))


def _write_output(job: FormatJob, payload: bytes, event_queue: Queue) -> str:
    path = job.temp_path
    if path is None:
        if job.destination is None:
            raise ValueError("A destination is required to write output.")
        path = allocate_temp(job.destination)
    try:
        # The handle is closed before this returns so Windows can replace it.
        with open(path, "wb") as handle:
            _write_chunks(job, payload, path, handle, event_queue)
    except Exception:
        unlink_temp(path)
        raise
    return path


def _write_chunks(
    job: FormatJob,
    payload: bytes,
    path: str,
    handle: BinaryIO,
    event_queue: Queue,
) -> None:
    total = len(payload)
    _report_write(event_queue, job.job_id, path, 0, total)
    written = 0
    step = job.chunk_size if job.chunk_size > 0 else 1
    pause_s = job.chunk_pause_s if job.chunk_pause_s > 0 else 0.0
    every = WRITE_PROGRESS_EVERY_CHUNKS if WRITE_PROGRESS_EVERY_CHUNKS > 0 else 1
    chunks = 0
    while written < total:
        end = min(written + step, total)
        handle.write(payload[written:end])
        written = end
        handle.flush()
        chunks += 1
        if chunks % every == 0 or written == total:
            _report_write(event_queue, job.job_id, path, written, total)
        if pause_s > 0 and written < total:
            time.sleep(pause_s)
    os.fsync(handle.fileno())


def _report_write(
    event_queue: Queue,
    job_id: int,
    temp_path: str,
    written: int,
    total: int,
) -> None:
    event_queue.put(ProgressEvent(job_id, "write", written, total, temp_path))


def _failure_event(
    job: FormatJob, exc: Exception, temp_path: str | None
) -> FailureEvent:
    error = exc if isinstance(exc, InputError) else None
    problems: tuple[Problem, ...] = ()
    if error is None:
        problems = collect_problems(job.text, job.source, exc)
    return FailureEvent(job.job_id, _failure_message(exc), temp_path, error, problems)


def _failure_message(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return message
    return exc.__class__.__name__


def run_compare(job: CompareJob) -> CompareEvent | CompareFailureEvent:
    """Load both sides, then diff them. Left is checked before right.

    This is the whole compare job without the queue, so it also runs
    in-process. A side that fails stops the job before ``diff`` runs.
    """
    sides: tuple[tuple[CompareSide, str, str], ...] = (
        ("left", job.left_text, job.left_format),
        ("right", job.right_text, job.right_format),
    )
    docs = []
    for side, text, source in sides:
        early = collect_problems(text, source)
        if early:
            return CompareFailureEvent(job.job_id, side, "", None, early)
        try:
            docs.append(load_document(text, source))
        except Exception as exc:
            return _compare_failure(job.job_id, side, text, source, exc)
    if job.hold_s > 0:
        time.sleep(job.hold_s)
    try:
        result = diff(docs[0], docs[1])
    except Exception as exc:
        error = exc if isinstance(exc, InputError) else None
        return CompareFailureEvent(job.job_id, None, _failure_message(exc), error)
    return CompareEvent(job.job_id, result)


def _execute_compare(job: CompareJob, event_queue: Queue) -> None:
    event_queue.put(ProgressEvent(job.job_id, "read", 0, 0, None))
    event_queue.put(run_compare(job))


def _compare_failure(
    job_id: int, side: CompareSide, text: str, source: str, exc: Exception
) -> CompareFailureEvent:
    error = exc if isinstance(exc, InputError) else None
    problems: tuple[Problem, ...] = ()
    if error is None:
        problems = collect_problems(text, source, exc)
    return CompareFailureEvent(job_id, side, _failure_message(exc), error, problems)


def _is_temp_name(path: str) -> bool:
    name = os.path.basename(path)
    return name.startswith(TEMP_PREFIX) and name.endswith(TEMP_SUFFIX)
