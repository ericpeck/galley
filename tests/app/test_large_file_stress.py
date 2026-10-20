"""Time large JSON documents and fail when an operation exceeds its budget.

The 1 MB and 2 MB fixtures are built in memory from a fixed shape: nested
objects and arrays, long strings, and non-ASCII text. Nothing large is
written into the tree. Each measurement is printed as
``large-file <size> <operation>: <seconds>s limit <seconds>s`` so
:data:`LARGE_FILE_LIMITS_S` can be tightened from CI logs.

Parse and format go through ``format_json``. Collapse hides nested lines in
the editor document: Expand all and Collapse all are still Coming soon, so
there is no fold command to call. Search uses the editor's find on a marker
planted at the end of the fixture. Syntax coloring is the editor highlighter
turning on for that document. Change markers use the gutter API from the
Compare prep work. The diff engine is timed only when ``galley.core.diff.diff``
is importable; this checkout does not invent one.
"""

import gc
import importlib
import importlib.util
import json
import time

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QPainter, QPalette
from PySide6.QtWidgets import QMessageBox, QProgressBar, QPushButton

from galley.app.editor import CHANGE_MARKERS, CodeEditor, LineMarker
from galley.app.main import create_main_window
from galley.app.main_window import SIZE_WARNING_DETAIL, si_size
from galley.app.parse_session import ParseSession
from galley.core.format import format_json

# SI megabytes, matching the size warning (1 MB is 1_000_000 bytes).
_BYTES = {"1 MB": 1_000_000, "2 MB": 2_000_000}
_NEEDLE = "galley-stress-needle"
_UNICODE = "café — 日本語"
_EMOJI = "😀"
_NOTE = "pack " * 80

# Generous ceilings, in seconds. Runners differ; the printed times are the
# record to tighten these from. A miss fails the job. Do not skip the test.
LARGE_FILE_LIMITS_S: dict[str, dict[str, float]] = {
    "1 MB": {
        "parse": 3.0,
        "format": 3.0,
        "collapse": 5.0,
        "search": 3.0,
        "syntax": 15.0,
        "compare-markers": 3.0,
        "compare": 20.0,
    },
    "2 MB": {
        "parse": 5.0,
        "format": 5.0,
        "collapse": 8.0,
        "search": 5.0,
        "syntax": 30.0,
        "compare-markers": 5.0,
        "compare": 40.0,
    },
}

_DOCUMENTS: dict[str, str] = {}


class _ChangeMarker(LineMarker):
    """Gutter mark with no line tint, so a full-document set stays linear."""

    def paint(self, painter: QPainter, rect: QRect, palette: QPalette) -> None:
        del painter, rect, palette


_CHANGE_MARKER = _ChangeMarker()


def test_generated_fixture_is_deterministic_json() -> None:
    """Both sizes are exact, nested, and end with the search marker."""
    for label, size in _BYTES.items():
        first = _build(size)
        second = _build(size)
        assert first == second
        assert len(first.encode("utf-8")) == size
        assert first.startswith("{\n")
        assert _UNICODE in first
        assert _EMOJI in first
        assert _NOTE.strip() in first
        assert first.count(_NEEDLE) == 1
        parsed = json.loads(first)
        assert isinstance(parsed["items"], list) and parsed["items"]
        sample = parsed["items"][0]
        assert isinstance(sample["child"], dict)
        assert isinstance(sample["tags"], list)
        assert isinstance(sample["note"], str) and len(sample["note"]) > 100
        marker_at = first.rfind(_NEEDLE)
        assert marker_at > len(first) * 9 // 10
        assert parsed["tail"]["marker"] == _NEEDLE
        rendered = json.dumps(parsed, indent=2, ensure_ascii=False) + "\n"
        assert first == rendered
        _DOCUMENTS[label] = first


@pytest.mark.parametrize("label", ["1 MB", "2 MB"])
def test_parse_and_format_stay_within_limits(label: str) -> None:
    text = _document(label)
    formatted, parse_s, format_s = _time_format_json(text)
    _check(label, "parse", parse_s)
    _check(label, "format", format_s)
    assert _NEEDLE in formatted
    assert json.loads(formatted)["tail"]["marker"] == _NEEDLE


