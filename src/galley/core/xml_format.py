"""XML Format and Validate with stdlib pyexpat (#63).

Format changes only whitespace between markup where that whitespace
carries no meaning. Everything else is copied from the source exactly as
written: Output is built from slices of the UTF-8 source, at the byte
offsets expat reports. Nothing is re-escaped, requoted, or expanded, and
``<a></a>`` stays as written.

- An element whose children are only elements, comments, and PIs, with
  whitespace between them, puts each child on its own line, indented two
  spaces per level.
- Any other content (text, CDATA, an entity reference, or nothing at all)
  and any element with ``xml:space="preserve"`` copies its whole inner
  content byte for byte.
- Whitespace runs inside a start tag, outside its quoted values, become one
  space, so a multi-line start tag joins into one line. A run just before
  ``>`` is dropped.
- The XML declaration, the doctype, and comments and PIs outside the root
  each go on their own line. Output uses LF and ends with one newline.

Security: the parser decodes UTF-8 whatever the declaration says, reads no
parameter entities and no external DTD, and stops at any ``<!ENTITY>``
declaration or external entity reference. No etree, minidom, or
defusedxml is used, and nothing is ever fetched. Expat 2.6.0 or newer is
required. Nesting past ``MAX_DEPTH`` raises ``too_deep``, and the walk uses
explicit stacks, never recursion.
"""

import re
from collections.abc import Callable
from xml.parsers import expat

from galley.core.errors import (
    MAX_DEPTH,
    XMLSyntaxError,
    nesting_error,
    utf8_input_error,
)

# 2.6.0 has the huge-token fix (CVE-2023-52425) and the billion-laughs
# protection from 2.4.1.
MIN_EXPAT_VERSION = (2, 6, 0)
INDENT = b"  "

_XML_SPACE = "http://www.w3.org/XML/1998/namespace space"
_NON_SPACE = re.compile(rb"[^ \t\r\n]")
# A start tag expat already accepted: the name, then attributes with
# quoted values, which may hold ``>``.
_START_TAG = re.compile(
    rb"""<[^\s/>]+(?:\s+[^\s=]+\s*=\s*(?:"[^"]*"|'[^']*'))*\s*/?>"""
)
_ATTRIBUTE = re.compile(rb"""\s([^\s=]+)\s*=\s*(?:"[^"]*"|'[^']*')""")
# Quoted values are kept. Any other whitespace run becomes one space.
_TAG_SPACE = re.compile(rb"""("[^"]*"|'[^']*')|\s+""")
_NAME = re.compile(rb"[^\s/>=;&<]+")
_CONTINUATION = re.compile(rb"[\x80-\xbf]")
# What expat reads after ``&`` as a name or character reference: anything
# but whitespace, markup, quotes, or the ``;`` that would end it.
_REFERENCE_TAIL = re.compile(rb"""[^\s<>&;"']*""")

_BAD_TOKEN_ERRORS = frozenset(
    {
        expat.errors.codes[expat.errors.XML_ERROR_INVALID_TOKEN],
        expat.errors.codes[expat.errors.XML_ERROR_PARTIAL_CHAR],
    }
)
# The text ends inside a tag, comment, or PI. Expat points at its ``<``.
_UNFINISHED = expat.errors.codes[expat.errors.XML_ERROR_UNCLOSED_TOKEN]
_ERROR_CODES = {
    expat.errors.codes[expat.errors.XML_ERROR_TAG_MISMATCH]: "xml_mismatched_tag",
    expat.errors.codes[expat.errors.XML_ERROR_DUPLICATE_ATTRIBUTE]: (
        "xml_duplicate_attribute"
    ),
    expat.errors.codes[expat.errors.XML_ERROR_UNDEFINED_ENTITY]: (
        "xml_undefined_entity"
    ),
    expat.errors.codes[expat.errors.XML_ERROR_UNBOUND_PREFIX]: "xml_unbound_prefix",
    expat.errors.codes[expat.errors.XML_ERROR_JUNK_AFTER_DOC_ELEMENT]: (
        "xml_after_root"
    ),
}
_NO_ELEMENTS = expat.errors.codes[expat.errors.XML_ERROR_NO_ELEMENTS]

