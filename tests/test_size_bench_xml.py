"""The format_xml row, which times XML Format for the #63 size bands."""

from pathlib import Path

from benchmarks.size_bench import (
    DEFAULT_JSON_SIZES_MB,
    DEFAULT_XML_SIZES_MB,
    _operation_selected,
    _sizes_for_operation,
    cutoffs_for,
    discover_operations,
    generate_files,
    render_xml_catalog,
)

from galley.core.xml_format import format_xml, validate_xml


def _xml_operation() -> object:
    operations, _skipped = discover_operations()
    found = [operation for operation in operations if operation.source_format == "xml"]
    assert [operation.operation_id for operation in found] == ["format_xml"]
    return found[0]


def test_format_xml_is_a_plain_corpus_ui_row_on_the_json_ladder() -> None:
    operation = _xml_operation()
    assert operation.group == "format"
    assert operation.cutoff_role == "ui"
    assert operation.fn is format_xml
    assert _operation_selected(operation, "plain")
    assert not _operation_selected(operation, "realistic")
    assert DEFAULT_XML_SIZES_MB == DEFAULT_JSON_SIZES_MB
    assert _sizes_for_operation(operation, None) == [
        float(size) for size in DEFAULT_XML_SIZES_MB
    ]


def test_format_xml_row_times_the_parse_and_the_format() -> None:
    text = render_xml_catalog(8192)
    result, total, load, dump = _xml_operation().phases(text)
    assert result == format_xml(text)
    assert total >= 0 and load >= 0 and dump >= 0
    assert dump <= total


def test_xml_catalog_is_the_exact_size_and_parses() -> None:
    for target in (4096, 65_536):
        text = render_xml_catalog(target)
        assert len(text.encode("utf-8")) == target
        validate_xml(text)
        assert text.count("<item ") > 1
        assert text.endswith("</padding>\n</catalog>\n")
    assert render_xml_catalog(4096) == render_xml_catalog(4096)


def test_generated_xml_files_are_checked(tmp_path: Path) -> None:
    written = generate_files(tmp_path, "xml", [0.01, 0.02])
    for size_mb, (path, count) in written.items():
        assert path.suffix == ".xml"
        assert path.stat().st_size == round(size_mb * 1024 * 1024)
        assert count > 0


def test_format_xml_gets_cutoffs() -> None:
    rows = [
        {
            "group": "format",
            "operation_id": "format_xml",
            "source_format": "xml",
            "cutoff_role": "ui",
            "size_mb": size_mb,
            "median_s": median_s,
        }
        for size_mb, median_s in ((1.0, 0.1), (5.0, 0.4), (10.0, 0.8), (50.0, 4.0))
    ]
    cutoffs = cutoffs_for(rows)
    assert [cutoff["operation_id"] for cutoff in cutoffs] == ["format_xml"]
