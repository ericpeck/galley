"""Validate runs Format's parse and checks, and writes nothing.

Parity: across the format corpus (``data/format_corpus.json``, valid and
invalid JSON and YAML) and the XML fixtures, a Validate job reports exactly
what a Format job of Input into its own format reports, problem by problem,
with each line and column. It runs once per YAML engine, like
``test_format_identity.py``.
"""

import json
from pathlib import Path
from queue import SimpleQueue

import pytest
from tests.core.test_format_identity import _corpus, engine  # noqa: F401

import galley.core.format as format_module
from galley.core.errors import MAX_DEPTH, InputError, JSONDuplicateKeyError
from galley.core.format import format_document, validate_document
from galley.core.worker import FailureEvent, FormatJob, SuccessEvent, _execute

_XML_DATA = Path(__file__).parent / "data" / "xml"

# One document per error kind Format reports, each with a place to point at.
_INVALID_JSON = (
    '{\n  "a": 1\n  "b": 2\n}\n',
    '{\n  "a": 1,\n}\n',
    '[1, 2\n',
    '{\n  "a": 1,\n  "a": 2,\n  "b": {"c": 1, "c": 2}\n}\n',
    '{"a": "\\ud800"}',
    '{"a": "\ud800"}',
    "[" * (MAX_DEPTH + 1) + "]" * (MAX_DEPTH + 1),
    '{"a": tru}',
    "",
    "   \n",
    '{"a": 1} {"b": 2}',
    "{'a': 1}",
)
_INVALID_YAML = (
    "a: 1\n  b: 2\n",
    "a: 1\na: 2\n",
    "a:\n\t- 1\n",
    "key: [1, 2\n",
    "- a\nb: c\n",
    '"unclosed\n',
    "a: &x 1\nb: *y\n",
    "a: !!binary not-base64!\n",
    "[" * (MAX_DEPTH + 1) + "]" * (MAX_DEPTH + 1),
    "a: 1\n---\nb: 2\n",
    "%YAML 1.1\n%YAML 1.1\n--- a\n",
)
_INVALID_XML = (
    "<r>\n  <a>\n  </b>\n</r>",
    "<r>\n  <a>\n",
    "<r>\n  a & b\n</r>",
    "<r>AT&T rocks</r>",
    '<!DOCTYPE r [<!ENTITY e "x">]><r>&e;</r>',
    "<r a='1' a='2'/>",
    "<a/><b/>",
    "",
    "<r>&undefined;</r>",
    "<p:r/>",
    "<r>" * (MAX_DEPTH + 1) + "</r>" * (MAX_DEPTH + 1),
)
_INVALID_UTF8 = (b'{"a": "\xff"}', b"a: \xc3\x28\n", b"<r>\xff</r>")


def _events(job: FormatJob) -> SuccessEvent | FailureEvent:
    queue: SimpleQueue = SimpleQueue()
    _execute(job, queue)
    last = None
    while not queue.empty():
        last = queue.get_nowait()
    assert isinstance(last, (SuccessEvent, FailureEvent))
    return last


def _report(event: SuccessEvent | FailureEvent) -> object:
    """What the window shows from a job: success, or every problem's place."""
    if isinstance(event, SuccessEvent):
        return "ok"
    error = event.error
    return (
        event.message,
        event.problems,
        None
        if error is None
        else (error.kind, error.lineno, error.colno, json.dumps(error.details)),
        event.not_json_value,
    )


def _format_and_validate(text: str, source: str) -> tuple[object, object]:
    formatted = _events(FormatJob(1, text, source, source))
    validated = _events(FormatJob(2, text, source, source, validate=True))
    return _report(formatted), _report(validated)


def _outcome(call) -> object:
    """The exception Format or Validate raised, with its place, or "ok"."""
    try:
        call()
    except InputError as exc:
        return ("InputError", exc.kind, exc.lineno, exc.colno, repr(exc.details))
    except json.JSONDecodeError as exc:
        return ("JSONDecodeError", exc.msg, exc.lineno, exc.colno)
    except JSONDuplicateKeyError as exc:
        return ("JSONDuplicateKeyError", exc.duplicates)
    except Exception as exc:
        problem = getattr(exc, "problem_mark", None)
        place = None if problem is None else (problem.line, problem.column)
        return (type(exc).__name__, str(exc), place)
    return "ok"


def _core_pair(text: str | bytes, source: str) -> tuple[object, object]:
    return (
        _outcome(lambda: format_document(text, source, source)),
        _outcome(lambda: validate_document(text, source)),
    )


def _xml_documents() -> dict[str, str]:
    documents = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(_XML_DATA.glob("*.xml"))
    }
    for index, text in enumerate(_INVALID_XML):
        documents[f"invalid#{index}"] = text
    return documents


# --- Parity with Format ---------------------------------------------------------


def test_validate_reports_what_format_reports_across_the_corpus(
    tmp_path: Path,
    engine: str,  # noqa: F811
) -> None:
    documents = _corpus(tmp_path)
    for index, text in enumerate((*_INVALID_JSON, *_INVALID_YAML)):
        documents[f"invalid#{index}"] = text
    differ: dict[str, tuple[object, object]] = {}
    failures = 0
    for key, text in documents.items():
        if not isinstance(text, str):
            continue
        for source in ("JSON", "YAML"):
            formatted, validated = _format_and_validate(text, source)
            failures += formatted != "ok"
            if formatted != validated:
                differ[f"{key} {source}"] = (formatted, validated)
    assert not differ
    assert failures > 100


