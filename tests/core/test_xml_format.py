"""XML Format and Validate on hardened pyexpat (#63 slice 2).

Output is built from source slices, so the golden files pin every byte.
Hostile DTDs live in ``test_hostile.py``. Worker jobs live in
``test_worker.py``. XML has no row in the format table until slice 3, so
tests that route it by name add the row from ``xml_row`` first.
"""

import os
import pickle
import pyexpat
from pathlib import Path
from xml.parsers import expat

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from tests.core.xml_row import XML_ROW, add_xml_row

from galley.core import xml_format
from galley.core.diff import load_document as diff_load_document
from galley.core.errors import MAX_DEPTH, InputError, XMLSyntaxError
from galley.core.escape import escape_document, unescape_document
from galley.core.format import dump_value, format_document, load_document
from galley.core.formats import (
    FORMATS,
    format_for_extension,
    format_named,
    sniff_format,
    targets_for,
)
from galley.core.load import detect_format
from galley.core.problems import Problem, collect_problems
from galley.core.worker import (
    CompareFailureEvent,
    CompareJob,
    _scan_folds,
    run_compare,
)
from galley.core.xml_format import (
    MIN_EXPAT_VERSION,
    ExpatTooOldError,
    expat_problem,
    format_xml,
    validate_xml,
)

DATA = Path(__file__).resolve().parent / "data" / "xml"
GOLDENS = (
    "element_only",
    "mixed_content",
    "xml_space",
    "comments_pis_cdata",
    "doctype_external_id",
    "doctype_internal_subset",
    "namespace_prefixes",
    "attributes",
    "entities_kept",
    "empty_elements",
    "astral",
)

# CI runs the same examples every time (GitHub Actions sets CI), so a red
# build is reproducible. Local runs stay random and keep exploring.
_SETTINGS = settings(
    max_examples=200, deadline=None, derandomize=bool(os.environ.get("CI"))
)


def _read(name: str) -> str:
    return (DATA / name).read_text(encoding="utf-8")


# --- Expat version --------------------------------------------------------


def test_expat_is_at_least_2_6_0() -> None:
    found = pyexpat.version_info
    print(f"expat found: {pyexpat.EXPAT_VERSION} {found}")
    assert found >= (2, 6, 0), (
        f"XML needs expat 2.6.0 or newer; this Python has {pyexpat.EXPAT_VERSION}"
    )
    assert MIN_EXPAT_VERSION == (2, 6, 0)
    assert expat_problem() is None


def test_an_older_expat_is_named_with_the_version_it_needs() -> None:
    assert expat_problem((2, 6, 0)) is None
    assert expat_problem((2, 7, 1)) is None
    assert expat_problem((2, 5, 0)) == (
        "XML needs expat 2.6.0 or newer, and this Python has expat 2.5.0."
    )
    assert expat_problem((2, 4, 1)) is not None


def test_format_raises_a_clear_error_on_an_older_expat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(xml_format, "_expat_checked", False)
    monkeypatch.setattr(expat, "version_info", (2, 5, 0))
    with pytest.raises(ExpatTooOldError, match="needs expat 2.6.0 or newer"):
        format_xml("<a/>")
    with pytest.raises(ExpatTooOldError, match="has expat 2.5.0"):
        validate_xml("<a/>")


# --- Golden files ---------------------------------------------------------


@pytest.mark.parametrize("name", GOLDENS)
def test_golden(name: str) -> None:
    assert format_xml(_read(f"{name}.xml")) == _read(f"{name}.formatted.xml")


@pytest.mark.parametrize("name", GOLDENS)
def test_formatting_twice_is_formatting_once(name: str) -> None:
    once = format_xml(_read(f"{name}.xml"))
    assert format_xml(once) == once


def test_every_golden_input_is_listed() -> None:
    inputs = {
        path.name.removesuffix(".xml")
        for path in DATA.glob("*.xml")
        if not path.name.endswith(".formatted.xml")
    }
    assert inputs == set(GOLDENS)


def test_output_is_lf_with_one_final_newline() -> None:
    assert format_xml("<a>\r\n<b/>\r\n</a>\r\n\r\n") == "<a>\n  <b/>\n</a>\n"
    assert format_xml("<a>x\ry</a>") == "<a>x\ny</a>\n"