# Event kinds. ``_START_PRESERVE`` is a start tag with xml:space="preserve".
_START = 0
_START_PRESERVE = 1
_END = 2
_COMMENT = 3
_PI = 4
_CDATA_START = 5
_CDATA_END = 6
_XML_DECL = 7
_DOCTYPE_START = 8
_DOCTYPE_END = 9
_STARTS = (_START, _START_PRESERVE)

_expat_checked = False


class ExpatTooOldError(RuntimeError):
    """This Python's expat is older than ``MIN_EXPAT_VERSION``."""


def expat_problem(found: tuple[int, int, int] | None = None) -> str | None:
    """Return why XML can't run on expat ``found``, or None when it can.

    ``found`` defaults to the expat this Python was built with.
    """
    version = expat.version_info if found is None else found
    if tuple(version) >= MIN_EXPAT_VERSION:
        return None
    return (
        f"XML needs expat {_dotted(MIN_EXPAT_VERSION)} or newer, "
        f"and this Python has expat {_dotted(version)}."
    )


def format_xml(text: str | bytes) -> str:
    """Return ``text`` formatted as XML.

    Raises ``XMLSyntaxError`` for XML that is not well formed or declares
    an entity, ``InputError`` for invalid UTF-8 or nesting past
    ``MAX_DEPTH``, and ``ExpatTooOldError`` when expat is too old.
    """
    _require_expat()
    data = _source_bytes(text)
    events = _parse(data)
    return _render(data, events).decode("utf-8", "surrogatepass")


def validate_xml(text: str | bytes) -> None:
    """Raise what ``format_xml`` raises for ``text``, without building Output."""
    _require_expat()
    _parse(_source_bytes(text))


def _require_expat() -> None:
    global _expat_checked
    if _expat_checked:
        return
    problem = expat_problem()
    if problem is not None:
        raise ExpatTooOldError(problem)
    _expat_checked = True


def _dotted(version: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in version)


def _source_bytes(text: str | bytes) -> bytes:
    """UTF-8 bytes to parse, with a leading BOM removed.

    Bytes must already be UTF-8. A lone surrogate in ``str`` is kept as
    its UTF-8 form, which expat then rejects at its place.
    """
    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise utf8_input_error(text, exc.start) from None
    if text.startswith("﻿"):
        text = text[1:]
    return text.encode("utf-8", "surrogatepass")


def _parse(data: bytes) -> list[tuple[int, int]]:
    """Check ``data`` and return its markup events as ``(kind, offset)``.

    Text has no events. It is the gap between one event's end and the next
    event's offset. ``offset`` is expat's byte index, which is where the
    markup starts, except for the doctype events, the end of an empty
    element (just past ``/>``), and the CDATA end (at ``]]>``).
    """
    parser = expat.ParserCreate(encoding="UTF-8", namespace_separator=" ")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.UseForeignDTD(False)
    parser.ordered_attributes = True
    events: list[tuple[int, int]] = []
    opened: list[int] = []
    add = events.append

    def start(_name: str, attributes: list[str]) -> None:
        index = parser.CurrentByteIndex
        if len(opened) >= MAX_DEPTH:
            line, column = _line_column(data, index)
            raise nesting_error(line, column, depth=len(opened) + 1)
        opened.append(index)
        add((_START_PRESERVE if _preserves_space(attributes) else _START, index))

    def end(_name: str) -> None:
        opened.pop()
        add((_END, parser.CurrentByteIndex))

    def marker(kind: int) -> Callable[..., None]:
        def record(*_args: object) -> None:
            add((kind, parser.CurrentByteIndex))

        return record

    def declared(*_args: object) -> None:
        index = parser.CurrentByteIndex
        found = data.rfind(b"<!ENTITY", 0, index + 1)
        raise _located("xml_entity_declared", data, max(found, 0))

    def external(*_args: object) -> None:
        raise _located("xml_other", data, max(parser.CurrentByteIndex, 0))

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CommentHandler = marker(_COMMENT)
    parser.ProcessingInstructionHandler = marker(_PI)
    parser.StartCdataSectionHandler = marker(_CDATA_START)
    parser.EndCdataSectionHandler = marker(_CDATA_END)
    parser.XmlDeclHandler = marker(_XML_DECL)
    parser.StartDoctypeDeclHandler = marker(_DOCTYPE_START)
    parser.EndDoctypeDeclHandler = marker(_DOCTYPE_END)
    parser.EntityDeclHandler = declared
    parser.UnparsedEntityDeclHandler = declared
    parser.ExternalEntityRefHandler = external
    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        raise _expat_error(exc, parser.ErrorByteIndex, data, opened) from None
    return events


