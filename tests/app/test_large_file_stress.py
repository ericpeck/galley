"""Time large JSON documents and check the format size gates.

Large and deeply nested XML and HTML (#63) are timed by ``perf_budget``
behind the ``perf`` marker. XML is core-only until slice 3 and HTML until
slice 5, so neither has editor rows.

The 1 MB and 2 MB fixtures are built in memory from ``random.Random`` with a
fixed seed: nested objects and arrays, long strings, and non-ASCII text.
Nothing large is written into the tree. Timings are printed as one table.
They are not a pass/fail gate. One sample from each CI runner is not a
median, and a shared runner is too noisy to fail the job on raw seconds.

Parse and format go through ``format_json``. Collapse is the editor's
Collapse all (#64), with folding turned on as it is for Output; the fold
scan runs before the clock starts, as the Format worker runs it. Search uses
the editor's find on a marker planted at the end of the fixture. Syntax
coloring is the editor highlighter turning on for that document. Change
markers use the gutter API from the Compare prep work. The diff engine is
timed only when ``galley.core.diff.diff`` is importable; this checkout does
not invent one.

The hard checks are the JSON busy spinner, the progress bar, and the size
warning. Those follow ``JSON_BUSY_BYTES``, ``JSON_PROGRESS_BYTES``, and
``JSON_WARNING_BYTES``.

A second table times the same content on one line: 1.6 MB of compact JSON
in a single block, the shape that stalled the editor (#91). It records
setText and a keystroke with Show Whitespace and colors wanted and with
both off. The long-line guard in ``editor.py`` keeps the two close; the
hard budget for that keystroke lives in ``test_long_line_guard.py``.
"""

import gc
import importlib
import importlib.util
import json
import random
import sys
import time

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QPainter, QPalette
from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
)
from tests.perf import time_runs
from tests.text_compare import assert_same_text

from galley.app.editor import CHANGE_MARKERS, CodeEditor, LineMarker
from galley.app.limits import MARKUP_FORMAT_LIMIT_BYTES
from galley.app.main import create_main_window
from galley.app.main_window import (
    FORMATTING_MESSAGE,
    JSON_BUSY_BYTES,
    JSON_PROGRESS_BYTES,
    JSON_WARNING_BYTES,
    SIZE_WARNING_DETAIL,
    si_size,
)
from galley.app.parse_session import ParseSession
from galley.core.errors import MAX_DEPTH
from galley.core.format import format_json
from galley.core.html_format import format_html
from galley.core.xml_format import format_xml, validate_xml

# SI megabytes, matching the size warning (1 MB is 1_000_000 bytes).
_BYTES = {"1 MB": 1_000_000, "2 MB": 2_000_000}
_ONE_LINE_LABEL = "1.6 MB line"
_ONE_LINE_BYTES = 1_600_000
_NEEDLE = "galley-stress-needle"
_UNICODE = "café — 日本語"
_EMOJI = "😀"
FIXTURE_SEED = 60
_NOTE_POOL = (
    "Pack my box with five dozen liquor jugs. "
    "The quick brown fox jumps over the lazy dog. "
    "How vexingly quick daft zebras jump. "
) * 4
_TAGS = ("alpha", "beta", "gamma", "delta")
_OPERATIONS = (
    "parse",
    "format",
    "collapse",
    "search",
    "syntax",
    "compare-markers",
)
# A hang guard for the highlighter, not a performance budget.
_SYNTAX_HANG_MS = 120_000

_DOCUMENTS: dict[str, str] = {}


class _ChangeMarker(LineMarker):
    """Gutter mark with no line tint, so a full-document set stays linear."""

    def paint(self, painter: QPainter, rect: QRect, palette: QPalette) -> None:
        del painter, rect, palette


_CHANGE_MARKER = _ChangeMarker()