def test_bytes_are_utf8_and_a_bom_is_dropped() -> None:
    text = "<a>é😀</a>"
    assert format_xml(text.encode("utf-8")) == text + "\n"
    assert format_xml("﻿" + text) == text + "\n"
    assert format_xml(b"\xef\xbb\xbf" + text.encode("utf-8")) == text + "\n"


def test_invalid_utf8_bytes_are_a_located_input_error() -> None:
    with pytest.raises(InputError) as caught:
        format_xml(b"<a>\n  \xff</a>")
    assert caught.value.kind == "invalid_utf8"
    assert (caught.value.lineno, caught.value.colno) == (2, 3)


def test_validate_checks_without_output() -> None:
    assert validate_xml("<a><b/></a>") is None
    with pytest.raises(XMLSyntaxError):
        validate_xml("<a><b></a>")


# --- Problems -------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "code", "line", "column", "token"),
    [
        ("<r>\n  <a>\n  </b>\n</r>", "xml_mismatched_tag", 3, 3, "a"),
        ("<r>\n  <a>\n", "xml_unclosed", 2, 3, "a"),
        ("<r>\n  a & b\n</r>", "xml_bad_token", 2, 6, ""),
        ('<r a="<"/>', "xml_bad_token", 1, 7, ""),
        ("<r>\n  <a</r>", "xml_bad_token", 2, 5, ""),
        ('<r>\n  <a x="1" y="2" x="3"/>\n</r>', "xml_duplicate_attribute", 2, 18, "x"),
        ("<r>\n  <a>&nbsp;</a>\n</r>", "xml_undefined_entity", 2, 6, "nbsp"),
        (
            '<?xml version="1.0"?>\n<!DOCTYPE r [\n  <!ELEMENT r ANY>\n'
            '  <!ENTITY e "x">\n]>\n<r/>',
            "xml_entity_declared",
            4,
            3,
            "",
        ),
        ("<r>\n  <p:a/>\n</r>", "xml_unbound_prefix", 2, 3, "p"),
        ('<r>\n  <a p:b="1"/>\n</r>', "xml_unbound_prefix", 2, 3, "p"),
        ('<r xmlns:a="u">\n  <a:b c:x="1"/>\n</r>', "xml_unbound_prefix", 2, 3, "c"),
        ("<r/>\n<s/>", "xml_after_root", 2, 1, ""),
        ("<r><a/></r>text", "xml_after_root", 1, 12, ""),
        ("", "xml_no_root", 1, 1, ""),
        ('<?xml version="1.0"?>\n<!-- only -->\n', "xml_no_root", 3, 1, ""),
        ("<r>\n  <![CDATA[ open\n</r>", "xml_other", 3, 5, ""),
    ],
)
def test_error_codes_with_their_lines(
    text: str, code: str, line: int, column: int, token: str
) -> None:
    with pytest.raises(XMLSyntaxError) as caught:
        format_xml(text)
    error = caught.value
    assert (error.code, error.lineno, error.colno, error.token) == (
        code,
        line,
        column,
        token,
    )
    problems = collect_problems(text, "xml", error)
    assert problems == (Problem(code, line, token, column),)
    assert problems[0].colno == column


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"], ids=["LF", "CRLF", "CR"])
@pytest.mark.parametrize(
    ("third_line", "code", "column"),
    [
        ("  </b>", "xml_mismatched_tag", 3),
        ("  a & b", "xml_bad_token", 6),
        ("  <c>&nbsp;</c>", "xml_undefined_entity", 6),
    ],
)
def test_every_line_ending_counts_as_one_line(
    newline: str, third_line: str, code: str, column: int
) -> None:
    """A bare CR is a line break, as it is for expat and the editor (#123)."""
    text = newline.join(["<r>", "  <a>", third_line, "</a></r>", ""])
    with pytest.raises(XMLSyntaxError) as caught:
        format_xml(text)
    assert (caught.value.code, caught.value.lineno, caught.value.colno) == (
        code,
        3,
        column,
    )


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"], ids=["LF", "CRLF", "CR"])
def test_too_deep_counts_every_line_ending(newline: str) -> None:
    text = newline.join(["<a>"] * (MAX_DEPTH + 1)) + "</a>" * (MAX_DEPTH + 1)
    with pytest.raises(InputError) as caught:
        format_xml(text)
    assert (caught.value.lineno, caught.value.colno) == (MAX_DEPTH + 1, 1)


