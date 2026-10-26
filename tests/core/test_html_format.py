"""HTML Format on stdlib html.parser (#63 slice 4).

Output is built from source slices, so the golden files pin every byte.
Worker jobs live in ``test_worker.py``. HTML has no row in the format table
until slice 5, so tests that route it by name add the row from ``html_row``
first.
"""

import os
import pickle
import re
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from tests.core.html_row import HTML_ROW, add_html_row

from galley.core.diff import load_document as diff_load_document
from galley.core.errors import (
    MAX_DEPTH,
    HTMLStructureError,
    InputError,
    TagProblem,
)
from galley.core.escape import escape_document, unescape_document
from galley.core.format import dump_value, format_document, load_document
from galley.core.formats import (
    FORMATS,
    format_for_extension,
    format_named,
    sniff_format,
    targets_for,
)
from galley.core.html_format import format_html
from galley.core.load import detect_format
from galley.core.problems import Problem, classify_failure, collect_problems
from galley.core.worker import (
    CompareFailureEvent,
    CompareJob,
    _scan_folds,
    run_compare,
)

DATA = Path(__file__).resolve().parent / "data" / "html"
GOLDENS = (
    "void_elements",
    "optional_end_tags",
    "verbatim_elements",
    "inline_runs",
    "conditional_comments",
    "doctype_case",
    "svg",
    "attributes_no_value",
    "uppercase_tags",
    "document",
)

# CI runs the same examples every time (GitHub Actions sets CI), so a red
# build is reproducible. Local runs stay random and keep exploring.
_SETTINGS = settings(
    max_examples=200, deadline=None, derandomize=bool(os.environ.get("CI"))
)
_SPACE = re.compile(r"[ \t\n\f\r]")


def _read(name: str) -> str:
    return (DATA / name).read_text(encoding="utf-8")


def _problems(text: str) -> tuple[TagProblem, ...]:
    with pytest.raises(HTMLStructureError) as caught:
        format_html(text)
    return caught.value.problems


# --- Golden files ---------------------------------------------------------


@pytest.mark.parametrize("name", GOLDENS)
def test_golden(name: str) -> None:
    assert format_html(_read(f"{name}.html")) == _read(f"{name}.formatted.html")


@pytest.mark.parametrize("name", GOLDENS)
def test_formatting_twice_is_formatting_once(name: str) -> None:
    once = format_html(_read(f"{name}.html"))
    assert format_html(once) == once


def test_every_golden_input_is_listed() -> None:
    inputs = {
        path.name.removesuffix(".html")
        for path in DATA.glob("*.html")
        if not path.name.endswith(".formatted.html")
    }
    assert inputs == set(GOLDENS)


# --- Text in, text out ------------------------------------------------------


def test_output_is_lf_with_one_final_newline() -> None:
    assert format_html("<ul>\r\n<li>a</li>\r\n</ul>\r\n\r\n") == (
        "<ul>\n  <li>a</li>\n</ul>\n"
    )
    assert format_html("<p>x\ry</p>") == "<p>x\ny</p>\n"


def test_blank_text_formats_to_nothing() -> None:
    assert format_html("") == ""
    assert format_html(" \n\t\n") == ""


def test_bytes_are_utf8_and_a_bom_is_dropped() -> None:
    text = "<p>é😀</p>"
    assert format_html(text.encode("utf-8")) == text + "\n"
    assert format_html("﻿" + text) == text + "\n"
    assert format_html(b"\xef\xbb\xbf" + text.encode("utf-8")) == text + "\n"


def test_invalid_utf8_bytes_are_a_located_input_error() -> None:
    with pytest.raises(InputError) as caught:
        format_html(b"<p>\n\xff</p>")
    assert caught.value.kind == "invalid_utf8"
    assert (caught.value.lineno, caught.value.colno) == (2, 1)


