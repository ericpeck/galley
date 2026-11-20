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
from galley.app.indent import INDENT_CHOICES, indent_unit
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
# A shared list of ten 1,000-byte strings, aliased in ``rows`` two levels
# deep: each alias to it adds 10,095 bytes. That is 10,020 for its items,
# 31 for a newline and one indent per item and the closing newline, and
# 44 for its 11 lines indented two more levels of two spaces.
_SHARED_LIST_BYTES = 10 * (2 + 1_000) + 10 * (1 + 2) + 1 + 11 * 2 * 2


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


# JSON indents each line by its depth, up to MAX_DEPTH levels, so a value
# an alias writes deep in nested lists costs far more than its own bytes.
# Format to JSON writes two spaces per level by default.


def _deep_aliases(depth: int, times: int, chars: int = 98, pad: int = 0) -> str:
    """A list of 99 strings of ``chars`` bytes aliased ``times`` times.

    The aliases sit inside ``depth`` nested flow lists. ``pad`` adds that
    many one-letter values of the document's own, so the value ratio
    allows more. Lines stay short.
    """
    item = "v" * chars if chars else '""'
    head = "l: &l [" + ", ".join([item] * 99) + "]\n"
    padding = ""
    if pad:
        rows = [", ".join(["a"] * 100) for _ in range(pad // 100)]
        padding = "pad: [" + ",\n".join(rows) + "]\n"
    refs = [", ".join(["*l"] * min(100, times - n)) for n in range(0, times, 100)]
    nested = "[" * depth + ",\n".join(refs) + "]" * depth
    return head + padding + "deep: " + nested + "\n"


# The most aliases each depth allows before the bytes they add, indentation
# included, pass the budget. Below 256 levels Format uses ruamel's loader,
# and above it an explicit stack. Converting at 240 levels hits the JSON
# writer's own nesting limit under pytest, so the guard below uses 200.
_DEEP_MOST = {200: 199, 240: 172, 500: 91}


class _Rendered(Exception):
    """Raised by the stand-in for a writer, so nothing large gets built."""


@pytest.fixture
def no_render(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if any YAML or JSON writer runs."""

    def rendered(*_args: object, **_kwargs: object) -> str:
        raise _Rendered("a refusal must come before the render")

    for name in ("_dump_yaml", "_dump_parsed", "_dump_block", "_dump_json"):
        monkeypatch.setattr(format_module, name, rendered)


@pytest.mark.parametrize(
    ("depth", "times"),
    [
        (500, 1_000),
        (240, 1_000),
        (500, _DEEP_MOST[500] + 1),
        (240, _DEEP_MOST[240] + 1),
    ],
    ids=["depth-500", "depth-240", "depth-500-one-over", "depth-240-one-over"],
)
def test_deep_aliases_that_add_too_many_bytes_raise_a_typed_error(
    engine: str, no_render: None, depth: int, times: int
) -> None:
    """Depth 500 is 14.9 KB that wrote 112.0 MB of JSON before this."""
    with pytest.raises(NotJSONValueError) as caught:
        format_document(_deep_aliases(depth, times), "yaml", "json")
    assert type(caught.value).__name__ == "AliasExpansionError"
    assert str(caught.value) == "alias_expansion"


def test_deep_aliases_the_value_ratio_allows_are_refused_before_any_render(
    engine: str, no_render: None
) -> None:
    """55,000 values of its own let 5,500 aliases add 550,000 values.

    This is a tenth of a 1.87 MB document whose YAML render wrote 5.5 GB;
    the next test checks that one's value without parsing it.
    """
    text = _deep_aliases(500, 5_500, chars=0, pad=55_000)
    with pytest.raises(NotJSONValueError) as caught:
        format_document(text, "yaml", "json")
    assert str(caught.value) == "alias_expansion"


def test_a_deep_document_the_value_ratio_allows_is_refused_by_the_check() -> None:
    """The 1.87 MB document, built as its loaded value: nothing is parsed or written.

    It adds 5.5 million values, under ten times its 550,000 of its own, and
    10.9 MB before indentation, under ten times its input's bytes.
    """
    text = _deep_aliases(500, 55_000, chars=0, pad=550_000)
    shared = [""] * 99
    nested: list[object] = [shared] * 55_000
    for _ in range(499):
        nested = [nested]
    value = {"l": shared, "pad": ["a"] * 550_000, "deep": nested}
    with pytest.raises(NotJSONValueError) as caught:
        format_module._check_alias_expansion(value, len(text.encode()))
    assert str(caught.value) == "alias_expansion"


@pytest.mark.parametrize("depth", [200, 500], ids=["depth-200", "depth-500"])
def test_a_deep_document_just_under_the_bytes_allowed_still_converts(
    engine: str, depth: int
) -> None:
    """About 10 MB of JSON, indentation included."""
    times = _DEEP_MOST[depth]
    written = format_document(_deep_aliases(depth, times), "yaml", "json")
    assert len(written.encode()) <= 15_000_000
    value = json.loads(written)
    inner = value["deep"]
    for _ in range(depth - 1):
        (inner,) = inner
    assert len(inner) == times
    assert inner[-1] == value["l"] == ["v" * 98] * 99


def _merged_key(key_bytes: int, rows: int) -> str:
    """One ``key_bytes``-byte key that ``rows`` mappings each merge (``<<``).

    Each row is a new mapping, so the key is written out again in each one.
    """
    head = "m: &m\n  ? " + "k" * key_bytes + "\n  : 1\n"
    return head + "rows:\n" + "- <<: *m\n" * rows


def _long_int_aliased(times: int, digits: int = 4_300) -> str:
    """One ``digits``-digit int aliased ``times`` times. 4,300 is Python's most."""
    refs = [", ".join(["*n"] * min(100, times - n)) for n in range(0, times, 100)]
    return "n: &n " + "9" * digits + "\nrows: [" + ",\n".join(refs) + "]\n"


# The most rows that merge a 100,000-byte key, and the most aliases of a
# 430-digit int, before the bytes they add pass the budget.
_MERGE_MOST = 99
_LONG_INT_MOST = 23_201


@pytest.mark.parametrize(
    "text",
    [
        _merged_key(100_000, 1_000),
        _merged_key(1_000_000, 10_000),
        _long_int_aliased(20_000),
        _merged_key(100_000, _MERGE_MOST + 1),
        _long_int_aliased(_LONG_INT_MOST + 1, digits=430),
    ],
    ids=[
        "merge-key-109-kb",
        "merge-key-1-mb",
        "long-int",
        "merge-key-one-over",
        "long-int-one-over",
    ],
)
def test_merge_keys_and_long_ints_that_add_too_many_bytes_raise_a_typed_error(
    engine: str, no_render: None, text: str
) -> None:
    """109 KB of merge keys wrote 100.1 MB of JSON, and 84 KB of ints 86.1 MB.

    The 1 MB merge-key file would write about 10 GB.
    """
    with pytest.raises(NotJSONValueError) as caught:
        format_document(text, "yaml", "json")
    assert type(caught.value).__name__ == "AliasExpansionError"
    assert str(caught.value) == "alias_expansion"


_JUST_UNDER = pytest.mark.parametrize(
    ("text", "rows"),
    [
        (_merged_key(100_000, _MERGE_MOST), _MERGE_MOST),
        (_long_int_aliased(_LONG_INT_MOST, digits=430), _LONG_INT_MOST),
    ],
    ids=["merge-key", "long-int"],
)


def _assert_converts(text: str, rows: int) -> None:
    written = format_document(text, "yaml", "json")
    assert len(written.encode()) <= 15_000_000
    assert len(json.loads(written)["rows"]) == rows


@_JUST_UNDER
def test_merge_keys_and_long_ints_just_under_the_bytes_allowed_still_convert(
    engine: str, monkeypatch: pytest.MonkeyPatch, text: str, rows: int
) -> None:
    """About 10 MB of JSON each.

    The YAML render writes merged keys and repeated ints out in full, which
    takes seconds to parse again in pure Python. So the pure engine parses
    the text and checks that the alias check returns, and the C engine also
    writes the JSON. The full pure conversion is a perf test below.
    """
    if engine == "c":
        _assert_converts(text, rows)
        return
    check = format_module._check_alias_expansion
    returned: list[object] = []

    def recording(*args: object) -> None:
        returned.append(check(*args))

    monkeypatch.setattr(format_module, "_check_alias_expansion", recording)
    assert format_module._run_yaml(text, check_only=True, alias_width=2) == ""
    assert returned == [None]


@pytest.mark.perf
@pytest.mark.parametrize("engine", ["pure"], indirect=True)
@_JUST_UNDER
def test_merge_keys_and_long_ints_just_under_the_bytes_allowed_convert_in_pure(
    engine: str, text: str, rows: int
) -> None:
    """A full end-to-end conversion on the pure engine, with no time budget."""
    _assert_converts(text, rows)


def _sample_ints() -> list[int]:
    """0, the smallest and largest ints of each digit count, and powers of 2."""
    counts = [*range(1, 41), 100, 309, 1_000, 2_000, 4_299, 4_300]
    edges = [n for d in counts for n in (10 ** (d - 1), 10**d - 1)]
    powers = [2**bits for bits in (0, 1, 31, 32, 63, 64, 1_000, 14_000)]
    return [0, *edges, *powers]


def test_an_ints_byte_estimate_is_never_below_its_digits() -> None:
    """Python won't write an int past 4,300 digits as text, so it's estimated."""
    for number in _sample_ints():
        for signed in (number, -number):
            written = len(str(signed))
            estimate = format_module._value_bytes(signed)
            assert written <= estimate <= written + 1, signed


def test_the_widest_json_indent_is_the_indent_settings_widest() -> None:
    """An unknown indent must count the widest the indent setting writes."""
    widest = max(
        format_module._json_indent_width(indent_unit(choice, "json"))
        for choice in INDENT_CHOICES
    )
    assert format_module._WIDEST_JSON_INDENT_WIDTH == widest


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
