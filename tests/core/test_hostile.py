"""Hostile JSON, YAML, XML, and Python-literal input.

Deep nesting past the recursion limit, alias expansion, very long scalars,
duplicate keys, custom YAML tags, and Python literals must not run code or
build arbitrary objects. XML entity declarations are rejected before any
expansion, and nothing is fetched. Gaps that still crash or skip a location are xfail.
"""

import ast
import builtins
import importlib
import json
import pkgutil
import socket
import subprocess
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path

import pytest
from ruamel.yaml import YAML

import galley.core as core
from galley.core import MAX_DEPTH
from galley.core.errors import InputError, JSONDuplicateKeyError, XMLSyntaxError
from galley.core.format import format_json, format_yaml
from galley.core.xml_format import format_xml, validate_xml

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


# A fixed depth past a few hundred levels overflows the 1 MB Windows stack
# and aborts pytest. Stay about 100 frames past the recursion limit, and run
# that parse in a child so a stack overflow fails only this case.
_NESTING_PROBE = r"""
import sys

from galley.core.format import format_json, format_yaml


def line_column(exc):
    lineno = getattr(exc, "lineno", None)
    colno = getattr(exc, "colno", None)
    if isinstance(lineno, int) and isinstance(colno, int):
        return lineno, colno
    mark = getattr(exc, "problem_mark", None)
    if (
        mark is not None
        and isinstance(getattr(mark, "line", None), int)
        and isinstance(getattr(mark, "column", None), int)
    ):
        return mark.line + 1, mark.column + 1
    return None


kind = sys.argv[1]
depth = sys.getrecursionlimit() + 100
if kind == "json":
    text = "[" * depth + "0" + "]" * depth
    format_text = format_json
else:
    lines = [f"{'  ' * level}k:" for level in range(depth)]
    lines.append(f"{'  ' * depth}1")
    text = "\n".join(lines) + "\n"
    format_text = format_yaml
try:
    format_text(text)
except RecursionError:
    raise SystemExit(2)
except Exception as exc:
    located = line_column(exc)
    if located is None:
        raise SystemExit(3)
    line, column = located
    if line < 1 or column < 1:
        raise SystemExit(3)
    raise SystemExit(0)
raise SystemExit(4)
"""


def _assert_excessive_nesting(kind: str) -> None:
    """Format one over-deep document in a child process."""
    formatter_name = "format_json" if kind == "json" else "format_yaml"
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _NESTING_PROBE, kind],
            check=False,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"{formatter_name} nesting probe timed out")
    code = completed.returncode
    if code == 0:
        return
    if code == 2:
        pytest.fail(f"{formatter_name} raised RecursionError with no line or column")
    if code == 4:
        pytest.fail(f"{formatter_name} returned a document instead of a located error")
    pytest.fail(
        f"{formatter_name} nesting probe exited {code}; "
        "a stack overflow stays in the child process"
    )


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


def test_json_excessive_nesting_names_line_and_column() -> None:
    _assert_excessive_nesting("json")


def test_yaml_excessive_nesting_names_line_and_column() -> None:
    _assert_excessive_nesting("yaml")


def test_json_at_max_depth_parses() -> None:
    formatted = format_json(_nested_json(MAX_DEPTH))
    value = json.loads(formatted)
    for _ in range(MAX_DEPTH):
        assert isinstance(value, list)
        value = value[0]
    assert value == 0
    assert format_json(formatted) == formatted


def test_yaml_at_max_depth_parses() -> None:
    formatted = format_yaml(_nested_yaml(MAX_DEPTH))
    lines = formatted.splitlines()
    assert len(lines) == MAX_DEPTH
    for level in range(MAX_DEPTH - 1):
        assert lines[level] == f"{'  ' * level}k:"
    assert lines[-1] == f"{'  ' * (MAX_DEPTH - 1)}k: 1"
    assert format_yaml(formatted) == formatted


def test_json_past_max_depth_is_too_deep() -> None:
    text = "[\n" * (MAX_DEPTH + 1) + "0" + "]" * (MAX_DEPTH + 1)
    with pytest.raises(InputError) as caught:
        format_json(text)
    error = caught.value
    assert error.kind == "too_deep"
    assert error.lineno == MAX_DEPTH + 1
    assert error.colno == 1
    assert error.details == {"depth": MAX_DEPTH + 1}


def test_yaml_past_max_depth_is_too_deep() -> None:
    with pytest.raises(InputError) as caught:
        format_yaml(_nested_yaml(MAX_DEPTH + 1))
    error = caught.value
    assert error.kind == "too_deep"
    assert error.lineno == MAX_DEPTH + 1
    assert error.colno == 2 * MAX_DEPTH + 1
    assert error.details == {"depth": MAX_DEPTH + 1}


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


def test_json_duplicate_keys_are_rejected_not_kept() -> None:
    with pytest.raises(JSONDuplicateKeyError) as caught:
        format_json('{"a": 1, "a": {"b": 2}, "a": {"b": 3}}')
    assert [(item.key, item.lineno) for item in caught.value.duplicates] == [
        ("a", 1),
        ("a", 1),
    ]


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


def test_json_lone_surrogate_output_is_utf8() -> None:
    with pytest.raises(InputError) as caught:
        format_json('{"a": "\\uD800"}')
    error = caught.value
    assert error.kind == "bad_escape"
    assert error.lineno == 1
    assert error.colno == 8
    assert error.details == {"escape": "\\uD800"}


def test_json_lone_surrogate_escape_keeps_the_source_spelling() -> None:
    with pytest.raises(InputError) as caught:
        format_json('{"a": "\\ud800"}')
    assert caught.value.kind == "bad_escape"
    assert caught.value.details == {"escape": "\\ud800"}