def test_a_no_break_space_is_text_and_is_kept() -> None:
    text = "<div>\n <p>a</p> \n</div>"
    assert format_html(text) == text + "\n"


def test_whitespace_between_blocks_is_dropped_but_never_added_to_inline() -> None:
    assert format_html("<div>  <p>a</p>  <p>b</p>  </div>") == (
        "<div>\n  <p>a</p>\n  <p>b</p>\n</div>\n"
    )
    inline = "<p><b>a</b><i>b</i> <span>c</span>\n   <em>d</em></p>"
    assert format_html(inline) == inline + "\n"


def test_a_block_next_to_text_stays_where_it_is() -> None:
    text = "<li>Item\n  <ul><li>sub</li></ul>\n</li>"
    assert format_html(text) == text + "\n"


def test_head_children_each_get_a_line() -> None:
    text = "<head><meta charset=utf-8><title>T</title><style>p{}</style></head>"
    assert format_html(text) == (
        "<head>\n  <meta charset=utf-8>\n  <title>T</title>\n"
        "  <style>p{}</style>\n</head>\n"
    )


def test_tags_inside_raw_text_are_not_read() -> None:
    text = "<textarea></div><p></textarea><title><b>x</title>"
    assert format_html(text) == text + "\n"


def test_pre_keeps_its_markup_and_whitespace() -> None:
    text = "<div><pre>\n  <b>a</b>\n\n   b </pre></div>"
    assert format_html(text) == "<div>\n  <pre>\n  <b>a</b>\n\n   b </pre>\n</div>\n"


def test_an_implied_close_drops_only_its_trailing_whitespace() -> None:
    assert format_html("<ul><li> a  \n<li>b\t</ul>") == (
        "<ul>\n  <li> a\n  <li>b\n</ul>\n"
    )


def test_hr_closes_an_open_paragraph() -> None:
    assert format_html("<div><p>a<hr><p>b</div>") == (
        "<div>\n  <p>a\n  <hr>\n  <p>b\n</div>\n"
    )


def test_a_slash_does_not_close_an_html_element() -> None:
    # Outside svg and math, <div/> is a start tag, as in a browser.
    assert format_html("<section><div/><p>a</p></div></section>") == (
        "<section>\n  <div/>\n    <p>a</p>\n  </div>\n</section>\n"
    )


# --- Structure problems ---------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("<div>\n</span>\n</div>", [("html_unexpected_close", 2, 1, "span")]),
        ("<p>a</p>\n  </p>", [("html_unexpected_close", 2, 3, "p")]),
        ("<p>a<br></br></p>", [("html_unexpected_close", 1, 9, "br")]),
        (
            "<div>\n<section>\n",
            [("html_unclosed", 1, 1, "div"), ("html_unclosed", 2, 1, "section")],
        ),
        ("<div><Span>x</div>", [("html_unclosed", 1, 6, "Span")]),
        (
            "<div>\n  <span>\n</DIV>\n</b>\n<em>",
            [
                ("html_unclosed", 2, 3, "span"),
                ("html_unexpected_close", 4, 1, "b"),
                ("html_unclosed", 5, 1, "em"),
            ],
        ),
        ("<p>é😀</x>", [("html_unexpected_close", 1, 6, "x")]),
    ],
)
def test_every_structure_problem_comes_with_its_line(
    text: str, expected: list[tuple[str, int, int, str]]
) -> None:
    assert [
        (found.code, found.lineno, found.colno, found.token)
        for found in _problems(text)
    ] == expected


@pytest.mark.parametrize(
    "text",
    [
        "<html><head><title>t</title><body><p>a",
        "<ul><li>a<li>b</ul><dl><dt>t<dd>d</dl>",
        "<table><caption>c<colgroup><col><thead><tr><th>h<tbody><tr><td>a</table>",
        "<select><optgroup label=a><option>x<option>y</select>",
        "<ruby>漢<rp>(<rt>kan<rp>)</ruby>",
        "<p><svg><path/><g><circle/></g></svg></p>",
        "<script>if (a < b) { x = '</div>'; }</script>",
        "<plaintext></div><p>",
    ],
)
def test_left_out_end_tags_are_not_problems(text: str) -> None:
    out = format_html(text)
    assert format_html(out) == out


