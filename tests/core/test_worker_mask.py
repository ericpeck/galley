"""Format jobs with masking on, run in-process and through a spawn child.

Every value here is fake: ``example.com`` addresses, 555-01xx phone
numbers and an all-zero Social Security number.
"""

import dataclasses
import json
import multiprocessing
import pickle
import re
import time
from pathlib import Path
from queue import Empty

import pytest
import ruamel.yaml.main as ruamel_main
from tests.core.mask_rows import (
    MASKED_PER_ROW,
    ONE_LINE_CHARS,
    ONE_MB,
    one_line,
    pretty_json,
    text_of_size,
    yaml_text,
)

import galley.core.format as format_module
import galley.core.worker as worker_module
from galley.app.limits import JSON_BUSY_BYTES, YAML_BUSY_BYTES
from galley.core import mask
from galley.core.errors import InputError
from galley.core.folds import fold_ranges, line_count, pack_ranges
from galley.core.format import format_document
from galley.core.mask import MASK, MaskError, MaskRules, mask_document, mask_tree
from galley.core.problems import Problem
from galley.core.worker import (
    MASK_FAILURE_MESSAGE,
    FailureEvent,
    FormatJob,
    ProgressEvent,
    SuccessEvent,
    _execute,
    run_worker,
    unlink_temp,
)

_SECRETS = (
    "pat@example.com",
    "backup@example.com",
    "555-0100",
    "555-0123",
    "000-00-0000",
    "Pat Example",
    "1 Example Street",
)

_JSON = json.dumps(
    {
        "name": "Pat Example",
        "email": "pat@example.com",
        "contact": {"workPhone": "555-0100", "fax": None},
        "address": {"street": "1 Example Street", "lines": ["Unit 2", 7]},
        "ssn": "000-00-0000",
        "orders": [{"id": 1, "total": 40.5}, {"id": 2, "total": 12}],
        "notes": ["backup@example.com", "keep this"],
        "active": True,
    }
)
_JSON_COUNT = 9

_YAML = """\
name: Pat Example
contact: &contact
  email: pat@example.com
  mobile: 555-0123
backup: *contact
address:
  street: 1 Example Street
  lines: [Unit 2, 7]
orders:
- id: 1
  total: 40.5
active: true
"""
_YAML_COUNT = 6

_KEY_RE = re.compile(r'^\s*(?:- )?("(?:[^"\\]|\\.)*"|[\w-]+):(?:\s|$)')

_PLAIN_JSON = '{"id": 7, "items": [1, 2.5, true, null, "x y"], "meta": {"k": "v"}}'
_PLAIN_YAML = "id: 7\nitems:\n- 1\n- 2.5\n- true\n- null\n- x y\nmeta:\n  k: v\n"


def _job(
    text: str,
    source: str,
    target: str | None = None,
    rules: MaskRules | None = MaskRules(),
    **extra: object,
) -> FormatJob:
    return FormatJob(
        job_id=1,
        text=text,
        source=source,
        target=target or source,
        mask=rules,
        **extra,
    )


class _Events:
    """The part of a queue ``_execute`` uses, kept in a list."""

    def __init__(self) -> None:
        self.items: list[object] = []

    def put(self, item: object) -> None:
        self.items.append(item)


def _run_here(job: FormatJob) -> list[object]:
    events = _Events()
    _execute(job, events)
    return events.items


def _assert_hidden(text: str) -> None:
    for secret in _SECRETS:
        assert secret not in text


# The job and its events cross the spawn boundary


def test_a_format_job_with_rules_pickles() -> None:
    rules = MaskRules(keys=("account id",), whole_keys=("label",))
    job = _job(_JSON, "json", rules=rules)
    copy = pickle.loads(pickle.dumps(job))
    assert copy == job
    assert copy.mask is not None
    assert copy.mask.matches("accountId")
    assert copy.mask.matches("label")
    assert not copy.mask.matches("email")


def test_masking_is_off_by_default() -> None:
    assert FormatJob(job_id=1, text="{}", source="json", target="json").mask is None


