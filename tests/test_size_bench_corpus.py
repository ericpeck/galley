"""Realistic YAML corpus shape, and the rows that time it."""

from pathlib import Path

import pytest
from benchmarks.size_bench import (
    DEFAULT_REALISTIC_SIZES_MB,
    DEFAULT_YAML_SIZES_MB,
    PATH_C_ONLY,
    PATH_C_PARSE_C_WRITER,
    PATH_C_PARSE_PURE_WRITER,
    PATH_C_REJECTED,
    PATH_PURE_REPARSE,
    RUAMEL_CLIB_SPEC,
    YAML_FOOTER_PREFIX,
    YAML_FOOTER_SUFFIX,
    YAML_HEADER,
    _format_yaml_realistic_pure_phases,
    _measure_loaded,
    _operation_selected,
    _sizes_for_operation,
    _worker_command,
    _yaml_item,
    classify_observed_path,
    discover_operations,
    parse_args,
    render_realistic_yaml,
)
from ruamel.yaml import YAML

import galley.core.format as core_format

_REALISTIC_ROWS = [
    "format_yaml_realistic_pure",
    "format_yaml_realistic_clib",
    "format_yaml_realistic_clib_reparse",
    "format_yaml_plain_clib",
    "yaml_safe_c_realistic",
]


def test_corpus_flag_defaults_to_plain() -> None:
    assert parse_args([]).corpus == "plain"
    assert parse_args(["--corpus", "realistic"]).corpus == "realistic"
    assert parse_args(["--corpus", "all"]).corpus == "all"


def test_realistic_rows_are_opt_in_and_capped() -> None:
    operations, _skipped = discover_operations()
    by_id = {operation.operation_id: operation for operation in operations}
    plain = [
        operation.operation_id
        for operation in operations
        if _operation_selected(operation, "plain")
    ]
    realistic = [
        operation.operation_id
        for operation in operations
        if _operation_selected(operation, "realistic")
    ]
    assert "format_yaml" in plain
    assert "yaml_safe_c" in plain
    assert "format_yaml_realistic_pure" not in plain
    assert realistic == _REALISTIC_ROWS
    expected = [float(size) for size in DEFAULT_REALISTIC_SIZES_MB]
    historical = [float(size) for size in DEFAULT_YAML_SIZES_MB]
    for operation_id in realistic:
        assert _sizes_for_operation(by_id[operation_id], None) == expected
    assert _sizes_for_operation(by_id["format_yaml"], None) == historical
    assert _sizes_for_operation(by_id["yaml_safe_c"], None) == historical
    assert 10.0 in historical
    assert 10.0 not in expected
    reparse = by_id["format_yaml_realistic_clib_reparse"]
    assert reparse.require_path == PATH_PURE_REPARSE
    assert reparse.cutoff_role == "comparison"
    assert by_id["yaml_safe_c_realistic"].reported_path == PATH_C_PARSE_C_WRITER


def test_shipped_clib_rows_use_the_pinned_extension() -> None:
    operations, _skipped = discover_operations()
    by_id = {operation.operation_id: operation for operation in operations}
    path = Path("sample.yaml")
    shipped = _worker_command(by_id["format_yaml"], path, 0.1, 1, 1)
    assert shipped[1:4] == ["run", "--isolated", "--"]
    assert "--with" not in shipped
    for operation_id in (
        "yaml_safe_c",
        "yaml_safe_c_realistic",
        "format_yaml_realistic_clib",
        "format_yaml_realistic_clib_reparse",
        "format_yaml_plain_clib",
    ):
        command = _worker_command(by_id[operation_id], path, 0.1, 1, 1)
        assert command[1:3] == ["run", "--with"]
        assert RUAMEL_CLIB_SPEC in command


def test_path_detection_does_not_use_confirm_marks() -> None:
    source_path = Path(__file__).resolve().parents[1] / "benchmarks" / "size_bench.py"
    source = source_path.read_text(encoding="utf-8")
    assert "_PURE_CONFIRM_MARKS" not in source
    assert "_format_yaml_impl" not in source


def test_plain_yaml_items_have_no_reparse_triggers() -> None:
    chunks = [YAML_HEADER]
    chunks.extend(_yaml_item(index) for index in range(20))
    chunks.append(YAML_FOOTER_PREFIX + YAML_FOOTER_SUFFIX)
    text = "".join(chunks)
    assert text.isascii()
    for mark in ("\t", "|", ">", "!", "%", "\\"):
        assert mark not in text


