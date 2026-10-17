"""Parse and format documents in a child process.

``run_worker`` is a module-level function so a spawn child can import it by
name. This module does not import Qt. The child writes a temp file beside
the destination and never replaces that destination; the UI process commits.
"""

import os
import tempfile
import time
from dataclasses import dataclass
from multiprocessing.queues import Queue
from typing import BinaryIO

from galley.core.format import format_document

TEMP_PREFIX = ".galley-"
TEMP_SUFFIX = ".tmp"
FAULT_AFTER_TEMP = "after-temp"
_KNOWN_FAULTS = frozenset({None, FAULT_AFTER_TEMP})


@dataclass(frozen=True)
class FormatJob:
    """One document to format. The body is a copy; the worker does not share it."""

    job_id: int
    text: str
    source: str
    target: str
    destination: str | None = None
    temp_path: str | None = None
    chunk_size: int = 65536
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
    """Formatting failed. ``temp_path`` is set when a temp file was created."""

    job_id: int
    message: str
    temp_path: str | None = None


def run_worker(job_queue: Queue, event_queue: Queue) -> None:
    """Format jobs until the queue yields ``None``.

    The spawn parent imports this function by module path. A ``None`` job is
    a clean shutdown. Cancel kills the process instead of using this sentinel.
    """
    while True:
        job = job_queue.get()
        if job is None:
            return
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
    try:
        text = format_document(job.text, job.source, job.target)
    except Exception as exc:
        event_queue.put(FailureEvent(job.job_id, _failure_message(exc), None))
        return
    payload = text.encode("utf-8")
    if job.destination is None and job.temp_path is None:
        event_queue.put(SuccessEvent(job.job_id, text, None))
        return
    temp_path: str | None = None
    try:
        temp_path = _write_output(job, payload, event_queue)
    except Exception as exc:
        event_queue.put(FailureEvent(job.job_id, _failure_message(exc), temp_path))
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
    while written < total:
        end = min(written + step, total)
        handle.write(payload[written:end])
        written = end
        handle.flush()
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


def _failure_message(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return message
    return exc.__class__.__name__


def _is_temp_name(path: str) -> bool:
    name = os.path.basename(path)
    return name.startswith(TEMP_PREFIX) and name.endswith(TEMP_SUFFIX)