def test_a_masked_success_event_pickles() -> None:
    success = _run_here(_job(_JSON, "json"))[-1]
    assert isinstance(success, SuccessEvent)
    assert pickle.loads(pickle.dumps(success)) == success


def test_a_spawned_worker_masks_with_the_rules_it_was_sent() -> None:
    text = json.dumps({"account_id": "acct-0001", "email": "n/a", "note": "x"})
    rules = MaskRules(keys=("account id",), whole_keys=())
    success = _run_in_child([_job(text, "json", rules=rules)])[-1]
    assert isinstance(success, SuccessEvent)
    assert json.loads(success.text) == {
        "account_id": MASK,
        "email": "n/a",
        "note": "x",
    }
    assert success.masked_count == 1


# Masked output


@pytest.mark.parametrize(
    ("text", "source", "count"),
    [(_JSON, "json", _JSON_COUNT), (_YAML, "yaml", _YAML_COUNT)],
    ids=["json", "yaml"],
)
@pytest.mark.parametrize("target", ["json", "yaml"])
def test_a_spawned_worker_sends_masked_text_and_its_count(
    text: str, source: str, count: int, target: str
) -> None:
    events = _run_in_child([_job(text, source, target)])
    assert events[0] == ProgressEvent(1, "read", 0, 0, None)
    success = events[-1]
    assert isinstance(success, SuccessEvent)
    expected = mask_document(text, source, target)
    assert success.text == expected.text
    assert success.masked_count == expected.masked_count == count
    assert MASK in success.text
    _assert_hidden(success.text)


@pytest.mark.parametrize(
    ("text", "source"), [(_JSON, "json"), (_YAML, "yaml")], ids=["json", "yaml"]
)
@pytest.mark.parametrize("target", ["json", "yaml"])
def test_masking_keeps_the_keys_lines_and_folds(
    text: str, source: str, target: str
) -> None:
    plain = format_document(text, source, target)
    success = _run_here(_job(text, source, target))[-1]
    assert isinstance(success, SuccessEvent)
    assert line_count(success.text) == line_count(plain)
    assert _key_lines(success.text) == _key_lines(plain)
    fmt = target.upper()
    assert success.folds == pack_ranges(fold_ranges(plain, fmt), line_count(plain))


def test_a_masked_subtree_keeps_its_keys_and_hides_every_value() -> None:
    success = _run_here(_job(_JSON, "json"))[-1]
    assert isinstance(success, SuccessEvent)
    value = json.loads(success.text)
    assert value["address"] == {"street": MASK, "lines": [MASK, MASK]}
    assert value["contact"] == {"workPhone": MASK, "fax": MASK}
    assert value["orders"] == [{"id": 1, "total": 40.5}, {"id": 2, "total": 12}]
    assert value["notes"] == [MASK, "keep this"]
    assert value["active"] is True
    assert "Unit 2" not in success.text


def test_a_yaml_alias_of_a_masked_anchor_is_masked() -> None:
    success = _run_here(_job(_YAML, "yaml", "json"))[-1]
    assert isinstance(success, SuccessEvent)
    value = json.loads(success.text)
    assert value["contact"] == {"email": MASK, "mobile": MASK}
    assert value["backup"] == value["contact"]


def test_a_masked_job_writes_the_masked_text_to_its_temp(tmp_path: Path) -> None:
    destination = tmp_path / "out.json"
    destination.write_bytes(b"{}\n")
    job = _job(_JSON, "json", destination=str(destination), chunk_size=64)
    events = _run_in_child([job])
    success = events[-1]
    assert isinstance(success, SuccessEvent)
    assert success.temp_path is not None
    written = Path(success.temp_path).read_bytes()
    assert written == success.text.encode("utf-8")
    _assert_hidden(written.decode("utf-8"))
    writes = [e for e in events if isinstance(e, ProgressEvent) and e.phase == "write"]
    assert len(writes) > 2
    assert writes[-1].written == writes[-1].total == len(written)
    assert destination.read_bytes() == b"{}\n"
    unlink_temp(success.temp_path)


