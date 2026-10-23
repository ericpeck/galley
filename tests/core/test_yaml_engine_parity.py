"""YAML output is the same with ruamel.yaml.clib and with pure Python (#110).

Each test runs once per engine. ``c`` uses the C parser where Galley uses it
and skips when ``ruamel.yaml.clib`` is not installed. ``pure`` hides the C
parser and emitter in-process, the way a Windows install runs. A ``!!set``
is written in a fixed order, whatever ``PYTHONHASHSEED`` is.
"""

import json
import os
import subprocess
import sys

import pytest
import ruamel.yaml.main as ruamel_main

import galley.core.format as format_module
from galley.core.errors import InputError
from galley.core.escape import escape_document
from galley.core.format import format_document, format_json

_ENGINES = ("c", "pure")


@pytest.fixture(params=_ENGINES)
def engine(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    if request.param == "c":
        if not format_module._clib_available():
            pytest.skip("ruamel.yaml.clib is not installed")
        return "c"
    monkeypatch.setattr(ruamel_main, "CParser", None)
    monkeypatch.setattr(ruamel_main, "CEmitter", None)
    monkeypatch.setattr(format_module, "_CLIB_READY", False)
    return "pure"


def test_pure_engine_hides_the_c_parser(engine: str) -> None:
    yaml = format_module._safe_yaml(pure=False)
    assert (yaml.Parser is ruamel_main.CParser) is (engine == "c")
    assert format_module._clib_available() is (engine == "c")


# JSON to YAML is written by the pure-Python emitter Format YAML shows. The
# C emitter escaped characters past U+FFFF and quoted these strings another way.
_JSON_TO_YAML = (
    ('"😀"', "😀\n...\n"),
    ('{"😀": 1, "k": null}', "😀: 1\nk: null\n"),
    ('{"emoji": "😀 👍🏽", "pair": "\\ud83d\\ude00"}', "emoji: 😀 👍🏽\npair: 😀\n"),
    ('{"a": "\\uDBFF\\uDFFF"}', "a: \U0010ffff\n"),
    ('"café 日本語 😀"', "café 日本語 😀\n...\n"),
    ('"two\\nlines"', '"two\\nlines"\n'),
    ('{"note": "a\\nb"}', 'note: "a\\nb"\n'),
    ("\"{'a': 1}\"", "\"{'a': 1}\"\n"),
    ('{"k": "é 😀 \\" \\\\ \\n \\t \\u0001"}', 'k: "é 😀 \\" \\\\ \\n \\t \\x01"\n'),
)


@pytest.mark.parametrize(("text", "expected"), _JSON_TO_YAML)
def test_json_to_yaml_writes_one_text(engine: str, text: str, expected: str) -> None:
    assert format_document(text, "json", "yaml") == expected


@pytest.mark.parametrize(("text", "expected"), _JSON_TO_YAML)
def test_json_to_yaml_reads_back_as_the_same_value(
    engine: str, text: str, expected: str
) -> None:
    assert json.loads(format_document(expected, "yaml", "json")) == json.loads(text)


# YAML ``\u`` escapes are code points, so a pair that JSON would join is two
# surrogates here. JSON output can't hold either, so each is a located
# ``bad_escape`` at the escape, like the same text read as JSON.
_SURROGATES = (
    ('{"a": "\\ud800"}', 1, 8, "\\ud800"),
    ('{"a": "\\uDC00\\uD800"}', 1, 8, "\\uDC00"),
    ('{\n  "a": "\\uD800"\n}', 2, 9, "\\uD800"),
    ('{"a": "\\\\\\uD800"}', 1, 10, "\\uD800"),
    ('"\\uD800"', 1, 2, "\\uD800"),
    ('a: "\\uD800\\uDC00"\n', 1, 5, "\\uD800"),
    ('a: "\\U0000DFFF"\n', 1, 5, "\\U0000DFFF"),
    ('a: "ok"\nb: "x\\\n  \\uD800"\n', 3, 3, "\\uD800"),
    ('{"\\ud83d\\ude00": 1, "😀": 2}', 1, 3, "\\ud83d"),
    (b'\xef\xbb\xbfk: "\xc3\xa9\\uD800"\n', 1, 6, "\\uD800"),
)


def _located(error: InputError) -> tuple[object, ...]:
    return (error.kind, error.lineno, error.colno, dict(error.details))


@pytest.mark.parametrize(("text", "line", "column", "escape"), _SURROGATES)
def test_yaml_to_json_rejects_a_surrogate_escape(
    engine: str, text: str | bytes, line: int, column: int, escape: str
) -> None:
    with pytest.raises(InputError) as caught:
        format_document(text, "yaml", "json")
    assert _located(caught.value) == ("bad_escape", line, column, {"escape": escape})


@pytest.mark.parametrize(("text", "line", "column", "escape"), _SURROGATES)
def test_escape_as_string_rejects_the_same_escape(
    engine: str, text: str | bytes, line: int, column: int, escape: str
) -> None:
    with pytest.raises(InputError) as caught:
        escape_document(text, "yaml")
    assert _located(caught.value) == ("bad_escape", line, column, {"escape": escape})


@pytest.mark.parametrize("text", ['{"a": "\\ud800"}', '{\n  "a": "\\uD800"\n}'])
def test_yaml_and_json_input_reject_a_lone_surrogate_alike(
    engine: str, text: str
) -> None:
    with pytest.raises(InputError) as as_yaml:
        format_document(text, "yaml", "json")
    with pytest.raises(InputError) as as_json:
        format_json(text)
    assert _located(as_yaml.value) == _located(as_json.value)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('a: "\\uD800"\n', 'a: "\\uD800"\n'),
        ('{"a": "\\uD800\\uDC00"}', 'a: "\\uD800\\uDC00"\n'),
    ],
)
def test_yaml_to_yaml_keeps_a_surrogate_escape(
    engine: str, text: str, expected: str
) -> None:
    assert format_document(text, "yaml", "yaml") == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('a: "\\\\uD800"\n', '{\n  "a": "\\\\uD800"\n}\n'),
        ('a: "😀"\n', '{\n  "a": "😀"\n}\n'),
        ('a: "\\U0001F600"\n', '{\n  "a": "😀"\n}\n'),
        ('a: "\\uFFFD"\n', '{\n  "a": "\ufffd"\n}\n'),
    ],
)
def test_yaml_to_json_keeps_text_that_is_not_a_surrogate(
    engine: str, text: str, expected: str
) -> None:
    assert format_document(text, "yaml", "json") == expected