def test_generated_fixture_is_deterministic_json() -> None:
    """Both sizes are exact, nested, and come from the fixed seed."""
    for label, size in _BYTES.items():
        first = _build(size)
        second = _build(size)
        other = _build(size, seed=FIXTURE_SEED + 1)
        assert_same_text(first, second)
        assert first != other
        assert len(first.encode("utf-8")) == size
        assert first.startswith("{\n")
        assert _UNICODE in first
        assert _EMOJI in first
        assert first.count(_NEEDLE) == 1
        parsed = json.loads(first)
        assert parsed["unicode"] == _UNICODE
        assert parsed["emoji"] == _EMOJI
        assert isinstance(parsed["items"], list) and parsed["items"]
        for item in parsed["items"]:
            assert isinstance(item["child"], dict)
            assert isinstance(item["tags"], list) and item["tags"]
            assert isinstance(item["note"], str) and len(item["note"]) >= 120
        marker_at = first.rfind(_NEEDLE)
        assert marker_at > len(first) * 9 // 10
        assert parsed["tail"]["marker"] == _NEEDLE
        rendered = json.dumps(parsed, indent=2, ensure_ascii=False) + "\n"
        assert_same_text(first, rendered)
        _DOCUMENTS[label] = first


def test_large_documents_are_timed(qtbot) -> None:
    """Log parse, format, collapse, search, syntax, and compare-marker times."""
    samples: dict[str, dict[str, float]] = {}
    compare_note = "large-file compare: no public API"
    try:
        for label in _BYTES:
            text = _document(label)
            formatted, parse_s, format_s = _time_format_json(text)
            _record(samples, "parse", label, parse_s)
            _record(samples, "format", label, format_s)
            assert _NEEDLE in formatted
            assert json.loads(formatted)["tail"]["marker"] == _NEEDLE

            editor = CodeEditor()
            qtbot.addWidget(editor)
            editor.resize(800, 600)
            editor.show()
            qtbot.waitExposed(editor)
            editor.set_syntax_colors_enabled(False)
            editor.set_syntax_mode("JSON")
            editor.setPlainText(text)
            qtbot.waitUntil(lambda: editor.blockCount() > 2)

            syntax_started = time.perf_counter()
            editor.set_syntax_colors_enabled(True)
            try:
                if not _first_block_is_colored(editor):
                    # Enabling colors recolors on the next event-loop pass.
                    qtbot.waitUntil(
                        lambda: _first_block_is_colored(editor),
                        timeout=_SYNTAX_HANG_MS,
                    )
            finally:
                _record(
                    samples,
                    "syntax",
                    label,
                    time.perf_counter() - syntax_started,
                )
            assert _first_block_is_colored(editor)

            # Search before collapse. Find can skip lines that are not visible.
            found_block = -1

            def search() -> None:
                nonlocal found_block
                cursor = editor.textCursor()
                cursor.setPosition(0)
                editor.setTextCursor(cursor)
                assert editor.find(_NEEDLE)
                found_block = editor.textCursor().blockNumber()

            _record(samples, "search", label, _elapsed(search))
            assert found_block >= editor.blockCount() * 9 // 10

            editor.set_folding_enabled(True)
            hidden = 0

            def collapse() -> None:
                nonlocal hidden
                hidden = _collapse_all(editor)

            _record(samples, "collapse", label, _elapsed(collapse))
            assert hidden > editor.blockCount() // 2
            assert _NEEDLE in editor.toPlainText()

            def mark() -> None:
                count = editor.blockCount()
                editor.set_line_markers(
                    CHANGE_MARKERS,
                    dict.fromkeys(range(1, count + 1), _CHANGE_MARKER),
                )

            _record(samples, "compare-markers", label, _elapsed(mark))
            assert len(editor.line_markers(CHANGE_MARKERS)) == editor.blockCount()

        engine = _public_diff()
        if engine is not None:
            diff, load_document = engine
            compare_note = ""
            for label in _BYTES:
                text = _document(label)
                edited = json.loads(text)
                edited["items"].insert(0, {"id": -1, "title": "inserted"})
                # Only the diff is timed; both documents are loaded first.
                left = load_document(text, "json")
                right = load_document(json.dumps(edited), "json")
                _record(samples, "compare", label, _elapsed(lambda: diff(left, right)))
    finally:
        _print_timings(samples, compare_note)


