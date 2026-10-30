"""The JSON fast path must match the character walk's errors.

Valid input is scanned, parsed, and measured for depth without the walk.
A surrogate, a depth past the limit, ``RecursionError``, or a located
syntax error falls back to that walk, so the message and the caret stay
the same.
"""

import ast
import json
from pathlib import Path

import pytest
from tests.text_compare import assert_same_text

from galley.core import format as format_module
from galley.core.errors import MAX_DEPTH, InputError
from galley.core.format import _format_json_walk_first, format_json

_TESTS_ROOT = Path(__file__).resolve().parents[1]


def _outcome(text: str | bytes) -> tuple[object, ...]:
    """Return the formatted text, or the located error, for ``text``."""
    try:
        formatted = format_json(text)
    except Exception as exc:
        return _error_parts(exc)
    return ("ok", formatted)


def _walk_outcome(text: str | bytes) -> tuple[object, ...]:
    try:
        formatted = _format_json_walk_first(text)
    except Exception as exc:
        return _error_parts(exc)
    return ("ok", formatted)


def _error_parts(exc: BaseException) -> tuple[object, ...]:
    return (
        "error",
        type(exc),
        str(exc),
        exc.args,
        getattr(exc, "lineno", None),
        getattr(exc, "colno", None),
        getattr(exc, "msg", None),
        getattr(exc, "pos", None),
        getattr(exc, "kind", None),
        getattr(exc, "details", None),
    )


def _assert_same(text: str | bytes) -> None:
    assert _outcome(text) == _walk_outcome(text)


def _const_text(node: ast.AST) -> str | bytes | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
        return node.value
    return None


