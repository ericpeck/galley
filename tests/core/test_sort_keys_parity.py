"""Sorted output is the same with ruamel.yaml.clib and with pure Python (#59, #110).

Each test runs once per engine, forced the way ``test_yaml_engine_parity.py``
forces them. Each case has one expected text, so both engines must write it.
"""

import pytest
import ruamel.yaml.main as ruamel_main

import galley.core.format as format_module
from galley.core.errors import NotJSONValueError
from galley.core.format import format_document

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


_JSON_TO_YAML = (
    (
        '{"b": 1, "a": {"d": [3, 1], "c": "😀"}}',
        "a:\n  c: 😀\n  d:\n  - 3\n  - 1\nb: 1\n",
    ),
    ('{"é": 1, "z": 2, "B": 3}', "B: 3\nz: 2\né: 1\n"),
    ('[{"y": 1, "x": 2}, "k"]', "- x: 2\n  y: 1\n- k\n"),
)

_YAML_TO_JSON = (
    (
        "b: 1\na:\n  d: [3, 1]\n  c: 😀\n",
        '{\n  "a": {\n    "c": "😀",\n'
        '    "d": [\n      3,\n      1\n    ]\n  },\n  "b": 1\n}\n',
    ),
    (
        "9: nine\n10: ten\nfalse: f\n",
        '{\n  "10": "ten",\n  "9": "nine",\n  "false": "f"\n}\n',
    ),
    (
        "z: &x {k: 1, j: 2}\na: *x\n",
        '{\n  "a": {\n    "j": 2,\n    "k": 1\n  },\n'
        '  "z": {\n    "j": 2,\n    "k": 1\n  }\n}\n',
    ),
)

_YAML_TO_YAML = (
    ("b: 1\na:\n  d: [3, 1]\n  c: 😀\n", "a:\n  c: 😀\n  d:\n  - 3\n  - 1\nb: 1\n"),
    ("z: &x\n  k: 1\n  j: 2\na: *x\n", "a: &id001\n  j: 2\n  k: 1\nz: *id001\n"),
    (
        "s: !!set {zeta, 2, alpha}\nb: 1\n",
        "b: 1\ns: !!set\n  alpha: null\n  zeta: null\n  2: null\n",
    ),
    ('note: "a\\tb"\nb: 1\n', 'b: 1\nnote: "a\\tb"\n'),
)


@pytest.mark.parametrize(("text", "expected"), _JSON_TO_YAML)
def test_json_to_yaml_sorted_is_one_text(engine: str, text: str, expected: str) -> None:
    assert format_document(text, "json", "yaml", sort_keys=True) == expected


@pytest.mark.parametrize(("text", "expected"), _YAML_TO_JSON)
def test_yaml_to_json_sorted_is_one_text(engine: str, text: str, expected: str) -> None:
    assert format_document(text, "yaml", "json", sort_keys=True) == expected


@pytest.mark.parametrize(("text", "expected"), _YAML_TO_YAML)
def test_yaml_to_yaml_sorted_is_one_text(engine: str, text: str, expected: str) -> None:
    assert format_document(text, "yaml", "yaml", sort_keys=True) == expected


@pytest.mark.parametrize("sort_keys", [False, True])
def test_value_json_cannot_hold_still_fails_when_sorting(
    engine: str, sort_keys: bool
) -> None:
    with pytest.raises(NotJSONValueError):
        format_document("b: !!set {x}\na: 1\n", "yaml", "json", sort_keys=sort_keys)
