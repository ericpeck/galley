"""JSON and YAML output stays byte-identical across format work (#63).

Erik's stop rule for #63: if any JSON or YAML output byte changes, stop.
``data/format_identity.json`` records, for every corpus document, what
``format_document`` gave on each source and target pair (a SHA-256 prefix of
the output, or the error raised) and what ``detect_format`` sniffs from the
text. It was written from ``main`` before the format table existed.

The macOS leg runs with ``ruamel.yaml.clib`` and the Windows leg without it.
Some JSON-to-YAML and YAML-to-JSON outputs differ between those two on
``main``, so ``data/format_identity_pure.json`` holds the pure-Python value
for each field that differs.

The corpus is ``data/format_corpus.json``, a frozen copy of the document-like
string and bytes constants in the test suite, plus size-bench documents
generated here (plain JSON, plain YAML, and every realistic YAML profile).

Regenerate only for an output change a reviewer has agreed to, once with
``uv sync`` and once with ``uv sync --no-install-package ruamel.yaml.clib``
(then ``uv run --no-sync``)::

    GALLEY_WRITE_FORMAT_IDENTITY=1 uv run pytest tests/core/test_format_identity.py
"""

import base64
import hashlib
import json
import os
from pathlib import Path

import pytest
from benchmarks.size_bench import (
    JSON_FOOTER_PREFIX,
    JSON_FOOTER_SUFFIX,
    JSON_HEADER,
    JSON_SEPARATOR,
    YAML_FOOTER_PREFIX,
    YAML_FOOTER_SUFFIX,
    YAML_HEADER,
    YAML_SEPARATOR,
    _json_item,
    _write_document,
    _yaml_item,
    render_realistic_yaml,
)

from galley.core.errors import InputError
from galley.core.format import _clib_available, format_document
from galley.core.load import detect_format

_DATA = Path(__file__).parent / "data"
_CORPUS = _DATA / "format_corpus.json"
_GOLDEN = _DATA / "format_identity.json"
_GOLDEN_PURE = _DATA / "format_identity_pure.json"
_WRITE = os.environ.get("GALLEY_WRITE_FORMAT_IDENTITY") == "1"
_PAIRS = (("json", "json"), ("json", "yaml"), ("yaml", "json"), ("yaml", "yaml"))
_DIGEST_CHARS = 24
_BENCH_BYTES = 24_000
_UNORDERED_TAG = "!!set"
_REALISTIC_PROFILES = (
    "natural",
    "reparse",
    "clean",
    "bare_tag",
    "quoted_tab",
    "directive",
)

Records = dict[str, dict[str, str | None]]


def _digest(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8", "surrogatepass")
    return hashlib.sha256(data).hexdigest()[:_DIGEST_CHARS]


def _outcome(text: str | bytes, source: str, target: str) -> str:
    try:
        output = format_document(text, source, target)
    except InputError as exc:
        located = f"{exc.lineno}:{exc.colno}"
        details = json.dumps(exc.details, sort_keys=True, default=repr)
        return f"InputError {exc.kind} {located} {details}"
    except Exception as exc:
        return f"{type(exc).__name__} {_digest(str(exc))}"
    if isinstance(text, str) and _UNORDERED_TAG in text:
        # A YAML set comes out in string-hash order, which changes with
        # PYTHONHASHSEED on main too. Only its lines are compared.
        lines = "\n".join(sorted(output.splitlines()))
        return f"output-lines {_digest(lines)}"
    return f"output {_digest(output)}"


def _sniffed(text: str | bytes) -> str | None:
    if isinstance(text, bytes):
        return None
    return detect_format(Path("document.txt"), text)


def _record(text: str | bytes) -> dict[str, str | None]:
    return {
        "input": _digest(text),
        "detect": _sniffed(text),
        **{f"{s}>{t}": _outcome(text, s, t) for s, t in _PAIRS},
    }


def _bench_document(tmp: Path, name: str, **layout: object) -> str:
    path = tmp / name
    _write_document(path, _BENCH_BYTES, **layout)
    return path.read_text(encoding="utf-8")


def _corpus(tmp: Path) -> dict[str, str | bytes]:
    documents: dict[str, str | bytes] = {}
    for index, entry in enumerate(json.loads(_CORPUS.read_text(encoding="utf-8"))):
        key = f"{entry['from']}#{index}"
        if "bytes" in entry:
            documents[key] = base64.b64decode(entry["bytes"])
        else:
            documents[key] = entry["text"]
    documents["size_bench/json-plain"] = _bench_document(
        tmp,
        "plain.json",
        header=JSON_HEADER,
        separator=JSON_SEPARATOR,
        footer_prefix=JSON_FOOTER_PREFIX,
        footer_suffix=JSON_FOOTER_SUFFIX,
        render_item=_json_item,
    )
    documents["size_bench/yaml-plain"] = _bench_document(
        tmp,
        "plain.yaml",
        header=YAML_HEADER,
        separator=YAML_SEPARATOR,
        footer_prefix=YAML_FOOTER_PREFIX,
        footer_suffix=YAML_FOOTER_SUFFIX,
        render_item=_yaml_item,
    )
    for profile in _REALISTIC_PROFILES:
        documents[f"size_bench/yaml-realistic-{profile}"] = render_realistic_yaml(
            _BENCH_BYTES, profile=profile
        )
    return documents


def _load(path: Path) -> Records:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, records: Records) -> None:
    path.write_text(json.dumps(records, indent=1) + "\n", encoding="utf-8")


def _expected() -> Records:
    expected = _load(_GOLDEN)
    if not _clib_available():
        for key, fields in _load(_GOLDEN_PURE).items():
            expected[key] = {**expected[key], **fields}
    return expected


def _write_golden(actual: Records) -> None:
    if _clib_available():
        _write(_GOLDEN, actual)
        return
    base = _load(_GOLDEN)
    overrides = {
        key: {f: v for f, v in record.items() if base[key][f] != v}
        for key, record in actual.items()
        if record != base[key]
    }
    _write(_GOLDEN_PURE, overrides)


def test_json_and_yaml_output_bytes_are_unchanged(tmp_path: Path) -> None:
    actual = {key: _record(text) for key, text in _corpus(tmp_path).items()}
    if _WRITE:
        _write_golden(actual)
        pytest.skip("wrote the format identity golden")
    expected = _expected()
    assert actual.keys() == expected.keys()
    changed_inputs = [k for k in expected if actual[k]["input"] != expected[k]["input"]]
    assert not changed_inputs, "corpus input changed; the golden no longer applies"
    changed = {
        key: {
            field: (expected[key][field], value)
            for field, value in record.items()
            if expected[key][field] != value
        }
        for key, record in actual.items()
        if record != expected[key]
    }
    assert not changed


def test_the_corpus_covers_both_formats_and_their_errors() -> None:
    expected = _expected()
    outcomes = [
        str(r[pair]) for r in expected.values() for pair in ("json>json", "yaml>yaml")
    ]
    assert len(expected) > 800
    assert sum(o.startswith("output ") for o in outcomes) > 500
    assert sum(o.startswith("InputError ") for o in outcomes) > 10
    assert {r["detect"] for r in expected.values()} == {"JSON", "YAML", None}
