"""Compare jobs in the worker, run in-process and through a spawn child."""

import multiprocessing
import pickle
import queue
import time
from dataclasses import replace

import pytest
import ruamel.yaml.main as ruamel_main

import galley.core.format as format_module
import galley.core.worker as worker_module
from galley.core.diff import diff, load_document
from galley.core.errors import InputError
from galley.core.format import ALIAS_EXPANSION
from galley.core.problems import collect_problems
from galley.core.worker import (
    CompareEvent,
    CompareFailureEvent,
    CompareJob,
    ProgressEvent,
    _execute_compare,
    not_comparable,
    run_compare,
    run_worker,
)

_LEFT = '{"name": "Ada", "orders": [{"total": 40}], "old": true}'
_RIGHT = "name: Ada\norders:\n  - total: 45\nnew: 1\n"
_BAD_ESCAPE = '{"a": "\\uD800"}'


def _job(
    left: str = _LEFT,
    left_format: str = "json",
    right: str = _RIGHT,
    right_format: str = "yaml",
) -> CompareJob:
    return CompareJob(
        job_id=7,
        left_text=left,
        left_format=left_format,
        right_text=right,
        right_format=right_format,
    )


def test_compare_job_pickles() -> None:
    job = _job()
    assert pickle.loads(pickle.dumps(job)) == job


def test_run_compare_matches_calling_diff_directly() -> None:
    expected = diff(load_document(_LEFT, "json"), load_document(_RIGHT, "yaml"))
    event = run_compare(_job())
    assert isinstance(event, CompareEvent)
    assert event.job_id == 7
    assert event.result == expected
    kinds = [change.kind for change in event.result.changes]
    assert sorted(kinds) == ["added", "changed", "removed"]


def test_compare_event_pickles_with_its_result() -> None:
    event = run_compare(_job())
    assert pickle.loads(pickle.dumps(event)) == event


def test_identical_sides_give_no_changes() -> None:
    event = run_compare(_job(right=_LEFT, right_format="json"))
    assert isinstance(event, CompareEvent)
    assert event.result.changes == []


def test_execute_compare_sends_a_read_event_then_the_result() -> None:
    events: queue.Queue = queue.Queue()
    _execute_compare(_job(), events)  # type: ignore[arg-type]
    read = events.get_nowait()
    assert read == ProgressEvent(7, "read", 0, 0, None)
    assert isinstance(events.get_nowait(), CompareEvent)
    assert events.empty()


@pytest.mark.parametrize("side", ["left", "right"])
def test_an_input_error_names_its_side(side: str) -> None:
    if side == "left":
        job = _job(left=_BAD_ESCAPE)
    else:
        job = _job(right=_BAD_ESCAPE, right_format="json")
    event = run_compare(job)
    assert isinstance(event, CompareFailureEvent)
    assert event.side == side
    error = event.error
    assert isinstance(error, InputError)
    assert (error.kind, error.lineno, error.colno) == ("bad_escape", 1, 8)
    assert pickle.loads(pickle.dumps(event)).error.details == error.details


def test_a_syntax_error_is_located_in_its_own_side() -> None:
    event = run_compare(_job(right='{\n  "a": 1,\n  "b" 2\n}', right_format="json"))
    assert isinstance(event, CompareFailureEvent)
    assert event.side == "right"
    assert event.error is None
    assert [problem.lineno for problem in event.problems] == [3]


def test_yaml_tabs_are_reported_before_loading() -> None:
    event = run_compare(_job(left="a:\n\tb: 1\n", left_format="yaml"))
    assert isinstance(event, CompareFailureEvent)
    assert event.side == "left"
    assert [problem.code for problem in event.problems] == ["yaml_tab"]


def test_the_left_side_is_checked_first() -> None:
    event = run_compare(_job(left=_BAD_ESCAPE, right=_BAD_ESCAPE, right_format="json"))
    assert isinstance(event, CompareFailureEvent)
    assert event.side == "left"


def test_a_spawned_worker_returns_the_diff_result() -> None:
    job = _job()
    events = _run_in_child(job)
    assert events[0] == ProgressEvent(7, "read", 0, 0, None)
    assert events[-1] == run_compare(job)


