"""Copy As text from an Output text. All data here is fake."""

import json
import pickle
import queue
import random

import pytest
from ruamel.yaml import YAML
from tests.core.mask_rows import MASKED_PER_ROW, ONE_MB, pretty_json, text_of_size

from galley.core.format import format_document
from galley.core.mask import (
    MASK,
    CopyAsError,
    CopyAsResult,
    MaskRules,
    copy_as_text,
)
from galley.core.worker import (
    CopyAsEvent,
    CopyAsFailureEvent,
    CopyAsJob,
    run_copy_as,
    run_worker,
)

SOURCE = """{
  "id": 4021,
  "name": "Pat Example",
  "email": "pat.example@example.com",
  "profile": {
    "phone_number": "+1 (555) 010-0199",
    "address": {"street": "100 Example Street", "unit": 7, "geo": [12.75, -3.5]},
    "verified": true
  },
  "contacts": [
    {"label": "work", "workEmail": "work.desk@example.com"},
    {"label": "cell", "mobile": "555-0142"}
  ],
  "api_key": "fake-not-a-real-token",
  "rows": [["Ada Example", "ada@example.com"]],
  "tags": ["alpha", "beta"],
  "notes": "write to the desk"
}"""

# Every fake value the masked keys and email values hold.
HIDDEN = (
    "Pat Example",
    "pat.example@example.com",
    "+1 (555) 010-0199",
    "100 Example Street",
    "work.desk@example.com",
    "555-0142",
    "fake-not-a-real-token",
    "ada@example.com",
)
KEPT = ("alpha", "beta", "write to the desk", "work", "cell", "Ada Example")
# name, email, phone_number, street, unit, geo x2, workEmail, mobile, api_key,
# and the email in rows.
HIDDEN_COUNT = 11


def _output(fmt: str) -> str:
    return format_document(SOURCE, "json", fmt)


def _load(text: str, fmt: str) -> object:
    if fmt == "json":
        return json.loads(text)
    return YAML(typ="safe", pure=True).load(text)


