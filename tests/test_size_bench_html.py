"""The format_html row, which times HTML Format for the #63 size bands."""

from pathlib import Path

from benchmarks.size_bench import (
    DEFAULT_HTML_SIZES_MB,
    _operation_selected,
    _sizes_for_operation,
    cutoffs_for,
    discover_operations,
    generate_files,
    render_html_catalog,
)
from tests.text_compare import assert_same_text

from galley.core.html_format import format_html


def _html_operation() -> object:
    operations, _skipped = discover_operations()
    found = [o for o in operations if o.source_format == "html"]
    assert [operation.operation_id for operation in found] == ["format_html"]
    return found[0]


def test_format_html_is_a_plain_corpus_ui_row_on_its_own_ladder() -> None:
    operation = _html_operation()
    assert operation.group == "format"
    assert operation.cutoff_role == "ui"
    assert operation.fn is format_html
    assert _operation_selected(operation, "plain")
    assert not _operation_selected(operation, "realistic")
    assert 1 in DEFAULT_HTML_SIZES_MB
    assert _sizes_for_operation(operation, None) == [
        float(size) for size in DEFAULT_HTML_SIZES_MB
    ]


def test_format_html_row_times_the_whole_format() -> None:
    text = render_html_catalog(8192)
    result, total, load, dump = _html_operation().phases(text)
    assert result == format_html(text)
    assert total >= 0 and load == total and dump == 0.0


def test_html_catalog_is_the_exact_size_and_formats() -> None:
    for target in (4096, 65_536):
        text = render_html_catalog(target)
        assert len(text.encode("utf-8")) == target
        assert text.count("<article ") > 1
        assert text.endswith("</p>\n</main>\n</body>\n</html>\n")
        out = format_html(text)
        assert_same_text(format_html(out), out)
    assert render_html_catalog(4096) == render_html_catalog(4096)


def test_generated_html_files_are_checked(tmp_path: Path) -> None:
    written = generate_files(tmp_path, "html", [0.01, 0.02])
    for size_mb, (path, count) in written.items():
        assert path.suffix == ".html"
        assert path.stat().st_size == round(size_mb * 1024 * 1024)
        assert count > 0


def test_format_html_gets_cutoffs() -> None:
    rows = [
        {
            "group": "format",
            "operation_id": "format_html",
            "source_format": "html",
            "cutoff_role": "ui",
            "size_mb": size_mb,
            "median_s": median_s,
        }
        for size_mb, median_s in ((1.0, 0.25), (2.0, 0.5), (10.0, 2.5), (20.0, 5.0))
    ]
    cutoffs = cutoffs_for(rows)
    assert [cutoff["operation_id"] for cutoff in cutoffs] == ["format_html"]