# Masking on with nothing to mask, and masking off


@pytest.mark.parametrize(
    ("text", "source"),
    [(_PLAIN_JSON, "json"), (_PLAIN_YAML, "yaml")],
    ids=["json", "yaml"],
)
@pytest.mark.parametrize("target", ["json", "yaml"])
@pytest.mark.parametrize("sort_keys", [False, True], ids=["unsorted", "sorted"])
def test_nothing_to_mask_gives_todays_text_and_a_zero_count(
    text: str, source: str, target: str, sort_keys: bool
) -> None:
    success = _run_here(_job(text, source, target, sort_keys=sort_keys))[-1]
    assert isinstance(success, SuccessEvent)
    assert success.text == format_document(text, source, target, sort_keys=sort_keys)
    assert success.masked_count == 0


@pytest.mark.parametrize(
    ("text", "source"),
    [(_JSON, "json"), (_YAML, "yaml"), (_PLAIN_JSON, "json"), (_PLAIN_YAML, "yaml")],
    ids=["json", "yaml", "plain-json", "plain-yaml"],
)
@pytest.mark.parametrize("target", ["json", "yaml"])
def test_masking_off_is_byte_identical_to_format_document(
    text: str, source: str, target: str
) -> None:
    success = _run_in_child([_job(text, source, target, rules=None)])[-1]
    assert isinstance(success, SuccessEvent)
    assert success.text.encode("utf-8") == format_document(text, source, target).encode(
        "utf-8"
    )
    assert success.masked_count is None


# Failures never show unmasked output


def test_a_mask_failure_fails_the_job_with_no_text(tmp_path: Path) -> None:
    destination = tmp_path / "out.yaml"
    destination.write_bytes(b"old: true\n")
    text = "list: !!pairs [email: pat@example.com]\n"
    job = _job(text, "yaml", destination=str(destination))
    events = _run_in_child([job])
    failure = events[-1]
    assert failure == FailureEvent(1, MASK_FAILURE_MESSAGE, None)
    assert not any(isinstance(event, SuccessEvent) for event in events)
    assert [e.phase for e in events if isinstance(e, ProgressEvent)] == ["read"]
    assert destination.read_bytes() == b"old: true\n"
    assert not any(path.name.startswith(".galley-") for path in tmp_path.iterdir())


def test_a_mask_failure_message_never_quotes_a_value(monkeypatch) -> None:
    def broken(*_args: object) -> object:
        raise RuntimeError("could not hide pat@example.com")

    monkeypatch.setattr(mask, "_build", broken)
    events = _run_here(_job(_JSON, "json"))
    assert events[-1] == FailureEvent(1, MASK_FAILURE_MESSAGE, None)
    assert "pat@example.com" not in repr(events)


@pytest.mark.parametrize(
    ("text", "source"),
    [
        ('{"email": "pat@example.com",}', "json"),
        ('{"email": "a", "email": "b"}', "json"),
        ('{"a": "\\uD800"}', "json"),
        ("email: pat@example.com\n\tphone: 555-0100\n", "yaml"),
        ("email: [pat@example.com\n", "yaml"),
    ],
    ids=["json-syntax", "json-repeated-key", "json-escape", "yaml-tab", "yaml-syntax"],
)
def test_input_errors_keep_their_problems_and_places(text: str, source: str) -> None:
    masked = _run_here(_job(text, source))[-1]
    plain = _run_here(_job(text, source, rules=None))[-1]
    assert isinstance(masked, FailureEvent)
    assert isinstance(plain, FailureEvent)
    assert masked.problems == plain.problems
    assert [p.colno for p in masked.problems] == [p.colno for p in plain.problems]
    assert _error_fields(masked) == _error_fields(plain)
    _assert_clean(masked)


# A masked job's failure names the kind, key and place, never a value

_REPEATED_PASSWORD = "user: pat\npassword: hunter22\npassword: fake-other-22\n"


