"""Showing a large result in Output, and coloring it a chunk at a time (#117).

Putting a 1.44 MB result into Output took about 0.35 s on the UI thread:
Qt builds the document in about 0.12 s, and the first highlight pass over
its 67,000 lines took the rest. The highlighter now colors one chunk of
never-colored ("cold") blocks per pass, the lines on screen before each
paint, and the rest on a zero-delay timer. The perf test here times the part
that blocks the UI thread and prints the breakdown; the other tests check
that every line still ends up with the colors an eager pass gives it.
"""

import json
import time
from collections.abc import Callable

import pytest
import ruamel.yaml.main as ruamel_main
from benchmarks.size_bench import render_python_literal
from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage, QTextBlock, QTextCursor
from tests.perf import STRICT, TIMED_RUNS, WARMUP_RUNS, Timing, current_mode, time_runs

import galley.app.editor as editor_module
import galley.core.format as format_module
from galley.app.editor import CodeEditor
from galley.app.limits import PYTHON_TO_JSON_BYTES, PYTHON_TO_YAML_BYTES
from galley.app.main import create_main_window
from galley.app.main_window import _replace_as_one_undo, si_size
from galley.core.convert_python import ConvertResult, convert_python
from galley.core.folds import fold_ranges, line_count

# Isaac's budgets from the worst CI medians at 04dd435 (JSON 445.8 ms on
# Windows, YAML 74.5 ms on macOS) x 1.25, rounded up. Local medians are
# about 0.22 s for the JSON result and 0.03 s for YAML.
_OUTPUT_BUDGET_S = {"JSON": 0.6, "YAML": 0.10}
# Isaac's budget for the longest UI-thread pause while the rest of a result
# is colored in the background: the largest gap between heartbeat ticks.
_PAUSE_BUDGET_S = 0.2
_HEARTBEAT_MS = 5
_RESULTS = pytest.mark.parametrize(
    ("target", "engine"),
    [("JSON", "c"), ("YAML", "c"), ("YAML", "pure")],
    ids=["json", "yaml-c", "yaml-pure"],
)
# Enough lines for several chunks while the tests stay quick.
_CHUNK = 200


@pytest.fixture
def chunk(monkeypatch) -> int:
    monkeypatch.setattr(editor_module, "HIGHLIGHT_CHUNK_BLOCKS", _CHUNK)
    return _CHUNK


def _use_engine(monkeypatch, engine: str) -> None:
    """Pick the YAML engine the way ``test_yaml_engine_parity`` does."""
    if engine == "c":
        if not format_module._clib_available():
            pytest.skip("ruamel.yaml.clib is not installed")
        return
    monkeypatch.setattr(ruamel_main, "CParser", None)
    monkeypatch.setattr(ruamel_main, "CEmitter", None)
    monkeypatch.setattr(format_module, "_CLIB_READY", False)


def _result_text(target: str) -> str:
    """The result Convert from Python shows for a literal at its size limit."""
    limit = PYTHON_TO_JSON_BYTES if target == "JSON" else PYTHON_TO_YAML_BYTES
    result = convert_python(render_python_literal(limit), target)
    assert isinstance(result, ConvertResult)
    return result.text


def _show_window(qtbot):
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