@pytest.mark.parametrize("label", ["1 MB", "2 MB"])
def test_editor_operations_stay_within_limits(qtbot, label: str) -> None:
    text = _document(label)
    editor = CodeEditor()
    qtbot.addWidget(editor)
    editor.resize(800, 600)
    editor.show()
    qtbot.waitExposed(editor)
    editor.set_syntax_colors_enabled(False)
    editor.set_syntax_mode("JSON")
    editor.setPlainText(text)
    qtbot.waitUntil(lambda: editor.blockCount() > 2)

    gc.collect()
    syntax_limit_ms = int(LARGE_FILE_LIMITS_S[label]["syntax"] * 1000)
    syntax_started = time.perf_counter()
    editor.set_syntax_colors_enabled(True)
    try:
        if not _first_block_is_colored(editor):
            # Enabling colors recolors on the next event-loop pass.
            qtbot.waitUntil(
                lambda: _first_block_is_colored(editor),
                timeout=syntax_limit_ms,
            )
    finally:
        syntax_s = time.perf_counter() - syntax_started
    _check(label, "syntax", syntax_s)
    assert _first_block_is_colored(editor)

    found_block = -1

    def search() -> None:
        nonlocal found_block
        cursor = editor.textCursor()
        cursor.setPosition(0)
        editor.setTextCursor(cursor)
        assert editor.find(_NEEDLE)
        found_block = editor.textCursor().blockNumber()

    search_s = _elapsed(search)
    _check(label, "search", search_s)
    assert found_block >= editor.blockCount() * 9 // 10

    hidden = 0

    def collapse() -> None:
        nonlocal hidden
        hidden = _collapse_all(editor)

    collapse_s = _elapsed(collapse)
    _check(label, "collapse", collapse_s)
    assert hidden > editor.blockCount() // 2
    assert _NEEDLE in editor.toPlainText()

    def mark() -> None:
        count = editor.blockCount()
        editor.set_line_markers(
            CHANGE_MARKERS,
            dict.fromkeys(range(1, count + 1), _CHANGE_MARKER),
        )

    markers_s = _elapsed(mark)
    _check(label, "compare-markers", markers_s)
    assert len(editor.line_markers(CHANGE_MARKERS)) == editor.blockCount()


def test_compare_engine_is_timed_when_it_is_public() -> None:
    """Time ``galley.core.diff.diff`` when that module is on this checkout."""
    diff = _public_diff()
    if diff is None:
        print("large-file compare: no public API")
        return
    for label in _BYTES:
        text = _document(label)
        left = json.loads(text)
        right = json.loads(text)
        right["items"].insert(0, {"id": -1, "title": "inserted"})
        elapsed = _elapsed(lambda: diff(left, right))
        _check(label, "compare", elapsed)


def test_large_format_shows_the_indicator_and_the_size_warning(
    qtbot, monkeypatch
) -> None:
    """A large format shows the busy UI, and the size prompt is not modal."""
    text = _document("1 MB")
    window = _show(qtbot)
    try:
        _hold_submit(monkeypatch, window)
        editor = window.findChild(CodeEditor, "inputEditor")
        assert isinstance(editor, CodeEditor)
        editor.set_syntax_colors_enabled(False)
        editor.setPlainText(text)
        qtbot.waitUntil(lambda: editor.toPlainText() == text)

        monkeypatch.setattr("galley.app.main_window.JSON_WARNING_BYTES", 1)
        window._actions.format.trigger()
        warning = _visible_warning(window)
        assert warning is not None
        assert warning.text() == f"Format this {si_size(len(text.encode()))} document?"
        assert warning.informativeText() == SIZE_WARNING_DETAIL
        anyway = warning.defaultButton()
        cancel = warning.escapeButton()
        assert anyway is not None and anyway.text() == "Format anyway"
        assert cancel is not None and cancel.text() == "Cancel"
        assert warning.windowModality() == Qt.WindowModality.WindowModal
        assert not window._formatting
        cancel.click()
        qtbot.waitUntil(lambda: _visible_warning(window) is None)
        assert editor.toPlainText() == text
        assert not window._formatting

        monkeypatch.setattr("galley.app.main_window.JSON_WARNING_BYTES", 10**12)
        monkeypatch.setattr("galley.app.main_window.JSON_BUSY_BYTES", 1)
        monkeypatch.setattr("galley.app.main_window.JSON_PROGRESS_BYTES", 10**12)
        window._actions.format.trigger()
        spinner = _bar(window, "formatSpinner")
        progress = _bar(window, "writeProgress")
        cancel_button = _button(window, "cancelButton")
        assert window._formatting
        assert spinner.isVisible()
        assert spinner.accessibleName() == "Formatting"
        assert not progress.isVisible()
        assert not cancel_button.isVisible()
        window._cancel_format()
        assert not window._formatting
        assert not spinner.isVisible()

        monkeypatch.setattr("galley.app.main_window.JSON_PROGRESS_BYTES", 1)
        window._actions.format.trigger()
        assert window._formatting
        assert progress.isVisible()
        assert progress.accessibleName() == "Formatting progress"
        assert cancel_button.isVisible()
        assert cancel_button.text() == "Cancel"
        assert not spinner.isVisible()
        window._cancel_format()
        assert not progress.isVisible()
        assert editor.toPlainText() == text
    finally:
        window.shutdown_parse_worker()


def _check(label: str, operation: str, elapsed: float) -> None:
    limit = LARGE_FILE_LIMITS_S[label][operation]
    print(f"large-file {label} {operation}: {elapsed:.4f}s limit {limit:.4f}s")
    assert elapsed <= limit, (
        f"{label} {operation} took {elapsed:.4f}s; limit is {limit:.4f}s"
    )


def _elapsed(operation) -> float:
    gc.collect()
    started = time.perf_counter()
    operation()
    return time.perf_counter() - started