def _assert_clean(event: object, values: tuple[str, ...] = _SECRETS) -> None:
    """No value in the event, its repr, or the bytes that cross spawn."""
    shown = repr(event)
    pickled = pickle.dumps(event)
    if isinstance(event, FailureEvent) and event.error is not None:
        shown += repr(event.error.details)
    for value in values:
        assert value not in shown, value
        assert value.encode("utf-8") not in pickled, value


@pytest.mark.parametrize("target", ["yaml", "json"])
def test_a_repeated_yaml_key_names_the_key_and_place_but_no_value(
    target: str,
) -> None:
    plain = _run_here(_job(_REPEATED_PASSWORD, "yaml", target, rules=None))[-1]
    assert isinstance(plain, FailureEvent)
    assert "hunter22" in plain.message  # ruamel quotes the value; mask off keeps it
    events = _run_in_child([_job(_REPEATED_PASSWORD, "yaml", target)])
    failure = events[-1]
    assert isinstance(failure, FailureEvent)
    assert failure.message == 'duplicate_key "password" at line 3, column 1'
    assert failure.problems == (Problem("yaml_duplicate", 3, "password"),)
    assert failure.error is None
    for event in events:
        _assert_clean(event, ("hunter22", "fake-other-22"))


def test_a_repeated_json_key_names_the_key_and_place_but_no_value() -> None:
    text = '{\n  "password": "hunter22",\n  "password": "fake-other-22"\n}'
    failure = _run_in_child([_job(text, "json")])[-1]
    assert isinstance(failure, FailureEvent)
    assert failure.message == 'duplicate_key "password" at line 3, column 3'
    assert failure.problems == (Problem("json_duplicate", 3, "password"),)
    _assert_clean(failure, ("hunter22", "fake-other-22"))


@pytest.mark.parametrize(
    ("text", "source", "target", "message"),
    [
        (
            '{"password": "hunter22",}',
            "json",
            "json",
            "Expecting property name enclosed in double quotes at line 1, column 25",
        ),
        (
            '{"password": hunter22}',
            "json",
            "json",
            "Expecting value at line 1, column 14",
        ),
        (
            '{"password": "hunter22\\uD800"}',
            "json",
            "json",
            "bad_escape at line 1, column 23",
        ),
        (
            "password: hunter22\nlist: [a, b\n",
            "yaml",
            "yaml",
            "yaml_parser_error at line",
        ),
        ("password: 'hunter22\n", "yaml", "yaml", "yaml_scanner_error at line"),
        ("a: &x hunter22\nb: *y\n", "yaml", "yaml", "yaml_composer_error at line"),
        (
            "when: 2024-01-01\npassword: hunter22\n",
            "yaml",
            "json",
            "JSON can't hold a date value",
        ),
    ],
    ids=[
        "json-trailing-comma",
        "json-bare-word",
        "json-escape",
        "yaml-flow",
        "yaml-quote",
        "yaml-alias",
        "yaml-date",
    ],
)
def test_every_masked_failure_keeps_its_kind_but_no_value(
    text: str, source: str, target: str, message: str
) -> None:
    failure = _run_here(_job(text, source, target))[-1]
    assert isinstance(failure, FailureEvent)
    assert failure.message.startswith(message)
    _assert_clean(failure, ("hunter22",))


def test_a_repeated_key_that_looks_personal_is_masked_too() -> None:
    text = "pat@example.com: 1\npat@example.com: 2\n"
    failure = _run_here(_job(text, "yaml"))[-1]
    assert isinstance(failure, FailureEvent)
    assert failure.problems == (Problem("yaml_duplicate", 2, MASK),)
    assert failure.message == f'duplicate_key "{MASK}" at line 2, column 1'
    _assert_clean(failure)


def test_input_error_details_keep_only_what_holds_no_value() -> None:
    error = InputError(
        "bad_escape",
        lineno=1,
        colno=9,
        details={"escape": "\\uD800", "offset": 8, "text": "hunter22"},
    )
    cleaned = worker_module._masked_error(error)
    assert (cleaned.kind, cleaned.lineno, cleaned.colno) == ("bad_escape", 1, 9)
    assert cleaned.details == {"escape": "\\uD800", "offset": 8}


