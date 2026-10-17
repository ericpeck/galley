"""Spawn-worker tests that do not need Qt."""

import ast
import multiprocessing
import subprocess
import sys
import time
from pathlib import Path
from queue import Empty

from galley.core.format import format_json
from galley.core.worker import (
    WRITE_CHUNK_SIZE,
    WRITE_PROGRESS_EVERY_CHUNKS,
    FailureEvent,
    FormatJob,
    SuccessEvent,
    run_worker,
    unlink_temp,
)

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


def _run_job(job: FormatJob, timeout: float = 30.0) -> list[object]:
    ctx = multiprocessing.get_context("spawn")
    jobs = ctx.Queue()
    events = ctx.Queue()
    process = ctx.Process(target=run_worker, args=(jobs, events))
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
