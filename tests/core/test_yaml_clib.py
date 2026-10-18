"""format_yaml uses ruamel.yaml.clib when it is installed, and matches pure Python."""

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


def test_plain_document_stays_on_the_c_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _require_clib()
    calls: list[bool] = []
    real = format_module._format_yaml_impl

    def track(text: str | bytes, *, pure: bool) -> str:
        calls.append(pure)
        return real(text, pure=pure)

    monkeypatch.setattr(format_module, "_format_yaml_impl", track)
    source = "a: 1\nb:\n  - 2\n  - 3\n"
    assert format_yaml(source) == real(source, pure=True)
    assert calls == [False]


@pytest.mark.parametrize("source", ["a: it's\n", 'a: ""\n', 'notes: "packed."\n'])
def test_common_quotes_stay_on_the_c_path(
    source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _require_clib()
    calls: list[bool] = []
    real = format_module._format_yaml_impl

    def track(text: str | bytes, *, pure: bool) -> str:
        calls.append(pure)
        return real(text, pure=pure)

    monkeypatch.setattr(format_module, "_format_yaml_impl", track)
    assert format_yaml(source) == real(source, pure=True)
    assert calls == [False]


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
    assert calls == [False, True]


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