def test_a_spawned_worker_returns_the_report_and_formatted_texts() -> None:
    job = replace(_job(), reformat=True)
    events = _run_in_child(job)
    event = events[-1]
    assert isinstance(event, CompareEvent)
    assert event == run_compare(job)
    assert event.left_text is not None and event.right_text is not None
    assert event.report.count == len(event.result.changes) == 3


def _run_in_child(job: CompareJob, timeout: float = 30.0) -> list[object]:
    ctx = multiprocessing.get_context("spawn")
    jobs = ctx.Queue()
    events = ctx.Queue()
    process = ctx.Process(target=run_worker, args=(jobs, events))
    process.start()
    collected: list[object] = []
    try:
        jobs.put(job)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                event = events.get(timeout=0.2)
            except queue.Empty:
                continue
            collected.append(event)
            if isinstance(event, (CompareEvent, CompareFailureEvent)):
                break
        jobs.put(None)
        process.join(timeout=10)
        assert process.exitcode == 0
    finally:
        if process.exitcode is None and process.pid is not None:
            process.kill()
            process.join(timeout=5)
        for item in (jobs, events):
            item.cancel_join_thread()
            item.close()
    return collected


def test_not_comparable_names_the_formats_compare_refuses() -> None:
    """Compare's Open refuses a file with the worker's own words."""
    assert not_comparable("XML") == "XML can't be compared."
    assert not_comparable("HTML") == "HTML can't be compared."
    assert not_comparable("JSON") is None
    assert not_comparable("YAML") is None
    # An unknown name is left for loading to report.
    assert not_comparable("TOML") is None


# --- YAML aliases (#232) ----------------------------------------------------
# Compare's diff walks every alias out in full, so it refuses YAML whose
# aliases repeat too many values, with Format's check and limits (#231).

_ALIAS_LIMIT = 100_000
_SMALL_YAML = "a0: [fake-note]\n"
_FEW_ALIASES = "base: &base [fake-note, fake-tag]\nfirst: *base\nsecond: *base\n"


