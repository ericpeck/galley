"""Hostile JSON, YAML, and Python-literal input.

Deep nesting past the recursion limit, alias expansion, very long scalars,
duplicate keys, custom YAML tags, and Python literals must not run code or
build arbitrary objects. Gaps that still crash or skip a location are xfail.
"""

import ast
import importlib
import json
import pkgutil
import sys
from pathlib import Path

import pytest
from ruamel.yaml import YAML

import galley.core as core
from galley.core.format import format_json, format_yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_ROOT = REPO_ROOT / "src" / "galley" / "core"
FORMAT_PATH = CORE_ROOT / "format.py"
BANNED_CALLS = frozenset({"eval", "exec", "literal_eval"})
CONVERTER_NAMES = (
    "convert_python",
    "convert_from_python",
    "python_to_json",
)


def line_column(exc: BaseException) -> tuple[int, int]:
    lineno = getattr(exc, "lineno", None)
    colno = getattr(exc, "colno", None)
    if isinstance(lineno, int) and isinstance(colno, int):
        return lineno, colno
    mark = getattr(exc, "problem_mark", None)
    if mark is not None and isinstance(mark.line, int) and isinstance(mark.column, int):
        return mark.line + 1, mark.column + 1
    message = f"{type(exc).__name__} does not name a line and column: {exc}"
    raise AssertionError(message)


def _nested_json(depth: int) -> str:
    return "[" * depth + "0" + "]" * depth


def _nested_yaml(depth: int) -> str:
    lines = [f"{'  ' * level}k:" for level in range(depth)]
    lines.append(f"{'  ' * depth}1")
    return "\n".join(lines) + "\n"


def _alias_document(levels: int, fan: int) -> str:
    lines = ['a0: &a0 ["x"]']
    for level in range(1, levels + 1):
        refs = ", ".join(f"*a{level - 1}" for _ in range(fan))
        lines.append(f"a{level}: &a{level} [{refs}]")
    lines.append(f"root: *a{levels}")
    return "\n".join(lines) + "\n"


