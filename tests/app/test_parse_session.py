"""Cancel, rollback, and commit tests for the parse worker."""

import json
import os
import sys
from pathlib import Path

from PySide6.QtWidgets import QLabel, QProgressBar, QPushButton

from galley.app.editor import CodeEditor
from galley.app.main import create_main_window
from galley.app.main_window import CANCELLED_MESSAGE
from galley.app.parse_session import ParseSession
from galley.core.format import format_json
from galley.core.worker import FAULT_AFTER_TEMP

_SPAWN_TIMEOUT_MS = 20000
_SLOW_CHUNK = 4096
_SLOW_PAUSE_S = 0.2


def test_cancel_mid_write_leaves_the_original_file(qtbot, tmp_path: Path) -> None:
    destination, original = _original_file(tmp_path)
    session = _started(qtbot)
    try:
        _submit_slow(session, destination)
        qtbot.waitUntil(_mid_write(session), timeout=_SPAWN_TIMEOUT_MS)
        pid = session.process.pid if session.process is not None else None
        session.cancel()
        assert session.reaped_exitcode is None
        qtbot.waitUntil(lambda: session.reaped_exitcode is not None, timeout=10000)
        assert destination.read_bytes() == original
        assert _temps(tmp_path) == []
        assert session.reaped_pid == pid
        _assert_reaped(pid)
        assert session.reaped_process is not None
        assert not session.reaped_process.is_alive()
    finally:
        session.shutdown()


def test_cancel_uses_fresh_queues_for_the_next_job(qtbot, tmp_path: Path) -> None:
    destination, _original = _original_file(tmp_path)
    session = _started(qtbot)
    results: list[str] = []
    session.succeeded.connect(results.append)
    try:
        old_jobs = session.job_queue
        old_events = session.event_queue
        assert old_jobs is not None
        assert old_events is not None
        _submit_slow(session, destination)
        qtbot.waitUntil(_mid_write(session), timeout=_SPAWN_TIMEOUT_MS)
        session.cancel()
        qtbot.waitUntil(
            lambda: session.job_queue is not None and session.job_queue is not old_jobs,
            timeout=_SPAWN_TIMEOUT_MS,
        )
        assert session.event_queue is not old_events
        assert session.reaped_exitcode is not None
        session.submit('{"a": 1}', "json", "json")
        qtbot.waitUntil(lambda: bool(results), timeout=_SPAWN_TIMEOUT_MS)
        assert results == [format_json('{"a": 1}')]
        assert destination.read_bytes() == _original
        assert _temps(tmp_path) == []
    finally:
        session.shutdown()


def test_worker_failure_keeps_the_original_and_removes_the_temp(
    qtbot, tmp_path: Path
) -> None:
    destination, original = _original_file(tmp_path)
    session = _started(qtbot)
    failures: list[str] = []
    session.failed.connect(failures.append)
    try:
        pid = session.process.pid if session.process is not None else None
        session.submit(
            '{"a": 1}',
            "json",
            "json",
            destination=str(destination),
            fault=FAULT_AFTER_TEMP,
        )
        qtbot.waitUntil(lambda: bool(failures), timeout=_SPAWN_TIMEOUT_MS)
        assert failures == ["forced failure"]
        assert destination.read_bytes() == original
        assert session.last_temp_path is not None
        assert Path(session.last_temp_path).parent == destination.parent
        assert _temps(tmp_path) == []
        assert session.process is not None
        assert session.process.pid == pid
        assert session.process.exitcode is None
    finally:
        session.shutdown()


def test_input_error_is_shown_as_a_banner(qtbot) -> None:
    session = _started(qtbot)
    messages: list[str] = []
    session.failed.connect(messages.append)
    try:
        session.submit('{"a": "\\uD800"}', "json", "json")
        qtbot.waitUntil(lambda: bool(messages), timeout=_SPAWN_TIMEOUT_MS)
        assert messages == [
            "Line 1, column 8 has an invalid "
            "character escape (\\uD800) that can't be saved as UTF-8."
        ]
    finally:
        session.shutdown()


