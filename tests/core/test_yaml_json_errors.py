"""YAML to JSON: keys JSON can't hold and aliases that contain themselves (#116).

``json.dumps`` rejects both before its ``default`` hook runs, so before
this they reached the user as Python's own text. Each test runs on both
YAML engines: ``c`` uses the C parser and skips without ``ruamel.yaml.clib``,
and ``pure`` hides it the way ``test_yaml_engine_parity.py`` does. Every
value is fake.
"""

import pickle

import pytest
import ruamel.yaml.main as ruamel_main

import galley.core.format as format_module
from galley.core.errors import NotJSONValueError
from galley.core.escape import escape_document
from galley.core.format import format_document
from galley.core.mask import MaskRules
from galley.core.worker import FailureEvent, FormatJob, _execute

_ENGINES = ("c", "pure")

_KEYS = (
    "2020-01-01: fake-note\n",
    "events:\n  2020-01-01: fake-note\n",
    "? 2020-01-01 12:30:00\n: fake-note\n",
    "? !!binary aGk=\n: fake-note\n",
    "rows:\n- 2020-01-01: fake-note\n",
)
_KEY_IDS = ("date", "nested-date", "datetime", "binary", "date-in-a-list")
_SELF = (
    "a: &x [*x]\n",
    "a: &x {b: *x}\n",
    "a: &x\n  b:\n    c: [fake-note, *x]\n",
    "- &x [fake-note, *x]\n",
)
_SELF_IDS = ("list", "mapping", "deep", "root-list")


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


class _Events(list):
    def put(self, event: object) -> None:
        self.append(event)


def _failure(text: str, rules: MaskRules | None = None) -> FailureEvent:
    events = _Events()
    _execute(FormatJob(1, text, "yaml", "json", mask=rules), events)
    failure = events[-1]
    assert isinstance(failure, FailureEvent)
    return failure


@pytest.mark.parametrize("text", _KEYS, ids=_KEY_IDS)
def test_a_key_json_cant_hold_raises_a_typed_error(engine: str, text: str) -> None:
    with pytest.raises(NotJSONValueError) as caught:
        format_document(text, "yaml", "json")
    assert type(caught.value).__name__ == "NotJSONKeyError"
    assert str(caught.value) == "not_json_key"


@pytest.mark.parametrize("text", _SELF, ids=_SELF_IDS)
def test_an_alias_inside_itself_raises_a_typed_error(engine: str, text: str) -> None:
    with pytest.raises(NotJSONValueError) as caught:
        format_document(text, "yaml", "json")
    assert type(caught.value).__name__ == "SelfReferenceError"
    assert str(caught.value) == "self_reference"


@pytest.mark.parametrize(
    ("text", "kind"),
    [(_KEYS[0], "not_json_key"), (_SELF[0], "self_reference")],
    ids=["date-key", "self-reference"],
)
@pytest.mark.parametrize("masked", [False, True], ids=["plain", "masked"])
def test_the_worker_sends_the_kind_as_a_value_json_cant_hold(
    engine: str, text: str, kind: str, masked: bool
) -> None:
    failure = _failure(text, MaskRules() if masked else None)
    assert failure.not_json_value
    assert failure.message == kind
    assert failure.problems == ()
    assert failure.error is None
    copy = pickle.loads(pickle.dumps(failure))
    assert copy == failure
    assert b"fake-note" not in pickle.dumps(failure)


@pytest.mark.parametrize(
    ("text", "kind"),
    [(_KEYS[0], "not_json_key"), (_SELF[0], "self_reference")],
    ids=["date-key", "self-reference"],
)
def test_escape_as_string_raises_the_same_errors(
    engine: str, text: str, kind: str
) -> None:
    with pytest.raises(NotJSONValueError) as caught:
        escape_document(text, "yaml")
    assert str(caught.value) == kind


@pytest.mark.parametrize(
    "text",
    [
        "a: &x [1, 2]\nb: *x\nc: [*x, *x]\n",
        "base: &b {plan: free}\nteam: {<<: *b, seats: 3}\nother: *b\n",
        "1: one\n2.5: two-and-a-half\nnull: none\n",
        "nested:\n  - &n {v: 1}\n  - *n\n",
    ],
    ids=["shared-list", "merge-key", "number-and-null-keys", "shared-in-list"],
)
def test_shared_aliases_and_number_keys_still_convert(engine: str, text: str) -> None:
    assert format_document(text, "yaml", "json")


def test_a_shared_alias_is_walked_once() -> None:
    # Ten levels that each hold the level below ten times: 10**10 paths but
    # eleven lists. Walking every path would never finish.
    lines = ["l0: &l0 [fake-note]"]
    for level in range(1, 11):
        refs = ", ".join([f"*l{level - 1}"] * 10)
        lines.append(f"l{level}: &l{level} [{refs}]")
    text = "\n".join(lines) + "\n"
    opened = format_module._yaml_limits(text)
    value = format_module._load_parsed(text, opened, pure=False)
    format_module._check_json_shape(value)


def test_the_kinds_are_the_format_modules_names() -> None:
    assert format_module.NOT_JSON_KEY == "not_json_key"
    assert format_module.SELF_REFERENCE == "self_reference"
    assert issubclass(format_module.NotJSONKeyError, NotJSONValueError)
    assert issubclass(format_module.SelfReferenceError, NotJSONValueError)