def _called_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _yaml_typs(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _called_name(node)
        if name != "YAML":
            continue
        for keyword in node.keywords:
            if (
                keyword.arg == "typ"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ):
                found.append(keyword.value.value)
        if (
            node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            found.append(node.args[0].value)
    return found


def _converter() -> object | None:
    for name in CONVERTER_NAMES:
        candidate = getattr(core, name, None)
        if callable(candidate):
            return candidate
    for module_info in pkgutil.iter_modules(list(core.__path__)):
        module = importlib.import_module(f"galley.core.{module_info.name}")
        for name in CONVERTER_NAMES:
            candidate = getattr(module, name, None)
            if callable(candidate):
                return candidate
    return None


def _require_converter() -> object:
    converter = _converter()
    if converter is None:
        pytest.fail("galley.core does not export a Python-literal converter")
    return converter


def test_json_deep_nesting_under_the_limit_round_trips() -> None:
    depth = 64
    formatted = format_json(_nested_json(depth))
    value = json.loads(formatted)
    for _ in range(depth):
        assert isinstance(value, list)
        value = value[0]
    assert value == 0
    assert format_json(formatted) == formatted


def test_yaml_deep_nesting_under_the_limit_round_trips() -> None:
    depth = 64
    formatted = format_yaml(_nested_yaml(depth))
    value = YAML(typ="safe").load(formatted)
    for _ in range(depth):
        assert isinstance(value, dict)
        value = value["k"]
    assert value == 1
    assert format_yaml(formatted) == formatted


@pytest.mark.xfail(
    reason=(
        "format_json raises RecursionError with no line or column once "
        "nesting exceeds the recursion limit"
    ),
    strict=True,
)
def test_json_excessive_nesting_names_line_and_column() -> None:
    text = _nested_json(sys.getrecursionlimit() + 50)
    try:
        format_json(text)
    except RecursionError:
        pytest.fail("format_json raised RecursionError with no line or column")
    except Exception as exc:
        line, column = line_column(exc)
        assert line >= 1
        assert column >= 1
        return
    pytest.fail("format_json returned a document instead of a located error")


@pytest.mark.xfail(
    reason=(
        "format_yaml raises RecursionError with no line or column once "
        "nesting exceeds the recursion limit"
    ),
    strict=True,
)
def test_yaml_excessive_nesting_names_line_and_column() -> None:
    text = _nested_yaml(600)
    try:
        format_yaml(text)
    except RecursionError:
        pytest.fail("format_yaml raised RecursionError with no line or column")
    except Exception as exc:
        line, column = line_column(exc)
        assert line >= 1
        assert column >= 1
        return
    pytest.fail("format_yaml returned a document instead of a located error")


def test_yaml_alias_bomb_stays_shared() -> None:
    # 10**6 logical leaves. Safe mode must keep aliases shared.
    text = _alias_document(levels=6, fan=10)
    formatted = format_yaml(text)
    assert len(formatted) < 5_000
    assert formatted.count("\nx\n") + formatted.count("x\n") < 5
    assert "*" in formatted
    assert format_yaml(formatted) == formatted


def test_yaml_recursive_alias_finishes() -> None:
    formatted = format_yaml("a: &a [*a]\n")
    assert len(formatted) < 200
    assert "*" in formatted
    assert format_yaml(formatted) == formatted


def test_long_json_string_and_key_round_trip() -> None:
    key = "k" * 100_000
    value = "v" * 100_000
    formatted = format_json(json.dumps({key: value}))
    assert json.loads(formatted) == {key: value}
    assert format_json(formatted) == formatted


def test_long_yaml_value_round_trips() -> None:
    value = "v" * 100_000
    formatted = format_yaml(f"k: {value}\n")
    assert YAML(typ="safe").load(formatted) == {"k": value}
    assert format_yaml(formatted) == formatted


def test_yaml_simple_key_at_spec_limit_round_trips() -> None:
    key = "k" * 1024
    formatted = format_yaml(f"{key}: 1\n")
    assert YAML(typ="safe").load(formatted) == {key: 1}
    assert format_yaml(formatted) == formatted


def test_yaml_simple_key_past_spec_limit_names_line_and_column() -> None:
    text = f"{'k' * 1025}: 1\n"
    with pytest.raises(Exception) as caught:
        format_yaml(text)
    assert line_column(caught.value) == (1, 1026)
    assert "line 1" in str(caught.value)
    assert "column 1026" in str(caught.value)


def test_yaml_explicit_long_key_round_trips() -> None:
    key = "k" * 5_000
    text = "? " + json.dumps(key) + "\n: 1\n"
    formatted = format_yaml(text)
    assert YAML(typ="safe").load(formatted) == {key: 1}
    assert format_yaml(formatted) == formatted


def test_json_duplicate_keys_keep_the_last_value() -> None:
    formatted = format_json('{"a": 1, "a": {"b": 2}, "a": {"b": 3}}')
    assert json.loads(formatted) == {"a": {"b": 3}}
    assert formatted.count('"a"') == 1


@pytest.mark.parametrize(
    ("text", "line", "column"),
    [
        ("a: 1\na: 2\n", 2, 1),
        ("a:\n  b: 1\n  b: 2\n", 3, 3),
        ("a: &a {v: 1}\nb:\n  <<: *a\n  <<: *a\n", 4, 3),
    ],
)
def test_yaml_duplicate_keys_name_line_and_column(
    text: str, line: int, column: int
) -> None:
    with pytest.raises(Exception) as caught:
        format_yaml(text)
    assert line_column(caught.value) == (line, column)
    assert "duplicate" in str(caught.value).lower()


@pytest.mark.parametrize(
    "text",
    [
        "!!python/object/apply:os.getcwd []\n",
        "!!python/name:os.system\n",
        '!!python/object/apply:operator.mul ["PWNED", 3]\n',
        "!!python/object/new:tuple\n- pwned\n",
        "!MyType value\n",
        "value: !custom [1, 2]\n",
    ],
)
def test_custom_yaml_tags_do_not_construct_python_objects(text: str) -> None:
    with pytest.raises(Exception) as caught:
        format_yaml(text)
    line, column = line_column(caught.value)
    assert line >= 1
    assert column >= 1
    message = str(caught.value)
    assert "tag" in message.lower()
    assert "PWNEDPWNEDPWNED" not in message


def test_standard_yaml_tags_still_format() -> None:
    loader = YAML(typ="safe")
    assert loader.load(format_yaml("!!str hello\n")) == "hello"
    assert loader.load(format_yaml("!!int 5\n")) == 5


def test_format_yaml_loader_is_safe_or_round_trip() -> None:
    tree = ast.parse(FORMAT_PATH.read_text(encoding="utf-8"))
    typs = _yaml_typs(tree)
    assert typs
    assert set(typs) <= {"safe", "rt"}


def test_core_does_not_call_eval_exec_or_literal_eval() -> None:
    offenders: list[str] = []
    for path in sorted(CORE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _called_name(node) in BANNED_CALLS:
                relative = path.relative_to(REPO_ROOT)
                offenders.append(f"{relative}:{node.lineno}")
    assert offenders == []


def test_python_literals_are_not_executed_by_formatters() -> None:
    with pytest.raises(json.JSONDecodeError) as caught:
        format_json("{'a': True, 'b': None,}")
    assert line_column(caught.value) == (1, 2)

    with pytest.raises(json.JSONDecodeError) as caught:
        format_json("__import__('os').name")
    assert line_column(caught.value) == (1, 1)

    loader = YAML(typ="safe")
    formatted = format_yaml("{'a': True, 'b': None}\n")
    assert loader.load(formatted) == {"a": True, "b": "None"}
    assert loader.load(format_yaml("get_rate()\n")) == "get_rate()"
    assert loader.load(format_yaml("datetime(2026, 1, 1)\n")) == "datetime(2026, 1, 1)"


@pytest.mark.xfail(
    reason=(
        "Convert from Python is not implemented in galley.core (ADR 0001). "
        "It must rewrite quotes, True, None, tuples, and trailing commas "
        "from ast.parse spans, and it must not run the text"
    ),
    strict=True,
)
def test_convert_python_literals_uses_ast_spans() -> None:
    convert = _require_converter()
    assert callable(convert)
    source = "{'a': True, 'b': None, 'c': (1, 2,), 'd': datetime(2026, 1, 1),}"
    converted = convert(source)
    assert converted == (
        '{"a": true, "b": null, "c": [1, 2], "d": "datetime(2026, 1, 1)"}'
    )
    hostile = "{'name': __import__('os').name}"
    result = convert(hostile)
    assert isinstance(result, str)
    assert "__import__('os').name" in result
    assert "posix" not in result
    assert "nt" not in result


@pytest.mark.xfail(
    reason=(
        "Convert from Python is not implemented in galley.core (ADR 0001). "
        "A syntax error must report a 1-based line and column from the "
        "ast spans and must not run the text"
    ),
    strict=True,
)
def test_convert_python_syntax_error_names_line_and_column() -> None:
    convert = _require_converter()
    assert callable(convert)
    source = "{\n  'a': <Customer object at 0x10>,\n}\n"
    with pytest.raises(Exception) as caught:
        convert(source)
    assert line_column(caught.value) == (2, 8)
    assert "posix" not in str(caught.value)
    assert "nt" not in str(caught.value)


@pytest.mark.xfail(
    reason=(
        "format_json emits a lone surrogate for a JSON unicode escape, and "
        "that string cannot be encoded as UTF-8"
    ),
    strict=True,
)
def test_json_lone_surrogate_output_is_utf8() -> None:
    try:
        formatted = format_json('{"a": "\\uD800"}')
    except json.JSONDecodeError as exc:
        assert exc.lineno >= 1
        assert exc.colno >= 1
        return
    encoded = formatted.encode("utf-8")
    assert b"\\uD800" in encoded or b"\\ud800" in encoded