def test_columns_count_code_points() -> None:
    with pytest.raises(XMLSyntaxError) as caught:
        format_xml('<r>\n  😀😀<a b="1" b="2"/></r>')
    assert (caught.value.lineno, caught.value.colno) == (2, 14)


def test_an_astral_name_is_a_bad_token() -> None:
    # Expat follows XML 1.0 fourth edition names, which stop at U+FFFF.
    with pytest.raises(XMLSyntaxError) as caught:
        format_xml("<r><\U0001d4b3/></r>")
    assert caught.value.code == "xml_bad_token"


def test_the_error_pickles_with_its_fields() -> None:
    error = XMLSyntaxError("xml_unclosed", lineno=4, colno=2, token="b")
    copy = pickle.loads(pickle.dumps(error))
    assert isinstance(copy, XMLSyntaxError)
    assert (copy.code, copy.lineno, copy.colno, copy.token) == (
        "xml_unclosed",
        4,
        2,
        "b",
    )
    assert str(copy) == "xml_unclosed"


# --- Depth ----------------------------------------------------------------


def _nested(depth: int) -> str:
    return "<a>" * depth + "</a>" * depth


def test_depth_512_formats() -> None:
    assert MAX_DEPTH == 512
    output = format_xml(_nested(MAX_DEPTH))
    lines = output.splitlines()
    assert len(lines) == MAX_DEPTH * 2 - 1
    assert lines[MAX_DEPTH - 1] == "  " * (MAX_DEPTH - 1) + "<a></a>"


def test_depth_513_is_too_deep_at_the_513th_start_tag() -> None:
    text = "<a>\n" * (MAX_DEPTH + 1) + "</a>\n" * (MAX_DEPTH + 1)
    with pytest.raises(InputError) as caught:
        format_xml(text)
    assert caught.value.kind == "too_deep"
    assert (caught.value.lineno, caught.value.colno) == (MAX_DEPTH + 1, 1)
    assert caught.value.details == {"depth": MAX_DEPTH + 1}


def test_a_hundred_thousand_levels_raise_too_deep_not_recursion_error() -> None:
    with pytest.raises(InputError) as caught:
        format_xml(_nested(100_000))
    assert caught.value.kind == "too_deep"
    assert caught.value.colno == MAX_DEPTH * 3 + 1


# --- The format table -------------------------------------------------------


def test_xml_stays_out_of_the_table_until_slice_3() -> None:
    # The pickers, the status reserve, and Open all read FORMATS.
    assert [fmt.name for fmt in FORMATS] == ["json", "yaml"]
    assert format_for_extension(".xml") is None
    assert sniff_format('<?xml version="1.0"?>\n<a/>') is None
    assert detect_format(Path("a.xml"), "<a/>") is None
    with pytest.raises(ValueError, match="Unsupported format: xml"):
        format_document("<a/>", "xml", "xml")


