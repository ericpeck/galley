"""Compare output stays byte-identical across diff speed-ups (#129).

``data/diff_identity.json`` records, for every corpus pair, a SHA-256 prefix
of what ``load_document``, ``diff_with_pairs``, ``build_report``, and
``run_compare`` give: the spans of both documents in insertion order, the
``DiffResult`` and the container pairs, the report, and the whole compare
event with and without ``reformat``. It was written from ``main`` before
any #129 change: first at ``c13cd80``, then again at ``d31bad7``, because
#130 changed the report wording (only the report and job digests moved).
#146 regenerated it once more for Moved's spoken "now at"; only the report
and job digests of the pairs with a move changed.

The corpus is generated here from fixed seeds: random nested documents with
array inserts, deletes, replacements, and moves; repetitive arrays that fall
back; JSON written indented and compact; YAML with anchors, merge keys, and
block scalars; and the #108 perf pairs.

Regenerate only for an output change a reviewer has agreed to::

    GALLEY_WRITE_DIFF_IDENTITY=1 uv run pytest tests/core/test_diff_identity.py
"""

import hashlib
import json
import os
import random
from datetime import date
from pathlib import Path

import pytest
from tests.core.test_compare_report import (
    _aligned_pair,
    _keys_changed_pair,
    _large_pair,
    _reordered_pair,
)

from galley.core.compare_report import build_report
from galley.core.diff import Doc, Span, _segment, diff_with_pairs, load_document
from galley.core.format import _dump_yaml_value
from galley.core.worker import CompareJob, run_compare

_GOLDEN = Path(__file__).parent / "data" / "diff_identity.json"
_KEYS_GOLDEN = Path(__file__).parent / "data" / "diff_identity_keys.json"
_WRITE = os.environ.get("GALLEY_WRITE_DIFF_IDENTITY") == "1"
_DIGEST_CHARS = 24
_RANDOM_PAIRS = 300

_KEYS = ("a", "b", "c", "id", "a.b", "0", "名前", 'q"uote')
_SCALARS = (None, True, False, 0, 1, -2, 1.0, 1.5, "", "x", "1", "true", "é")

Pair = tuple[str, str, str]


def _value(rng: random.Random, depth: int) -> object:
    roll = rng.random()
    if depth >= 4 or roll < 0.45:
        return rng.choice(_SCALARS)
    if roll < 0.72:
        return [_value(rng, depth + 1) for _ in range(rng.randint(0, 6))]
    return {rng.choice(_KEYS): _value(rng, depth + 1) for _ in range(rng.randint(0, 5))}


def _edited(rng: random.Random, value: object, depth: int = 0) -> object:
    """A copy of ``value`` with a few random edits, so the sides overlap."""
    if isinstance(value, dict):
        result = {key: _edited(rng, child, depth + 1) for key, child in value.items()}
        if result and rng.random() < 0.2:
            del result[rng.choice(list(result))]
        if rng.random() < 0.2:
            result[rng.choice(_KEYS)] = _value(rng, depth + 1)
        return result
    if isinstance(value, list):
        result = [_edited(rng, child, depth + 1) for child in value]
        for _ in range(rng.randint(0, 2)):
            action = rng.random()
            if action < 0.4 or not result:
                result.insert(rng.randint(0, len(result)), _value(rng, depth + 1))
            elif action < 0.7:
                del result[rng.randrange(len(result))]
            else:
                result[rng.randrange(len(result))] = _value(rng, depth + 1)
        return result
    if rng.random() < 0.15:
        return rng.choice(_SCALARS)
    return value


def _moved(rng: random.Random) -> tuple[object, object]:
    subtree = {"id": rng.randint(0, 9), "tags": [rng.choice(_SCALARS)]}
    left = {"from": {"item": subtree}, "to": {}, "rest": _value(rng, 1)}
    right = {"from": {}, "to": {"item": subtree}, "rest": _value(rng, 1)}
    return left, right


def _repetitive(rng: random.Random) -> tuple[object, object]:
    size = rng.choice((40, 400, 1_600))
    items = [{"k": index % 3} for index in range(size)]
    edited = list(items)
    edited.insert(rng.randrange(size), {"k": "new"})
    edited[0] = {"k": "first"}
    if size == 1_600:
        # Over the alignment budget: the middle compares by position.
        return {"flags": [0, 1] * 800, "items": items}, {
            "flags": [1, 0] * 800,
            "items": edited,
        }
    return {"items": items}, {"items": edited}


