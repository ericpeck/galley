"""YAML to JSON: keys JSON can't hold and aliases that contain themselves (#116).

Also aliases that, written out in full, would add too many values (#223).

``json.dumps`` rejects both before its ``default`` hook runs, so before
this they reached the user as Python's own text. Each test runs on both
YAML engines: ``c`` uses the C parser and skips without ``ruamel.yaml.clib``,
and ``pure`` hides it the way ``test_yaml_engine_parity.py`` does. Every
value is fake.
"""

import json
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


# JSON writes every alias out in full, so a few lines of YAML can name
# millions of values (#223). Aliases may add this many values, or this many
# times the document's own distinct values, whichever is more.
_ALIAS_LIMIT = 100_000
_ALIAS_RATIO = 10


def _alias_chain(levels: int, fan: int) -> str:
    """Each level lists the one below ``fan`` times: ``fan**levels`` leaves."""
    lines = ['a0: &a0 ["fake-note"]']
    for level in range(1, levels + 1):
        refs = ", ".join([f"*a{level - 1}"] * fan)
        lines.append(f"a{level}: &a{level} [{refs}]")
    lines.append(f"root: *a{levels}")
    return "\n".join(lines) + "\n"


def _aliases_adding(values: int, distinct: int = 104) -> str:
    """YAML whose aliases, written out in full, add exactly ``values`` values.

    Each ``*h`` repeats a list and its 99 items, 100 values, and each ``*e``
    repeats an empty list, one value. The document has ``distinct`` values
    of its own, at least 104: the root, ``h`` and its items, ``e``,
    ``rows``, and the ``pad`` list and its items. Mapping keys aren't values.
    """
    hundreds, ones = divmod(values, 100)
    items = ", ".join(["fake-note"] * 99)
    pad = ", ".join(["fake-pad"] * (distinct - 104))
    refs = ", ".join(["*h"] * hundreds + ["*e"] * ones)
    return f"h: &h [{items}]\ne: &e []\npad: [{pad}]\nrows: [{refs}]\n"


# 10,001 distinct values: ten times that, 100,010, is just past the fixed
# limit, so only the ratio allows it. A small document keeps the pure-Python
# parse fast on CI.
_LARGE = 10_001


@pytest.mark.parametrize(
    "text",
    [
        _alias_chain(levels=6, fan=10),
        _aliases_adding(_ALIAS_LIMIT + 1),
        _aliases_adding(_ALIAS_RATIO * _LARGE + 1, distinct=_LARGE),
    ],
    ids=["chain-from-223", "one-over", "one-over-ten-times-the-document"],
)
def test_aliases_that_add_too_many_values_raise_a_typed_error(
    engine: str, text: str
) -> None:
    """The chain is #223's example: 378 bytes that wrote 117.6 MB of JSON."""
    with pytest.raises(NotJSONValueError) as caught:
        format_document(text, "yaml", "json")
    assert type(caught.value).__name__ == "AliasExpansionError"
    assert str(caught.value) == "alias_expansion"


@pytest.mark.parametrize(
    ("added", "distinct"),
    [(_ALIAS_LIMIT, 104), (_ALIAS_RATIO * _LARGE, _LARGE)],
    ids=["the-limit", "ten-times-the-document"],
)
def test_aliases_that_add_the_most_allowed_still_convert(
    engine: str, added: int, distinct: int
) -> None:
    """A larger document may repeat more, as merge keys across many rows do."""
    text = _aliases_adding(added, distinct=distinct)
    written = json.loads(format_document(text, "yaml", "json"))
    hundreds, ones = divmod(added, 100)
    assert len(written["rows"]) == hundreds + ones
    assert written["rows"][0] == ["fake-note"] * 99
    assert len(written["pad"]) == distinct - 104


# Aliases may also add this many bytes of JSON, or ten times the input's
# bytes when that is more. A string counts its UTF-8 bytes, any other value
# 8, each list item or mapping entry 2, and each key its own length.
_ALIAS_BYTES = 10_000_000
# A shared list of ten 1,000-byte strings: each alias to it adds 10,020 bytes.
_SHARED_LIST_BYTES = 10 * (2 + 1_000)


def _long_string_aliased(times: int, length: int = 100_000) -> str:
    """One ``length``-byte string aliased ``times`` times: few values, many bytes."""
    refs = ", ".join(["*s"] * times)
    return f"s: &s {'v' * length}\nrows: [{refs}]\n"


def _shared_list_aliased(times: int) -> str:
    """A list of ten 1,000-byte strings aliased ``times`` times."""
    items = ", ".join([f"fake-note-{n}-" + "v" * 988 for n in range(10)])
    refs = ", ".join(["*l"] * times)
    return f"l: &l [{items}]\nrows: [{refs}]\n"


@pytest.mark.parametrize(
    "text",
    [
        _long_string_aliased(_ALIAS_BYTES // 100_000 + 1),
        _shared_list_aliased(_ALIAS_BYTES // _SHARED_LIST_BYTES + 1),
    ],
    ids=["long-string", "shared-list-of-long-strings"],
)
def test_aliases_that_add_too_many_bytes_raise_a_typed_error(
    engine: str, text: str
) -> None:
    """Few values, but each alias writes a long string out again."""
    with pytest.raises(NotJSONValueError) as caught:
        format_document(text, "yaml", "json")
    assert type(caught.value).__name__ == "AliasExpansionError"
    assert str(caught.value) == "alias_expansion"


def test_aliases_that_add_just_under_the_bytes_allowed_still_convert(
    engine: str,
) -> None:
    times = _ALIAS_BYTES // _SHARED_LIST_BYTES
    assert times * _SHARED_LIST_BYTES <= _ALIAS_BYTES
    written = json.loads(format_document(_shared_list_aliased(times), "yaml", "json"))
    assert len(written["rows"]) == times
    assert written["rows"][-1] == written["l"]
    assert written["l"][0] == "fake-note-0-" + "v" * 988


@pytest.mark.parametrize("masked", [False, True], ids=["plain", "masked"])
def test_the_worker_sends_alias_expansion_as_a_value_json_cant_hold(
    engine: str, masked: bool
) -> None:
    failure = _failure(
        _aliases_adding(_ALIAS_LIMIT + 1), MaskRules() if masked else None
    )
    assert failure.not_json_value
    assert failure.message == "alias_expansion"
    assert failure.problems == ()
    assert failure.error is None
    copy = pickle.loads(pickle.dumps(failure))
    assert copy == failure
    assert b"fake-note" not in pickle.dumps(failure)


def test_escape_as_string_refuses_the_same_aliases(engine: str) -> None:
    with pytest.raises(NotJSONValueError) as caught:
        escape_document(_aliases_adding(_ALIAS_LIMIT + 1), "yaml")
    assert str(caught.value) == "alias_expansion"


def test_the_alias_limit_and_kind_are_the_format_modules() -> None:
    assert format_module.ALIAS_EXPANSION == "alias_expansion"
    assert format_module.ALIAS_EXPANSION_LIMIT == _ALIAS_LIMIT
    assert format_module.ALIAS_EXPANSION_RATIO == _ALIAS_RATIO
    assert issubclass(format_module.AliasExpansionError, NotJSONValueError)