def test_a_write_failure_keeps_its_path(tmp_path: Path) -> None:
    missing = tmp_path / "gone" / "out.json"
    job = _job(_JSON, "json", temp_path=str(missing))
    failure = _run_here(job)[-1]
    assert isinstance(failure, FailureEvent)
    assert "out.json" in failure.message
    _assert_clean(failure)


# Deep YAML: past 256 levels Format walks parser events, not ruamel's loader

_DEEP_LEAF = "{email: pat@example.com, name: Pat Example, note: keep me}"


def _deep_yaml(depth: int) -> str:
    return "{a: " * depth + _DEEP_LEAF + "}" * depth + "\n"


@pytest.mark.parametrize("depth", [300, 500])
@pytest.mark.parametrize("target", ["yaml", "json"])
@pytest.mark.parametrize("engine", ["pure", "c"])
def test_deep_yaml_is_masked_on_the_event_path(
    monkeypatch, engine: str, target: str, depth: int
) -> None:
    if engine == "c":
        _needs_clib()
    else:
        _pure_engine(monkeypatch)
    walked: list[bool] = []
    real = format_module._yaml_from_events

    def spy(text: str | bytes, *, pure: bool = False) -> object:
        walked.append(pure)
        return real(text, pure=pure)

    monkeypatch.setattr(format_module, "_yaml_from_events", spy)
    success = _run_here(_job(_deep_yaml(depth), "yaml", target))[-1]
    assert isinstance(success, SuccessEvent)
    assert walked, "the deep path did not run"
    assert success.masked_count == 2
    assert "pat@example.com" not in success.text
    assert "Pat Example" not in success.text
    assert "keep me" in success.text
    assert success.text.count(MASK) == 2


def test_the_json_character_walk_is_masked_too(monkeypatch) -> None:
    # The walk runs when the fast scan needs a line and column. Force it.
    monkeypatch.setattr(
        format_module,
        "_json_fast_value",
        lambda _source: format_module._CHARACTER_WALK,
    )
    masked = mask_document(_JSON, "json", "json")
    assert masked.masked_count == _JSON_COUNT
    _assert_hidden(masked.text)


# Cancel


def test_cancel_mid_write_leaves_only_masked_bytes(tmp_path: Path) -> None:
    destination = tmp_path / "out.json"
    destination.write_bytes(b"{}\n")
    job = _job(
        _JSON,
        "json",
        destination=str(destination),
        chunk_size=16,
        chunk_pause_s=0.2,
    )
    ctx = multiprocessing.get_context("spawn")
    jobs = ctx.Queue()
    events = ctx.Queue()
    process = ctx.Process(target=run_worker, args=(jobs, events))
    process.start()
    try:
        jobs.put(job)
        temp_path = _first_write(events, process)
        # Cancel kills the worker and the parent deletes the temp.
        process.kill()
        process.join(timeout=10)
        assert process.exitcode is not None and process.exitcode != 0
        partial = Path(temp_path).read_bytes()
        expected = mask_document(_JSON, "json", "json").text.encode("utf-8")
        assert 0 < len(partial) < len(expected)
        assert expected.startswith(partial)
        unlink_temp(temp_path)
        assert not Path(temp_path).exists()
        assert destination.read_bytes() == b"{}\n"
    finally:
        _close(process, jobs, events)


# Size bands: masking adds no limit of its own, so a busy-size document
# still formats in the worker and is masked whole.


