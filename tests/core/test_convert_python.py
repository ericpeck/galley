"""Convert Python literals to JSON or YAML without running them."""

import ast
import builtins
import json
import math

import pytest
from ruamel.yaml import YAML

import galley.core.convert_python as convert_python_module
from galley.core.convert_python import ConvertError, ConvertResult, convert_python
from galley.core.errors import MAX_DEPTH
from galley.core.format import format_json

_YAML = YAML(typ="safe")


def _load_yaml(text: str) -> object:
    return _YAML.load(text)


def _convert(source: str, target: str = "json") -> ConvertResult:
    result = convert_python(source, target)
    assert isinstance(result, ConvertResult)
    return result


def _error(source: str, target: str = "json") -> ConvertError:
    result = convert_python(source, target)
    assert isinstance(result, ConvertError)
    return result


def test_scalars_round_trip_through_json_and_yaml() -> None:
    source = "{'name': 'café', 'n': 1, 'ok': True, 'no': False, 'empty': None}"
    json_result = _convert(source, "json")
    yaml_result = _convert(source, "YAML")
    expected = {"name": "café", "n": 1, "ok": True, "no": False, "empty": None}
    assert json_result.notices == ()
    assert yaml_result.notices == ()
    assert json.loads(json_result.text) == expected
    assert _load_yaml(yaml_result.text) == expected
    assert json_result.text == format_json(json.dumps(expected, ensure_ascii=False))
    assert json_result.text.endswith("\n")
    assert yaml_result.text.endswith("\n")


def test_lists_and_nested_dicts_round_trip() -> None:
    source = "{'b': [1, {'c': 'x'}], 'a': []}"
    expected = {"b": [1, {"c": "x"}], "a": []}
    assert json.loads(_convert(source).text) == expected
    assert _load_yaml(_convert(source, "yaml").text) == expected


def test_unary_numbers_round_trip() -> None:
    source = "[-1, +2, -3.5, -(4)]"
    expected = [-1, 2, -3.5, -4]
    assert json.loads(_convert(source).text) == expected
    assert _load_yaml(_convert(source, "yaml").text) == expected


def test_tuples_become_lists_with_a_notice() -> None:
    source = "{'t': (1, 2), 'u': (3, 4)}"
    json_result = _convert(source)
    yaml_result = _convert(source, "yaml")
    expected = {"t": [1, 2], "u": [3, 4]}
    assert json.loads(json_result.text) == expected
    assert _load_yaml(yaml_result.text) == expected
    assert json_result.notices[0].message == "2 tuples were converted to lists."
    assert [notice.message for notice in yaml_result.notices] == [
        notice.message for notice in json_result.notices
    ]


def test_one_tuple_notice_uses_the_singular() -> None:
    result = _convert("(1,)")
    assert json.loads(result.text) == [1]
    assert result.notices[0].message == "1 tuple was converted to a list."


def test_non_string_keys_become_strings_with_a_notice() -> None:
    source = "{1: 'a', 1.5: 'b', True: 'c', False: 'd', None: 'e', -2: 'f'}"
    expected = {
        "1": "a",
        "1.5": "b",
        "true": "c",
        "false": "d",
        "null": "e",
        "-2": "f",
    }
    json_result = _convert(source)
    yaml_result = _convert(source, "yaml")
    assert json.loads(json_result.text) == expected
    assert _load_yaml(yaml_result.text) == expected
    notice = "6 dictionary keys were converted to strings."
    assert json_result.notices[0].message == notice
    assert yaml_result.notices[0].message == notice


def test_true_and_one_stay_distinct_string_keys() -> None:
    loaded = json.loads(_convert("{True: 'a', 1: 'b'}").text)
    assert loaded == {"true": "a", "1": "b"}


def test_stringified_duplicate_keys_keep_the_last_value() -> None:
    assert json.loads(_convert("{1: 'a', '1': 'b'}").text) == {"1": "b"}


def test_stringified_key_collision_notices_the_kept_value() -> None:
    result = _convert("{1: 'a', '1': 'b'}")
    assert json.loads(result.text) == {"1": "b"}
    assert [notice.message for notice in result.notices] == [
        "1 dictionary key was converted to a string.",
        "1 key matched another after conversion. The last value was kept.",
    ]
    reversed_keys = _convert("{'1': 'a', 1: 'b'}")
    assert json.loads(reversed_keys.text) == {"1": "b"}
    assert reversed_keys.notices[-1].message == (
        "1 key matched another after conversion. The last value was kept."
    )


def test_stringified_key_collisions_use_the_plural() -> None:
    result = _convert("{1: 'a', '1': 'b', 2: 'c', '2': 'd'}")
    assert json.loads(result.text) == {"1": "b", "2": "d"}
    assert [notice.message for notice in result.notices] == [
        "2 dictionary keys were converted to strings.",
        "2 keys matched another after conversion. The last value was kept.",
    ]
    same_key_twice = _convert("{1: 'a', 1: 'b', 1: 'c'}")
    assert json.loads(same_key_twice.text) == {"1": "c"}
    assert same_key_twice.notices[-1].message == (
        "2 keys matched another after conversion. The last value was kept."
    )