def test_recursive_alias_with_a_surrogate_is_located(engine: str) -> None:
    with pytest.raises(InputError) as caught:
        format_document('a: &a ["\\uD800", *a]\n', "yaml", "json")
    assert _located(caught.value) == ("bad_escape", 1, 9, {"escape": "\\uD800"})


def test_recursive_alias_without_a_surrogate_still_finishes(engine: str) -> None:
    """Escaped text that looks like a surrogate escape walks a cycle once."""
    with pytest.raises(ValueError, match="Circular reference"):
        format_document('a: &a ["\\\\uD800", *a]\n', "yaml", "json")


def test_root_binary_fails_the_same_way(engine: str) -> None:
    """The C parser can't read the emitter's root ``!!binary`` block."""
    with pytest.raises(TypeError, match="bytes is not JSON serializable"):
        format_document("!!binary aGVsbG8=\n", "yaml", "json")


_SET_TEXT = "tags: !!set {gamma, alpha, Beta, 订单, café, delta, 10, 9, null}\n"
_SET_OUTPUT = (
    "tags: !!set\n"
    "  Beta: null\n"
    "  alpha: null\n"
    "  café: null\n"
    "  delta: null\n"
    "  gamma: null\n"
    "  订单: null\n"
    "  null: null\n"
    "  10: null\n"
    "  9: null\n"
)


def test_set_members_are_written_in_code_point_order(engine: str) -> None:
    assert format_document(_SET_TEXT, "yaml", "yaml") == _SET_OUTPUT


def test_set_order_is_the_same_under_any_hash_seed() -> None:
    script = (
        "import sys\n"
        "from galley.core.format import format_document\n"
        "sys.stdout.write(format_document(sys.stdin.read(), 'yaml', 'yaml'))\n"
    )
    for seed in ("0", "1", "2", "3", "4"):
        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            check=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONIOENCODING": "utf-8"},
            input=_SET_TEXT,
        )
        assert completed.stdout == _SET_OUTPUT, seed