_YAML_PAIRS: tuple[tuple[str, str], ...] = (
    (
        "base: &b\n  x: 1\n  y: [1, 2]\nm:\n  <<: *b\n  y: 3\ns: |\n  one\n  two\n",
        "base: &b\n  x: 1\n  y: [1, 2, 3]\nm:\n  <<: *b\n  y: 4\ns: |\n  one\n  2\n",
    ),
    (
        "users:\n  - name: Ada\n    role: user\n  - name: Bo\n    role: admin\n",
        "users:\n  - name: New\n  - name: Ada\n    role: admin\n  - name: Bo\n",
    ),
    ("a: .nan\nb: 2026-10-18\nc: !!binary aGk=\n", "a: .nan\nb: '2026-10-18'\nc: x\n"),
    ("flow: {a: [1, {b: 2}]}\nq: 'it''s'\n", "flow: {a: [1, {b: 3}], c: 4}\n"),
)


def _corpus() -> dict[str, Pair]:
    rng = random.Random(129)
    corpus: dict[str, Pair] = {}
    for index in range(_RANDOM_PAIRS):
        choice = index % 10
        if choice == 8:
            left, right = _moved(rng)
        elif choice == 9:
            left, right = _repetitive(rng)
        else:
            left = _value(rng, 0)
            right = _edited(rng, left)
        indent = 2 if index % 3 else None
        corpus[f"random/{index:03d}"] = (
            "json",
            json.dumps(left, indent=indent, ensure_ascii=index % 2 == 0),
            json.dumps(right, indent=indent, ensure_ascii=index % 2 == 0),
        )
        if index % 15 == 0:
            corpus[f"random-yaml/{index:03d}"] = (
                "yaml",
                _dump_yaml_value(left),
                _dump_yaml_value(right),
            )
    for index, (left_text, right_text) in enumerate(_YAML_PAIRS):
        corpus[f"yaml/{index}"] = ("yaml", left_text, right_text)
    for make in (_large_pair, _reordered_pair, _keys_changed_pair, _aligned_pair):
        left_text, right_text = make()
        corpus[f"perf/{make.__name__}"] = ("json", left_text, right_text)
    return corpus


def _digest(value: object) -> str:
    text = value if isinstance(value, str) else repr(value)
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[
        :_DIGEST_CHARS
    ]


def _record(name: str, pair: Pair) -> dict[str, str]:
    source, left_text, right_text = pair
    left = load_document(left_text, source)
    right = load_document(right_text, source)
    result, pairs = diff_with_pairs(left, right)
    record = {
        "input": _digest(left_text + "\0" + right_text),
        "spans": _digest((list(left.spans.items()), list(right.spans.items()))),
        # ``pairs`` comes from a set of paths, so its order follows the hash
        # seed; the pairs themselves are recorded.
        "diff": _digest((result, sorted(pairs.items(), key=repr))),
        "report": _digest(build_report(result, left, right, pairs)),
    }
    if not name.startswith("perf/_large"):
        for reformat in (False, True):
            job = CompareJob(1, left_text, source, right_text, source, 0.0, reformat)
            record[f"job reformat={reformat}"] = _digest(run_compare(job))
    return record


def test_compare_output_bytes_are_unchanged() -> None:
    actual = {name: _record(name, pair) for name, pair in _corpus().items()}
    if _WRITE:
        _GOLDEN.write_text(json.dumps(actual, indent=1) + "\n", encoding="utf-8")
        pytest.skip("wrote the diff identity golden")
    expected = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    assert actual.keys() == expected.keys()
    changed_inputs = [k for k in expected if actual[k]["input"] != expected[k]["input"]]
    assert not changed_inputs, "corpus input changed; the golden no longer applies"
    changed = {
        name: sorted(
            field for field in record if record[field] != expected[name][field]
        )
        for name, record in actual.items()
        if record != expected[name]
    }
    assert not changed