def _calls_format_json(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "format_json":
            return True
        if isinstance(func, ast.Attribute) and func.attr == "format_json":
            return True
    return False


def _parametrize_texts(fn: ast.FunctionDef) -> list[str | bytes]:
    if not _calls_format_json(fn):
        return []
    texts: list[str | bytes] = []
    for deco in fn.decorator_list:
        if not isinstance(deco, ast.Call) or len(deco.args) < 2:
            continue
        func = deco.func
        is_mark = isinstance(func, ast.Attribute) and func.attr == "parametrize"
        if not is_mark:
            continue
        names = deco.args[0]
        if not isinstance(names, ast.Constant) or not isinstance(names.value, str):
            continue
        fields = [part.strip() for part in names.value.split(",")]
        if "text" not in fields:
            continue
        index = fields.index("text")
        rows = deco.args[1]
        if not isinstance(rows, (ast.List, ast.Tuple)):
            continue
        for row in rows.elts:
            if isinstance(row, (ast.List, ast.Tuple)):
                if index >= len(row.elts):
                    continue
                value = _const_text(row.elts[index])
            else:
                value = _const_text(row) if index == 0 else None
            if value is not None:
                texts.append(value)
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        named = isinstance(func, ast.Name) and func.id == "format_json"
        attributed = isinstance(func, ast.Attribute) and func.attr == "format_json"
        if not named and not attributed:
            continue
        value = _const_text(node.args[0])
        if value is not None:
            texts.append(value)
    return texts


def _existing_json_fixtures() -> list[str | bytes]:
    """Literals already passed to ``format_json`` under ``tests/``."""
    found: list[str | bytes] = []
    seen: set[str | bytes] = set()
    for path in sorted(_TESTS_ROOT.rglob("*.py")):
        if path.name == Path(__file__).name:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for text in _parametrize_texts(node):
                if text in seen:
                    continue
                seen.add(text)
                found.append(text)
    return found


def _nested_objects(depth: int) -> str:
    return '{"k":' * depth + "1" + "}" * depth


def _loads_recursion_depth() -> int:
    """Return a nesting depth at which ``json.loads`` raises RecursionError."""
    depth = 1024
    while depth <= 100_000:
        try:
            json.loads("[" * depth + "0" + "]" * depth)
        except RecursionError:
            return depth
        depth *= 2
    raise AssertionError("json.loads did not raise RecursionError")


_EDGE_FIXTURES: tuple[str, ...] = (
    '{"a": "\\uD800"}',
    '{"a": "\\ud800"}',
    '{"a": "\\uDC00"}',
    '{"a": "\\udc00"}',
    '{"a": "\\uDC00\\uD800"}',
    '{"a": "\\udc00\\ud800"}',
    '{"a": "\\uD800\\uD800"}',
    '{"a": "\\uDC00\\uDC00"}',
    '{"a": "\\uD800\\uDC00"}',
    '{"a": "\\ud800\\udc00"}',
    '{"a": "\\uD800\\uDC00\\uD800"}',
    '{"a": "\\uDBFF\\uDFFF"}',
    '{"a": "\\uD7FF"}',
    '{"a": "\\\\uD800"}',
    '{"a": "\\\\\\uD800"}',
    '{"a": "\\\\uD800\\uDC00"}',
    '{"a": "\\\\\\uD800\\uDC00"}',
    '{"a": "\ud800"}',
    '{"a": "\udc00"}',
    '{"a": "\ud800\udc00"}',
    '{"a": "\udc00\ud800"}',
    '{"a": "\\\ud800"}',
    '{"a": "\n\ud800"}',
    '{\n  "a": "\\uD800"\n}',
    '"\\uD800"',
    "[]",
    "{}",
    "null",
    "1",
    '"ok"',
    '{"a":"\\t\\n\\u0041"}',
    "[" * MAX_DEPTH + "0" + "]" * MAX_DEPTH,
    "[" * (MAX_DEPTH + 1) + "0" + "]" * (MAX_DEPTH + 1),
    "[\n" * (MAX_DEPTH + 1) + "0" + "]" * (MAX_DEPTH + 1),
    _nested_objects(MAX_DEPTH),
    _nested_objects(MAX_DEPTH + 1),
)


@pytest.mark.parametrize(
    ("text", "escape", "line", "column"),
    [
        ('{"a": "\\uD800"}', "\\uD800", 1, 8),
        ('{"a": "\\ud800"}', "\\ud800", 1, 8),
        ('{"a": "\ud800"}', "\\uD800", 1, 8),
        ('{"a": "\\uDC00"}', "\\uDC00", 1, 8),
        ('{"a": "\\udc00"}', "\\udc00", 1, 8),
        ('{"a": "\udc00"}', "\\uDC00", 1, 8),
        ('{"a": "\\uDC00\\uD800"}', "\\uDC00", 1, 8),
        ('{"a": "\udc00\ud800"}', "\\uDC00", 1, 8),
        ('{"a": "\\uD800\\uDC00\\uD800"}', "\\uD800", 1, 20),
        ('{\n  "a": "\\ud800"\n}', "\\ud800", 2, 9),
        ('{"a": "\n\ud800"}', "\\uD800", 2, 1),
    ],
)
def test_lone_surrogates_keep_the_walk_message(
    text: str, escape: str, line: int, column: int
) -> None:
    with pytest.raises(InputError) as caught:
        format_json(text)
    error = caught.value
    assert str(error) == "bad_escape"
    assert error.kind == "bad_escape"
    assert error.lineno == line
    assert error.colno == column
    assert error.details == {"escape": escape}
    _assert_same(text)


def test_valid_surrogate_pair_passes() -> None:
    text = '{"a": "\\uD800\\uDC00"}'
    formatted = format_json(text)
    assert formatted == _format_json_walk_first(text)
    assert json.loads(formatted) == {"a": "\U00010000"}
    lower = format_json('{"a": "\\ud800\\udc00"}')
    assert json.loads(lower) == {"a": "\U00010000"}


def test_nesting_exactly_at_the_limit_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "[" * MAX_DEPTH + "0" + "]" * MAX_DEPTH
    formatted = format_json(text)
    assert_same_text(formatted, _format_json_walk_first(text))
    value = json.loads(formatted)
    for _ in range(MAX_DEPTH):
        assert isinstance(value, list)
        value = value[0]
    assert value == 0

    calls = {"n": 0}
    real = format_module._json_limits

    def spy(source: str) -> tuple[int, int, int]:
        calls["n"] += 1
        return real(source)

    monkeypatch.setattr(format_module, "_json_limits", spy)
    assert_same_text(format_json(text), formatted)
    assert calls["n"] == 0


def test_nesting_one_past_the_limit_matches_the_walk() -> None:
    text = "[\n" * (MAX_DEPTH + 1) + "0" + "]" * (MAX_DEPTH + 1)
    with pytest.raises(InputError) as caught:
        format_json(text)
    error = caught.value
    assert str(error) == "too_deep"
    assert error.kind == "too_deep"
    assert error.lineno == MAX_DEPTH + 1
    assert error.colno == 1
    assert error.details == {"depth": MAX_DEPTH + 1}
    _assert_same(text)
    _assert_same(_nested_objects(MAX_DEPTH + 1))


def test_json_loads_recursion_error_matches_the_walk() -> None:
    depth = _loads_recursion_depth()
    text = "[" * depth + "0" + "]" * depth
    with pytest.raises(RecursionError):
        json.loads(text)
    with pytest.raises(InputError) as caught:
        format_json(text)
    error = caught.value
    assert str(error) == "too_deep"
    assert error.kind == "too_deep"
    assert error.lineno == 1
    assert error.colno == MAX_DEPTH + 1
    assert error.details == {"depth": MAX_DEPTH + 1}
    _assert_same(text)


def test_fast_path_matches_the_walk_on_existing_json_fixtures() -> None:
    fixtures = _existing_json_fixtures()
    assert len(fixtures) >= 14
    mismatches: list[str] = []
    for text in [*fixtures, *_EDGE_FIXTURES]:
        if _outcome(text) == _walk_outcome(text):
            continue
        shown = text if isinstance(text, str) else text.decode("utf-8", "replace")
        preview = shown.encode("unicode_escape").decode()
        mismatches.append(preview[:160])
    assert mismatches == []


def test_valid_megabyte_json_does_not_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid ~1 MB document must not run the character walk.

    The walk used to dominate format time. This checks the call, not the clock.
    """
    calls = {"n": 0}
    real = format_module._json_limits

    def spy(source: str) -> tuple[int, int, int]:
        calls["n"] += 1
        return real(source)

    monkeypatch.setattr(format_module, "_json_limits", spy)
    unit = '{"emoji":"\\uD800\\uDC00","note":"a\\nb"}'
    repeats = (1_000_000 // (len(unit) + 1)) + 1
    text = "[" + ",".join([unit] * repeats) + "]"
    assert len(text) >= 1_000_000
    formatted = format_json(text)
    assert calls["n"] == 0
    assert json.loads(formatted)[0]["emoji"] == "\U00010000"

    with pytest.raises(InputError):
        format_json('{"a": "\\uD800"}')
    assert calls["n"] == 1