@pytest.mark.perf
@_RESULTS
def test_showing_a_result_in_output_is_within_budget(
    perf_budget, qtbot, qapp, monkeypatch, target: str, engine: str
) -> None:
    """Time the UI-thread part of showing a result at the Convert size limit.

    The steps are the ones ``_on_format_succeeded`` takes: replace the text
    as one undo step, hand the editor the fold ranges the worker found (so
    the editor does not scan for them itself), then paint the first frame.
    The budget covers that, the freeze a user feels. The breakdown prints
    the document build without colors, the colored build (the synchronous
    part of highlighting is the difference), the first frame, and one timer
    chunk of the coloring that goes on between frames afterwards.
    """
    _use_engine(monkeypatch, engine)
    text = _result_text(target)
    window = _show_window(qtbot)
    output = window._output_pane.editor
    output.set_syntax_mode(target)
    limit = PYTHON_TO_JSON_BYTES if target == "JSON" else PYTHON_TO_YAML_BYTES
    fixture = f"{si_size(len(text.encode()))} {target} result from {si_size(limit)}"
    if engine == "pure":
        fixture += " (pure engine)"

    def clear() -> None:
        output.set_syntax_colors_wanted(True)
        output.setPlainText("")
        qapp.processEvents()

    folds = fold_ranges(text, target.lower())
    lines = line_count(text)

    def insert() -> None:
        _replace_as_one_undo(output, text)
        output.set_fold_ranges(folds, lines)

    frame = QImage(output.viewport().size(), QImage.Format.Format_ARGB32)

    def paint_frame() -> None:
        # The widget is not mapped under pytest-qt, so repaint() would do
        # nothing; render() paints the viewport the same way, now.
        output.viewport().render(frame)

    def show() -> None:
        insert()
        paint_frame()

    def inserted() -> None:
        clear()
        insert()

    def plain_setup() -> None:
        clear()
        output.set_syntax_colors_wanted(False)

    plain = time_runs(insert, setup=plain_setup)
    output.set_syntax_colors_wanted(True)
    colored = time_runs(insert, setup=clear)
    first_frame = time_runs(paint_frame, setup=inserted)
    chunk = time_runs(output._color_cold_chunk, setup=inserted)
    print(
        f"output display [{fixture}]: document build {plain.median * 1000:.1f} ms, "
        f"synchronous highlight {(colored.median - plain.median) * 1000:.1f} ms, "
        f"first frame {first_frame.median * 1000:.1f} ms, "
        f"one chunk of {editor_module.HIGHLIGHT_CHUNK_BLOCKS} lines "
        f"{chunk.median * 1000:.1f} ms; {text.count(chr(10))} lines",
        flush=True,
    )
    perf_budget.within(_OUTPUT_BUDGET_S[target], show, fixture=fixture, setup=clear)
    qtbot.waitUntil(lambda: not output.coloring_pending(), timeout=60_000)


@pytest.mark.perf
def test_background_coloring_never_blocks_the_ui_thread(
    perf_budget, qtbot, qapp
) -> None:
    """The chunks that color the rest of a result keep the UI thread free.

    A heartbeat ticks on the UI thread from the moment the 1.4 MB JSON
    result is shown until every block is colored. The largest gap between
    ticks is the longest the UI thread was blocked by a chunk and the paint
    around it. As in ``test_mask_session``, gate mode checks the median of
    the runs' largest gaps and strict mode the largest gap of all. Showing
    the result itself is the other test's budget.
    """
    text = _result_text("JSON")
    window = _show_window(qtbot)
    output = window._output_pane.editor
    output.set_syntax_mode("JSON")
    folds = fold_ranges(text, "json")
    lines = line_count(text)
    gaps = []
    for _ in range(WARMUP_RUNS + TIMED_RUNS):
        output.setPlainText("")
        qapp.processEvents()
        _replace_as_one_undo(output, text)
        output.set_fold_ranges(folds, lines)
        assert output.coloring_pending()
        gaps.append(_largest_gap(qtbot, output))
        assert _cold_numbers(output) == []
    largest = Timing(tuple(gaps[WARMUP_RUNS:]))
    if current_mode() == STRICT:
        largest = Timing((max(largest.samples),))
    perf_budget.check(
        largest,
        _PAUSE_BUDGET_S,
        fixture=(
            f"1.4 MB JSON result, chunks of {editor_module.HIGHLIGHT_CHUNK_BLOCKS} "
            "lines: largest UI-thread gap"
        ),
    )


def _largest_gap(qtbot, editor: CodeEditor) -> float:
    """Let the coloring finish and return the largest gap between heartbeats."""
    ticks = [time.monotonic()]
    heartbeat = QTimer()
    heartbeat.setInterval(_HEARTBEAT_MS)
    heartbeat.timeout.connect(lambda: ticks.append(time.monotonic()))
    heartbeat.start()
    try:
        qtbot.waitUntil(lambda: not editor.coloring_pending(), timeout=120_000)
        ticks.append(time.monotonic())
    finally:
        heartbeat.stop()
    return max(later - earlier for earlier, later in zip(ticks, ticks[1:]))