def _preserves_space(attributes: list[str]) -> bool:
    for index in range(0, len(attributes), 2):
        if attributes[index] == _XML_SPACE:
            return attributes[index + 1] == "preserve"
    return False


def _expat_error(
    exc: expat.ExpatError, index: int, data: bytes, opened: list[int]
) -> XMLSyntaxError:
    code = exc.code
    if code == _NO_ELEMENTS:
        if opened:
            where = opened[-1]
            return _located("xml_unclosed", data, where, _name_at(data, where + 1))
        return _located("xml_no_root", data, max(index, 0))
    if code == _UNFINISHED:
        return _located("xml_unfinished_tag", data, max(index, 0))
    if code in _BAD_TOKEN_ERRORS:
        return _located("xml_bad_token", data, _stray_at(data, max(index, 0)))
    kind = _ERROR_CODES.get(code, "xml_other")
    if index < 0:
        return XMLSyntaxError(kind, lineno=exc.lineno, colno=exc.offset + 1)
    token = ""
    found = ""
    if kind == "xml_mismatched_tag":
        # Expat points at the name. The end tag starts two bytes earlier.
        found = _name_at(data, index)
        if data[index - 2 : index] == b"</":
            index -= 2
        if opened:
            token = _name_at(data, opened[-1] + 1)
    elif kind == "xml_duplicate_attribute":
        token = _name_at(data, index)
    elif kind == "xml_undefined_entity":
        token = _name_at(data, index + 1)
    elif kind == "xml_unbound_prefix":
        token = _unbound_prefix(data, index, opened)
    return _located(kind, data, index, token, found)


def _stray_at(data: bytes, index: int) -> int:
    """Where a bad-token error really starts, given expat's ``index``.

    Expat stops on the byte after a stray ``<``. After a stray ``&`` it
    first reads any letters, digits, or ``#`` that follow as an entity or
    character reference, as in ``AT&T`` or ``&nbsp`` with no ``;``. Either
    way the ``&`` or ``<`` is what to fix, so the error points at it.
    """
    if index > 0 and data[index - 1 : index] == b"<":
        return index - 1
    amp = data.rfind(b"&", 0, index)
    if amp >= 0 and _REFERENCE_TAIL.fullmatch(data, amp + 1, index):
        return amp
    return index


def _unbound_prefix(data: bytes, index: int, opened: list[int]) -> str:
    """The first prefix in the start tag at ``index`` that nothing declares.

    Expat points at the tag. Its ancestors are still in ``opened``, so the
    ``xmlns:`` attributes on them and on this tag are the declared prefixes.
    """
    declared = {b"xml", b"xmlns"}
    names: list[bytes] = []
    for start in (*opened, index):
        match = _START_TAG.match(data, start)
        if match is None:
            continue
        # The loop ends on the tag at ``index``, so ``names`` are its names.
        names = _tag_names(match.group())
        declared.update(name[6:] for name in names if name.startswith(b"xmlns:"))
    for name in names:
        prefix, colon, _local = name.partition(b":")
        if colon and prefix not in declared:
            return prefix.decode("utf-8", "surrogatepass")
    return ""