def test_the_error_names_the_first_problem_and_pickles_every_one() -> None:
    with pytest.raises(HTMLStructureError) as caught:
        format_html("<div>\n</b>\n<span>")
    error = caught.value
    assert str(error) == "html_unclosed"
    assert (error.lineno, error.colno) == (1, 1)
    copy = pickle.loads(pickle.dumps(error))
    assert isinstance(copy, HTMLStructureError)
    assert copy.problems == error.problems
    assert (copy.lineno, copy.colno) == (1, 1)
    with pytest.raises(ValueError, match="at least one"):
        HTMLStructureError(())


def test_problems_reach_the_problems_list_all_at_once() -> None:
    text = "<div>\n</b>\n<span>"
    with pytest.raises(HTMLStructureError) as caught:
        format_html(text)
    expected = (
        Problem("html_unclosed", 1, "div", 1),
        Problem("html_unexpected_close", 2, "b", 1),
        Problem("html_unclosed", 3, "span", 1),
    )
    found = collect_problems(text, "html", caught.value)
    assert found == expected
    assert [problem.colno for problem in found] == [1, 1, 1]
    assert classify_failure(caught.value, text) == expected[0]


def test_every_stray_tag_is_reported_with_its_own_line() -> None:
    problems = _problems("<p>" + "</b>\n" * 5000)
    assert len(problems) == 5000
    assert [found.lineno for found in problems] == list(range(1, 5001))
    assert problems[0].colno == 4 and problems[1].colno == 1


def test_problems_are_in_text_order() -> None:
    problems = _problems("<div>\n<span>\n</div>\n</p>")
    assert [(found.lineno, found.code) for found in problems] == [
        (2, "html_unclosed"),
        (4, "html_unexpected_close"),
    ]


# --- Depth ----------------------------------------------------------------


def _nested(depth: int) -> str:
    return "<span>" * depth + "x" + "</span>" * depth


def test_depth_512_formats() -> None:
    out = format_html(_nested(MAX_DEPTH))
    assert out == _nested(MAX_DEPTH) + "\n"


def test_depth_513_is_too_deep_at_the_513th_start_tag() -> None:
    with pytest.raises(InputError) as caught:
        format_html(_nested(MAX_DEPTH + 1))
    error = caught.value
    assert error.kind == "too_deep"
    assert (error.lineno, error.colno) == (1, MAX_DEPTH * 6 + 1)
    assert error.details == {"depth": MAX_DEPTH + 1}


def test_a_hundred_thousand_levels_raise_too_deep_not_recursion_error() -> None:
    with pytest.raises(InputError) as caught:
        format_html("<span>" * 100_000)
    assert caught.value.kind == "too_deep"


def test_deep_block_nesting_lays_out_without_recursion() -> None:
    divs = MAX_DEPTH - 1
    text = "<div>" * divs + "<p>x</p>" + "</div>" * divs
    lines = format_html(text).splitlines()
    assert len(lines) == divs * 2 + 1
    assert lines[divs] == "  " * divs + "<p>x</p>"
    assert format_html("\n".join(lines)) == "\n".join(lines) + "\n"


# --- The format table -------------------------------------------------------


def test_html_stays_out_of_the_table_until_slice_5() -> None:
    # The pickers, the status reserve, and Open all read FORMATS.
    assert "html" not in [fmt.name for fmt in FORMATS]
    assert format_for_extension(".html") is None
    assert format_for_extension(".htm") is None
    assert sniff_format("<!DOCTYPE html>\n<p>a") is None
    assert detect_format(Path("a.html"), "<html></html>") is None
    with pytest.raises(ValueError, match="Unsupported format: html"):
        format_document("<p>a</p>", "html", "html")