def _json_lines(count: int) -> str:
    items = [
        {"id": index, "name": f"item-{index}", "ok": index % 2 == 0}
        for index in range(count // 5)
    ]
    return json.dumps({"items": items}, indent=2) + "\n"


def _yaml_lines(count: int) -> str:
    """Mappings with block scalars, whose colors depend on the line before."""
    parts = []
    for index in range(count // 6):
        parts.append(
            f"item{index}:\n"
            f"  name: item-{index}\n"
            f"  note: |\n"
            f"    first line {index}\n"
            f"    second: not a key\n"
            f"  ok: true\n"
        )
    return "".join(parts)


def _xml_lines(count: int) -> str:
    """Elements with comments and CDATA that span lines."""
    parts = ['<?xml version="1.0"?>\n<catalog>\n']
    for index in range(count // 7):
        parts.append(
            f'  <item id="{index}">\n'
            f"    <!-- a comment\n"
            f"         that continues -->\n"
            f"    <name>item-{index}</name>\n"
            f"    <note><![CDATA[one < two\n"
            f"    still data]]></note>\n"
            f"  </item>\n"
        )
    parts.append("</catalog>\n")
    return "".join(parts)


_SHAPES: dict[str, Callable[[int], str]] = {
    "JSON": _json_lines,
    "YAML": _yaml_lines,
    "XML": _xml_lines,
}


def _editor(qtbot, mode: str) -> CodeEditor:
    editor = CodeEditor()
    qtbot.addWidget(editor)
    editor.resize(600, 400)
    editor.show()
    qtbot.waitExposed(editor)
    editor.set_syntax_mode(mode)
    return editor


def _block(editor: CodeEditor, number: int) -> QTextBlock:
    block = editor.document().findBlockByNumber(number)
    assert block.isValid(), number
    return block


def _cold(editor: CodeEditor, number: int) -> bool:
    return editor._highlighter.is_cold(_block(editor, number))


def _cold_numbers(editor: CodeEditor) -> list[int]:
    cold = []
    block = editor.document().firstBlock()
    while block.isValid():
        if editor._highlighter.is_cold(block):
            cold.append(block.blockNumber())
        block = block.next()
    return cold


def _formats(editor: CodeEditor) -> list[list[tuple[int, int, str, int]]]:
    """Every block's colors: start, length, color, and weight of each range."""
    out = []
    block = editor.document().firstBlock()
    while block.isValid():
        out.append(
            [
                (
                    span.start,
                    span.length,
                    span.format.foreground().color().name(),
                    span.format.fontWeight(),
                )
                for span in block.layout().formats()
            ]
        )
        block = block.next()
    return out


def _visible_numbers(editor: CodeEditor) -> list[int]:
    return [
        lineno - 1
        for lineno, _top in editor._visible_line_tops(editor.viewport().rect())
    ]


def test_a_large_result_is_colored_a_chunk_at_a_time(qtbot, chunk: int) -> None:
    editor = _editor(qtbot, "JSON")
    _replace_as_one_undo(editor, _json_lines(10 * chunk))
    count = editor.blockCount()
    assert count > 5 * chunk

    # The replace colored one chunk from the top and no more.
    assert editor.coloring_pending()
    assert not _cold(editor, 0)
    assert not _cold(editor, chunk - 1)
    assert _cold(editor, chunk + 5)
    assert _cold(editor, count // 2)

    qtbot.waitUntil(lambda: not editor.coloring_pending())
    assert _cold_numbers(editor) == []
    assert all(_block(editor, number).layout().formats() for number in (0, chunk + 5))


def _paint(editor: CodeEditor, qapp) -> None:
    """Paint the viewport now. Under pytest-qt the widget is not mapped, so
    ``repaint`` would do nothing; ``update`` plus an event loop pass does."""
    editor.viewport().update()
    qapp.processEvents()


def test_the_lines_on_screen_are_colored_before_they_are_painted(
    qtbot, qapp, chunk: int
) -> None:
    editor = _editor(qtbot, "JSON")
    _replace_as_one_undo(editor, _json_lines(10 * chunk))
    middle = editor.blockCount() // 2
    assert _cold(editor, middle)
    editor._cold_timer.stop()  # hold the walk, so only paints color lines

    cursor = QTextCursor(_block(editor, middle))
    editor.setTextCursor(cursor)
    editor.centerCursor()
    _paint(editor, qapp)

    shown = _visible_numbers(editor)
    assert middle in shown
    assert all(not _cold(editor, number) for number in shown)
    assert _cold(editor, shown[-1] + 3)
    assert _cold(editor, shown[0] - 3)

    editor._cold_timer.start()
    qtbot.waitUntil(lambda: not editor.coloring_pending())
    assert _cold_numbers(editor) == []


def test_an_edit_among_cold_lines_colors_one_chunk_around_it(qtbot, chunk: int) -> None:
    editor = _editor(qtbot, "JSON")
    _replace_as_one_undo(editor, _json_lines(12 * chunk))
    editor._cold_timer.stop()
    target = 6 * chunk
    assert _cold(editor, target)

    cursor = QTextCursor(_block(editor, target))
    cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
    cursor.insertText(" ")

    # The edited block and the chunk after it, then Qt's pass stops.
    assert not _cold(editor, target)
    assert not _cold(editor, target + chunk - 2)
    assert _cold(editor, target + chunk + 2)
    assert _cold(editor, target - 2)

    editor._cold_timer.start()
    qtbot.waitUntil(lambda: not editor.coloring_pending())
    assert _cold_numbers(editor) == []


def test_removing_lines_during_the_walk_does_not_leave_cold_lines(
    qtbot, chunk: int
) -> None:
    editor = _editor(qtbot, "JSON")
    _replace_as_one_undo(editor, _json_lines(12 * chunk))
    editor._cold_timer.stop()
    # Color two chunks, then cut lines around the walk's position.
    editor._color_cold_chunk()
    editor._cold_timer.stop()
    position = editor._cold_number
    assert position is not None and position >= chunk

    cursor = QTextCursor(_block(editor, position - 5))
    cursor.movePosition(
        QTextCursor.MoveOperation.NextBlock, QTextCursor.MoveMode.KeepAnchor, 50
    )
    cursor.removeSelectedText()

    qtbot.waitUntil(lambda: not editor.coloring_pending())
    assert _cold_numbers(editor) == []


@pytest.mark.parametrize("mode", ["JSON", "YAML", "XML"])
def test_chunked_colors_match_one_eager_pass(
    qtbot, qapp, monkeypatch, mode: str
) -> None:
    """Out-of-order coloring settles to the same colors as a single pass.

    YAML block scalars and XML comments depend on the line before them. The
    lines on screen are colored before the lines above them, so a line is
    colored with a cold line before it, and Qt's pass fixes it when that
    line is colored. The test scrolls into the cold middle to force that.
    """
    text = _SHAPES[mode](15 * _CHUNK)

    eager = _editor(qtbot, mode)
    monkeypatch.setattr(editor_module, "HIGHLIGHT_CHUNK_BLOCKS", 10**9)
    _replace_as_one_undo(eager, text)
    assert not eager.coloring_pending()
    assert _cold_numbers(eager) == []
    expected = _formats(eager)
    assert any(expected)

    monkeypatch.setattr(editor_module, "HIGHLIGHT_CHUNK_BLOCKS", _CHUNK)
    lazy = _editor(qtbot, mode)
    _replace_as_one_undo(lazy, text)
    assert lazy.coloring_pending()
    lazy._cold_timer.stop()  # so only the paints color lines, out of order
    for fraction in (0.5, 0.75, 0.3):
        middle = int(lazy.blockCount() * fraction)
        lazy.setTextCursor(QTextCursor(_block(lazy, middle)))
        lazy.centerCursor()
        _paint(lazy, qapp)
        assert not _cold(lazy, middle)
    assert _cold_numbers(lazy)
    lazy._cold_timer.start()
    qtbot.waitUntil(lambda: not lazy.coloring_pending())

    assert _cold_numbers(lazy) == []
    assert _formats(lazy) == expected


def test_coloring_passes_are_not_edits(qtbot, qapp, chunk: int) -> None:
    """A pass changes formats only: folds stay, trackers and the window hear
    nothing, and the undo stack is untouched."""
    editor = _editor(qtbot, "JSON")
    editor.set_folding_enabled(True)
    text = _json_lines(10 * chunk)
    _replace_as_one_undo(editor, text)
    ranges = fold_ranges(text, "JSON")
    editor.set_fold_ranges(ranges, line_count(text))
    assert editor.coloring_pending()
    # Fold one small item near the top, away from where the cursor goes.
    item = next(item for item in ranges if item.depth == 2)
    folded = editor.fold_line(item.start_line + 1)
    assert folded == item
    folded_lines = editor.folded_lines()
    assert folded_lines
    text_changes: list[int] = []
    editor.textChanged.connect(lambda: text_changes.append(1))
    contents_changes: list[int] = []
    editor.document().contentsChange.connect(lambda *_args: contents_changes.append(1))
    undo_steps = editor.document().availableUndoSteps()

    middle = editor.blockCount() // 2
    editor.setTextCursor(QTextCursor(_block(editor, middle)))
    editor.centerCursor()
    _paint(editor, qapp)
    qtbot.waitUntil(lambda: not editor.coloring_pending())

    assert _cold_numbers(editor) == []
    assert editor.folded_lines() == folded_lines
    assert not editor._fold_rescan.isActive()
    assert text_changes == []
    assert contents_changes == []
    assert editor.document().availableUndoSteps() == undo_steps
    assert editor.toPlainText() == text


def test_small_documents_are_colored_at_once(qtbot, chunk: int) -> None:
    editor = _editor(qtbot, "JSON")
    _replace_as_one_undo(editor, _json_lines(chunk // 2))
    assert not editor.coloring_pending()
    assert _cold_numbers(editor) == []


def test_turning_colors_on_colors_in_chunks(qtbot, qapp, chunk: int) -> None:
    editor = _editor(qtbot, "JSON")
    editor.set_syntax_colors_wanted(False)
    _replace_as_one_undo(editor, _json_lines(10 * chunk))
    assert not editor.coloring_pending()
    count = editor.blockCount()
    assert len(_cold_numbers(editor)) == count

    editor.set_syntax_colors_wanted(True)
    assert editor.coloring_pending()
    # Qt's own pass on reattach runs on the next event loop pass, limited to
    # one chunk; the timer's first chunk may land in the same pass.
    qapp.processEvents()
    assert chunk <= count - len(_cold_numbers(editor)) <= 3 * chunk

    qtbot.waitUntil(lambda: not editor.coloring_pending())
    assert _cold_numbers(editor) == []

    # Colors off again drops the walk with the colors.
    _replace_as_one_undo(editor, _json_lines(10 * chunk))
    assert editor.coloring_pending()
    editor.set_syntax_colors_wanted(False)
    assert not editor.coloring_pending()
    assert not editor._cold_timer.isActive()


def test_a_long_line_still_switches_colors_off(qtbot, chunk: int) -> None:
    """#94's guard wins: a gated document starts no coloring walk."""
    editor = _editor(qtbot, "JSON")
    long_line = json.dumps({"pad": "x" * (editor_module.COLORS_LONG_LINE_CHARS + 10)})
    _replace_as_one_undo(editor, long_line + "\n" + _json_lines(10 * chunk))
    assert not editor.syntax_colors_enabled()
    assert not editor.coloring_pending()

    _replace_as_one_undo(editor, _json_lines(10 * chunk))
    assert editor.syntax_colors_enabled()
    assert editor.coloring_pending()
    qtbot.waitUntil(lambda: not editor.coloring_pending())
    assert _cold_numbers(editor) == []


def _settled(qtbot, editor: CodeEditor) -> None:
    qtbot.waitUntil(lambda: not editor.coloring_pending())
    assert not editor._cold_timer.isActive()


def _eager_formats(qtbot, monkeypatch, mode: str, text: str) -> list:
    """The colors one unlimited pass gives ``text``."""
    monkeypatch.setattr(editor_module, "HIGHLIGHT_CHUNK_BLOCKS", 10**9)
    eager = _editor(qtbot, mode)
    _replace_as_one_undo(eager, text)
    assert not eager.coloring_pending()
    assert _cold_numbers(eager) == []
    monkeypatch.setattr(editor_module, "HIGHLIGHT_CHUNK_BLOCKS", _CHUNK)
    return _formats(eager)


def _insert_at(editor: CodeEditor, operation: QTextCursor.MoveOperation, text: str):
    cursor = QTextCursor(editor.document())
    cursor.movePosition(operation)
    cursor.insertText(text)


def _paste_at_end(qtbot, editor: CodeEditor, chunk: int) -> None:
    _insert_at(editor, QTextCursor.MoveOperation.End, _json_lines(20 * chunk))


def _prepend(qtbot, editor: CodeEditor, chunk: int) -> None:
    _insert_at(editor, QTextCursor.MoveOperation.Start, _json_lines(20 * chunk))


def _delete_then_undo(qtbot, editor: CodeEditor, chunk: int) -> None:
    _replace_as_one_undo(editor, _json_lines(15 * chunk))
    _settled(qtbot, editor)
    cursor = QTextCursor(_block(editor, 2))
    cursor.movePosition(
        QTextCursor.MoveOperation.NextBlock, QTextCursor.MoveMode.KeepAnchor, 10 * chunk
    )
    cursor.removeSelectedText()
    assert editor.blockCount() < 6 * chunk
    _settled(qtbot, editor)
    editor.undo()


def _paste_undo_redo(qtbot, editor: CodeEditor, chunk: int) -> None:
    _paste_at_end(qtbot, editor, chunk)
    _settled(qtbot, editor)
    editor.undo()
    assert editor.blockCount() < chunk
    _settled(qtbot, editor)
    editor.redo()


_LARGE_EDITS: dict[str, Callable[[object, CodeEditor, int], None]] = {
    "paste at end": _paste_at_end,
    "prepend": _prepend,
    "delete then undo": _delete_then_undo,
    "paste, undo, redo": _paste_undo_redo,
}


@pytest.mark.parametrize("edit", list(_LARGE_EDITS))
def test_a_large_edit_in_a_colored_document_colors_every_line(
    qtbot, monkeypatch, chunk: int, edit: str
) -> None:
    """An edit that adds more lines than one chunk, into a document with
    nothing pending, colors the rest of them on the timer."""
    editor = _editor(qtbot, "JSON")
    _replace_as_one_undo(editor, _json_lines(chunk // 10))
    _settled(qtbot, editor)
    assert _cold_numbers(editor) == []

    _LARGE_EDITS[edit](qtbot, editor, chunk)
    # Any lines left cold after the edit are colored in order, and the
    # walk ends with the timer stopped.
    assert editor.blockCount() > 5 * chunk
    _settled(qtbot, editor)
    assert _cold_numbers(editor) == []
    assert _formats(editor) == _eager_formats(
        qtbot, monkeypatch, "JSON", editor.toPlainText()
    )


def test_a_large_paste_into_input_or_output_colors_every_line(
    qtbot, chunk: int
) -> None:
    window = _show_window(qtbot)
    for editor in (window._input_pane.editor, window._output_pane.editor):
        editor.set_syntax_mode("JSON")
        _replace_as_one_undo(editor, _json_lines(chunk // 10))
        _settled(qtbot, editor)
        _paste_at_end(qtbot, editor, chunk)
        _settled(qtbot, editor)
        assert _cold_numbers(editor) == [], editor.objectName()


def test_a_line_whose_coloring_raises_is_skipped_not_retried(qtbot, chunk: int) -> None:
    """A raising ``highlightBlock`` leaves its line cold; the walk moves past
    it instead of retrying that line on every timer pass."""
    editor = _editor(qtbot, "JSON")
    highlighter = editor._highlighter
    original = highlighter.highlightBlock
    bad = 3 * chunk + 7

    def raising(text: str) -> None:
        if highlighter.currentBlock().blockNumber() == bad:
            raise RuntimeError("bad line")
        original(text)

    highlighter.highlightBlock = raising
    with qtbot.capture_exceptions() as raised:
        _replace_as_one_undo(editor, _json_lines(10 * chunk))
        assert editor.coloring_pending()
        _settled(qtbot, editor)
    assert _cold_numbers(editor) == [bad]
    # Once in the walk; the paints and the pass before it may add a couple.
    assert 1 <= len(raised) <= 3