def test_the_1_5_mb_perf_pair_gives_mains_diff_and_pairs() -> None:
    """Erik's check for #129, on #108's 1.5 MB fixture.

    The ``DiffResult`` and the ``diff_with_pairs`` map must equal what
    ``main`` gave before the speed-up. The stored digest is the corpus
    golden's ``perf/_large_pair`` entry, written from ``main``'s code.
    """
    expected = json.loads(_GOLDEN.read_text(encoding="utf-8"))["perf/_large_pair"]
    left_text, right_text = _large_pair()
    left = load_document(left_text, "json")
    right = load_document(right_text, "json")
    result, pairs = diff_with_pairs(left, right)
    assert result.unaligned == (("flags",),)
    assert _digest((result, sorted(pairs.items(), key=repr))) == expected["diff"]
    assert _digest(build_report(result, left, right, pairs)) == expected["report"]


# --- Non-string keys ------------------------------------------------------

_WHEN = date(2026, 10, 18)


def _reversed_spans(value: object) -> dict[tuple[object, ...], Span]:
    """Fake spans at the paths the diff builds, last member first.

    Every path gets a span, keyed by the diff's own segment text, and each
    object's members start in reverse dict order, so the walk has to look
    every span up and sort the members.
    """
    spans: dict[tuple[object, ...], Span] = {}
    line = 1000
    stack: list[tuple[tuple[object, ...], object]] = [((), value)]
    while stack:
        path, node = stack.pop()
        spans[path] = Span(line, 1, line, 9)
        line -= 1
        # Pushed in order, so the first member pops first and gets the
        # highest line: members start in reverse dict order.
        if isinstance(node, dict):
            stack.extend(
                ((*path, _segment(key)), child) for key, child in reversed(node.items())
            )
        elif isinstance(node, list):
            stack.extend(
                ((*path, index), child)
                for index, child in reversed(list(enumerate(node)))
            )
    return spans


def _non_string_key_docs() -> dict[str, tuple[Doc, Doc]]:
    """Small flat dicts keyed by an int, None, and a date (fake data)."""
    left_value = {
        "flat": {1: "one", None: "nothing", _WHEN: "launch", "x": 1.5},
        "rows": [{2: True, None: None}, {_WHEN: [1, 2]}],
        "gone": {None: 0},
    }
    right_value = {
        "flat": {1: "uno", None: "nothing", _WHEN: "launch day", "y": 2},
        "rows": [{2: False}, {_WHEN: [1, 2, 3]}, {None: "new"}],
        "here": {1: 0},
    }
    yaml_left = "1: one\nnull: nothing\n2026-10-18: launch\nx: 1.5\n"
    yaml_right = "1: uno\nnull: nothing\n2026-10-18: launch day\ny: 2\n"
    return {
        "hand-built json": (
            Doc(left_value, "json", _reversed_spans(left_value)),
            Doc(right_value, "json", _reversed_spans(right_value)),
        ),
        "hand-built, no spans": (
            Doc(left_value, "json"),
            Doc(right_value, "json"),
        ),
        "yaml": (
            load_document(yaml_left, "yaml"),
            load_document(yaml_right, "yaml"),
        ),
    }


def test_non_string_keys_give_mains_diff_pairs_and_report() -> None:
    """Small flat dicts with int, None, and date keys, through every layer.

    The hand-built documents keep the raw keys, so the walk, the span
    lookups, the member sort, and the summaries all see non-string keys;
    the summary fast path must decline them. The YAML document's keys reach
    the diff as text. ``data/diff_identity_keys.json`` was written from
    ``main``'s code at ``f79164f``, before the #129 speed-up, with the same
    ``GALLEY_WRITE_DIFF_IDENTITY=1`` switch.
    """
    actual = {}
    for name, (left, right) in _non_string_key_docs().items():
        result, pairs = diff_with_pairs(left, right)
        assert result.changes
        actual[name] = {
            "diff": _digest((result, sorted(pairs.items(), key=repr))),
            "report": _digest(build_report(result, left, right, pairs)),
        }
    if _WRITE:
        _KEYS_GOLDEN.write_text(json.dumps(actual, indent=1) + "\n", encoding="utf-8")
        pytest.skip("wrote the non-string key golden")
    assert actual == json.loads(_KEYS_GOLDEN.read_text(encoding="utf-8"))