def test_realistic_generator_is_deterministic_and_marked() -> None:
    target = 16 * 1024
    first = render_realistic_yaml(target)
    second = render_realistic_yaml(target)
    assert first == second
    assert len(first.encode("utf-8")) == target
    assert any(line.endswith("|") for line in first.splitlines())
    assert any(line.endswith(">") for line in first.splitlines())
    assert "café" in first or "São" in first
    assert "🚀" in first or "😀" in first
    assert "!!binary" in first
    assert "%YAML 1.2" in first
    assert ": ! " in first
    assert "\\" in first
    assert not first.startswith("%YAML")
    for line in first.splitlines():
        indent = line[: len(line) - len(line.lstrip(" "))]
        assert "\t" not in indent
    parsed = YAML(typ="safe").load(first)
    assert isinstance(parsed, dict)
    items = parsed["items"]
    assert isinstance(items, list) and items


def test_reparse_directive_keeps_the_same_items() -> None:
    target = 32 * 1024
    natural = render_realistic_yaml(target)
    forced = render_realistic_yaml(target, directive=True)
    assert forced.startswith("%YAML 1.2\n---\n")
    assert len(forced.encode("utf-8")) == target
    parsed_natural = YAML(typ="safe").load(natural)
    parsed_forced = YAML(typ="safe").load(forced)
    natural_items = parsed_natural["items"]
    forced_items = parsed_forced["items"]
    assert forced_items
    assert forced_items == natural_items[: len(forced_items)]


def test_classify_observed_path_covers_each_engine_mix() -> None:
    assert (
        classify_observed_path(
            {
                "c_load_ok": True,
                "pure_load": True,
                "c_dump": True,
                "pure_dump": True,
                "c_load_error": False,
                "c_dump_error": False,
            }
        )
        == PATH_PURE_REPARSE
    )
    assert (
        classify_observed_path(
            {
                "c_load_ok": True,
                "pure_load": False,
                "c_dump": False,
                "pure_dump": True,
                "c_load_error": False,
                "c_dump_error": False,
            }
        )
        == PATH_C_PARSE_PURE_WRITER
    )
    assert (
        classify_observed_path(
            {
                "c_load_ok": False,
                "pure_load": True,
                "c_dump": False,
                "pure_dump": True,
                "c_load_error": True,
                "c_dump_error": False,
            }
        )
        == PATH_C_REJECTED
    )
    assert (
        classify_observed_path(
            {
                "c_load_ok": True,
                "pure_load": False,
                "c_dump": True,
                "pure_dump": False,
                "c_load_error": False,
                "c_dump_error": False,
            }
        )
        == PATH_C_ONLY
    )
    assert (
        classify_observed_path(
            {
                "c_scan_ok": True,
                "c_scan_error": False,
                "c_load_ok": False,
                "pure_load": True,
                "c_dump": False,
                "pure_dump": True,
                "c_load_error": False,
                "c_dump_error": False,
            }
        )
        == PATH_PURE_REPARSE
    )


def test_realistic_pure_phases_require_clib_to_be_absent() -> None:
    if core_format._clib_available():
        with pytest.raises(RuntimeError, match="not importable"):
            _format_yaml_realistic_pure_phases("a: 1\n")
        return
    result, _total, _load_s, _dump_s = _format_yaml_realistic_pure_phases("a: 1\n")
    assert result == "a: 1\n"


def test_shipped_clib_paths_match_the_document() -> None:
    if not core_format._clib_available():
        pytest.skip("ruamel.yaml.clib is not installed")
    operations, _skipped = discover_operations()
    by_id = {operation.operation_id: operation for operation in operations}
    plain = "catalog: bench\nversion: 1\nitems:\n- id: 1\n  name: widget\n"
    plain_row = _measure_loaded(by_id["format_yaml_plain_clib"], plain, 0.01, 1, 1)
    assert plain_row["yaml_path"] in {PATH_C_ONLY, PATH_C_PARSE_PURE_WRITER}
    natural = render_realistic_yaml(8 * 1024)
    natural_row = _measure_loaded(
        by_id["format_yaml_realistic_clib"],
        natural,
        0.01,
        1,
        1,
    )
    assert natural_row["yaml_path"] in {
        PATH_PURE_REPARSE,
        PATH_C_PARSE_PURE_WRITER,
        PATH_C_ONLY,
        PATH_C_REJECTED,
    }
    forced = render_realistic_yaml(8 * 1024, directive=True)
    forced_row = _measure_loaded(
        by_id["format_yaml_realistic_clib_reparse"],
        forced,
        0.01,
        1,
        1,
    )
    assert forced_row["yaml_path"] == PATH_PURE_REPARSE
    with pytest.raises(RuntimeError, match="expected pure re-parse"):
        _measure_loaded(
            by_id["format_yaml_realistic_clib_reparse"],
            plain,
            0.01,
            1,
            1,
        )