def test_json_surrogate_pair_formats_as_utf8() -> None:
    formatted = format_json('{"a": "\\uD800\\uDC00"}')
    assert json.loads(formatted) == {"a": "\U00010000"}
    formatted.encode("utf-8")


# --- XML --------------------------------------------------------------------


def _billion_laughs() -> str:
    lines = ['<?xml version="1.0"?>', "<!DOCTYPE lolz [", '  <!ENTITY lol "lol">']
    previous = "lol"
    for level in range(1, 10):
        name = f"lol{level}"
        lines.append(f'  <!ENTITY {name} "{f"&{previous};" * 10}">')
        previous = name
    lines.extend(["]>", f"<lolz>&{previous};</lolz>"])
    return "\n".join(lines) + "\n"


_QUADRATIC_BLOWUP = (
    f'<!DOCTYPE r [\n  <!ENTITY a "{"a" * 50_000}">\n]>\n<r>{"&a;" * 50_000}</r>\n'
)

# Each case and the line its first <!ENTITY is on.
_HOSTILE_XML = {
    "billion laughs": (_billion_laughs(), 3),
    "quadratic blowup": (_QUADRATIC_BLOWUP, 2),
    "XXE file": (
        '<!DOCTYPE r [\n  <!ENTITY xxe SYSTEM "file:///etc/passwd">\n]>\n'
        "<r>&xxe;</r>\n",
        2,
    ),
    "external parameter entity": (
        "<!DOCTYPE r [\n"
        '  <!ENTITY % ext SYSTEM "http://example.invalid/evil.dtd">\n'
        "  %ext;\n]>\n<r/>\n",
        2,
    ),
    "unparsed entity": (
        '<!DOCTYPE r [\n  <!NOTATION gif SYSTEM "image/gif">\n'
        '  <!ENTITY logo SYSTEM "logo.gif" NDATA gif>\n]>\n<r/>\n',
        3,
    ),
}


class _NoNetwork:
    """Patch sockets, urllib, and ``open`` to fail, and record each try."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[str] = []
        targets = (
            (socket, "socket"),
            (socket, "create_connection"),
            (socket, "getaddrinfo"),
            (urllib.request, "urlopen"),
            (urllib.request.OpenerDirector, "open"),
            (builtins, "open"),
        )
        for owner, name in targets:
            monkeypatch.setattr(owner, name, self._refuse(f"{owner.__name__}.{name}"))

    def _refuse(self, name: str) -> Callable[..., object]:
        def refuse(*_args: object, **_kwargs: object) -> object:
            self.calls.append(name)
            raise AssertionError(f"XML tried {name}")

        return refuse


@pytest.mark.parametrize("case", list(_HOSTILE_XML))
def test_xml_entity_declarations_are_rejected_and_nothing_is_fetched(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    text, line = _HOSTILE_XML[case]
    guard = _NoNetwork(monkeypatch)
    try:
        with pytest.raises(XMLSyntaxError) as caught:
            format_xml(text)
        with pytest.raises(XMLSyntaxError):
            validate_xml(text)
    finally:
        monkeypatch.undo()
    assert guard.calls == []
    assert (caught.value.code, caught.value.lineno, caught.value.colno) == (
        "xml_entity_declared",
        line,
        3,
    )


@pytest.mark.perf
def test_billion_laughs_is_rejected_in_under_a_tenth_of_a_second(perf_budget) -> None:
    text = _billion_laughs()
    found: list[tuple[str, int]] = []

    def reject() -> None:
        try:
            format_xml(text)
        except XMLSyntaxError as exc:
            found.append((exc.code, exc.lineno))

    perf_budget.within(0.1, reject, fixture="billion laughs, 9 levels of 10")
    # Every run stopped at the first declaration, before the root, so no
    # entity was ever expanded.
    assert found == [("xml_entity_declared", 3)] * 6


@pytest.mark.parametrize(
    "doctype",
    [
        '<!DOCTYPE r SYSTEM "http://example.invalid/evil.dtd">',
        '<!DOCTYPE r SYSTEM "file:///etc/passwd">',
        '<!DOCTYPE r PUBLIC "-//Fake//DTD Fake//EN" "https://example.invalid/x.dtd">',
    ],
)
def test_an_external_dtd_is_kept_and_never_fetched(
    monkeypatch: pytest.MonkeyPatch, doctype: str
) -> None:
    text = f"{doctype}\n<r><a>&undeclared;</a></r>\n"
    guard = _NoNetwork(monkeypatch)
    try:
        output = format_xml(text)
    finally:
        monkeypatch.undo()
    assert guard.calls == []
    assert output == f"{doctype}\n<r>\n  <a>&undeclared;</a>\n</r>\n"


def test_xml_reads_utf8_whatever_the_declaration_says() -> None:
    # Read as ISO-8859-1, these UTF-8 bytes would hold a C1 control
    # inside the element name, and expat would reject it.
    for encoding in ("ISO-8859-1", "UTF-16", "Shift_JIS", "not-an-encoding"):
        text = f'<?xml version="1.0" encoding="{encoding}"?>\n<名前>値</名前>\n'
        assert format_xml(text) == text


def test_the_network_guard_records_each_try(monkeypatch: pytest.MonkeyPatch) -> None:
    guard = _NoNetwork(monkeypatch)
    try:
        for attempt in (
            lambda: socket.create_connection(("example.invalid", 80)),
            lambda: urllib.request.urlopen("http://example.invalid/"),
            lambda: open("/etc/passwd"),  # noqa: SIM115
        ):
            with pytest.raises(AssertionError, match="XML tried"):
                attempt()
    finally:
        monkeypatch.undo()
    assert guard.calls == [
        "socket.create_connection",
        "urllib.request.urlopen",
        "builtins.open",
    ]