def test_success_replaces_the_destination(qtbot, tmp_path: Path) -> None:
    destination = tmp_path / "out.json"
    destination.write_bytes(b"OLD")
    text = '{"a": 1}'
    expected = format_json(text).encode()
    session = _started(qtbot)
    results: list[str] = []
    session.succeeded.connect(results.append)
    try:
        session.submit(text, "json", "json", destination=str(destination))
        qtbot.waitUntil(lambda: bool(results), timeout=_SPAWN_TIMEOUT_MS)
        assert results == [format_json(text)]
        assert destination.read_bytes() == expected
        assert _temps(tmp_path) == []
    finally:
        session.shutdown()


def test_cancel_updates_the_window_before_the_worker_is_killed(
    qtbot, tmp_path: Path
) -> None:
    destination, original = _original_file(tmp_path)
    window = _show(qtbot)
    window.start_parse_worker()
    session = window._session
    qtbot.waitUntil(_warm(session), timeout=_SPAWN_TIMEOUT_MS)
    previous = '{"keep": true}\n'
    output = window.findChild(CodeEditor, "outputEditor")
    status = window.findChild(QLabel, "statusMessage")
    bar = window.findChild(QProgressBar, "writeProgress")
    cancel = window.findChild(QPushButton, "cancelButton")
    assert isinstance(output, CodeEditor)
    assert status is not None
    assert isinstance(bar, QProgressBar)
    assert isinstance(cancel, QPushButton)
    output.setPlainText(previous)
    window._output_before_format = previous
    window._formatting = True
    _submit_slow(session, destination)
    qtbot.waitUntil(
        lambda: _mid_write(session)() and bar.isVisible(),
        timeout=_SPAWN_TIMEOUT_MS,
    )
    assert status.text() == "Formatting… · writing output"
    process = session.process
    assert process is not None
    joined: list[str] = []
    killed: list[str] = []
    real_join = process.join
    real_kill = process.kill

    def _join(*args: object, **kwargs: object) -> None:
        joined.append("join")
        real_join(*args, **kwargs)

    def _kill() -> None:
        killed.append(status.text())
        killed.append(output.toPlainText())
        killed.append("hidden" if not bar.isVisible() else "visible")
        real_kill()

    process.join = _join  # type: ignore[method-assign]
    process.kill = _kill  # type: ignore[method-assign]
    cancel.click()
    assert joined == []
    assert killed == [CANCELLED_MESSAGE, previous, "hidden"]
    assert session.reaped_exitcode is None
    qtbot.waitUntil(lambda: bool(joined), timeout=10000)
    qtbot.waitUntil(lambda: session.reaped_exitcode is not None, timeout=10000)
    assert destination.read_bytes() == original
    assert _temps(tmp_path) == []
    _assert_reaped(session.reaped_pid)
    assert not bar.isVisible()
    assert status.text() == CANCELLED_MESSAGE
    assert output.toPlainText() == previous


def _show(qtbot):
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def _started(qtbot) -> ParseSession:
    session = ParseSession()
    session.start()
    qtbot.waitUntil(_warm(session), timeout=_SPAWN_TIMEOUT_MS)
    return session


def _warm(session: ParseSession):
    def _ready() -> bool:
        process = session.process
        return (
            session.job_queue is not None
            and process is not None
            and process.pid is not None
        )

    return _ready


def _original_file(tmp_path: Path) -> tuple[Path, bytes]:
    destination = tmp_path / "out.json"
    original = b'{"keep": true}\n'
    destination.write_bytes(original)
    return destination, original


def _submit_slow(session: ParseSession, destination: Path) -> None:
    text = json.dumps({"blob": "x" * 80000})
    session.submit(
        text,
        "json",
        "json",
        destination=str(destination),
        chunk_size=_SLOW_CHUNK,
        chunk_pause_s=_SLOW_PAUSE_S,
    )


def _mid_write(session: ParseSession):
    def _partial() -> bool:
        return session.total > 0 and 0 < session.written < session.total

    return _partial


def _temps(directory: Path) -> list[Path]:
    return sorted(directory.glob(".galley-*"))


def _assert_reaped(pid: int | None) -> None:
    assert pid is not None
    if sys.platform == "win32":
        return
    try:
        waited, _status = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return
    assert waited != pid, "worker is a zombie"
    raise AssertionError(f"worker pid {pid} is still running")
