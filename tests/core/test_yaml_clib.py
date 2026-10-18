"""format_yaml uses ruamel.yaml.clib when it is installed, and matches pure Python."""

import os
import random
import warnings

import pytest
from ruamel.yaml import YAML
from ruamel.yaml.main import CEmitter, CParser

import galley.core.format as format_module
from galley.core.format import format_yaml

# Malformed documents, documents the C parser accepts and pure Python rejects,
# documents pure Python accepts and the C parser rejects, and edge cases whose
# dump text can differ. The user-facing result has to equal the pure parser.
_CORPUS: tuple[str | bytes, ...] = (
    "a: 1\n",
    "a: 1\nb:\n  - 2\n  - 3\n",
    "b: 1\na: 2\n",
    '- id: 1\n  name: "widget"\n  tags:\n  - "sale"\n',
    "yes\n",
    "no\n",
    "012\n",
    "a: yes\n",
    "a: 012\n",
    "a: .inf\n",
    "a: 1:2:3\n",
    "a: 2020-01-01\n",
    "true\n",
    "null\n",
    "~\n",
    "a: null\nb: ~\nc:\n",
    "get_rate()\n",
    "datetime(2026, 1, 1)\n",
    "{'a': True, 'b': None}\n",
    "a: &a 1\nb: *a\n",
    "a: &a {x: 1}\nb:\n  <<: *a\n  y: 2\n",
    "a: &a [*a]\n",
    "# only\n",
    "",
    "---\n",
    "a: 1 # keep\nb: 2\n",
    "\ufeffa: 1\n",
    "a: 1\r\nb: 2\r\n",
    "café: 1\n",
    "订单: 1\n",
    "a: 😀\n",
    "a: [1, 2",
    'a: "\\q"\n',
    "a:\n\tb: 1\n",
    "a: 1\r\nb:\r\n\tc: 2\n",
    "a: 1\r\nb: [\n",
    "a: 1\na: 2\n",
    "a:\n  b: 1\n  b: 2\n",
    "value: !custom [1, 2]\n",
    "a: &a {v: 1}\nb:\n  <<: *a\n  <<: *a\n",
    "!!python/object/apply:os.getcwd []\n",
    "!!str hello\n",
    "!!int 5\n",
    "a:\t1\n",
    "a: b\tc\n",
    "key:\tvalue\n",
    "plain\tvalue\n",
    "a: 1\t\n",
    "---\t\n",
    "a: |\n \n  hello\n",
    ": foo\n",
    'a: "\\uD800"\n',
    'a: "\\uD800\\uDC00"\n',
    "%FOO bar\n---\na: 1\n",
    "a: &id1 1\nb: &id1 2\n",
    "a: {b:1}\n",
    'a: "\\n"\n',
    "a: |\n  hello\n  world\n",
    "a: >\n  hello\n  world\n",
    "%YAML 1.1\n--- yes\n",
    "%YAML 1.1\n--- 012\n",
    "a: !\n",
    "!!binary aGVsbG8=\n",
    "a: \u2028x\n",
    "a: \u2029x\n",
    "*missing\n",
    "a: *missing\n",
    "a: 1\n  b: 2\n",
    "foo: bar: baz\n",
    b'a: "\xff"\n',
    b'a: "\xc3\xa9\xff"\n',
    b"\xef\xbb\xbfa: 1\n",
    'note: "has\\ttab"\n',
    'a: "\'"\n',
    "a: ''''\n",
    "a: ''''''\n",
    "a: '''it'''\n",
    "a: \"''\"\n",
    "a: |\r\n \r\n  hello\r\n",
    b"a: |\r\n \r\n  hello\r\n",
    "%TAG !e! tag:example.com,2000:app/\n---\na: 1\n",
    "a: 1 # \there\n",
    'note: "a\tb"\n',
    "note: 'a\tb'\n",
    'note: "has\n\ttab"\n',
    "note: 'has\n\ttab'\n",
    "- !\n",
    "[! ]\n",
    "--- !\n",
    "!<!> \n",
    "a: !<!>\n",
    "! 123\n",
    "! true\n",
    "! null\n",
    "! [1, 2]\n",
    "! {a: 1}\n",
    "a: !\n  - 1\n",
    "!\n- 1\n",
    'note: "a\tb"\nitem: ! 3\n',
    b'\xc3\xa9\xc3\xa9:\t"ab"\n',
    b'\xef\xbb\xbf\xc3\xa9\xc3\xa9:\t"ab"\n',
    b"\xef\xbb\xbf",
)