def test_with_the_slice_5_row_format_document_formats_html(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_html_row(monkeypatch)
    assert format_named("HTML") is HTML_ROW
    assert targets_for("html") == (HTML_ROW,)
    text = _read("document.html")
    assert format_document(text, "html", "HTML") == format_html(text)
    assert _scan_folds(format_html(text), "html") == b""


def test_sort_keys_leaves_html_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    add_html_row(monkeypatch)
    text = '<div z="1" a="2"><p>b</p><p>a</p></div>'
    plain = format_document(text, "html", "html")
    assert format_document(text, "html", "html", sort_keys=True) == plain
    assert plain == '<div z="1" a="2">\n  <p>b</p>\n  <p>a</p>\n</div>\n'


@pytest.mark.parametrize(
    ("source", "target"), [("html", "json"), ("yaml", "html"), ("html", "yaml")]
)
def test_html_does_not_convert(
    monkeypatch: pytest.MonkeyPatch, source: str, target: str
) -> None:
    add_html_row(monkeypatch)
    with pytest.raises(ValueError, match="Can't write"):
        format_document("<p>a</p>", source, target)


def test_html_does_not_load_to_a_value(monkeypatch: pytest.MonkeyPatch) -> None:
    add_html_row(monkeypatch)
    for call in (
        lambda: load_document("<p>a</p>", "html"),
        lambda: diff_load_document("<p>a</p>", "html"),
        lambda: dump_value({"a": 1}, "html"),
        lambda: escape_document("<p>a</p>", "html"),
        lambda: unescape_document('"{}"', "html"),
        lambda: format_document("<p>a</p>", "html", "html", prepare=lambda v: v),
    ):
        with pytest.raises(ValueError, match="HTML doesn't load to a value"):
            call()


@pytest.mark.parametrize(
    ("left", "right", "side"),
    [("html", "json", "left"), ("yaml", "HTML", "right"), ("html", "html", "left")],
)
def test_run_compare_rejects_html(
    monkeypatch: pytest.MonkeyPatch, left: str, right: str, side: str
) -> None:
    add_html_row(monkeypatch)
    event = run_compare(CompareJob(7, "<p>a</p>", left, "a: 1", right))
    assert isinstance(event, CompareFailureEvent)
    assert (event.job_id, event.side) == (7, side)
    assert event.message == "HTML can't be compared."


# --- Properties -----------------------------------------------------------

_WORDS = st.sampled_from(["a", "b c", "é", "😀", "&amp;", "&#169;", "x y"])
_GAP = st.sampled_from(["", " ", "\n", "  ", "\n    ", "\t"])
_INLINE_LEAF = st.one_of(
    _WORDS,
    _GAP,
    st.sampled_from(["<br>", "<br/>", '<img src="f.png" alt="">', "<!-- c -->"]),
)


def _wrap(name: str) -> object:
    return lambda inner: f"<{name}>{inner}</{name}>"


_INLINE = st.recursive(
    st.lists(_INLINE_LEAF, min_size=1, max_size=4).map("".join),
    lambda children: st.one_of(
        st.lists(children, min_size=1, max_size=3).map("".join),
        st.sampled_from(["span", "b", "a", "em"]).flatmap(
            lambda name: children.map(_wrap(name))
        ),
    ),
    max_leaves=6,
)
_VERBATIM = st.one_of(
    st.sampled_from(["\n  a  <b>b</b>\n\n c ", " x\ty ", ""]).map(_wrap("pre")),
    st.sampled_from(["\n  if (a < b) {}\n", "x = 1;"]).map(_wrap("script")),
    st.sampled_from(["  <b>keep</b>\n  ", ""]).map(_wrap("textarea")),
)
_BLOCK = st.recursive(
    st.one_of(_INLINE.map(_wrap("p")), _VERBATIM, st.just("<hr>")),
    lambda children: st.one_of(
        st.tuples(
            st.sampled_from(["div", "section"]),
            st.lists(st.tuples(_GAP, st.one_of(children, _INLINE)), max_size=4),
            _GAP,
        ).map(
            lambda parts: (
                f"<{parts[0]}>"
                + "".join(gap + child for gap, child in parts[1])
                + parts[2]
                + f"</{parts[0]}>"
            )
        ),
        st.lists(st.tuples(_GAP, children), min_size=1, max_size=3).map(
            lambda items: (
                "<ul>" + "".join(f"{gap}<li>{child}" for gap, child in items) + "</ul>"
            )
        ),
    ),
    max_leaves=8,
)
_DOCUMENTS = st.lists(st.tuples(_GAP, _BLOCK), min_size=1, max_size=3).map(
    lambda items: "".join(gap + block for gap, block in items)
)
_VERBATIM_CONTENT = re.compile(r"<(pre|script|textarea)>(.*?)</\1>", re.DOTALL)


def _squash(text: str) -> str:
    return _SPACE.sub("", text)


@_SETTINGS
@given(_DOCUMENTS)
def test_format_changes_only_whitespace_next_to_blocks(text: str) -> None:
    out = format_html(text)
    assert _squash(out) == _squash(text)
    assert format_html(out) == out
    assert out.endswith("\n") and not out.endswith("\n\n")
    assert "\r" not in out
    assert _VERBATIM_CONTENT.findall(out) == _VERBATIM_CONTENT.findall(text)


@_SETTINGS
@given(_INLINE)
def test_inline_content_is_copied_as_written(inline: str) -> None:
    text = f"<p>{inline}</p>"
    assert format_html(text) == text + "\n"


# --- Performance ------------------------------------------------------------


def _one_megabyte_html() -> str:
    """About 1 MB of generated, fake catalog HTML."""
    parts = [
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        "<title>Fake catalog</title>\n</head>\n<body>\n<main>\n"
    ]
    size = len(parts[0])
    index = 0
    while size < 1_000_000:
        item = (
            f'<article class="item" id="item-{index}"><h2>Item {index} &amp; co</h2>'
            f"<p>SKU-{index:06d} costs <b>{index % 997}.99</b> USD. Café 😀<br>"
            f"In stock: {index % 50}</p><ul><li>red<li>blue"
            f"<li>bin-{index % 12:02d}</ul><!-- note {index} --></article>\n"
        )
        parts.append(item)
        size += len(item.encode("utf-8"))
        index += 1
    parts.append("</main>\n</body>\n</html>\n")
    return "".join(parts)


@pytest.mark.perf
def test_format_one_megabyte_of_html(perf_budget) -> None:
    text = _one_megabyte_html()
    assert 1_000_000 <= len(text.encode("utf-8")) < 1_100_000
    perf_budget.within(
        0.5,
        lambda: format_html(text),
        fixture="~1 MB generated catalog HTML, 5k articles",
    )


@pytest.mark.perf
def test_a_hundred_thousand_problems_are_found_in_linear_time(perf_budget) -> None:
    text = "<p>" + "</b>\n" * 100_000

    def find() -> None:
        with pytest.raises(HTMLStructureError):
            format_html(text)

    # A guard against quadratic search, not one of Isaac's budgets.
    perf_budget.within(1.0, find, fixture="100k stray end tags, one per line")


@pytest.mark.perf
def test_a_wide_svg_lays_out_in_linear_time(perf_budget) -> None:
    text = "<svg>" + "<path d='M0 0'/>\n" * 50_000 + "</svg>"
    assert format_html(text).count("\n  <path") == 50_000
    # A guard against quadratic layout, not one of Isaac's budgets.
    perf_budget.within(
        1.0, lambda: format_html(text), fixture="svg with 50k path children"
    )