@pytest.mark.parametrize(
    ("source", "limit"), [("json", JSON_BUSY_BYTES), ("yaml", YAML_BUSY_BYTES)]
)
def test_a_busy_size_document_is_masked_whole(source: str, limit: int) -> None:
    text, rows = _rows_over(source, limit)
    success = _run_in_child([_job(text, source)], timeout=120.0)[-1]
    assert isinstance(success, SuccessEvent)
    assert success.masked_count == 3 * rows
    for index in (0, rows // 2, rows - 1):
        assert f"user{index}@example.com" not in success.text
    assert line_count(success.text) == line_count(format_document(text, source, source))


def _rows_over(source: str, limit: int) -> tuple[str, int]:
    row = {
        "id": 0,
        "email": "user0@example.com",
        "phone": "555-0100",
        "address": {"street": "1 Example Street"},
        "tags": ["a", "b"],
    }
    one = len(format_document(json.dumps([row]), "json", source).encode("utf-8"))
    rows = limit // one + 1
    while True:
        value = [
            {**row, "id": index, "email": f"user{index}@example.com"}
            for index in range(rows)
        ]
        text = format_document(json.dumps(value), "json", source)
        if len(text.encode("utf-8")) > limit:
            return text, rows
        rows += rows // 10 + 1


# Sort keys


_PAIRS = pytest.mark.parametrize(
    ("text", "source", "target"),
    [
        (_JSON, "json", "json"),
        (_JSON, "json", "yaml"),
        (_YAML, "yaml", "yaml"),
        (_YAML, "yaml", "json"),
    ],
    ids=["json-json", "json-yaml", "yaml-yaml", "yaml-json"],
)


@_PAIRS
def test_sort_keys_orders_the_masked_output(
    text: str, source: str, target: str
) -> None:
    unsorted = _run_here(_job(text, source, target))[-1]
    ordered = _run_here(_job(text, source, target, sort_keys=True))[-1]
    assert isinstance(unsorted, SuccessEvent)
    assert isinstance(ordered, SuccessEvent)
    plain_sorted = format_document(text, source, target, sort_keys=True)
    assert _key_lines(ordered.text) == _key_lines(plain_sorted)
    assert ordered.text != unsorted.text
    assert ordered.masked_count == unsorted.masked_count
    assert line_count(ordered.text) == line_count(plain_sorted)
    _assert_hidden(ordered.text)


@_PAIRS
def test_sort_keys_off_leaves_the_masked_order_alone(
    text: str, source: str, target: str
) -> None:
    unsorted = _run_here(_job(text, source, target))[-1]
    assert isinstance(unsorted, SuccessEvent)
    assert _key_lines(unsorted.text) == _key_lines(
        format_document(text, source, target)
    )
    assert unsorted.text == mask_document(text, source, target).text


def test_a_spawned_worker_masks_and_sorts_in_one_job() -> None:
    job = _job(_JSON, "json", sort_keys=True)
    assert pickle.loads(pickle.dumps(job)) == job
    success = _run_in_child([job])[-1]
    assert isinstance(success, SuccessEvent)
    value = json.loads(success.text)
    assert list(value) == sorted(value)
    assert list(value["address"]) == ["lines", "street"]
    assert success.masked_count == _JSON_COUNT
    _assert_hidden(success.text)


# YAML engines: the C parser and pure Python give the same masked text


_ALIASED_SCALARS = """name: &n Pat Example
owner: *n
label: &l keep me
copy: *l
"""


def _pure_engine(patch: pytest.MonkeyPatch) -> None:
    """Hide the C parser and emitter, as ``test_yaml_engine_parity`` does."""
    patch.setattr(ruamel_main, "CParser", None)
    patch.setattr(ruamel_main, "CEmitter", None)
    patch.setattr(format_module, "_CLIB_READY", False)


def _needs_clib() -> None:
    if not format_module._clib_available():
        pytest.skip("ruamel.yaml.clib is not installed")


@pytest.mark.parametrize(
    "text", [_YAML, _PLAIN_YAML, _ALIASED_SCALARS], ids=["yaml", "plain", "aliases"]
)
@pytest.mark.parametrize("target", ["yaml", "json"])
@pytest.mark.parametrize("sort_keys", [False, True], ids=["unsorted", "sorted"])
def test_masked_yaml_is_the_same_with_either_engine(
    text: str, target: str, sort_keys: bool
) -> None:
    _needs_clib()
    with_c = mask_document(text, "yaml", target, sort_keys=sort_keys)
    with pytest.MonkeyPatch.context() as patch:
        _pure_engine(patch)
        assert not format_module._clib_available()
        with_pure = mask_document(text, "yaml", target, sort_keys=sort_keys)
    assert with_pure == with_c


@pytest.mark.parametrize("engine", ["c", "pure"])
def test_an_alias_of_a_masked_scalar_is_masked(monkeypatch, engine: str) -> None:
    if engine == "c":
        _needs_clib()
    else:
        _pure_engine(monkeypatch)
    success = _run_here(_job(_ALIASED_SCALARS, "yaml", "json"))[-1]
    assert isinstance(success, SuccessEvent)
    assert json.loads(success.text) == {
        "name": MASK,
        "owner": MASK,
        "label": "keep me",
        "copy": "keep me",
    }
    assert "Pat Example" not in success.text


@pytest.mark.parametrize(
    ("text", "pure"),
    [(format_document(_JSON, "json", "yaml"), [False]), (_ALIASED_SCALARS, [True])],
    ids=["no-alias", "alias"],
)
def test_masking_keeps_the_c_parser_unless_there_is_an_alias(
    monkeypatch, text: str, pure: list[bool]
) -> None:
    # The C parser gives each alias of a scalar its own string, so masking
    # parses a document with an alias in pure Python. Others keep the C speed.
    _needs_clib()
    loads: list[bool] = []
    real = format_module._load_parsed

    def spy(source: object, opened: object, *, pure: bool) -> object:
        loads.append(pure)
        return real(source, opened, pure=pure)

    monkeypatch.setattr(format_module, "_load_parsed", spy)
    mask_document(text, "yaml", "yaml")
    assert loads == pure


def test_a_format_that_does_not_load_to_a_value_is_never_shown(monkeypatch) -> None:
    real = mask.format_named

    def no_value(name: str) -> object:
        return dataclasses.replace(real(name), loads_to_value=False, label="XML")

    monkeypatch.setattr(mask, "format_named", no_value)
    with pytest.raises(MaskError, match="XML"):
        mask_document(_JSON, "json", "json")
    assert _run_here(_job(_JSON, "json"))[-1] == FailureEvent(
        1, MASK_FAILURE_MESSAGE, None
    )


# Perf budgets (``uv run pytest -m perf``), Isaac's on #107. Each is the
# #104 Convert budget at that size plus 0.2 s of masking per MB, timed from
# the job going in to its result coming out of a warm worker. macOS and
# Windows use the same numbers.
_JOB_JSON_BUDGET_S = 0.7
# #112 writes YAML through ruamel's pure-Python emitter on every platform so
# both engines give identical bytes. The team chose to accept the slowdown
# (#115 tracks making the C emitter match), so YAML gets 1 s until #115
# lands. This budget is Isaac's 1.1 s: that 1 s plus about 0.05 s of masking
# (192 KB at 0.2 s per MB), rounded up. It drops to 0.6 s when #115 restores
# the 0.5 s YAML budget.
_JOB_YAML_BUDGET_S = 1.1
# 0.8 s to parse and format the line, plus 0.32 s of masking.
_JOB_ONE_LINE_BUDGET_S = 1.2
# The mask step alone on 1 MB with the default keys (#66).
_MASK_TREE_BUDGET_S = 0.2


@pytest.mark.perf
@pytest.mark.parametrize(
    ("label", "render", "size", "source", "budget"),
    [
        ("1 MB JSON", pretty_json, ONE_MB, "json", _JOB_JSON_BUDGET_S),
        ("192 KB YAML", yaml_text, YAML_BUSY_BYTES, "yaml", _JOB_YAML_BUDGET_S),
        (
            "1.6 MB minified JSON line",
            one_line,
            ONE_LINE_CHARS,
            "json",
            _JOB_ONE_LINE_BUDGET_S,
        ),
    ],
    ids=["json-1mb", "yaml-192kb", "json-one-line"],
)
def test_a_masked_format_job_is_within_budget(
    perf_budget, label: str, render, size: int, source: str, budget: float
) -> None:
    text, rows = text_of_size(size, render)
    assert size <= len(text.encode("utf-8")) < size * 11 // 10
    if render is one_line:
        assert "\n" not in text
    job = _job(text, source)
    with _WarmWorker() as worker:
        success = worker.run(job)
        assert isinstance(success, SuccessEvent)
        assert success.masked_count == MASKED_PER_ROW * rows
        assert "user0@example.com" not in success.text
        perf_budget.within(
            budget, lambda: worker.run(job), fixture=f"{label} to {source}, masked"
        )


@pytest.mark.perf
def test_masking_1_mb_with_the_default_keys_is_within_budget(perf_budget) -> None:
    text, rows = text_of_size(ONE_MB, pretty_json)
    value = json.loads(text)
    assert mask_tree(value).masked_count == MASKED_PER_ROW * rows
    perf_budget.within(
        _MASK_TREE_BUDGET_S,
        lambda: mask_tree(value),
        fixture="1 MB JSON, default keys, mask_tree only",
    )


# Helpers


class _WarmWorker:
    """A spawned worker kept running, so a timed job pays no start-up."""

    def __enter__(self) -> "_WarmWorker":
        ctx = multiprocessing.get_context("spawn")
        self.jobs = ctx.Queue()
        self.events = ctx.Queue()
        self.process = ctx.Process(target=run_worker, args=(self.jobs, self.events))
        self.process.start()
        return self

    def run(self, job: FormatJob, timeout: float = 60.0) -> object:
        """Send ``job`` and return its success or failure event."""
        self.jobs.put(job)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                event = self.events.get(timeout=0.2)
            except Empty:
                assert self.process.exitcode is None, self.process.exitcode
                continue
            if isinstance(event, (SuccessEvent, FailureEvent)):
                return event
        raise AssertionError("worker timed out")

    def __exit__(self, *_exc: object) -> None:
        self.jobs.put(None)
        self.process.join(timeout=10)
        _close(self.process, self.jobs, self.events)


def _key_lines(text: str) -> list[tuple[int, str | None]]:
    """Each line's indent and key, so values can differ and keys cannot."""
    shape: list[tuple[int, str | None]] = []
    for line in text.splitlines():
        found = _KEY_RE.match(line)
        indent = len(line) - len(line.lstrip())
        shape.append((indent, found.group(1) if found else None))
    return shape


def _error_fields(event: FailureEvent) -> tuple[object, ...] | None:
    error = event.error
    if error is None:
        return None
    return (error.kind, error.lineno, error.colno, error.details)


def _first_write(events, process) -> str:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            event = events.get(timeout=0.2)
        except Empty:
            assert process.exitcode is None
            continue
        assert not isinstance(event, (SuccessEvent, FailureEvent))
        if event.phase == "write" and event.written > 0:
            return event.temp_path
    raise AssertionError("no write progress")


def _run_in_child(jobs_to_run: list[FormatJob], timeout: float = 30.0) -> list[object]:
    ctx = multiprocessing.get_context("spawn")
    jobs = ctx.Queue()
    events = ctx.Queue()
    process = ctx.Process(target=run_worker, args=(jobs, events))
    process.start()
    collected: list[object] = []
    try:
        for job in jobs_to_run:
            jobs.put(job)
        done = 0
        deadline = time.monotonic() + timeout
        while done < len(jobs_to_run):
            if time.monotonic() > deadline:
                raise AssertionError("worker timed out")
            try:
                event = events.get(timeout=0.2)
            except Empty:
                assert process.exitcode is None, process.exitcode
                continue
            collected.append(event)
            if isinstance(event, (SuccessEvent, FailureEvent)):
                done += 1
        jobs.put(None)
        process.join(timeout=10)
        assert process.exitcode == 0, process.exitcode
        return collected
    finally:
        _close(process, jobs, events)


def _close(process, jobs, events) -> None:
    if process.exitcode is None and process.pid is not None:
        process.kill()
        process.join(timeout=5)
    for item in (jobs, events):
        item.cancel_join_thread()
        item.close()