def test_duplicate_string_keys_are_not_conversion_collisions() -> None:
    result = _convert("{'a': 1, 'a': 2}")
    assert json.loads(result.text) == {"a": 2}
    assert result.notices == ()


def test_tuple_and_key_notices_both_show() -> None:
    result = _convert("{'nums': (1, 2), 3: 'x'}")
    assert json.loads(result.text) == {"nums": [1, 2], "3": "x"}
    assert [notice.message for notice in result.notices] == [
        "1 tuple was converted to a list.",
        "1 dictionary key was converted to a string.",
    ]


def test_infinity_is_rejected_for_json_and_kept_for_yaml() -> None:
    source = "[\n  1e309,\n  -1e309\n]\n"
    error = _error(source, "json")
    assert error.lineno == 2
    assert error.colno == 3
    assert error.message == (
        "NaN and Infinity can't be converted to JSON: line 2 column 3"
    )

    loaded = _load_yaml(_convert(source, "yaml").text)
    assert isinstance(loaded, list)
    assert math.isinf(loaded[0]) and loaded[0] > 0
    assert math.isinf(loaded[1]) and loaded[1] < 0


def test_nan_constant_is_rejected_for_json_and_kept_for_yaml(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = ast.Constant(value=float("nan"))
    node.lineno = 3
    node.col_offset = 4

    def parse(_text: str, mode: str = "exec", **_kwargs: object) -> ast.Expression:
        return ast.Expression(body=node)

    monkeypatch.setattr(convert_python_module.ast, "parse", parse)
    error = _error("0", "json")
    assert error.lineno == 3
    assert error.colno == 5
    assert "NaN and Infinity" in error.message
    assert "line 3" in error.message
    assert "column 5" in error.message
    loaded = _load_yaml(_convert("0", "yaml").text)
    assert isinstance(loaded, float)
    assert math.isnan(loaded)


@pytest.mark.parametrize(
    ("source", "line", "column", "problem"),
    [
        (
            "{\n  'a': __import__('os'),\n}\n",
            2,
            8,
            "Function calls can't be converted",
        ),
        ("{\n  'a': missing,\n}\n", 2, 8, "Names can't be converted"),
        (
            "{\n  'a': ().real,\n}\n",
            2,
            8,
            "Attribute access can't be converted",
        ),
        (
            "{\n  'a': {'b': 1}['b'],\n}\n",
            2,
            8,
            "Subscripts can't be converted",
        ),
        (
            "{\n  'a': [1 for x in [1]],\n}\n",
            2,
            8,
            "Comprehensions can't be converted",
        ),
        (
            "{\n  'a': {1 for x in [1]},\n}\n",
            2,
            8,
            "Comprehensions can't be converted",
        ),
        (
            "{\n  'a': {1: 2 for x in [1]},\n}\n",
            2,
            8,
            "Comprehensions can't be converted",
        ),
        (
            "{\n  'a': (1 for x in [1]),\n}\n",
            2,
            8,
            "Comprehensions can't be converted",
        ),
        (
            "{\n  'a': lambda: 1,\n}\n",
            2,
            8,
            "Lambda expressions can't be converted",
        ),
        ("{\n  'a': f'hi',\n}\n", 2, 8, "F-strings can't be converted"),
        (
            "{\n  'a': 9 ** 9,\n}\n",
            2,
            8,
            "Binary operators can't be converted",
        ),
        (
            "{\n  'a': True and False,\n}\n",
            2,
            8,
            "Boolean operators can't be converted",
        ),
        (
            "{\n  'a': 1 < 2,\n}\n",
            2,
            8,
            "Comparison operators can't be converted",
        ),
        ("{\n  'a': not True,\n}\n", 2, 8, "This operator can't be converted"),
        ("{\n  'a': ~1,\n}\n", 2, 8, "This operator can't be converted"),
        (
            "{\n  'a': 1 if True else 0,\n}\n",
            2,
            8,
            "Conditional expressions can't be converted",
        ),
        ("{\n  'a': {1, 2},\n}\n", 2, 8, "Sets can't be converted"),
        ("{\n  'a': b'hi',\n}\n", 2, 8, "Bytes can't be converted"),
        ("{\n  'a': 1j,\n}\n", 2, 8, "Complex numbers can't be converted"),
    ],
    ids=[
        "call",
        "name",
        "attribute",
        "subscript",
        "list-comp",
        "set-comp",
        "dict-comp",
        "generator",
        "lambda",
        "f-string",
        "binop",
        "boolop",
        "compare",
        "not",
        "invert",
        "ifexp",
        "set",
        "bytes",
        "complex",
    ],
)
def test_rejected_node_names_line_and_column(
    source: str, line: int, column: int, problem: str
) -> None:
    error = _error(source)
    assert error.lineno == line
    assert error.colno == column
    assert error.message == f"{problem}: line {line} column {column}"


def test_syntax_error_names_line_and_column() -> None:
    error = _error("{\n  'a': <Customer object at 0x10>,\n}\n")
    assert error.lineno == 2
    assert error.colno == 8
    assert error.message == "invalid syntax: line 2 column 8"


def test_hostile_inputs_are_rejected_without_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = builtins.__import__
    real_open = builtins.open
    real_compile = builtins.compile

    def guarded_import(name: str, *args: object, **kwargs: object) -> object:
        if name in {"os", "posix", "nt"}:
            raise AssertionError(f"imported {name}")
        return real_import(name, *args, **kwargs)

    def guarded_open(file: object, *args: object, **kwargs: object) -> object:
        if file == "x":
            raise AssertionError("open was called")
        return real_open(file, *args, **kwargs)

    def guarded_compile(*args: object, **kwargs: object) -> object:
        flags = args[3] if len(args) >= 4 else kwargs.get("flags", 0)
        if isinstance(flags, int) and flags & ast.PyCF_ONLY_AST:
            return real_compile(*args, **kwargs)
        raise AssertionError("compile was used to run code")

    def fail(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("builtin was called")

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(builtins, "eval", fail)
    monkeypatch.setattr(builtins, "exec", fail)
    monkeypatch.setattr(builtins, "compile", guarded_compile)
    monkeypatch.setattr(ast, "literal_eval", fail)
    for source in (
        "__import__('os')",
        "open('x')",
        "().__class__",
        "9**9**9",
        "lambda: None",
    ):
        error = _error(source)
        assert error.lineno == 1
        assert error.colno == 1
        assert "posix" not in error.message
        assert "nt" not in error.message


def _nested_list(depth: int) -> ast.List:
    """Build ``depth`` nested lists without asking the parser to read them."""
    node: ast.AST = ast.Constant(value=0)
    node.lineno = 1
    node.col_offset = depth
    for index in range(depth):
        wrapper = ast.List(elts=[node], ctx=ast.Load())
        wrapper.lineno = 1
        wrapper.col_offset = depth - 1 - index
        node = wrapper
    assert isinstance(node, ast.List)
    return node


def _parse_tree(tree: ast.AST):
    def parse(_text: str, mode: str = "exec", **_kwargs: object) -> ast.AST:
        return tree

    return parse


def test_deep_nesting_round_trips_up_to_the_parser_limit() -> None:
    """The parser accepts 200 nested brackets. That input still converts."""
    depth = 200
    source = "[" * depth + "0" + "]" * depth
    value = json.loads(_convert(source).text)
    for _ in range(depth):
        assert isinstance(value, list)
        assert len(value) == 1
        value = value[0]
    assert value == 0
    assert _convert(source, "yaml").text.endswith("\n")


def test_input_past_the_parser_nesting_limit_is_an_error() -> None:
    source = "[" * 201 + "0" + "]" * 201
    error = _error(source)
    assert error.lineno == 1
    assert error.colno == 201
    assert error.message == "too many nested parentheses: line 1 column 201"


def test_nesting_at_max_depth_round_trips(monkeypatch: pytest.MonkeyPatch) -> None:
    tree = ast.Expression(body=_nested_list(MAX_DEPTH))
    monkeypatch.setattr(convert_python_module.ast, "parse", _parse_tree(tree))
    value = json.loads(_convert("[]").text)
    for _ in range(MAX_DEPTH):
        assert isinstance(value, list)
        assert len(value) == 1
        value = value[0]
    assert value == 0
    assert _convert("[]", "yaml").text.endswith("\n")


def test_nesting_past_max_depth_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    tree = ast.Expression(body=_nested_list(MAX_DEPTH + 1))
    monkeypatch.setattr(convert_python_module.ast, "parse", _parse_tree(tree))
    error = _error("[]")
    assert error.lineno == 1
    assert error.colno == MAX_DEPTH + 1
    assert error.message == (
        f"This document is nested more than {MAX_DEPTH} levels deep: "
        f"line 1 column {MAX_DEPTH + 1}"
    )


def test_wide_input_round_trips() -> None:
    """A long literal is accepted. format_json has no character cap."""
    value = "v" * 100_000
    source = repr({"k": value})
    assert json.loads(_convert(source).text) == {"k": value}
    assert _load_yaml(_convert(source, "yaml").text) == {"k": value}


def test_recursion_error_becomes_an_error_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(*_args: object, **_kwargs: object) -> object:
        raise RecursionError("too deep")

    monkeypatch.setattr(convert_python_module.ast, "parse", explode)
    error = _error("[1]")
    assert isinstance(error, ConvertError)
    assert error.lineno == 1
    assert error.colno == 1
    assert "nested more than" in error.message

    monkeypatch.undo()

    def explode_render(*_args: object, **_kwargs: object) -> str:
        raise RecursionError("dump")

    monkeypatch.setattr(convert_python_module, "_render", explode_render)
    rendered = _error("[1]")
    assert rendered.lineno == 1
    assert "line 1" in rendered.message
    assert "column 1" in rendered.message


def test_unsupported_target_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported format"):
        convert_python("1", "xml")