def test_one_line_document_is_timed(qtbot, qapp) -> None:
    """Log setText and a keystroke on one 1.6 MB line, options wanted or off."""
    text = _build_one_line(_ONE_LINE_BYTES)
    assert len(text.encode("utf-8")) == _ONE_LINE_BYTES
    assert "\n" not in text
    samples: dict[str, dict[str, float]] = {}
    try:
        for wanted in (True, False):
            state = "wanted" if wanted else "off"
            editor = CodeEditor()
            qtbot.addWidget(editor)
            editor.resize(800, 600)
            editor.show()
            qtbot.waitExposed(editor)
            editor.set_syntax_mode("JSON")
            editor.set_whitespace_wanted(wanted)
            editor.set_syntax_colors_wanted(wanted)

            def set_text() -> None:
                editor.setPlainText(text)
                qapp.processEvents()

            _record(samples, f"setText ({state})", _ONE_LINE_LABEL, _elapsed(set_text))
            assert editor.blockCount() == 1
            assert editor.display_limited() == wanted
            cursor = editor.textCursor()
            cursor.setPosition(len(text) // 2)
            editor.setTextCursor(cursor)

            def keystroke() -> None:
                editor.insertPlainText(" ")
                qapp.processEvents()

            best = min(_elapsed(keystroke) for _ in range(3))
            _record(samples, f"keystroke ({state})", _ONE_LINE_LABEL, best)
    finally:
        _print_one_line_timings(samples)


def test_indicators_follow_the_json_size_thresholds(qtbot, monkeypatch) -> None:
    """Busy, progress, and the size warning follow the JSON byte thresholds."""
    assert JSON_BUSY_BYTES == 5_000_000
    assert JSON_PROGRESS_BYTES == 20_000_000
    assert JSON_WARNING_BYTES == 100_000_000
    assert si_size(JSON_BUSY_BYTES) == "5 MB"
    assert si_size(JSON_PROGRESS_BYTES) == "20 MB"
    assert si_size(JSON_WARNING_BYTES) == "100 MB"
    assert _BYTES["2 MB"] < JSON_BUSY_BYTES < JSON_PROGRESS_BYTES < JSON_WARNING_BYTES

    clock = {"now": 1_000.0}
    monkeypatch.setattr("galley.app.main_window._monotonic", lambda: clock["now"])
    window = _show(qtbot)
    _hold_submit(monkeypatch, window)
    editor, status, spinner, progress, cancel = _chrome(window)
    _select_json(window)
    editor.set_syntax_colors_enabled(False)
    original = editor.toPlainText
    try:
        for label in _BYTES:
            text = _document(label)
            assert len(text.encode()) == _BYTES[label]
            editor.toPlainText = original  # type: ignore[method-assign]
            editor.setPlainText(text)
            qtbot.waitUntil(lambda: editor.blockCount() > 2)
            _trigger_format(window)
            _assert_quiet(window, spinner, progress, cancel)
            _stop(window, spinner, progress)

        _run_size(window, editor, original, JSON_BUSY_BYTES - 1)
        _assert_quiet(window, spinner, progress, cancel)
        _stop(window, spinner, progress)

        _run_size(window, editor, original, JSON_BUSY_BYTES)
        _assert_spinner(window, status, spinner, progress, cancel)
        _stop(window, spinner, progress)

        _run_size(window, editor, original, JSON_PROGRESS_BYTES - 1)
        _assert_spinner(window, status, spinner, progress, cancel)
        _stop(window, spinner, progress)

        _run_size(window, editor, original, JSON_PROGRESS_BYTES)
        _assert_progress(window, status, spinner, progress, cancel)
        _stop(window, spinner, progress)

        _run_size(window, editor, original, JSON_WARNING_BYTES - 1)
        _assert_progress(window, status, spinner, progress, cancel)
        _stop(window, spinner, progress)

        _run_size(window, editor, original, JSON_WARNING_BYTES)
        warning = _visible_warning(window)
        assert warning is not None
        assert warning.text() == f"Format this {si_size(JSON_WARNING_BYTES)} document?"
        assert warning.informativeText() == SIZE_WARNING_DETAIL
        assert warning.windowModality() == Qt.WindowModality.WindowModal
        anyway = warning.defaultButton()
        dismiss = warning.escapeButton()
        assert anyway is not None and anyway.text() == "Format anyway"
        assert dismiss is not None and dismiss.text() == "Cancel"
        assert not window._formatting
        dismiss.click()
        qtbot.waitUntil(lambda: _visible_warning(window) is None)
        assert not window._formatting
        assert not window._size_warning_accepted

        _trigger_format(window)
        again = _visible_warning(window)
        assert again is not None
        accept = again.defaultButton()
        assert accept is not None and accept.text() == "Format anyway"
        accept.click()
        qtbot.waitUntil(lambda: window._formatting)
        assert _visible_warning(window) is None
        _assert_progress(window, status, spinner, progress, cancel)
        _stop(window, spinner, progress)
    finally:
        editor.toPlainText = original  # type: ignore[method-assign]
        window.shutdown_parse_worker()


def _xml_items(target: int, seed: int = FIXTURE_SEED) -> str:
    """At least ``target`` UTF-8 bytes of XML from the seeded items.

    Notes and titles are text-only elements, ``child`` nests one level, and
    every item carries an attribute and a comment.
    """
    parts = ['<?xml version="1.0" encoding="UTF-8"?>\n<stress kind="galley">\n']
    size = _utf8_len(parts[0])
    count = 0
    while size < target:
        count = max(count * 2, 256)
        parts = parts[:1]
        size = _utf8_len(parts[0])
        for item in _items(count, seed):
            child = item["child"]
            tags = "".join(f"<tag>{tag}</tag>" for tag in item["tags"])
            rendered = (
                f'<item id="{item["id"]}" ok="{item["ok"]}">'
                f"<!-- n {item['n']} --><title>{item['title']} {_EMOJI}</title>"
                f"<tags>{tags}</tags>"
                f'<child depth="1"><label>{child["label"]}</label></child>'
                f"<note>{item['note']}</note></item>\n"
            )
            parts.append(rendered)
            size += _utf8_len(rendered)
            if size >= target:
                break
    parts.append("</stress>\n")
    return "".join(parts)


def _xml_nested(target: int) -> str:
    """``target`` bytes of item XML with a chain ``MAX_DEPTH`` deep in the middle.

    One chain is enough to reach the limit. A file made only of such chains
    formats to about 150 times its size, since every line is indented.
    """
    chain = "<n>" * (MAX_DEPTH - 1) + "x" + "</n>" * (MAX_DEPTH - 1) + "\n"
    items = _xml_items(target - _utf8_len(chain))
    middle = items.index("<item ", len(items) // 2)
    return items[:middle] + chain + items[middle:]


@pytest.mark.perf
@pytest.mark.parametrize("shape", ["large", "deeply nested"])
def test_large_and_deeply_nested_xml_format_within_budget(
    perf_budget, shape: str
) -> None:
    """Format and validate 1 MB of XML in 0.5 s (Isaac's #63 budget).

    The deeply nested fixture reaches ``MAX_DEPTH`` over and over, so the
    explicit stacks in ``xml_format`` are what is timed.
    """
    size = _BYTES["1 MB"]
    text = _xml_items(size) if shape == "large" else _xml_nested(size)
    assert size <= _utf8_len(text) < size * 11 // 10

    def format_and_validate() -> None:
        validate_xml(text)
        format_xml(text)

    perf_budget.within(0.5, format_and_validate, fixture=f"1 MB {shape} XML")


def _html_items(target: int, seed: int = FIXTURE_SEED) -> str:
    """At least ``target`` UTF-8 bytes of HTML from the seeded items.

    Each item is a block with a heading, an inline run, a list whose end
    tags are left out, a comment, and a ``pre``.
    """
    head = (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        "<title>Galley stress</title>\n</head>\n<body>\n"
    )
    tail = "</body>\n</html>\n"
    parts = [head]
    size = _utf8_len(head) + _utf8_len(tail)
    count = 0
    while size < target:
        count = max(count * 2, 256)
        parts = parts[:1]
        size = _utf8_len(head) + _utf8_len(tail)
        for item in _items(count, seed):
            tags = "".join(f"<li>{tag}" for tag in item["tags"])
            rendered = (
                f'<section id="s{item["id"]}" data-ok="{item["ok"]}">'
                f"<h2>{item['title']} {_EMOJI}</h2>"
                f"<p>{item['note']} <b>{item['child']['label']}</b></p>"
                f"<ul>{tags}</ul><!-- n {item['n']} -->"
                f"<pre>  {item['n']}\n    kept</pre></section>\n"
            )
            parts.append(rendered)
            size += _utf8_len(rendered)
            if size >= target:
                break
    parts.append(tail)
    return "".join(parts)


def _html_nested(target: int) -> str:
    """``target`` bytes of item HTML with a ``div`` chain to ``MAX_DEPTH``.

    ``html`` and ``body`` are the first two levels, so the chain is two
    shorter than the limit.
    """
    depth = MAX_DEPTH - 2
    chain = "<div>" * depth + "x" + "</div>" * depth + "\n"
    items = _html_items(target - _utf8_len(chain))
    middle = items.index("<section ", len(items) // 2)
    return items[:middle] + chain + items[middle:]


# Both HTML shapes, both platforms. Isaac's rule from #108, the worst CI
# median plus 25%, rounded up to the next 0.1 s: 429.5 ms on perf (Windows)
# at d031816 x 1.25 = 536.9 ms. It was 0.5 s, Isaac's #63 budget.
_HTML_STRESS_BUDGET_S = 0.6


@pytest.mark.perf
@pytest.mark.parametrize("shape", ["large", "deeply nested"])
def test_large_and_deeply_nested_html_format_within_budget(
    perf_budget, shape: str
) -> None:
    """Format 1 MB of HTML within ``_HTML_STRESS_BUDGET_S``.

    The deeply nested fixture reaches ``MAX_DEPTH``, so the explicit stacks
    in ``html_format`` are what is timed.
    """
    size = _BYTES["1 MB"]
    text = _html_items(size) if shape == "large" else _html_nested(size)
    assert size <= _utf8_len(text) < size * 11 // 10
    perf_budget.within(
        _HTML_STRESS_BUDGET_S, lambda: format_html(text), fixture=f"1 MB {shape} HTML"
    )


# The largest XML and HTML Format takes: the shared 2 MB limit (Eric, #63
# slice 5). Isaac's #108 rule: the worst CI median × 1.25, rounded up to the
# next 0.1 s. Medians at 81d295a: XML 371.1 ms (Windows); HTML 637.4 ms
# (macOS) and 865.5 ms (Windows).
_TWO_MB_XML_BUDGET_S = 0.5
_TWO_MB_HTML_BUDGET_S = 1.1 if sys.platform == "win32" else 0.8


@pytest.mark.perf
@pytest.mark.parametrize("fmt", ["XML", "HTML"])
def test_two_megabytes_of_markup_format_within_budget(perf_budget, fmt: str) -> None:
    """Format the largest XML and HTML documents the 2 MB limit lets through.

    Only Format is timed, as the app runs it. The XML fixture is checked
    once before, outside the clock (Isaac, #148). Each fixture ends within
    one item of the limit and never past it.
    """
    limit = MARKUP_FORMAT_LIMIT_BYTES
    build = _xml_items if fmt == "XML" else _html_items
    text = build(limit - 2048)
    assert limit - 4096 < _utf8_len(text) <= limit

    if fmt == "XML":
        validate_xml(text)
        perf_budget.within(
            _TWO_MB_XML_BUDGET_S, lambda: format_xml(text), fixture="2 MB XML"
        )
        return
    perf_budget.within(
        _TWO_MB_HTML_BUDGET_S, lambda: format_html(text), fixture="2 MB HTML"
    )


def _record(
    samples: dict[str, dict[str, float]], operation: str, label: str, elapsed: float
) -> None:
    samples.setdefault(operation, {})[label] = elapsed


def _print_timings(samples: dict[str, dict[str, float]], compare_note: str) -> None:
    labels = tuple(_BYTES)
    header = f"{'operation':<16}" + "".join(f"{label:>10}" for label in labels)
    lines = [
        "large-file timings (seconds; informational, not a pass/fail gate)",
        header,
    ]
    operations = [name for name in (*_OPERATIONS, "compare") if name in samples]
    for operation in operations:
        row = samples[operation]
        rendered = f"{operation:<16}"
        for label in labels:
            value = row.get(label)
            rendered += f"{value:10.4f}" if value is not None else f"{'':>10}"
        lines.append(rendered)
    if compare_note:
        lines.append(compare_note)
    print("\n".join(lines), flush=True)


def _print_one_line_timings(samples: dict[str, dict[str, float]]) -> None:
    lines = [
        "one-line timings (seconds; informational, not a pass/fail gate)",
        f"{'operation':<20}{_ONE_LINE_LABEL:>14}",
    ]
    for operation, row in samples.items():
        lines.append(f"{operation:<20}{row[_ONE_LINE_LABEL]:14.4f}")
    print("\n".join(lines), flush=True)


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


def _items(count: int, seed: int) -> list[dict[str, object]]:
    """Return ``count`` items from ``seed``.

    The generator is reseeded on every call, so item ``i`` does not depend on
    how many items a binary search asked for earlier.
    """
    rng = random.Random(seed)
    pool = len(_NOTE_POOL)
    items: list[dict[str, object]] = []
    for index in range(count):
        note_len = rng.randrange(120, 240)
        start = rng.randrange(0, pool - note_len)
        title = _UNICODE if rng.randrange(5) == 0 else f"item-{index}"
        tags = [rng.choice(_TAGS) for _ in range(rng.randrange(2, 5))]
        items.append(
            {
                "id": index,
                "title": title,
                "ok": rng.choice((True, False)),
                "n": rng.randrange(1_000_000),
                "tags": tags,
                "child": {
                    "depth": 1,
                    "label": rng.choice(("left", "right", "center")),
                    "values": [rng.randrange(10_000), None, rng.choice((True, False))],
                },
                "note": _NOTE_POOL[start : start + note_len],
            }
        )
    return items


def _render(items: list[dict[str, object]], pad: str) -> str:
    payload = {
        "kind": "galley-stress",
        "unicode": _UNICODE,
        "emoji": _EMOJI,
        "items": items,
        "tail": {"pad": pad, "marker": _NEEDLE},
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if not text.endswith("\n"):
        text += "\n"
    return text


def _build(target: int, seed: int = FIXTURE_SEED) -> str:
    """Return pretty-printed JSON of exactly ``target`` UTF-8 bytes.

    The item count is a binary search, so generation does not re-dump the
    document once per item. The pad is ASCII, so each extra character is one
    UTF-8 byte.
    """

    def bare_size(count: int) -> int:
        return _utf8_len(_render(_items(count, seed), ""))

    lo = 0
    hi = 1
    while bare_size(hi) <= target:
        lo = hi
        hi *= 2
        if hi > 200_000:
            raise RuntimeError(f"fixture item count exceeded 200_000 for {target}")
    best = lo
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if bare_size(mid) <= target:
            best = mid
            lo = mid
        else:
            hi = mid
    items = _items(best, seed)
    gap = target - _utf8_len(_render(items, ""))
    if gap < 0:
        raise RuntimeError(f"fixture wrapper exceeds {target} bytes")
    text = _render(items, "x" * gap)
    size = _utf8_len(text)
    if size != target:
        raise RuntimeError(f"fixture is {size} bytes, wanted {target}")
    return text


def _build_one_line(target: int, seed: int = FIXTURE_SEED) -> str:
    """Return compact JSON of exactly ``target`` UTF-8 bytes on one line.

    The same items as ``_build``, without indentation. The pad is ASCII.
    """

    def render_one_line(items: list[dict[str, object]], pad: str) -> str:
        payload = {
            "kind": "galley-stress",
            "unicode": _UNICODE,
            "emoji": _EMOJI,
            "items": items,
            "tail": {"pad": pad, "marker": _NEEDLE},
        }
        return json.dumps(payload, ensure_ascii=False)

    def bare_size(count: int) -> int:
        return _utf8_len(render_one_line(_items(count, seed), ""))

    lo = 0
    hi = 1
    while bare_size(hi) <= target:
        lo = hi
        hi *= 2
        if hi > 200_000:
            raise RuntimeError(f"fixture item count exceeded 200_000 for {target}")
    best = lo
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if bare_size(mid) <= target:
            best = mid
            lo = mid
        else:
            hi = mid
    items = _items(best, seed)
    gap = target - bare_size(best)
    if gap < 0:
        raise RuntimeError(f"fixture wrapper exceeds {target} bytes")
    text = render_one_line(items, "x" * gap)
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
    """Return ``(diff, load_document)``, or None when Compare has no engine yet.

    ``diff`` compares two documents that ``load_document`` parsed and located.
    """
    if importlib.util.find_spec("galley.core.diff") is None:
        return None
    module = importlib.import_module("galley.core.diff")
    diff = getattr(module, "diff", None)
    load_document = getattr(module, "load_document", None)
    if callable(diff) and callable(load_document):
        return diff, load_document
    return None


def _show(qtbot):
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def _hold_submit(monkeypatch, window) -> None:
    """Keep Format in progress without sending the document to the worker."""
    monkeypatch.setattr(ParseSession, "active_job_id", property(lambda _self: 1))
    window._session.submit = lambda *_args, **_kwargs: None  # type: ignore[method-assign]


def _select_json(window) -> None:
    combo = window.findChild(QComboBox, "inputFormat")
    assert isinstance(combo, QComboBox)
    combo.setCurrentText("JSON")


def _chrome(window):
    editor = window.findChild(CodeEditor, "inputEditor")
    status = window.findChild(QLabel, "statusMessage")
    spinner = window.findChild(QProgressBar, "formatSpinner")
    progress = window.findChild(QProgressBar, "writeProgress")
    cancel = window.findChild(QPushButton, "cancelButton")
    assert isinstance(editor, CodeEditor)
    assert isinstance(status, QLabel)
    assert isinstance(spinner, QProgressBar)
    assert isinstance(progress, QProgressBar)
    assert isinstance(cancel, QPushButton)
    return editor, status, spinner, progress, cancel


def _trigger_format(window) -> None:
    assert not window._formatting
    assert _visible_warning(window) is None
    window._size_warning_accepted = False
    window._actions.format.trigger()


def _run_size(window, editor: CodeEditor, original, size: int) -> None:
    """Point Format at ``size`` ASCII bytes without laying that text out."""
    assert not window._formatting
    assert _visible_warning(window) is None
    editor.toPlainText = original  # type: ignore[method-assign]
    gc.collect()
    payload = "x" * size
    editor.toPlainText = lambda payload=payload: payload  # type: ignore[method-assign]
    window._size_warning_accepted = False
    window._actions.format.trigger()


def _stop(window, spinner: QProgressBar, progress: QProgressBar) -> None:
    window._cancel_format()
    assert not window._formatting
    assert not spinner.isVisible()
    assert not progress.isVisible()


def _assert_quiet(
    window, spinner: QProgressBar, progress: QProgressBar, cancel: QPushButton
) -> None:
    assert _visible_warning(window) is None
    assert window._formatting
    assert not spinner.isVisible()
    assert not progress.isVisible()
    assert not cancel.isVisible()


def _assert_spinner(window, status: QLabel, spinner, progress, cancel) -> None:
    assert _visible_warning(window) is None
    assert window._formatting
    assert spinner.isVisible()
    assert spinner.accessibleName() == "Formatting"
    assert not progress.isVisible()
    assert not cancel.isVisible()
    assert status.text() == FORMATTING_MESSAGE


def _assert_progress(window, status: QLabel, spinner, progress, cancel) -> None:
    assert _visible_warning(window) is None
    assert window._formatting
    assert progress.isVisible()
    assert progress.accessibleName() == "Formatting progress"
    assert cancel.isVisible()
    assert cancel.text() == "Cancel"
    assert not spinner.isVisible()
    assert status.text() == FORMATTING_MESSAGE


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


@pytest.mark.perf
def test_format_at_four_spaces_and_tab_is_timed() -> None:
    """Time Format at 4 spaces and at Tab on the 2 MB JSON and XML fixtures.

    Timing only: the medians are printed with no budget (Isaac). The
    2-space medians print beside them for comparison; their budgets are
    the existing ones and do not change.
    """
    json_text = _build(_BYTES["2 MB"])
    xml_text = _xml_items(MARKUP_FORMAT_LIMIT_BYTES - 2048)
    validate_xml(xml_text)
    timings = {
        "JSON 2 spaces": time_runs(lambda: format_json(json_text)),
        "JSON 4 spaces": time_runs(lambda: format_json(json_text, indent="    ")),
        "JSON Tab": time_runs(lambda: format_json(json_text, indent="\t")),
        "XML 2 spaces": time_runs(lambda: format_xml(xml_text)),
        "XML 4 spaces": time_runs(lambda: format_xml(xml_text, indent="    ")),
    }
    lines = [
        f"Format indent timings, {_utf8_len(json_text):,} B JSON and "
        f"{_utf8_len(xml_text):,} B XML ({sys.platform}; timing only, no budget)"
    ]
    for label, timing in timings.items():
        samples = ", ".join(f"{sample * 1000:.1f}" for sample in timing.samples)
        lines.append(f"  {label}: median {timing.median * 1000:.1f} ms ({samples})")
    print("\n".join(lines), flush=True)
    assert format_json(json_text, indent="\t").count("\n") == format_json(
        json_text
    ).count("\n")