def test_with_the_slice_3_row_format_document_formats_xml(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_xml_row(monkeypatch)
    assert format_named("XML") is XML_ROW
    assert targets_for("xml") == (XML_ROW,)
    text = _read("element_only.xml")
    assert format_document(text, "xml", "XML") == format_xml(text)
    assert _scan_folds(format_xml(text), "xml") == b""


# Attributes and child elements deliberately out of A-Z order.
_UNSORTED_XML = (
    '<?xml version="1.0"?>\n'
    '<root zeta="1" alpha="2"><z>last</z><a>first</a><m b="2" a="1"/></root>\n'
)


def test_sort_keys_leaves_xml_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    # Sort keys orders JSON and YAML keys only. XML keeps attribute and
    # element order exactly as written, whatever the toggle says.
    add_xml_row(monkeypatch)
    plain = format_document(_UNSORTED_XML, "xml", "xml")
    assert plain == format_xml(_UNSORTED_XML)
    assert format_document(_UNSORTED_XML, "xml", "xml", sort_keys=True) == plain
    assert plain.index('zeta="1"') < plain.index('alpha="2"')
    assert plain.index("<z>") < plain.index("<a>")
    assert '<m b="2" a="1"/>' in plain


@pytest.mark.parametrize(("source", "target"), [("xml", "json"), ("json", "xml")])
def test_xml_does_not_convert(
    monkeypatch: pytest.MonkeyPatch, source: str, target: str
) -> None:
    add_xml_row(monkeypatch)
    with pytest.raises(ValueError, match="Can't write"):
        format_document("<a/>", source, target)


def test_xml_does_not_load_to_a_value(monkeypatch: pytest.MonkeyPatch) -> None:
    add_xml_row(monkeypatch)
    for call in (
        lambda: load_document("<a/>", "xml"),
        lambda: diff_load_document("<a/>", "xml"),
        lambda: dump_value({"a": 1}, "xml"),
        lambda: escape_document("<a/>", "xml"),
        lambda: unescape_document('"{}"', "xml"),
    ):
        with pytest.raises(ValueError, match="XML doesn't load to a value"):
            call()


@pytest.mark.parametrize(
    ("left", "right", "side"),
    [("xml", "json", "left"), ("json", "XML", "right"), ("xml", "xml", "left")],
)
def test_run_compare_rejects_xml(
    monkeypatch: pytest.MonkeyPatch, left: str, right: str, side: str
) -> None:
    add_xml_row(monkeypatch)
    job = CompareJob(7, "<a/>", left, "{}", right)
    event = run_compare(job)
    assert isinstance(event, CompareFailureEvent)
    assert (event.job_id, event.side) == (7, side)
    assert event.message == "XML can't be compared."


def test_run_compare_still_reports_an_unknown_format_as_a_failure() -> None:
    event = run_compare(CompareJob(9, "{}", "json", "<a/>", "html"))
    assert isinstance(event, CompareFailureEvent)
    assert (event.side, event.message) == ("right", "Unsupported format: html")


# --- Properties -----------------------------------------------------------

_NAMES = st.sampled_from(["a", "b", "item", "n-1", "_z", "名前"])
_TEXT = st.lists(
    st.sampled_from(["a", "b c", " ", "\n", "  ", "\t", "&amp;", "&lt;", "😀", "é"]),
    min_size=1,
    max_size=4,
).map("".join)
_SPACE = st.lists(st.sampled_from([" ", "\n", "  ", "\t", "\n    "]), max_size=3).map(
    "".join
)
_TAG_SPACE = st.sampled_from([" ", "  ", "\n  ", "\t"])
_VALUE = st.lists(
    st.sampled_from(["v", " ", "&amp;", "😀", ">", "\n", "x  y"]), max_size=3
).map("".join)
_ATTRIBUTES = st.lists(
    st.tuples(
        st.sampled_from(["id", "k", "z"]),
        _VALUE,
        st.sampled_from(['"', "'"]),
        _TAG_SPACE,
    ),
    max_size=3,
    unique_by=lambda attribute: attribute[0],
)
_LEAVES = st.one_of(
    _TEXT,
    _SPACE,
    st.sampled_from(["<!-- c -->", "<!--x\n y-->", "<?p data?>", "<?q?>"]),
    st.sampled_from(["<![CDATA[x<y]]>", "<![CDATA[ ]]>", "<![CDATA[]]>"]),
)


def _element(
    name: str,
    attributes: list[tuple[str, str, str, str]],
    children: list[str],
    closing_space: str,
    preserve: bool,
) -> str:
    head = "<" + name
    for attribute, value, quote, space in attributes:
        head += f"{space}{attribute}={quote}{value}{quote}"
    if preserve:
        head += ' xml:space="preserve"'
    if not children:
        return head + closing_space + "/>"
    return head + ">" + "".join(children) + "</" + name + ">"


def _elements(children: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    return st.builds(
        _element,
        _NAMES,
        _ATTRIBUTES,
        st.lists(st.one_of(children, _SPACE), max_size=4),
        st.sampled_from(["", " "]),
        st.just(False),
    ) | st.builds(
        _element,
        _NAMES,
        _ATTRIBUTES,
        st.lists(children, max_size=3),
        st.just(""),
        st.just(True),
    )


_NODES = st.recursive(_LEAVES, _elements, max_leaves=25)
_DOCUMENTS = st.builds(
    lambda prolog, before, root, after: prolog + before + root + after,
    st.sampled_from(["", '<?xml version="1.0"?>', "<!-- head -->"]),
    _SPACE,
    _elements(_NODES),
    _SPACE,
)


def _token_stream(text: str) -> list[tuple[object, ...]]:
    """Expat's events, with whitespace-only text dropped from element-only content.

    An element's content is element-only when none of its own text is
    anything but whitespace and it has no CDATA section.
    """
    parser = expat.ParserCreate(encoding="UTF-8")
    parser.ordered_attributes = True
    root: list[object] = []
    stack: list[list[object]] = [root]

    def start(name: str, attributes: list[str]) -> None:
        children: list[object] = []
        stack[-1].append(("element", name, tuple(attributes), children))
        stack.append(children)

    def end(_name: str) -> None:
        stack.pop()

    def text_data(data: str) -> None:
        stack[-1].append(("text", data))

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = text_data
    parser.CommentHandler = lambda data: stack[-1].append(("comment", data))
    parser.ProcessingInstructionHandler = lambda target, data: stack[-1].append(
        ("pi", target, data)
    )
    parser.StartCdataSectionHandler = lambda: stack[-1].append(("cdata",))
    parser.EndCdataSectionHandler = lambda: stack[-1].append(("/cdata",))
    parser.Parse(text.encode("utf-8"), True)

    flat: list[tuple[object, ...]] = []
    work: list[object] = [root]
    while work:
        item = work.pop()
        if isinstance(item, tuple) and item[0] == "element":
            _kind, name, attributes, children = item
            flat.append(("start", name, attributes))
            work.append(("end", name))
            work.append(children)
            continue
        if isinstance(item, tuple):
            flat.append(item)
            continue
        children = item
        loose = not any(
            child[0] == "cdata" or (child[0] == "text" and child[1].strip(" \t\r\n"))
            for child in children
        )
        merged: list[tuple[object, ...]] = []
        for child in children:
            if child[0] == "text" and merged and merged[-1][0] == "text":
                merged[-1] = ("text", merged[-1][1] + child[1])
            else:
                merged.append(child)
        kept = [
            child
            for child in merged
            if not (loose and child[0] == "text" and not child[1].strip(" \t\r\n"))
        ]
        work.extend(reversed(kept))
    return flat


def test_the_token_stream_sees_whitespace_in_mixed_content() -> None:
    assert _token_stream("<a>x <b/></a>") != _token_stream("<a>x<b/></a>")
    assert _token_stream("<a> <b/></a>") == _token_stream("<a><b/></a>")
    assert _token_stream("<a><![CDATA[ ]]><b/></a>") != _token_stream("<a><b/></a>")


@_SETTINGS
@given(_DOCUMENTS)
def test_format_changes_only_whitespace_in_element_only_content(text: str) -> None:
    output = format_xml(text)
    assert _token_stream(output) == _token_stream(text)
    assert format_xml(output) == output
    assert output.endswith("\n") and not output.endswith("\n\n")
    assert "\r" not in output


# --- Performance ------------------------------------------------------------


def _one_megabyte_xml() -> str:
    """About 1 MB of generated, fake catalog XML."""
    parts = ['<?xml version="1.0" encoding="UTF-8"?>\n<catalog version="1">\n']
    size = len(parts[0])
    index = 0
    while size < 1_000_000:
        item = (
            f'<item id="{index}" active="true"><name>Item {index} &amp; co</name>'
            f"<sku>SKU-{index:06d}</sku>"
            f'<price currency="USD">{index % 997}.99</price>'
            "<tags><tag>red</tag><tag>blue</tag></tags>"
            f'<location warehouse="W{index % 7}" aisle="{index % 40}"/>'
            f"<!-- note {index} --><notes>Fake <b>record</b> café 😀</notes></item>\n"
        )
        parts.append(item)
        size += len(item.encode("utf-8"))
        index += 1
    parts.append("</catalog>\n")
    return "".join(parts)


@pytest.mark.perf
def test_format_and_validate_one_megabyte_of_xml(perf_budget) -> None:
    text = _one_megabyte_xml()
    assert 1_000_000 <= len(text.encode("utf-8")) < 1_100_000

    def format_and_validate() -> None:
        validate_xml(text)
        format_xml(text)

    perf_budget.within(
        0.5, format_and_validate, fixture="~1 MB generated catalog XML, 7k items"
    )