def _tag_names(tag: bytes) -> list[bytes]:
    """The element name, then each attribute name, of start tag ``tag``."""
    element = _NAME.match(tag, 1)
    names = [] if element is None else [element.group()]
    names.extend(attribute.group(1) for attribute in _ATTRIBUTE.finditer(tag))
    return names


def _located(
    code: str, data: bytes, index: int, token: str = "", found: str = ""
) -> XMLSyntaxError:
    """An error at byte ``index``. At the end of the text it has no column.

    A problem found only because the text ran out, such as no root element
    or an unclosed CDATA section, names its line alone, as YAML problems
    do (#131). Its column would only point past the last character.
    """
    line, column = _line_column(data, index)
    if index >= len(data):
        column = 0
    return XMLSyntaxError(code, lineno=line, colno=column, token=token, found=found)


def _name_at(data: bytes, index: int) -> str:
    match = _NAME.match(data, index)
    if match is None:
        return ""
    return match.group().decode("utf-8", "surrogatepass")


def _line_column(data: bytes, index: int) -> tuple[int, int]:
    """1-based line and code-point column of byte ``index``.

    LF, CRLF, and a bare CR each end one line, as expat and the editor
    count them (#123). UTF-8 continuation bytes are not counted, so each
    code point is one column, a character past U+FFFF included.
    """
    breaks = (
        data.count(b"\n", 0, index)
        + data.count(b"\r", 0, index)
        - data.count(b"\r\n", 0, index)
    )
    line_start = max(data.rfind(b"\n", 0, index), data.rfind(b"\r", 0, index)) + 1
    prefix = data[line_start:index]
    column = len(prefix) - len(_CONTINUATION.findall(prefix)) + 1
    return breaks + 1, column


def _render(data: bytes, events: list[tuple[int, int]]) -> bytes:
    """Build Output from source slices. ``events`` came from ``_parse``."""
    first, after, root = _build_tree(data, events)
    lines = _top_level(data, events[:first])
    _render_element(data, root, lines)
    lines.extend(_top_level(data, events[after:]))
    text = b"\n".join(lines) + b"\n"
    if b"\r" in text:
        text = text.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return text


class _Element:
    """One element, located in the source.

    ``tag_end`` is just past the start tag. ``close`` is the end tag's
    ``(start, end)``, or None for ``<a/>``. ``verbatim`` is True when the
    inner content is copied as written. ``children`` holds elements and
    the ``(start, end)`` of comments and PIs, in order.
    """

    __slots__ = ("children", "close", "start", "tag_end", "verbatim")

    def __init__(self, start: int, tag_end: int, verbatim: bool) -> None:
        self.start = start
        self.tag_end = tag_end
        self.close: tuple[int, int] | None = None
        self.verbatim = verbatim
        self.children: list[_Element | tuple[int, int]] = []