def _location(exc: BaseException) -> tuple[int | None, int | None]:
    lineno = getattr(exc, "lineno", None)
    colno = getattr(exc, "colno", None)
    if isinstance(lineno, int) and isinstance(colno, int):
        return lineno, colno
    mark = getattr(exc, "problem_mark", None)
    if mark is not None and isinstance(mark.line, int) and isinstance(mark.column, int):
        return mark.line + 1, mark.column + 1
    return None, None


def _pure(text: str | bytes) -> str:
    return format_module._format_yaml_impl(text, pure=True)


def _outcome(text: str | bytes, *, pure: bool) -> tuple[object, ...]:
    function = _pure if pure else format_yaml
    try:
        return ("value", function(text))
    except Exception as exc:
        line, column = _location(exc)
        return ("error", type(exc).__name__, str(exc), line, column)


def _require_clib() -> None:
    if format_module._clib_available():
        return
    pytest.skip("ruamel.yaml.clib is not installed")


def test_clib_flag_matches_the_extension() -> None:
    installed = CParser is not None and CEmitter is not None
    assert format_module._clib_available() is installed


def test_plain_document_emits_no_warning() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert format_yaml("a: 1\n") == _pure("a: 1\n")


@pytest.mark.parametrize("text", _CORPUS)
def test_user_facing_result_matches_pure_python(text: str | bytes) -> None:
    assert _outcome(text, pure=False) == _outcome(text, pure=True)


def _watch_reparse(monkeypatch: pytest.MonkeyPatch) -> list[str | bytes]:
    calls: list[str | bytes] = []
    real = format_module._pure_python_yaml

    def track(text: str | bytes) -> str:
        calls.append(text)
        return real(text)

    monkeypatch.setattr(format_module, "_pure_python_yaml", track)
    return calls


