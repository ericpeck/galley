"""Convert from Python rows, which time the limits in galley.app.limits."""

import ast

from benchmarks.size_bench import (
    DEFAULT_PYTHON_SIZES_MB,
    _operation_selected,
    _sizes_for_operation,
    cutoffs_for,
    discover_operations,
    render_python_literal,
)

from galley.app.limits import PYTHON_TO_JSON_BYTES, PYTHON_TO_YAML_BYTES
from galley.core.convert_python import ConvertResult

_PYTHON_ROWS = ["python_to_json", "python_to_yaml"]


def _python_operations() -> dict[str, object]:
    operations, _skipped = discover_operations()
    return {
        operation.operation_id: operation
        for operation in operations
        if operation.source_format == "python"
    }


def test_python_rows_run_on_the_plain_corpus_and_bracket_both_limits() -> None:
    by_id = _python_operations()
    assert list(by_id) == _PYTHON_ROWS
    ladder = [float(size) for size in DEFAULT_PYTHON_SIZES_MB]
    for operation in by_id.values():
        assert operation.group == "convert"
        assert operation.cutoff_role == "ui"
        assert _operation_selected(operation, "plain")
        assert not _operation_selected(operation, "realistic")
        assert _sizes_for_operation(operation, None) == ladder
    mebibyte = 1024 * 1024
    assert min(ladder) * mebibyte < PYTHON_TO_YAML_BYTES
    assert max(ladder) * mebibyte > PYTHON_TO_JSON_BYTES


def test_python_rows_time_convert_from_python_for_their_target() -> None:
    text = render_python_literal(4096)
    by_id = _python_operations()
    for operation_id in _PYTHON_ROWS:
        result, total, load, dump = by_id[operation_id].phases(text)
        assert isinstance(result, ConvertResult)
        assert total == load
        assert dump == 0.0
    assert by_id["python_to_json"].phases(text)[0].text.startswith("{")
    assert by_id["python_to_yaml"].phases(text)[0].text.startswith("catalog:")


def test_python_literal_is_the_exact_size_and_parses() -> None:
    for target in (4096, PYTHON_TO_YAML_BYTES):
        text = render_python_literal(target)
        assert len(text.encode("utf-8")) == target
        parsed = ast.literal_eval(text)
        assert parsed["catalog"] == "galley-size-bench"
        assert parsed["items"]
        assert set(parsed["padding"]) <= {"x"}
    assert render_python_literal(4096) == render_python_literal(4096)


def test_python_rows_get_cutoffs() -> None:
    rows = [
        {
            "group": "convert",
            "operation_id": "python_to_yaml",
            "source_format": "python",
            "cutoff_role": "ui",
            "size_mb": size_mb,
            "median_s": median_s,
        }
        for size_mb, median_s in ((0.25, 0.1), (0.5, 0.3), (1.0, 0.7))
    ]
    other = {**rows[0], "operation_id": "json_to_yaml", "source_format": "json"}
    cutoffs = cutoffs_for([*rows, other])
    assert [cutoff["operation_id"] for cutoff in cutoffs] == ["python_to_yaml"]
    assert 0.5 < cutoffs[0]["no_indicator_below_mb"] < 1.0
