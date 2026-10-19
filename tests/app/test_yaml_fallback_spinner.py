"""The busy spinner stays up while a failed C parse retries in pure Python."""

import time
from pathlib import Path

from PySide6.QtWidgets import QComboBox, QLabel, QProgressBar, QPushButton

from galley.app.editor import CodeEditor
from galley.app.main import create_main_window
from galley.app.main_window import FORMATTING_MESSAGE, SPINNER_REVEAL_MS
from galley.core.format import YAML_FORCE_C_PARSER_ERROR, YAML_PURE_RETRY_GATE


def test_spinner_stays_visible_through_the_pure_python_retry(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    gate = tmp_path / "pure-retry"
    monkeypatch.setenv(YAML_FORCE_C_PARSER_ERROR, "1")
    monkeypatch.setenv(YAML_PURE_RETRY_GATE, str(gate))
    clock = {"now": None}

    def now() -> float:
        if clock["now"] is None:
            return time.monotonic()
        return clock["now"]

    monkeypatch.setattr("galley.app.main_window._monotonic", now)
    ready = Path(str(gate) + ".ready")
    go = Path(str(gate) + ".go")
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    try:
        input_format = window.findChild(QComboBox, "inputFormat")
        output_format = window.findChild(QComboBox, "outputFormat")
        editor = window.findChild(CodeEditor, "inputEditor")
        output = window.findChild(CodeEditor, "outputEditor")
        status = window.findChild(QLabel, "statusMessage")
        spinner = window.findChild(QProgressBar, "formatSpinner")
        bar = window.findChild(QProgressBar, "writeProgress")
        cancel = window.findChild(QPushButton, "cancelButton")
        assert isinstance(input_format, QComboBox)
        assert isinstance(output_format, QComboBox)
        assert isinstance(editor, CodeEditor)
        assert isinstance(output, CodeEditor)
        assert status is not None
        assert isinstance(spinner, QProgressBar)
        assert isinstance(bar, QProgressBar)
        assert isinstance(cancel, QPushButton)
        input_format.setCurrentText("YAML")
        output_format.setCurrentText("YAML")
        editor.setPlainText("a: 1\n")
        window._actions.format.trigger()
        qtbot.waitUntil(ready.exists, timeout=15000)
        assert window._formatting
        assert output.toPlainText() == ""
        clock["now"] = window._format_started_s + (SPINNER_REVEAL_MS / 1000)
        window._apply_deferred_reveal()
        assert spinner.isVisible()
        assert not cancel.isVisible()
        assert not bar.isVisible()
        assert status.text() == FORMATTING_MESSAGE
        for _ in range(4):
            qtbot.wait(40)
            assert spinner.isVisible()
            assert not cancel.isVisible()
            assert not bar.isVisible()
            assert output.toPlainText() == ""
            assert window._formatting
        go.write_text("go")
        qtbot.waitUntil(lambda: output.toPlainText() != "", timeout=15000)
        assert output.toPlainText() == "a: 1\n"
        assert status.text() == "Valid YAML"
        assert not spinner.isVisible()
        assert not cancel.isVisible()
        assert not window._formatting
    finally:
        go.write_text("go")
        window.shutdown_parse_worker()