@pytest.fixture(params=("c", "pure"))
def engine(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    if request.param == "c":
        if not format_module._clib_available():
            pytest.skip("ruamel.yaml.clib is not installed")
        return "c"
    monkeypatch.setattr(ruamel_main, "CParser", None)
    monkeypatch.setattr(ruamel_main, "CEmitter", None)
    monkeypatch.setattr(format_module, "_CLIB_READY", False)
    return "pure"


def _alias_chain(levels: int = 6, fan: int = 10) -> str:
    """Each level lists the one below ``fan`` times: ``fan**levels`` leaves."""
    lines = ['a0: &a0 ["fake-note"]']
    for level in range(1, levels + 1):
        refs = ", ".join([f"*a{level - 1}"] * fan)
        lines.append(f"a{level}: &a{level} [{refs}]")
    lines.append(f"root: *a{levels}")
    return "\n".join(lines) + "\n"


def _pairs_chain(levels: int = 6, fan: int = 10) -> str:
    """The same chain written as ``!!pairs``, which JSON writes as lists."""
    lines = ["a0: &a0 !!pairs [{k: fake-note}]"]
    for level in range(1, levels + 1):
        refs = ", ".join(f"{{k{i}: *a{level - 1}}}" for i in range(fan))
        lines.append(f"a{level}: &a{level} !!pairs [{refs}]")
    lines.append(f"root: *a{levels}")
    return "\n".join(lines) + "\n"


def _aliases_adding(values: int, distinct: int = 104) -> str:
    """YAML whose aliases, written out in full, add exactly ``values`` values.

    The same document as ``test_yaml_json_errors._aliases_adding``.
    """
    hundreds, ones = divmod(values, 100)
    items = ", ".join(["fake-note"] * 99)
    pad = ", ".join(["fake-pad"] * (distinct - 104))
    refs = ", ".join(["*h"] * hundreds + ["*e"] * ones)
    return f"h: &h [{items}]\ne: &e []\npad: [{pad}]\nrows: [{refs}]\n"


def _yaml_job(left: str, right: str, *, reformat: bool = False) -> CompareJob:
    return replace(_job(left, "yaml", right, "yaml"), reformat=reformat)


def _assert_alias_refusal(event: object, side: str) -> None:
    assert isinstance(event, CompareFailureEvent)
    assert event.side == side
    assert event.message == ALIAS_EXPANSION
    assert event.error is None
    assert event.problems == ()


def _spy_alias_checks(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Record the value each alias check in the worker is given."""
    checked: list[object] = []
    check = format_module._check_alias_expansion

    def recording(value: object, *args: object) -> None:
        checked.append(value)
        check(value, *args)

    monkeypatch.setattr(
        worker_module, "_check_alias_expansion", recording, raising=False
    )
    return checked


def test_a_yaml_alias_bomb_is_refused_on_each_engine(engine: str) -> None:
    _assert_alias_refusal(run_compare(_yaml_job(_alias_chain(), _SMALL_YAML)), "left")


@pytest.mark.parametrize("side", ["left", "right"])
def test_a_yaml_alias_bomb_names_its_side(side: str) -> None:
    bomb, small = _alias_chain(), _SMALL_YAML
    job = _yaml_job(bomb, small) if side == "left" else _yaml_job(small, bomb)
    _assert_alias_refusal(run_compare(job), side)


def test_a_yaml_alias_bomb_is_refused_when_compare_formats_first(
    engine: str,
) -> None:
    job = _yaml_job(_SMALL_YAML, _alias_chain(), reformat=True)
    _assert_alias_refusal(run_compare(job), "right")


def test_a_pairs_alias_bomb_is_refused(engine: str) -> None:
    _assert_alias_refusal(run_compare(_yaml_job(_pairs_chain(), _SMALL_YAML)), "left")


def test_the_alias_check_runs_once_per_yaml_side(
    engine: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    checked = _spy_alias_checks(monkeypatch)
    right = _FEW_ALIASES.replace("fake-tag", "fake-label")
    event = run_compare(_yaml_job(_FEW_ALIASES, right))
    assert isinstance(event, CompareEvent)
    assert len(checked) == 2
    assert checked[0] == load_document(_FEW_ALIASES, "yaml").value
    assert checked[1] == load_document(right, "yaml").value


def test_a_json_side_is_never_alias_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    checked = _spy_alias_checks(monkeypatch)
    # Both mention "*", which is all the YAML pre-check looks for.
    left = '{"note": "*fake-note*"}'
    event = run_compare(_job(left, "json", '{"note": "*fake-label*"}', "json"))
    assert isinstance(event, CompareEvent)
    assert checked == []


def test_yaml_with_a_few_aliases_still_diffs(engine: str) -> None:
    right = _FEW_ALIASES.replace("fake-tag", "fake-label")
    expected = diff(load_document(_FEW_ALIASES, "yaml"), load_document(right, "yaml"))
    event = run_compare(_yaml_job(_FEW_ALIASES, right))
    assert isinstance(event, CompareEvent)
    assert event.result == expected
    assert event.result.changes


def test_a_syntax_error_on_the_left_wins_over_a_bomb_on_the_right() -> None:
    event = run_compare(_yaml_job("a: [fake-note\n", _alias_chain()))
    assert isinstance(event, CompareFailureEvent)
    assert event.side == "left"
    assert event.message != ALIAS_EXPANSION


def test_aliases_that_add_the_most_allowed_are_still_compared() -> None:
    event = run_compare(_yaml_job(_aliases_adding(_ALIAS_LIMIT), _SMALL_YAML))
    assert isinstance(event, CompareEvent)


def test_diffs_yaml_loader_keeps_an_anchor_shared(engine: str) -> None:
    doc = load_document("a: &a [fake-note]\nb: *a\n", "yaml")
    assert isinstance(doc.value, dict)
    assert doc.value["b"] is doc.value["a"]


def test_a_valid_yaml_alias_bomb_has_no_problems() -> None:
    assert collect_problems(_alias_chain(), "yaml") == ()