def _reject_reparse(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(text: str | bytes) -> str:
        raise AssertionError(text)

    monkeypatch.setattr(format_module, "_pure_python_yaml", boom)


def test_plain_document_dumps_the_c_value(monkeypatch: pytest.MonkeyPatch) -> None:
    _require_clib()
    _reject_reparse(monkeypatch)
    source = "a: 1\nb:\n  - 2\n  - 3\n"
    assert format_yaml(source) == _pure(source)


@pytest.mark.parametrize("source", ["a: it's\n", 'a: ""\n', 'notes: "packed."\n'])
def test_common_quotes_dump_the_c_value(
    source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _require_clib()
    _reject_reparse(monkeypatch)
    assert format_yaml(source) == _pure(source)


def test_c_parser_error_retries_with_pure_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_clib()
    calls: list[bool] = []
    real = format_module._format_yaml_impl

    def track(text: str | bytes, *, pure: bool) -> str:
        calls.append(pure)
        return real(text, pure=pure)

    monkeypatch.setattr(format_module, "_format_yaml_impl", track)
    text = "a: [1, 2"
    with pytest.raises(Exception) as caught:
        format_yaml(text)
    with pytest.raises(Exception) as pure_caught:
        real(text, pure=True)
    assert type(caught.value) is type(pure_caught.value)
    assert str(caught.value) == str(pure_caught.value)
    assert _location(caught.value) == (1, 9)
    assert calls == [True]


def test_tab_accepted_by_c_uses_the_pure_python_rejection() -> None:
    _require_clib()
    text = "a:\t1\n"
    assert YAML(typ="safe", pure=False).load(text) == {"a": 1}
    with pytest.raises(Exception) as caught:
        format_yaml(text)
    with pytest.raises(Exception) as pure_caught:
        _pure(text)
    assert type(caught.value) is type(pure_caught.value)
    assert str(caught.value) == str(pure_caught.value)
    assert _location(caught.value) == _location(pure_caught.value)


def test_stubbed_c_success_still_returns_the_pure_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A C success cannot hide a document the pure parser rejects."""
    monkeypatch.setattr(format_module, "_clib_available", lambda: True)
    real = format_module._format_yaml_impl

    def fake(text: str | bytes, *, pure: bool) -> str:
        if pure:
            return real(text, pure=True)
        return "from-c\n"

    monkeypatch.setattr(format_module, "_format_yaml_impl", fake)
    text = "a:\t1\n"
    with pytest.raises(Exception) as caught:
        format_yaml(text)
    with pytest.raises(Exception) as pure_caught:
        real(text, pure=True)
    assert type(caught.value) is type(pure_caught.value)
    assert str(caught.value) == str(pure_caught.value)
    assert caught.value.args == pure_caught.value.args


def test_forced_c_failure_returns_the_pure_python_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(format_module.YAML_FORCE_C_PARSER_ERROR, "1")
    source = "a: 1\nb:\n  - 2\n  - 3\n"
    assert format_yaml(source) == _pure(source)
    text = "a:\t1\n"
    assert _outcome(text, pure=False) == _outcome(text, pure=True)


@pytest.mark.parametrize(
    "text",
    [
        "%YAML 1.1\n--- yes\n",
        "%TAG !e! tag:example.com,2000:app/\n---\na: 1\n",
    ],
)
def test_directive_reparses_with_pure_python(
    text: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _require_clib()
    calls = _watch_reparse(monkeypatch)
    assert _outcome(text, pure=False) == _outcome(text, pure=True)
    assert calls == [text]


@pytest.mark.parametrize("text", ["a:\t1\n", "a: b\tc\n", b"a:\t1\n"])
def test_tab_outside_a_comment_reparses_with_pure_python(
    text: str | bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    _require_clib()
    calls = _watch_reparse(monkeypatch)
    assert _outcome(text, pure=False) == _outcome(text, pure=True)
    assert calls == [text]


def test_tab_inside_a_comment_keeps_the_c_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_clib()
    _reject_reparse(monkeypatch)
    text = "a: 1 # \there\n"
    assert format_yaml(text) == _pure(text)


@pytest.mark.parametrize(
    "text",
    [
        'note: "a\tb"\n',
        "note: 'a\tb'\n",
        b'note: "a\tb"\n',
        b"note: 'a\tb'\n",
        b'\xc3\xa9\xc3\xa9: "a\tb"\n',
    ],
)
def test_tab_inside_single_line_quoted_scalar_keeps_the_c_value(
    text: str | bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    _require_clib()
    _reject_reparse(monkeypatch)
    assert format_yaml(text) == _pure(text)


@pytest.mark.parametrize(
    "text",
    [
        'note: "has\n\ttab"\n',
        "note: 'has\n\ttab'\n",
        b'note: "has\n\ttab"\n',
        b"note: 'has\n\ttab'\n",
    ],
)
def test_tab_inside_multiline_quoted_scalar_reparses(
    text: str | bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outputs match, so only the re-parse call shows the rule is still there."""
    _require_clib()
    calls = _watch_reparse(monkeypatch)
    opened = format_module._yaml_limits(text, pure=False)
    loaded = format_module._load_parsed(text, opened, pure=False)
    assert format_module._dump_parsed(loaded, pure=True) == _pure(text)
    assert format_yaml(text) == _pure(text)
    assert calls == [text]


@pytest.mark.parametrize(
    "text",
    [
        b'\xc3\xa9\xc3\xa9:\t"ab"\n',
        b'\xef\xbb\xbf\xc3\xa9\xc3\xa9:\t"ab"\n',
    ],
)
def test_tab_after_multibyte_key_reparses(
    text: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    _require_clib()
    calls = _watch_reparse(monkeypatch)
    with pytest.raises(Exception) as caught:
        format_yaml(text)
    with pytest.raises(Exception) as pure_caught:
        _pure(text)
    assert type(caught.value) is type(pure_caught.value)
    assert str(caught.value) == str(pure_caught.value)
    assert calls == [text]


@pytest.mark.parametrize("text", [b"\xef\xbb\xbf", "\ufeff"])
def test_bom_only_document_keeps_the_c_value(
    text: str | bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    _require_clib()
    _reject_reparse(monkeypatch)
    assert format_yaml(text) == _pure(text)


@pytest.mark.parametrize(
    "text",
    ["a: !\n", "- !\n", "[! ]\n", "--- !\n", "!<!> \n", "a: !<!>\n"],
)
def test_bare_tag_on_an_empty_node_reparses(
    text: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _require_clib()
    calls = _watch_reparse(monkeypatch)
    assert format_yaml(text) == _pure(text)
    assert calls == [text]
    loaded = YAML(typ="safe").load(text)
    assert format_module._dump_parsed(loaded, pure=True) != _pure(text)


# ruamel.yaml 0.19.1 and ruamel.yaml.clib 0.2.15. Not YAML-spec strings.
_BARE_TAG_SAME_LINE_SCALARS = (
    ("! 123\n", 123),
    ("! true\n", True),
    ("! null\n", None),
)


@pytest.mark.parametrize(("text", "value"), _BARE_TAG_SAME_LINE_SCALARS)
def test_bare_tag_same_line_scalar_keeps_the_c_value(
    text: str, value: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    _require_clib()
    _reject_reparse(monkeypatch)
    c_value = YAML(typ="safe", pure=False).load(text)
    pure_value = YAML(typ="safe", pure=True).load(text)
    assert c_value == value
    assert pure_value == value
    assert type(c_value) is type(pure_value)
    assert type(c_value) is type(value)
    assert format_yaml(text) == _pure(text)


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("! [1, 2]\n", [1, 2]),
        ("! {a: 1}\n", {"a": 1}),
    ],
)
def test_bare_tag_same_line_flow_keeps_the_c_value(
    text: str, value: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    _require_clib()
    _reject_reparse(monkeypatch)
    assert YAML(typ="safe", pure=False).load(text) == value
    assert YAML(typ="safe", pure=True).load(text) == value
    assert format_yaml(text) == _pure(text)


@pytest.mark.parametrize("text", ['! ""\n', "! ''\n", 'a: ! ""\n'])
def test_bare_tag_before_an_empty_scalar_reparses(
    text: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _require_clib()
    calls = _watch_reparse(monkeypatch)
    assert format_yaml(text) == _pure(text)
    assert calls == [text]


@pytest.mark.parametrize(
    "text",
    ["a: !\n  - 1\n", "!\n- 1\n", "- !\n  - 1\n"],
)
def test_bare_tag_before_a_block_collection_reparses(
    text: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _require_clib()
    calls = _watch_reparse(monkeypatch)
    assert format_yaml(text) == _pure(text)
    assert calls == [text]


@pytest.mark.parametrize(
    "text",
    ["!foo 1\n", "a: !foo bar\n", "value: !custom [1, 2]\n"],
)
def test_local_tag_reparses_with_pure_python(
    text: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _require_clib()
    calls = _watch_reparse(monkeypatch)
    assert _outcome(text, pure=False) == _outcome(text, pure=True)
    assert calls == [text]


def test_quoted_tab_and_bare_scalar_keep_the_c_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_clib()
    _reject_reparse(monkeypatch)
    text = 'note: "a\tb"\nitem: ! 3\n'
    assert YAML(typ="safe", pure=False).load(text) == {"note": "a\tb", "item": 3}
    assert YAML(typ="safe", pure=True).load(text) == {"note": "a\tb", "item": 3}
    assert format_yaml(text) == _pure(text)


@pytest.mark.parametrize(
    "text",
    [
        "a: |\n \n  hello\n",
        "a: >\n \n  hello\n",
        "a: |\r\n \r\n  hello\r\n",
        b"a: |\r\n \r\n  hello\r\n",
    ],
)
def test_block_scalar_indent_hole_reparses_with_pure_python(
    text: str | bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    _require_clib()
    calls = _watch_reparse(monkeypatch)
    assert _outcome(text, pure=False) == _outcome(text, pure=True)
    assert calls == [text]


def test_ordinary_block_scalar_keeps_the_c_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_clib()
    _reject_reparse(monkeypatch)
    text = "a: |\n  hello\n    world\n"
    assert format_yaml(text) == _pure(text)


_KEYS = ("name", "count", "note", "title", "sku", "flag", "label", "city")
_WORDS = ("alpha", "beta", "widget", "packed", "plain", "sale", "north")
_PHRASES = ("café", "订单", "naïve", "😀", "line one")
_COMPARE_SEED = 0x0C11B015
_CI_COMPARE_COUNT = 48
_FULL_COMPARE_COUNT = 20_000
_KINDS = (
    "plain",
    "tab_double",
    "tab_single",
    "tab_multi_double",
    "tab_multi_single",
    "tab_between",
    "bare_empty",
    "bare_scalar",
    "local_tag",
    "both",
)


def _formats_equal(actual: str, expected: str) -> bool:
    return actual == expected


def _quoted(rng: random.Random, text: str) -> str:
    if rng.randrange(2) == 0:
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return "'" + text.replace("'", "''") + "'"


def _scalar_token(rng: random.Random) -> str:
    roll = rng.randrange(9)
    if roll == 0:
        return str(rng.randint(0, 40))
    if roll == 1:
        return rng.choice(("true", "false", "null", "~", "1.25"))
    if roll == 2:
        return _quoted(rng, rng.choice(_WORDS))
    if roll == 3:
        return _quoted(rng, rng.choice(_PHRASES))
    if roll == 4:
        return '"' + rng.choice(_WORDS) + "\\n" + rng.choice(_WORDS) + '"'
    if roll == 5:
        return "!!str " + rng.choice(_WORDS)
    if roll == 6:
        return "!!int " + str(rng.randint(0, 12))
    if roll == 7:
        return "!!binary aGVsbG8="
    return rng.choice(_WORDS)


def _block_body(rng: random.Random, indent: int) -> str:
    style = rng.choice(("|", ">", "|-", ">-"))
    pad = " " * indent
    rows = [
        f"{pad}{rng.choice(_WORDS)} {rng.choice(_PHRASES)}"
        for _ in range(rng.randint(1, 3))
    ]
    return f"{style}\n" + "\n".join(rows) + "\n"


def _mapping_lines(rng: random.Random, indent: int, depth: int) -> str:
    pad = " " * indent
    pieces: list[str] = []
    used: set[str] = set()
    for _ in range(rng.randint(1, 3)):
        key = rng.choice(_KEYS) + str(rng.randint(0, 6))
        while key in used:
            key += "x"
        used.add(key)
        nested = depth < 2 and rng.randrange(4) == 0
        block = (not nested) and depth < 2 and rng.randrange(4) == 0
        if block:
            pieces.append(f"{pad}{key}: {_block_body(rng, indent + 2)}")
            continue
        if nested and rng.randrange(2) == 0:
            pieces.append(f"{pad}{key}:\n{_sequence_lines(rng, indent + 2, depth + 1)}")
            continue
        if nested:
            pieces.append(f"{pad}{key}:\n{_mapping_lines(rng, indent + 2, depth + 1)}")
            continue
        line = f"{pad}{key}: {_scalar_token(rng)}"
        if rng.randrange(4) == 0:
            line += " # note"
        pieces.append(line + "\n")
    return "".join(pieces)


def _sequence_lines(rng: random.Random, indent: int, depth: int) -> str:
    pad = " " * indent
    pieces: list[str] = []
    for _ in range(rng.randint(1, 3)):
        if depth < 2 and rng.randrange(3) == 0:
            pieces.append(f"{pad}-\n{_mapping_lines(rng, indent + 2, depth + 1)}")
            continue
        pieces.append(f"{pad}- {_scalar_token(rng)}\n")
    return "".join(pieces)


def _random_yaml_document(rng: random.Random) -> str | bytes:
    if rng.randrange(3) == 0:
        document = _sequence_lines(rng, 0, 0)
    else:
        document = _mapping_lines(rng, 0, 0)
    if rng.randrange(5) == 0:
        return document.encode()
    return document


def _quoted_tab_document(rng: random.Random, quote: str, *, multiline: bool) -> str:
    key = rng.choice(_KEYS) + str(rng.randint(0, 6))
    left = rng.choice(_WORDS)
    right = rng.choice(_WORDS)
    body = f"{left}\n\t{right}" if multiline else f"{left}\t{right}"
    return f"{key}: {quote}{body}{quote}\n"


def _tab_between_document(rng: random.Random) -> str:
    key = rng.choice(_KEYS) + str(rng.randint(0, 6))
    if rng.randrange(2) == 0:
        return f"{key}:\t{rng.randint(0, 40)}\n"
    return f"{key}: {rng.choice(_WORDS)}\t{rng.choice(_WORDS)}\n"


def _bare_scalar_document(rng: random.Random) -> str:
    roll = rng.randrange(5)
    if roll == 0:
        return f"! {rng.randint(0, 40)}\n"
    if roll == 1:
        return f"! {rng.choice(('true', 'false', 'null'))}\n"
    if roll == 2:
        return f"! [{rng.randint(0, 9)}, {rng.randint(0, 9)}]\n"
    if roll == 3:
        return f"! {{{rng.choice(_KEYS)}: {rng.randint(0, 9)}}}\n"
    return f"item: ! {rng.randint(0, 40)}\n"


def _local_tag_document(rng: random.Random) -> str:
    name = rng.choice(("foo", "custom", "local"))
    if rng.randrange(2) == 0:
        return f"!{name} {rng.choice(_WORDS)}\n"
    return f"value: !{name} [{rng.randint(0, 4)}, {rng.randint(0, 4)}]\n"


def _both_document(rng: random.Random) -> str:
    quote = rng.choice(("'", '"'))
    multiline = rng.randrange(2) == 0
    left = rng.choice(_WORDS)
    right = rng.choice(_WORDS)
    body = f"{left}\n\t{right}" if multiline else f"{left}\t{right}"
    roll = rng.randrange(3)
    if roll == 0:
        tagged = f"item: ! {rng.randint(0, 40)}\n"
    elif roll == 1:
        tagged = f"item: ! [{rng.randint(0, 9)}]\n"
    else:
        name = rng.choice(("foo", "custom", "local"))
        tagged = f"item: !{name} {rng.choice(_WORDS)}\n"
    document = f"note: {quote}{body}{quote}\n{tagged}"
    if rng.randrange(2) == 0:
        document += f"extra:\t{rng.randint(0, 9)}\n"
    return document


def _triggered_document(rng: random.Random, kind: str) -> str:
    if kind == "tab_double":
        return _quoted_tab_document(rng, '"', multiline=False)
    if kind == "tab_single":
        return _quoted_tab_document(rng, "'", multiline=False)
    if kind == "tab_multi_double":
        return _quoted_tab_document(rng, '"', multiline=True)
    if kind == "tab_multi_single":
        return _quoted_tab_document(rng, "'", multiline=True)
    if kind == "tab_between":
        return _tab_between_document(rng)
    if kind == "bare_empty":
        return rng.choice(
            ("a: !\n", "- !\n", "[! ]\n", "--- !\n", "!<!> \n", "a: !<!>\n")
        )
    if kind == "bare_scalar":
        return _bare_scalar_document(rng)
    if kind == "local_tag":
        return _local_tag_document(rng)
    if kind == "both":
        return _both_document(rng)
    raise AssertionError(kind)


def _comparison_document(rng: random.Random, kind: str) -> str | bytes:
    if kind == "plain":
        return _random_yaml_document(rng)
    document = _triggered_document(rng, kind)
    if document.isascii() and rng.randrange(5) == 0:
        return document.encode()
    return document


def _compare_random_documents(count: int) -> None:
    rng = random.Random(_COMPARE_SEED)
    for index in range(count):
        document = _comparison_document(rng, _KINDS[index % len(_KINDS)])
        assert _outcome(document, pure=False) == _outcome(document, pure=True), document


def test_random_documents_match_pure_python_bytes() -> None:
    _require_clib()
    _compare_random_documents(_CI_COMPARE_COUNT)


def test_random_documents_match_pure_python_full() -> None:
    if os.environ.get("GALLEY_YAML_COMPARE_FULL") != "1":
        pytest.skip("the 20000-document comparison runs in the nightly job")
    if not format_module._clib_available():
        pytest.fail("ruamel.yaml.clib is not installed")
    _compare_random_documents(_FULL_COMPARE_COUNT)


def test_format_comparison_fails_on_a_deliberate_mismatch() -> None:
    _require_clib()
    document = "a: 1\n"
    expected = _pure(document)
    assert _formats_equal(format_yaml(document), expected)
    drifted = expected.replace("1", "9", 1)
    assert not _formats_equal(drifted, expected)
