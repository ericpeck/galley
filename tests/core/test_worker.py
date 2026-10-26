"""Spawn-worker tests that do not need Qt."""

import ast
import multiprocessing
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from queue import Empty

from tests.core.xml_row import run_worker_with_xml_row

from galley.core.errors import MAX_DEPTH, InputError
from galley.core.format import format_json
from galley.core.problems import Problem
from galley.core.worker import (
    WRITE_CHUNK_SIZE,
    WRITE_PROGRESS_EVERY_CHUNKS,
    FailureEvent,
    FormatJob,
    ProgressEvent,
    SuccessEvent,
    run_worker,
    unlink_temp,
)
from galley.core.xml_format import format_xml

ROOT = Path(__file__).resolve().parents[2]
WORKER_PATH = ROOT / "src" / "galley" / "core" / "worker.py"

_NO_START_METHOD = """
import multiprocessing

from galley.core.format import format_json
from galley.core.worker import FailureEvent, FormatJob, SuccessEvent, run_worker

def main() -> None:
    # Spawn re-imports this file as __mp_main__. Only the original process
    # checks the start method, and it does so before the child is created.
    assert multiprocessing.get_start_method(allow_none=True) is None
    ctx = multiprocessing.get_context("spawn")
    jobs = ctx.Queue()
    events = ctx.Queue()
    process = ctx.Process(target=run_worker, args=(jobs, events))
    assert process._start_method == "spawn"
    process.start()
    text = '{"a": 1}'
    try:
        jobs.put(FormatJob(job_id=1, text=text, source="json", target="json"))
        event = None
        for _ in range(10):
            event = events.get(timeout=30)
            if isinstance(event, (SuccessEvent, FailureEvent)):
                break
        jobs.put(None)
        process.join(timeout=10)
        assert process.exitcode == 0, process.exitcode
        assert isinstance(event, SuccessEvent)
        assert event.text == format_json(text)
        print("ok")
    finally:
        if process.exitcode is None and process.pid is not None:
            process.kill()
            process.join(timeout=5)
        jobs.cancel_join_thread()
        events.cancel_join_thread()
        jobs.close()
        events.close()

if __name__ == "__main__":
    main()
"""


def test_worker_runs_without_a_global_start_method(tmp_path: Path) -> None:
    script = tmp_path / "run_worker.py"
    script.write_text(_NO_START_METHOD, encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(script)],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "ok"


def test_write_cutoffs_are_named_placeholders() -> None:
    job = FormatJob(job_id=1, text="{}", source="json", target="json")
    assert job.chunk_size == WRITE_CHUNK_SIZE
    assert WRITE_PROGRESS_EVERY_CHUNKS >= 1


def test_worker_writes_a_temp_and_leaves_the_destination(tmp_path: Path) -> None:
    destination = tmp_path / "out.json"
    original = b'{"keep": true}\n'
    destination.write_bytes(original)
    text = '{"a": 1}'
    events = _run_job(
        FormatJob(
            job_id=1,
            text=text,
            source="json",
            target="json",
            destination=str(destination),
        )
    )
    success = events[-1]
    assert isinstance(success, SuccessEvent)
    assert success.text == format_json(text)
    assert success.temp_path is not None
    temp = Path(success.temp_path)
    assert temp.parent == destination.parent
    assert temp.name.startswith(".galley-")
    assert temp.read_bytes() == format_json(text).encode()
    assert destination.read_bytes() == original
    unlink_temp(success.temp_path)
    assert not temp.exists()


def test_worker_error_reaches_the_parent_with_its_fields() -> None:
    events = _run_job(
        FormatJob(
            job_id=1,
            text='{"a": "\\uD800"}',
            source="json",
            target="json",
        )
    )
    failure = events[-1]
    assert isinstance(failure, FailureEvent)
    error = failure.error
    assert isinstance(error, InputError)
    assert error.kind == "bad_escape"
    assert error.lineno == 1
    assert error.colno == 8
    assert error.details == {"escape": "\\uD800"}


def test_worker_flags_a_value_json_cannot_hold() -> None:
    events = _run_job(
        FormatJob(
            job_id=1,
            text="file:\n  data: !!binary aGVsbG8=\n",
            source="yaml",
            target="json",
        )
    )
    failure = events[-1]
    assert isinstance(failure, FailureEvent)
    assert failure.not_json_value is True
    assert failure.error is None
    assert failure.problems == ()


def test_other_worker_failures_are_not_flagged() -> None:
    events = _run_job(
        FormatJob(job_id=1, text='{"a": "\\uD800"}', source="json", target="json")
    )
    failure = events[-1]
    assert isinstance(failure, FailureEvent)
    assert failure.not_json_value is False