def _build_tree(
    data: bytes, events: list[tuple[int, int]]
) -> tuple[int, int, _Element]:
    """Return the root's first event, the first event after it, and the root.

    Text between two events inside an element is ``data[position:offset]``.
    Any text that is not XML whitespace, and any CDATA section, makes that
    element's content verbatim.
    """
    stack: list[_Element] = []
    first = -1
    position = 0
    for number, (kind, offset) in enumerate(events):
        if kind in _STARTS:
            if stack and _NON_SPACE.search(data, position, offset):
                stack[-1].verbatim = True
            match = _START_TAG.match(data, offset)
            if match is None:
                raise AssertionError(f"expat accepted a start tag at byte {offset}")
            element = _Element(offset, match.end(), kind == _START_PRESERVE)
            if stack:
                stack[-1].children.append(element)
            else:
                first = number
            stack.append(element)
            position = element.tag_end
            continue
        if not stack:
            continue
        top = stack[-1]
        if kind == _CDATA_START:
            top.verbatim = True
        elif kind == _CDATA_END:
            position = offset + 3
        elif kind in (_COMMENT, _PI):
            if _NON_SPACE.search(data, position, offset):
                top.verbatim = True
            end = _markup_end(data, kind, offset)
            top.children.append((offset, end))
            position = end
        elif kind == _END:
            if _NON_SPACE.search(data, position, offset):
                top.verbatim = True
            if offset == top.tag_end and data[offset - 2 : offset] == b"/>":
                position = offset
            else:
                position = data.index(b">", offset) + 1
                top.close = (offset, position)
                if not top.children:
                    top.verbatim = True
            stack.pop()
            if not stack:
                return first, number + 1, top
    raise AssertionError("expat accepted a document without a root element")


def _markup_end(data: bytes, kind: int, offset: int) -> int:
    if kind == _COMMENT:
        return data.index(b"-->", offset + 4) + 3
    return data.index(b"?>", offset + 2) + 2


def _top_level(data: bytes, events: list[tuple[int, int]]) -> list[bytes]:
    """The declaration, doctype, comments, and PIs in ``events``.

    ``events`` are the ones before the root or after it. Comments and PIs
    inside the doctype's internal subset stay part of the doctype.
    """
    found: list[bytes] = []
    position = 0
    doctype_start: int | None = None
    for kind, offset in events:
        if kind == _DOCTYPE_START:
            doctype_start = data.index(b"<!DOCTYPE", position)
            continue
        if kind == _DOCTYPE_END and doctype_start is not None:
            position = data.index(b">", offset) + 1
            found.append(data[doctype_start:position])
            doctype_start = None
            continue
        if doctype_start is not None:
            continue
        if kind == _XML_DECL:
            end = data.index(b"?>", offset) + 2
        elif kind in (_COMMENT, _PI):
            end = _markup_end(data, kind, offset)
        else:
            continue
        found.append(data[offset:end])
        position = end
    return found


def _render_element(data: bytes, root: _Element, lines: list[bytes]) -> None:
    """Append the lines for ``root`` and everything inside it.

    Work items are an element or comment/PI with its depth, or an end tag
    with its depth, so nesting never recurses.
    """
    work: list[tuple[int, _Element | tuple[int, int] | bytes]] = [(0, root)]
    while work:
        depth, item = work.pop()
        indent = INDENT * depth
        if isinstance(item, bytes):
            lines.append(indent + item)
            continue
        if isinstance(item, tuple):
            lines.append(indent + data[item[0] : item[1]])
            continue
        head = _start_tag(data[item.start : item.tag_end])
        if item.close is None:
            lines.append(indent + head)
            continue
        close = _end_tag(data[item.close[0] : item.close[1]])
        if item.verbatim:
            inner = data[item.tag_end : item.close[0]]
            lines.append(indent + head + inner + close)
            continue
        lines.append(indent + head)
        work.append((depth, close))
        work.extend((depth + 1, child) for child in reversed(item.children))


def _start_tag(tag: bytes) -> bytes:
    """Collapse whitespace outside quoted values. Values stay as written."""
    if not (b"\n" in tag or b"\t" in tag or b"\r" in tag or b"  " in tag) and not (
        tag.endswith(b" >")
    ):
        return tag
    joined = _TAG_SPACE.sub(_keep_quoted, tag)
    if joined.endswith(b" >"):
        joined = joined[:-2] + b">"
    return joined


def _keep_quoted(match: re.Match[bytes]) -> bytes:
    quoted = match.group(1)
    return quoted if quoted is not None else b" "


def _end_tag(tag: bytes) -> bytes:
    """``</name>``, with any whitespace before ``>`` dropped."""
    return tag[:-1].rstrip() + b">"
