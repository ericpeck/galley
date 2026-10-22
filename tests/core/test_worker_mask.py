"""Format jobs with masking on, run in-process and through a spawn child.

Every value here is fake: ``example.com`` addresses, 555-01xx phone
numbers and an all-zero Social Security number.
"""

import json
import multiprocessing
import pickle
import re
import time
from pathlib import Path
from queue import Empty

import pytest

from galley.app.limits import JSON_BUSY_BYTES, YAML_BUSY_BYTES
from galley.core import mask
from galley.core.folds import fold_ranges, line_count, pack_ranges
from galley.core.format import format_document
from galley.core.mask import MASK, MaskRules, mask_document
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
def test_nothing_to_mask_gives_todays_text_and_a_zero_count(
    text: str, source: str, target: str
) -> None:
    success = _run_here(_job(text, source, target))[-1]
    assert isinstance(success, SuccessEvent)
    assert success.text == format_document(text, source, target)
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
def test_input_errors_match_masking_off(text: str, source: str) -> None:
    masked = _run_here(_job(text, source))[-1]
    plain = _run_here(_job(text, source, rules=None))[-1]
    assert isinstance(masked, FailureEvent)
    assert isinstance(plain, FailureEvent)
    assert masked.message == plain.message
    assert masked.problems == plain.problems
    assert _error_fields(masked) == _error_fields(plain)


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


# Helpers


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
