"""Copy As rows in the size bench (#66). All data here is fake."""

import json

from benchmarks.size_bench import (
    DEFAULT_JSON_SIZES_MB,
    DEFAULT_YAML_SIZES_MB,
    _measure_loaded,
    _operation_selected,
    _sizes_for_operation,
    cutoffs_for,
    discover_operations,
)
from ruamel.yaml import YAML

from galley.core.mask import MASK, copy_as_text

_COPY_AS_ROWS = [
    "copy_as_masked_json",
    "copy_as_fake_json",
    "copy_as_masked_yaml",
    "copy_as_fake_yaml",
]
_JSON = '{"id": 1, "name": "widget", "email": "pat@example.com", "tags": ["a"]}\n'
_YAML = "id: 1\nname: widget\nemail: pat@example.com\ntags:\n- a\n"


def _copy_as_operations():
    operations, _skipped = discover_operations()
    return {
        operation.operation_id: operation
        for operation in operations
        if operation.group == "copy_as"
    }


def test_copy_as_rows_run_on_the_plain_ladders() -> None:
    operations, _skipped = discover_operations()
    ids = [operation.operation_id for operation in operations]
    assert [item for item in ids if item.startswith("copy_as_")] == _COPY_AS_ROWS
    assert ids[-len(_COPY_AS_ROWS) :] == _COPY_AS_ROWS
    by_id = _copy_as_operations()
    for operation_id, operation in by_id.items():
        assert _operation_selected(operation, "plain")
        assert _operation_selected(operation, "all")
        assert not _operation_selected(operation, "realistic")
        ladder = (
            DEFAULT_JSON_SIZES_MB if "json" in operation_id else DEFAULT_YAML_SIZES_MB
        )
        assert _sizes_for_operation(operation, None) == [float(s) for s in ladder]


def test_copy_as_phases_time_the_shipped_copy() -> None:
    by_id = _copy_as_operations()
    for fmt, text in (("json", _JSON), ("yaml", _YAML)):
        masked, total, load_s, dump_s = by_id[f"copy_as_masked_{fmt}"].phases(text)
        assert masked == copy_as_text(text, fmt, "masked").text
        assert 0 <= load_s <= total and 0 <= dump_s <= total
        fake = by_id[f"copy_as_fake_{fmt}"].phases(text)[0]
        loaded = json.loads(fake) if fmt == "json" else YAML(typ="safe").load(fake)
        assert loaded["email"].endswith("@example.com")
        assert loaded["email"] != "pat@example.com"
        assert loaded["name"] != "widget"
        assert MASK not in fake


def test_copy_as_rows_measure_and_stay_out_of_the_format_cutoffs() -> None:
    operation = _copy_as_operations()["copy_as_masked_json"]
    row = _measure_loaded(operation, _JSON, 0.0001, 1, 1)
    assert row["group"] == "copy_as"
    assert row["operation_id"] == "copy_as_masked_json"
    assert row["median_s"] >= 0
    assert cutoffs_for([row]) == []