def _document(label: str) -> str:
    cached = _DOCUMENTS.get(label)
    if cached is None:
        cached = _build(_BYTES[label])
        _DOCUMENTS[label] = cached
    return cached


def _item(index: int) -> dict[str, object]:
    note = _NOTE if index % 11 else f"{_NOTE} {_EMOJI}"
    title = _UNICODE if index % 4 == 0 else f"item-{index}"
    return {
        "id": index,
        "title": title,
        "ok": index % 2 == 0,
        "n": index,
        "tags": ["alpha", "beta", index % 5],
        "child": {"depth": 1, "values": [index, None, True]},
        "note": note,
    }


def _render(items: list[dict[str, object]], pad: str) -> str:
    payload = {
        "kind": "galley-stress",
        "unicode": _UNICODE,
        "items": items,
        "tail": {"pad": pad, "marker": _NEEDLE},
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if not text.endswith("\n"):
        text += "\n"
    return text


def _build(target: int) -> str:
    """Return pretty-printed JSON of exactly ``target`` UTF-8 bytes.

    The item count is a binary search, so generation does not re-dump the
    document once per item.
    """
    lo = 0
    hi = 1
    while _utf8_len(_render([_item(i) for i in range(hi)], "")) <= target:
        lo = hi
        hi *= 2
    best = lo
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if _utf8_len(_render([_item(i) for i in range(mid)], "")) <= target:
            best = mid
            lo = mid
        else:
            hi = mid
    items = [_item(i) for i in range(best)]
    gap = target - _utf8_len(_render(items, ""))
    if gap < 0:
        raise RuntimeError(f"fixture wrapper exceeds {target} bytes")
    text = _render(items, "x" * gap)
    size = _utf8_len(text)
    if size != target:
        raise RuntimeError(f"fixture is {size} bytes, wanted {target}")
    return text


def _utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def _time_format_json(text: str) -> tuple[str, float, float]:
    """Return formatted text, parse seconds, and the rest of ``format_json``.

    Parse runs from the start of the call through ``json.loads``, including
    the checks in front of the parser. Format is the rest of the call.
    """
    load_end: float | None = None
    calls = 0
    real_loads = json.loads

    def timed_loads(*args: object, **kwargs: object) -> object:
        nonlocal load_end, calls
        calls += 1
        try:
            return real_loads(*args, **kwargs)
        finally:
            load_end = time.perf_counter()

    json.loads = timed_loads  # type: ignore[assignment]
    try:
        gc.collect()
        started = time.perf_counter()
        result = format_json(text)
        total = time.perf_counter() - started
    finally:
        json.loads = real_loads
    assert calls == 1
    assert load_end is not None
    parse_s = load_end - started
    return result, parse_s, total - parse_s


def _collapse_all(editor: CodeEditor) -> int:
    """Hide nested lines. Use a public ``collapse_all`` when the editor has one."""
    method = getattr(editor, "collapse_all", None)
    if callable(method):
        method()
        return _hidden_blocks(editor)
    return _hide_nested_lines(editor)


def _hide_nested_lines(editor: CodeEditor) -> int:
    document = editor.document()
    hidden = 0
    block = document.firstBlock()
    while block.isValid():
        if block.text()[:1] in " \t":
            block.setVisible(False)
            hidden += 1
        block = block.next()
    document.markContentsDirty(0, max(0, document.characterCount() - 1))
    editor.viewport().update()
    return hidden


def _hidden_blocks(editor: CodeEditor) -> int:
    hidden = 0
    block = editor.document().firstBlock()
    while block.isValid():
        if not block.isVisible():
            hidden += 1
        block = block.next()
    return hidden


def _first_block_is_colored(editor: CodeEditor) -> bool:
    block = editor.document().firstBlock()
    return block.isValid() and bool(block.layout().formats())


def _public_diff():
    """Return the public tree diff, or None when Compare has no engine yet."""
    if importlib.util.find_spec("galley.core.diff") is None:
        return None
    module = importlib.import_module("galley.core.diff")
    diff = getattr(module, "diff", None)
    if callable(diff):
        return diff
    return None


def _show(qtbot):
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def _hold_submit(monkeypatch: pytest.MonkeyPatch, window) -> None:
    """Keep Format in progress without sending the document to the worker."""
    monkeypatch.setattr(ParseSession, "active_job_id", property(lambda _self: 1))
    window._session.submit = lambda *_args, **_kwargs: None  # type: ignore[method-assign]


def _visible_warning(window) -> QMessageBox | None:
    visible = [
        box
        for box in window.findChildren(QMessageBox)
        if box.isVisible() and box.objectName() == "sizeWarning"
    ]
    if not visible:
        return None
    assert len(visible) == 1
    return visible[0]


def _bar(window, name: str) -> QProgressBar:
    bar = window.findChild(QProgressBar, name)
    assert isinstance(bar, QProgressBar)
    return bar


def _button(window, name: str) -> QPushButton:
    button = window.findChild(QPushButton, name)
    assert isinstance(button, QPushButton)
    return button
