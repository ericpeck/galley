"""Realistic YAML corpus shape, and the rows that time it."""

import subprocess
from pathlib import Path

import pytest
from benchmarks.size_bench import (
    DEFAULT_REALISTIC_SIZES_MB,
    DEFAULT_TRIGGER_SIZES_MB,
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
    "format_yaml_realistic_clean_pure",
    "format_yaml_realistic_clean_clib",
    "format_yaml_realistic_only_bare_tag_clib",
    "format_yaml_realistic_only_tab_in_quotes_clib",
    "format_yaml_realistic_only_directive_clib",
]
_TRIGGER_ROWS = [
    "format_yaml_realistic_only_bare_tag_clib",
    "format_yaml_realistic_only_tab_in_quotes_clib",
    "format_yaml_realistic_only_directive_clib",
]
_FIVE_MB_ROWS = [
    "format_yaml_realistic_pure",
    "format_yaml_realistic_clib",
    "format_yaml_realistic_clib_reparse",
    "format_yaml_plain_clib",
    "yaml_safe_c_realistic",
    "format_yaml_realistic_clean_pure",
    "format_yaml_realistic_clean_clib",
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
    five_mb = [float(size) for size in DEFAULT_REALISTIC_SIZES_MB]
    one_mb = [float(size) for size in DEFAULT_TRIGGER_SIZES_MB]
    historical = [float(size) for size in DEFAULT_YAML_SIZES_MB]
    for operation_id in _FIVE_MB_ROWS:
        assert _sizes_for_operation(by_id[operation_id], None) == five_mb
    for operation_id in _TRIGGER_ROWS:
        assert _sizes_for_operation(by_id[operation_id], None) == one_mb
    assert _sizes_for_operation(by_id["format_yaml"], None) == historical
    assert _sizes_for_operation(by_id["yaml_safe_c"], None) == historical
    assert 10.0 in historical
    assert 5.0 in five_mb
    assert 5.0 not in one_mb
    assert 10.0 not in five_mb
    reparse = by_id["format_yaml_realistic_clib_reparse"]
    assert reparse.require_path == PATH_PURE_REPARSE
    assert reparse.cutoff_role == "comparison"
    natural = by_id["format_yaml_realistic_clib"]
    assert natural.require_path == PATH_PURE_REPARSE
    clean = by_id["format_yaml_realistic_clean_clib"]
    assert clean.require_path == PATH_C_PARSE_PURE_WRITER
    bare = by_id["format_yaml_realistic_only_bare_tag_clib"]
    assert bare.require_path == PATH_C_PARSE_PURE_WRITER
    assert bare.expect_path is None
    quoted_tab = by_id["format_yaml_realistic_only_tab_in_quotes_clib"]
    assert quoted_tab.require_path == PATH_PURE_REPARSE
    assert quoted_tab.expect_path is None
    directive = by_id["format_yaml_realistic_only_directive_clib"]
    assert directive.require_path == PATH_PURE_REPARSE
    assert directive.expect_path is None
    assert by_id["yaml_safe_c_realistic"].reported_path == PATH_C_PARSE_C_WRITER


def test_shipped_clib_rows_use_the_pinned_extension() -> None:
    operations, _skipped = discover_operations()
    by_id = {operation.operation_id: operation for operation in operations}
    path = Path("sample.yaml")
    shipped = _worker_command(by_id["format_yaml"], path, 0.1, 1, 1)
    again = _worker_command(by_id["format_yaml"], path, 1, 3, 2)
    script = str(Path(__file__).resolve().parents[1] / "benchmarks" / "size_bench.py")
    assert shipped[0] == again[0]
    assert Path(shipped[0]).name in {"python", "python.exe"}
    assert "--isolated" not in shipped
    assert "--with" not in shipped
    assert shipped[1:] == [
        script,
        "--worker",
        "--operation",
        "format_yaml",
        "--input-file",
        str(path),
        "--size-mb",
        "0.1",
        "--runs",
        "1",
        "--item-count",
        "1",
    ]
    subprocess.run(
        [
            shipped[0],
            "-c",
            "import galley.core.format as format_module\n"
            "raise SystemExit(0 if format_module._clib_available() else 1)\n",
        ],
        check=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    for operation_id in (
        "yaml_safe_c",
        "yaml_safe_c_realistic",
        "format_yaml_realistic_clib",
        "format_yaml_realistic_clib_reparse",
        "format_yaml_plain_clib",
        "format_yaml_realistic_clean_clib",
        *_TRIGGER_ROWS,
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


def _parsed_items(text: str) -> list[object]:
    parsed = YAML(typ="safe").load(text)
    items = parsed["items"]
    assert isinstance(items, list) and items
    return items


def test_clean_and_single_trigger_corpora_are_deterministic() -> None:
    target = 16 * 1024
    clean = render_realistic_yaml(target, profile="clean")
    assert clean == render_realistic_yaml(target, profile="clean")
    assert len(clean.encode("utf-8")) == target
    assert any(line.endswith("|") for line in clean.splitlines())
    assert any(line.endswith(">") for line in clean.splitlines())
    assert "café" in clean or "São" in clean
    assert ": ! " not in clean
    assert "\t" not in clean
    assert "%YAML" not in clean
    assert "%TAG" not in clean
    for line in clean.splitlines():
        indent = line[: len(line) - len(line.lstrip(" "))]
        assert "\t" not in indent
    clean_items = _parsed_items(clean)

    bare = render_realistic_yaml(target, profile="bare_tag")
    assert bare == render_realistic_yaml(target, profile="bare_tag")
    assert bare.count(": ! ") == 1
    assert "\t" not in bare
    assert "%YAML" not in bare
    assert "channel: ! stable" in bare.split("items:", 1)[0]
    assert _parsed_items(bare) == clean_items[: len(_parsed_items(bare))]

    tabbed = render_realistic_yaml(target, profile="quoted_tab")
    assert tabbed == render_realistic_yaml(target, profile="quoted_tab")
    assert tabbed.count("\t") == 1
    assert ": ! " not in tabbed
    assert "%YAML" not in tabbed
    assert '"name\tregion"' in tabbed.split("items:", 1)[0]
    assert _parsed_items(tabbed) == clean_items[: len(_parsed_items(tabbed))]

    directed = render_realistic_yaml(target, profile="directive")
    assert directed == render_realistic_yaml(target, profile="directive")
    assert directed.startswith("%YAML 1.2\n---\n")
    assert directed.count("%YAML") == 1
    assert ": ! " not in directed
    assert "\t" not in directed
    assert _parsed_items(directed) == clean_items[: len(_parsed_items(directed))]


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
    # 12 KB is the smallest realistic file that includes a quoted tab.
    # A tab forces the pure reparse path.
    natural = render_realistic_yaml(12 * 1024)
    natural_row = _measure_loaded(
        by_id["format_yaml_realistic_clib"],
        natural,
        12 / 1024,
        1,
        1,
    )
    assert "\t" in natural
    assert natural_row["yaml_path"] == PATH_PURE_REPARSE
    forced = render_realistic_yaml(8 * 1024, directive=True)
    forced_row = _measure_loaded(
        by_id["format_yaml_realistic_clib_reparse"],
        forced,
        0.01,
        1,
        1,
    )
    assert forced_row["yaml_path"] == PATH_PURE_REPARSE
    clean = render_realistic_yaml(8 * 1024, profile="clean")
    clean_row = _measure_loaded(
        by_id["format_yaml_realistic_clean_clib"],
        clean,
        0.01,
        1,
        1,
    )
    assert clean_row["yaml_path"] == PATH_C_PARSE_PURE_WRITER
    for profile, operation_id, expected in (
        (
            "bare_tag",
            "format_yaml_realistic_only_bare_tag_clib",
            PATH_C_PARSE_PURE_WRITER,
        ),
        (
            "quoted_tab",
            "format_yaml_realistic_only_tab_in_quotes_clib",
            PATH_PURE_REPARSE,
        ),
        ("directive", "format_yaml_realistic_only_directive_clib", PATH_PURE_REPARSE),
    ):
        triggered = render_realistic_yaml(8 * 1024, profile=profile)
        triggered_row = _measure_loaded(by_id[operation_id], triggered, 0.01, 1, 1)
        assert triggered_row["yaml_path"] == expected
        assert "path_mismatch" not in triggered_row
    with pytest.raises(RuntimeError, match="expected C parse plus pure writer"):
        _measure_loaded(
            by_id["format_yaml_realistic_only_bare_tag_clib"],
            "%YAML 1.2\n---\n" + plain,
            0.01,
            1,
            1,
        )
    with pytest.raises(RuntimeError, match="expected pure re-parse"):
        _measure_loaded(
            by_id["format_yaml_realistic_clib_reparse"],
            plain,
            0.01,
            1,
            1,
        )