def _shape(value: object) -> object:
    """Keys and container types, with every scalar replaced by its type."""
    if isinstance(value, dict):
        return {key: _shape(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_shape(child) for child in value]
    return type(value).__name__


def test_original_is_the_text_as_it_is() -> None:
    text = "{ not even valid"
    result = copy_as_text(text, "JSON", "original")
    assert result == CopyAsResult("original", text, 0)


@pytest.mark.parametrize("fmt", ["json", "yaml"])
def test_masked_hides_every_matched_value_and_keeps_the_rest(fmt: str) -> None:
    output = _output(fmt)
    result = copy_as_text(output, fmt.upper(), "masked")
    assert result.mode == "masked"
    assert result.count == HIDDEN_COUNT
    for hidden in HIDDEN:
        assert hidden not in result.text
    for kept in KEPT:
        assert kept in result.text
    copied = _load(result.text, fmt)
    assert isinstance(copied, dict)
    assert copied["email"] == MASK
    assert copied["profile"]["address"] == {
        "street": MASK,
        "unit": MASK,
        "geo": [MASK, MASK],
    }
    assert copied["profile"]["verified"] is True
    assert copied["rows"] == [["Ada Example", MASK]]
    assert result.text.count("\n") == output.count("\n")


@pytest.mark.parametrize("fmt", ["json", "yaml"])
def test_random_fake_keeps_types_and_structure(fmt: str) -> None:
    output = _output(fmt)
    result = copy_as_text(output, fmt, "fake", rng=random.Random(7))
    assert result.mode == "fake"
    assert result.count == HIDDEN_COUNT
    for hidden in HIDDEN:
        assert hidden not in result.text
    assert MASK not in result.text
    copied = _load(result.text, fmt)
    assert _shape(copied) == _shape(_load(output, fmt))
    assert isinstance(copied, dict)
    assert copied["email"].endswith("@example.com")
    assert copied["tags"] == ["alpha", "beta"]
    assert result.text.count("\n") == output.count("\n")


def test_random_fake_is_new_on_every_copy_and_seeded_runs_repeat() -> None:
    output = _output("json")
    first = copy_as_text(output, "json", "fake")
    second = copy_as_text(output, "json", "fake")
    assert first.text != second.text
    seeded = [
        copy_as_text(output, "json", "fake", rng=random.Random(3)).text
        for _ in range(2)
    ]
    assert seeded[0] == seeded[1]


@pytest.mark.parametrize(
    ("fragment", "fmt"),
    [
        ('{"street": "100 Example Street", "unit": 7}', "json"),
        ("street: 100 Example Street\nunit: 7\n", "yaml"),
    ],
)
def test_a_selected_value_copies_on_its_own(fragment: str, fmt: str) -> None:
    result = copy_as_text(fragment, fmt, "masked", MaskRules(keys=("street",)))
    assert _load(result.text, fmt) == {"street": MASK, "unit": 7}
    assert result.count == 1


def test_yaml_alias_to_a_masked_value_is_masked_too() -> None:
    text = "primary: &mail pat@example.com\nbackup: *mail\nlabel: home\n"
    result = copy_as_text(text, "yaml", "masked")
    assert "pat@example.com" not in result.text
    assert _load(result.text, "yaml") == {
        "primary": MASK,
        "backup": MASK,
        "label": "home",
    }


@pytest.mark.parametrize(
    ("text", "fmt"),
    [
        ('"email": "pat@example.com"', "json"),
        ('{"email": "pat@example.com",', "json"),
        ("email: [pat@example.com\n", "yaml"),
    ],
)
@pytest.mark.parametrize("mode", ["masked", "fake"])
def test_text_that_does_not_load_is_invalid(text: str, fmt: str, mode) -> None:
    with pytest.raises(CopyAsError) as caught:
        copy_as_text(text, fmt, mode)
    assert caught.value.invalid
    assert caught.value.mode == mode
    assert "pat@example.com" not in str(caught.value)


@pytest.mark.parametrize("mode", ["masked", "fake"])
def test_a_masking_failure_copies_nothing(mode) -> None:
    text = "email: !!pairs\n- a: pat@example.com\n"
    with pytest.raises(CopyAsError) as caught:
        copy_as_text(text, "yaml", mode)
    assert not caught.value.invalid
    assert caught.value.mode == mode
    assert "pat@example.com" not in str(caught.value)


def test_run_copy_as_reports_the_result_or_the_failure() -> None:
    output = _output("json")
    event = run_copy_as(CopyAsJob(4, output, "json", "masked"))
    assert isinstance(event, CopyAsEvent)
    assert event.job_id == 4
    assert event.result.count == HIDDEN_COUNT
    failed = run_copy_as(CopyAsJob(5, "{", "json", "fake"))
    assert isinstance(failed, CopyAsFailureEvent)
    assert failed == CopyAsFailureEvent(5, "fake", True)


def test_a_held_copy_as_job_still_builds_the_copy() -> None:
    held = run_copy_as(CopyAsJob(6, _output("json"), "json", "masked", hold_s=0.01))
    assert isinstance(held, CopyAsEvent)
    assert held.result == copy_as_text(_output("json"), "json", "masked")


@pytest.mark.parametrize("mode", ["masked", "fake"])
def test_the_error_names_the_mode_and_quotes_no_text(mode) -> None:
    text = "api_key: [fake-not-a-real-token\nemail: pat@example.com\n"
    with pytest.raises(CopyAsError) as caught:
        copy_as_text(text, "yaml", mode)
    error = caught.value
    assert error.mode == mode
    assert error.invalid
    assert str(error) == f"Copy As {mode}: the yaml text did not load (ParserError)"
    # The parser's own message quotes the line, so it stays only the cause.
    assert "fake-not-a-real-token" in str(error.__cause__)
    assert "pat@example.com" in str(error.__cause__)
    assert error.args == (str(error),)


def test_a_failure_event_carries_no_error_text() -> None:
    text = "api_key: [fake-not-a-real-token\nemail: pat@example.com\n"
    failed = run_copy_as(CopyAsJob(8, text, "yaml", "masked"))
    assert failed == CopyAsFailureEvent(8, "masked", True)
    sent = pickle.dumps(failed)
    assert b"fake-not-a-real-token" not in sent
    assert b"example.com" not in sent


def test_copy_as_jobs_and_events_pickle() -> None:
    job = CopyAsJob(
        1, _output("yaml"), "yaml", "fake", MaskRules(keys=("email",)), hold_s=0.5
    )
    assert pickle.loads(pickle.dumps(job)) == job
    event = run_copy_as(CopyAsJob(1, job.text, "yaml", "fake", job.rules))
    assert pickle.loads(pickle.dumps(event)) == event
    failure = CopyAsFailureEvent(2, "masked", True)
    assert pickle.loads(pickle.dumps(failure)) == failure


def test_the_worker_loop_runs_copy_as_jobs() -> None:
    jobs: queue.Queue = queue.Queue()
    events: queue.Queue = queue.Queue()
    jobs.put(CopyAsJob(9, _output("json"), "json", "masked"))
    jobs.put(None)
    run_worker(jobs, events)  # type: ignore[arg-type]
    event = events.get_nowait()
    assert isinstance(event, CopyAsEvent)
    assert event.job_id == 9
    assert "pat.example@example.com" not in event.result.text
    assert events.empty()


# Copy As › Masked on 1 MB (#66, Erik's status note of 2026-10-25). This
# times ``run_copy_as``, the step the worker runs for a Copy As job: load,
# mask and write. The queue transfer and the clipboard are not timed.
_COPY_AS_MASKED_BUDGET_S = 0.2


@pytest.mark.perf
def test_copy_as_masked_on_1_mb_is_within_budget(perf_budget) -> None:
    text, rows = text_of_size(ONE_MB, pretty_json)
    job = CopyAsJob(1, text, "json", "masked")
    event = run_copy_as(job)
    assert isinstance(event, CopyAsEvent)
    assert event.result.count == MASKED_PER_ROW * rows
    assert "user0@example.com" not in event.result.text
    perf_budget.within(
        _COPY_AS_MASKED_BUDGET_S,
        lambda: run_copy_as(job),
        fixture="1 MB JSON, Copy As Masked, run_copy_as",
    )