def test_worker_module_does_not_replace_the_destination() -> None:
    tree = ast.parse(WORKER_PATH.read_text(encoding="utf-8"))
    replaced = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "replace"
    ]
    assert replaced == []


def _run_job(
    job: FormatJob, timeout: float = 30.0, target: Callable[..., None] = run_worker
) -> list[object]:
    ctx = multiprocessing.get_context("spawn")
    jobs = ctx.Queue()
    events = ctx.Queue()
    process = ctx.Process(target=target, args=(jobs, events))
    process.start()
    collected: list[object] = []
    try:
        jobs.put(job)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                event = events.get(timeout=0.2)
            except Empty:
                if process.exitcode is not None:
                    break
                continue
            collected.append(event)
            if isinstance(event, (SuccessEvent, FailureEvent)):
                break
        else:
            raise AssertionError("worker timed out")
        jobs.put(None)
        process.join(timeout=10)
        assert process.exitcode == 0, process.exitcode
        return collected
    finally:
        if process.exitcode is None and process.pid is not None:
            process.kill()
            process.join(timeout=5)
        jobs.cancel_join_thread()
        events.cancel_join_thread()
        jobs.close()
        events.close()


def _run_xml_job(job: FormatJob) -> list[object]:
    """Run one job in a spawn child whose table has the slice 3 XML row."""
    return _run_job(job, target=run_worker_with_xml_row)


def test_worker_formats_xml_with_no_fold_ranges() -> None:
    text = '<?xml version="1.0"?><r><a x="1"/><b>t</b></r>'
    events = _run_xml_job(FormatJob(job_id=3, text=text, source="xml", target="XML"))
    success = events[-1]
    assert isinstance(success, SuccessEvent)
    assert success.text == format_xml(text)
    assert success.folds == b""


def test_worker_xml_problem_crosses_spawn() -> None:
    text = "<r>\n  <a>\n  </b>\n</r>\n"
    events = _run_xml_job(FormatJob(job_id=4, text=text, source="xml", target="xml"))
    failure = events[-1]
    assert isinstance(failure, FailureEvent)
    assert failure.error is None
    assert failure.message == "xml_mismatched_tag"
    assert failure.problems == (Problem("xml_mismatched_tag", 3, "a", 3),)
    assert failure.problems[0].colno == 3


def test_worker_xml_too_deep_crosses_spawn() -> None:
    text = "<a>" * (MAX_DEPTH + 1) + "</a>" * (MAX_DEPTH + 1)
    events = _run_xml_job(FormatJob(job_id=5, text=text, source="xml", target="xml"))
    failure = events[-1]
    assert isinstance(failure, FailureEvent)
    assert isinstance(failure.error, InputError)
    assert failure.error.kind == "too_deep"
    assert failure.error.details == {"depth": MAX_DEPTH + 1}


def test_worker_without_the_xml_row_rejects_an_xml_job() -> None:
    events = _run_job(FormatJob(job_id=8, text="<a/>", source="xml", target="xml"))
    failure = events[-1]
    assert isinstance(failure, FailureEvent)
    assert failure.message == "Unsupported format: xml"


def test_an_xml_job_cancelled_mid_write_leaves_the_destination(
    tmp_path: Path,
) -> None:
    """Cancel kills the child, so the temp is the parent's to delete."""
    destination = tmp_path / "out.xml"
    original = b"<keep/>\n"
    destination.write_bytes(original)
    text = "<r>" + "<item><name>fake</name></item>" * 4000 + "</r>"
    job = FormatJob(
        job_id=6,
        text=text,
        source="xml",
        target="xml",
        destination=str(destination),
        chunk_size=4096,
        chunk_pause_s=0.2,
    )
    ctx = multiprocessing.get_context("spawn")
    jobs = ctx.Queue()
    events = ctx.Queue()
    process = ctx.Process(target=run_worker_with_xml_row, args=(jobs, events))
    process.start()
    temp_path: str | None = None
    try:
        jobs.put(job)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            event = events.get(timeout=30)
            assert not isinstance(event, (SuccessEvent, FailureEvent)), event
            if isinstance(event, ProgressEvent) and 0 < event.written < event.total:
                temp_path = event.temp_path
                break
        assert temp_path is not None
        process.kill()
        process.join(timeout=10)
        assert process.exitcode is not None and process.exitcode != 0
    finally:
        if process.exitcode is None:
            process.kill()
            process.join(timeout=5)
        jobs.cancel_join_thread()
        events.cancel_join_thread()
        jobs.close()
        events.close()
    assert Path(temp_path).exists()
    unlink_temp(temp_path)
    assert not Path(temp_path).exists()
    assert destination.read_bytes() == original
