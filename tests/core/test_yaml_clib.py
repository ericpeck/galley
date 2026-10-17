"""Safe YAML uses ruamel.yaml.clib, and errors keep the pure-Python locations."""

import pytest
from ruamel.yaml import YAML
from ruamel.yaml.main import CEmitter, CParser
from ruamel.yaml.reader import ReaderError

import galley.core.format as format_module
from galley.core.errors import InputError
from galley.core.format import format_yaml

_MALFORMED = (
    ("a: [1, 2", 1, 9),
    ('a: "\\q"\n', 1, 6),
    ("a:\n\tb: 1\n", 2, 1),
    ("a: 1\r\nb:\r\n\tc: 2\n", 3, 1),
    ("a: 1\r\nb: [\n", 3, 1),
    ("a: 1\na: 2\n", 2, 1),
    ("a:\n  b: 1\n  b: 2\n", 3, 3),
    ("value: !custom [1, 2]\n", 1, 8),
)


def _line_column(exc: BaseException) -> tuple[int, int]:
    lineno = getattr(exc, "lineno", None)
    colno = getattr(exc, "colno", None)
    if isinstance(lineno, int) and isinstance(colno, int):
        return lineno, colno
    mark = getattr(exc, "problem_mark", None)
    if mark is not None and isinstance(mark.line, int) and isinstance(mark.column, int):
        return mark.line + 1, mark.column + 1
    message = f"{type(exc).__name__} does not name a line and column: {exc}"
    raise AssertionError(message)


def _track_yaml(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record pure=True constructions and require every other YAML to be C."""
    calls: list[str] = []
    real = format_module.YAML

    class TrackingYAML(real):  # type: ignore[misc, valid-type]
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            if kwargs.get("pure") is True:
                calls.append("pure")
                return
            assert self.Parser is CParser
            assert self.Emitter is CEmitter
            calls.append("c")

    monkeypatch.setattr(format_module, "YAML", TrackingYAML)
    return calls


def test_c_loader_and_emitter_are_available() -> None:
    assert CParser is not None
    assert CEmitter is not None
    yaml = YAML(typ="safe")
    assert yaml.Parser is CParser
    assert yaml.Emitter is CEmitter


def test_successful_format_uses_c_path_and_not_the_pure_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "a: 1\nb:\n  - 2\n  - 3\n"
    expected = format_yaml(source)
    calls = _track_yaml(monkeypatch)
    assert format_yaml(source) == expected
    assert "c" in calls
    assert "pure" not in calls


@pytest.mark.parametrize(("text", "line", "column"), _MALFORMED)
def test_malformed_yaml_with_clib_keeps_pure_locations(
    monkeypatch: pytest.MonkeyPatch,
    text: str,
    line: int,
    column: int,
) -> None:
    calls = _track_yaml(monkeypatch)
    with pytest.raises(Exception) as caught:
        format_yaml(text)
    assert _line_column(caught.value) == (line, column)
    assert f"line {line}" in str(caught.value)
    assert f"column {column}" in str(caught.value)
    assert "c" in calls
    assert "pure" in calls


def test_invalid_utf8_is_decoded_before_ruamel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _track_yaml(monkeypatch)
    with pytest.raises(InputError) as caught:
        format_yaml(b'a: "\xff"\n')
    error = caught.value
    assert error.kind == "invalid_utf8"
    assert error.lineno == 1
    assert error.colno == 5
    assert error.details == {"offset": 4}
    assert calls == []
    calls.clear()
    with pytest.raises(InputError) as caught:
        format_yaml(b'a: "\xc3\xa9\xff"\n')
    error = caught.value
    assert error.kind == "invalid_utf8"
    assert error.lineno == 1
    assert error.colno == 6
    assert error.details == {"offset": 6}
    assert calls == []


def test_reader_error_from_the_c_loader_is_reparsed_with_pure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(_text: str) -> object:
        raise ReaderError("input", 0, "?", "utf-8", "forced")

    monkeypatch.setattr(format_module, "_load_c_safe", boom)
    calls = _track_yaml(monkeypatch)
    with pytest.raises(Exception) as caught:
        format_yaml("a: [1, 2")
    assert not isinstance(caught.value, ReaderError)
    assert _line_column(caught.value) == (1, 9)
    assert "pure" in calls