def test_validate_raises_what_format_raises_across_the_corpus(
    tmp_path: Path,
    engine: str,  # noqa: F811
) -> None:
    """The core call too, bytes documents and invalid UTF-8 included."""
    documents: dict[str, str | bytes] = dict(_corpus(tmp_path))
    for index, text in enumerate((*_INVALID_JSON, *_INVALID_YAML, *_INVALID_UTF8)):
        documents[f"invalid#{index}"] = text
    differ = {}
    for key, text in documents.items():
        for source in ("json", "yaml"):
            formatted, validated = _core_pair(text, source)
            if formatted != validated:
                differ[f"{key} {source}"] = (formatted, validated)
    assert not differ


def test_validate_reports_what_format_reports_for_xml() -> None:
    differ = {}
    for key, text in _xml_documents().items():
        formatted, validated = _format_and_validate(text, "XML")
        if formatted != validated:
            differ[key] = (formatted, validated)
        core = _core_pair(text, "xml")
        if core[0] != core[1]:
            differ[f"{key} core"] = core
    assert not differ


@pytest.mark.parametrize(
    ("text", "source", "code", "line", "column"),
    [
        ('{\n  "a": 1\n  "b": 2\n}\n', "JSON", "missing_comma", 2, 9),
        ('{\n  "a": 1,\n  "a": 2\n}\n', "JSON", "json_duplicate", 3, 3),
        ("a: 1\na: 2\n", "YAML", "yaml_duplicate", 2, 1),
        ("<r>\n  <a>\n  </b>\n</r>", "XML", "xml_mismatched_tag", 3, 3),
    ],
)
def test_each_problem_keeps_its_line_and_column(
    text: str, source: str, code: str, line: int, column: int
) -> None:
    formatted, validated = _format_and_validate(text, source)
    assert formatted == validated
    event = _events(FormatJob(3, text, source, source, validate=True))
    assert isinstance(event, FailureEvent)
    problem = event.problems[0]
    assert (problem.code, problem.lineno, problem.colno) == (code, line, column)


def test_each_located_error_kind_keeps_its_place() -> None:
    """Errors that come as an ``InputError`` point at the same place."""
    cases = (
        ('{"a": "\\ud800"}', "json"),
        ("[" * (MAX_DEPTH + 1) + "]" * (MAX_DEPTH + 1), "json"),
        (_INVALID_UTF8[0], "json"),
        ("[" * (MAX_DEPTH + 1) + "]" * (MAX_DEPTH + 1), "yaml"),
        (_INVALID_UTF8[1], "yaml"),
        ("<r>" * (MAX_DEPTH + 1) + "</r>" * (MAX_DEPTH + 1), "xml"),
        (_INVALID_UTF8[2], "xml"),
    )
    for text, source in cases:
        formatted, validated = _core_pair(text, source)
        assert formatted[0] == "InputError", (text[:20], source)
        assert formatted == validated


# --- No output ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "source"),
    [
        ('{\n  "b": 1,\n  "a": [1, 2]\n}\n', "json"),
        ("b: 1\na:\n  - 1\n", "yaml"),
        ("<r><a/></r>", "xml"),
    ],
)
def test_valid_input_returns_none(text: str, source: str) -> None:
    assert validate_document(text, source) is None
    assert validate_document(text, source.upper()) is None


def test_validate_stops_before_the_output_step(monkeypatch) -> None:
    def no_output(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("Validate wrote output")

    for name in (
        "_dump_json",
        "_dump_yaml",
        "_dump_parsed",
        "_dump_block",
        "format_xml",
    ):
        monkeypatch.setattr(format_module, name, no_output)
    validate_document('{"a": [1, 2]}', "json")
    validate_document("a: [1, 2]\n", "yaml")
    # A tab sends YAML to the pure-Python parser, which also stops early.
    validate_document("a: 'x\ty'\n", "yaml")
    validate_document("<r><a/></r>", "xml")


def test_validate_takes_the_character_walk_when_format_does(monkeypatch) -> None:
    monkeypatch.setattr(
        format_module, "_json_fast_value", lambda source: format_module._CHARACTER_WALK
    )
    assert validate_document('{"a": [1, 2]}', "json") is None
    with pytest.raises(JSONDuplicateKeyError):
        validate_document('{"a": 1, "a": 2}', "json")


def test_xml_validate_calls_validate_xml(monkeypatch) -> None:
    seen: list[object] = []
    monkeypatch.setattr(format_module, "validate_xml", seen.append)
    validate_document("<r/>", "xml")
    assert seen == ["<r/>"]


@pytest.mark.parametrize("source", ["html", "HTML", "python", "csv"])
def test_validate_is_for_json_yaml_and_xml_only(source: str) -> None:
    with pytest.raises(ValueError):
        validate_document("<p>a</p>", source)


# --- Worker ---------------------------------------------------------------------


def test_a_validate_job_sends_no_text_and_no_folds() -> None:
    event = _events(
        FormatJob(7, '{\n  "a": [1, 2]\n}\n', "JSON", "JSON", validate=True)
    )
    assert isinstance(event, SuccessEvent)
    assert event.text == ""
    assert event.folds == b""
    assert event.temp_path is None


def test_a_validate_job_ignores_target_and_sort_keys(monkeypatch) -> None:
    """Validate reads Input in its own format, whatever the job's target says."""
    calls: list[tuple[object, ...]] = []

    def spy(text: object, source: str) -> None:
        calls.append((text, source))

    monkeypatch.setattr("galley.core.worker.validate_document", spy)
    monkeypatch.setattr(
        "galley.core.worker.format_document",
        lambda *_args, **_kwargs: pytest.fail("Validate formatted"),
    )
    job = FormatJob(8, "a: 1\n", "YAML", "JSON", sort_keys=True, validate=True)
    assert isinstance(_events(job), SuccessEvent)
    assert calls == [("a: 1\n", "YAML")]
