"""HTML Format with stdlib html.parser (#63).

Format changes only whitespace that carries no meaning, next to block-level
elements. Everything else is copied from the source exactly as written:
Output is built from slices of the source text, at the positions
``html.parser`` reports. Nothing is re-escaped, requoted, or re-cased, tags
and attributes are copied as typed, and no optional end tag is added.

- A child that is a block-level element (``_BLOCK``), with only whitespace
  or nothing between it and its neighbors, goes on its own line, indented
  two spaces per level. That whitespace is dropped. Inside ``head`` every
  element counts as block-level, since nothing there renders.
- Everything between two such children is an inline run: text, inline
  elements, and comments. A run is copied as written. Only the whitespace
  at its two ends, next to a block edge, is dropped. A run that is a single
  element is laid out the same way as a block child.
- An element with no child to put on its own line copies its content as
  written. The contents of ``pre``, ``listing``, and the raw-text elements
  (``_RAW_TEXT``: ``textarea``, ``script``, ``style``, ``title``, and
  others) are always copied byte for byte.
- Void elements (``_VOID``) never take an end tag and never indent. ``<br>``
  and ``<br/>`` stay as written. An optional end tag (``_CLOSED_BY``) is
  never added; ``<li>one<li>two`` closes the first ``li`` where the second
  starts, as a browser does.
- Inside ``svg`` and ``math``, an element whose children are only elements
  and comments, with whitespace between them, puts each child on its own
  line, as XML does. ``<path/>`` closes itself and does not indent. Text
  elements such as ``text`` and ``mi`` copy their content.
- CRLF and CR become LF, as an HTML parser reads them. Output ends with
  one newline.

Structure: an end tag with nothing open to close is
``html_unexpected_close``. An element whose end tag is required, and that
is still open when its parent closes or the text ends, is ``html_unclosed``.
Every problem is found before any is raised, so all of them are reported at
once, and any problem blocks Output. Nesting past ``MAX_DEPTH`` raises
``too_deep``. The tree is built and written with explicit stacks, never
recursion.

Known limit: CSS that changes an element's ``display`` is not seen.
"""

import re
from bisect import bisect_right
from html.parser import HTMLParser

from galley.core.errors import (
    MAX_DEPTH,
    HTMLStructureError,
    TagProblem,
    nesting_error,
    utf8_input_error,
)

INDENT = "  "

# HTML's ASCII whitespace. A no-break space is text, and it renders.
_SPACE = " \t\n\f\r"
_NON_SPACE = re.compile(r"[^ \t\n\f\r]")
_TAG_NAME = re.compile(r"</?([^\s/>]+)")

# Elements the HTML parser closes as soon as they open.
_VOID = frozenset(
    {
        "area",
        "base",
        "basefont",
        "bgsound",
        "br",
        "col",
        "embed",
        "frame",
        "hr",
        "img",
        "input",
        "keygen",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
# Elements whose content is text up to their own end tag. Tags inside are
# not read, whichever html.parser version runs.
_RAW_TEXT = frozenset(
    {
        "iframe",
        "noembed",
        "noframes",
        "plaintext",
        "script",
        "style",
        "textarea",
        "title",
        "xmp",
    }
)
# Content copied byte for byte: the raw-text elements, plus pre and listing,
# whose whitespace renders.
_VERBATIM = _RAW_TEXT | {"listing", "pre"}

_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
# Block-level boxes, list items, table parts, and the options of a select.
# Whitespace next to these never renders. ``select`` itself is inline-block,
# so it is not here.
_BLOCK = _HEADINGS | {
    "address",
    "article",
    "aside",
    "blockquote",
    "body",
    "caption",
    "colgroup",
    "dd",
    "details",
    "dialog",
    "div",
    "dl",
    "dt",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "head",
    "header",
    "hgroup",
    "hr",
    "html",
    "li",
    "main",
    "menu",
    "nav",
    "ol",
    "optgroup",
    "option",
    "p",
    "pre",
    "search",
    "section",
    "summary",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
}

# A start tag that ends an open ``p``.
_P_CLOSERS = _HEADINGS | {
    "address",
    "article",
    "aside",
    "blockquote",
    "dd",
    "details",
    "dialog",
    "div",
    "dl",
    "dt",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "header",
    "hgroup",
    "hr",
    "li",
    "main",
    "menu",
    "nav",
    "ol",
    "p",
    "pre",
    "search",
    "section",
    "table",
    "ul",
}
_CELL_CLOSERS = frozenset({"td", "th", "tr", "thead", "tbody", "tfoot"})
# Elements whose end tag may be left out, and the start tags that close
# them when they are open (the "optional end tags" table in the HTML spec).
_CLOSED_BY: dict[str, frozenset[str]] = {
    "caption": frozenset({"colgroup", "thead", "tbody", "tfoot", "tr"}),
    "colgroup": frozenset({"caption", "colgroup", "thead", "tbody", "tfoot", "tr"}),
    "dd": frozenset({"dd", "dt"}),
    "dt": frozenset({"dd", "dt"}),
    "head": frozenset({"body"}),
    "li": frozenset({"li"}),
    "optgroup": frozenset({"optgroup"}),
    "option": frozenset({"option", "optgroup"}),
    "p": _P_CLOSERS,
    "rp": frozenset({"rp", "rt"}),
    "rt": frozenset({"rp", "rt"}),
    "tbody": frozenset({"tbody", "tfoot"}),
    "td": _CELL_CLOSERS,
    "tfoot": frozenset({"tbody"}),
    "th": _CELL_CLOSERS,
    "thead": frozenset({"tbody", "tfoot"}),
    "tr": frozenset({"tr", "thead", "tbody", "tfoot"}),
}
_OPTIONAL_END = frozenset(_CLOSED_BY) | {"body", "html", "plaintext"}

_FOREIGN_ROOTS = frozenset({"math", "svg"})
# Foreign elements that hold text, so their content is copied as written.
_FOREIGN_TEXT = frozenset(
    {
        "annotation",
        "annotation-xml",
        "desc",
        "foreignobject",
        "mi",
        "mn",
        "mo",
        "ms",
        "mtext",
        "text",
        "textpath",
        "tspan",
    }
)

# Event kinds from the parser.
_TEXT = 0
_START = 1
_SELF_CLOSING = 2
_END = 3
_COMMENT = 4
_DECLARATION = 5
_PI = 6
_MARKED = 7


def format_html(text: str | bytes) -> str:
    """Return ``text`` formatted as HTML.

    Raises ``HTMLStructureError`` listing every tag that does not nest, and
    ``InputError`` for invalid UTF-8 or nesting past ``MAX_DEPTH``.
    """
    source = _source_text(text)
    line_starts = _line_starts(source)
    root = _build_tree(source, line_starts, _events(source, line_starts))
    lines: list[str] = []
    _render_children(source, root, 0, lines)
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def _source_text(text: str | bytes) -> str:
    """The text to parse: UTF-8, no leading BOM, and LF newlines."""
    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise utf8_input_error(text, exc.start) from None
    if text.startswith("﻿"):
        text = text[1:]
    if "\r" in text:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


class _Atom:
    """Text, a comment, a declaration, a PI, a marked section, or a tag that
    has no content (a void element, or ``<x/>`` inside ``svg`` or ``math``).

    ``name`` is the lowercase tag name, or "" when the atom is not a tag.
    """

    __slots__ = ("end", "kind", "name", "start")

    def __init__(self, kind: int, start: int, end: int, name: str = "") -> None:
        self.kind = kind
        self.start = start
        self.end = end
        self.name = name


class _Element:
    """One element with content, located in the source.

    ``tag_end`` is just past the start tag. ``close`` is the end tag's
    ``(start, end)``, or None when the end tag was left out. ``end`` is
    where the element stops, its end tag included. ``children`` holds atoms
    and elements in source order, with no gaps between them.
    """

    __slots__ = ("children", "close", "end", "foreign", "name", "start", "tag_end")

    def __init__(
        self, name: str, start: int, tag_end: int, *, foreign: bool = False
    ) -> None:
        self.name = name
        self.start = start
        self.tag_end = tag_end
        self.close: tuple[int, int] | None = None
        self.end = tag_end
        self.foreign = foreign
        self.children: list[_Atom | _Element] = []


class _Collector(HTMLParser):
    """Records each token's kind, start offset, and tag details.

    Text, entity references, and character references are all text. The
    offset comes from ``getpos``, which is the start of the token whose
    handler is running.
    """

    def __init__(self, line_starts: list[int]) -> None:
        super().__init__(convert_charrefs=False)
        self.events: list[tuple[int, int, str, int]] = []
        self._line_starts = line_starts

    def _offset(self) -> int:
        line, column = self.getpos()
        return self._line_starts[line - 1] + column

    def _add(self, kind: int, name: str = "", tag_length: int = 0) -> None:
        self.events.append((kind, self._offset(), name, tag_length))

    def handle_data(self, data: str) -> None:
        self._add(_TEXT)

    def handle_entityref(self, name: str) -> None:
        self._add(_TEXT)

    def handle_charref(self, name: str) -> None:
        self._add(_TEXT)

    def handle_starttag(self, tag: str, attrs: object) -> None:
        self._add(_START, tag, len(self.get_starttag_text() or ""))

    def handle_startendtag(self, tag: str, attrs: object) -> None:
        self._add(_SELF_CLOSING, tag, len(self.get_starttag_text() or ""))

    def handle_endtag(self, tag: str) -> None:
        self._add(_END, tag)

    def handle_comment(self, data: str) -> None:
        self._add(_COMMENT)

    def handle_decl(self, decl: str) -> None:
        self._add(_DECLARATION)

    def handle_pi(self, data: str) -> None:
        self._add(_PI)

    def unknown_decl(self, data: str) -> None:
        self._add(_MARKED)


def _line_starts(source: str) -> list[int]:
    """The offset where each line starts. Line 1 starts at 0."""
    starts = [0]
    starts.extend(match.end() for match in re.finditer("\n", source))
    return starts


def _events(
    source: str, line_starts: list[int]
) -> list[tuple[int, int, int, str, int]]:
    """Every token as ``(kind, start, end, name, tag_end)``.

    A token ends where the next one starts, so the spans cover the source
    with no gaps. ``tag_end`` is just past a start tag, and ``end`` for the
    other kinds.
    """
    collector = _Collector(line_starts)
    collector.feed(source)
    collector.close()
    found = collector.events
    events: list[tuple[int, int, int, str, int]] = []
    for index, (kind, start, name, tag_length) in enumerate(found):
        end = found[index + 1][1] if index + 1 < len(found) else len(source)
        tag_end = start + tag_length if tag_length else end
        events.append((kind, start, end, name, tag_end))
    return events


def _build_tree(
    source: str, line_starts: list[int], events: list[tuple[int, int, int, str, int]]
) -> _Element:
    """Nest the tokens under a document root, or raise every problem found."""
    root = _Element("", 0, 0)
    stack = [root]
    problems = _Problems(source, line_starts)
    for kind, start, end, name, tag_end in events:
        top = stack[-1]
        if top.name in _RAW_TEXT:
            if kind == _END and name == top.name:
                _close(stack, start, end)
            continue
        if kind == _END:
            _end_tag(stack, problems, name, start, end)
            continue
        if kind not in (_START, _SELF_CLOSING):
            top.children.append(_Atom(kind, start, end))
            continue
        leaf = name in _VOID or (kind == _SELF_CLOSING and _in_foreign(top, name))
        _close_implied(stack, name, start)
        top = stack[-1]
        if leaf:
            top.children.append(_Atom(_SELF_CLOSING, start, tag_end, name))
        else:
            if len(stack) > MAX_DEPTH:
                line, column = problems.line_column(start)
                raise nesting_error(line, column, depth=len(stack))
            element = _Element(name, start, tag_end, foreign=_in_foreign(top, name))
            top.children.append(element)
            stack.append(element)
        if tag_end < end:
            stack[-1].children.append(_Atom(_TEXT, tag_end, end))
    for element in reversed(stack[1:]):
        element.end = len(source)
        if element.name not in _OPTIONAL_END:
            problems.add("html_unclosed", element.start)
    if problems.found:
        problems.found.sort(key=lambda problem: (problem.lineno, problem.colno))
        raise HTMLStructureError(tuple(problems.found))
    root.end = len(source)
    return root


class _Problems:
    """Problems found so far, located through precomputed line starts."""

    def __init__(self, source: str, line_starts: list[int]) -> None:
        self._source = source
        self._line_starts = line_starts
        self.found: list[TagProblem] = []

    def add(self, code: str, start: int) -> None:
        line, column = self.line_column(start)
        match = _TAG_NAME.match(self._source, start)
        token = match.group(1) if match else ""
        self.found.append(TagProblem(code, line, column, token))

    def line_column(self, index: int) -> tuple[int, int]:
        """1-based line and code-point column of ``index``."""
        line = bisect_right(self._line_starts, index)
        return line, index - self._line_starts[line - 1] + 1


def _in_foreign(parent: _Element, name: str) -> bool:
    """True when an element ``name`` opened in ``parent`` is SVG or MathML."""
    if parent.foreign:
        return parent.name != "foreignobject"
    return name in _FOREIGN_ROOTS


def _close_implied(stack: list[_Element], name: str, start: int) -> None:
    """Close the open elements a ``name`` start tag ends, as a browser does.

    Only the run of elements at the top whose end tag may be left out is
    searched, so ``<ul><li><ul><li>`` nests the second list. Everything above
    the lowest element that ``name`` closes is closed with it.
    """
    lowest = None
    for index in range(len(stack) - 1, 0, -1):
        open_name = stack[index].name
        if open_name not in _OPTIONAL_END:
            break
        if name in _CLOSED_BY.get(open_name, ()):
            lowest = index
    if lowest is None:
        return
    while len(stack) > lowest:
        stack.pop().end = start


def _end_tag(
    stack: list[_Element], problems: _Problems, name: str, start: int, end: int
) -> None:
    """Close the nearest open ``name``, or record a stray end tag."""
    for index in range(len(stack) - 1, 0, -1):
        if stack[index].name == name:
            break
    else:
        problems.add("html_unexpected_close", start)
        stack[-1].children.append(_Atom(_TEXT, start, end))
        return
    while len(stack) - 1 > index:
        inner = stack.pop()
        inner.end = start
        if inner.name not in _OPTIONAL_END:
            problems.add("html_unclosed", inner.start)
    _close(stack, start, end)


def _close(stack: list[_Element], start: int, end: int) -> None:
    element = stack.pop()
    element.close = (start, end)
    element.end = end


def _render_children(
    source: str, parent: _Element, depth: int, lines: list[str]
) -> None:
    """Append the lines for ``parent``'s children, at ``depth``.

    Work items are a child to lay out or a finished line, so nesting never
    recurses.
    """
    work: list[tuple[int, _Element | _Atom | str]] = [
        (depth, piece) for piece in reversed(_pieces(source, parent))
    ]
    while work:
        level, item = work.pop()
        indent = INDENT * level
        if isinstance(item, str):
            lines.append(indent + item)
            continue
        if isinstance(item, _Atom):
            lines.append(indent + source[item.start : item.end])
            continue
        head = source[item.start : item.tag_end]
        close = "" if item.close is None else source[item.close[0] : item.close[1]]
        pieces = None if item.name in _VERBATIM else _pieces(source, item)
        if pieces is None:
            inner_end = item.end if item.close is None else item.close[0]
            inner = source[item.tag_end : inner_end]
            if item.close is None:
                # The left-out end tag sits after this whitespace, at a
                # block edge, where it does not render. The one verbatim
                # element left open is plaintext, which runs to the end
                # of the text, so this is the text's final newline.
                inner = inner.rstrip(_SPACE)
            lines.append(indent + head + inner + close)
            continue
        lines.append(indent + head)
        if close:
            work.append((level, close))
        work.extend((level + 1, piece) for piece in reversed(pieces))


def _pieces(source: str, parent: _Element) -> list[_Element | _Atom | str] | None:
    """What goes on ``parent``'s child lines, or None to copy it as written.

    A piece is a child that goes on its own line (an element or an atom),
    or the text of an inline run. The document root always lays out.
    """
    children = parent.children
    if parent.foreign:
        on_own_line = _foreign_own_lines(source, parent)
    else:
        on_own_line = [
            _own_line(source, parent, children, i) for i in range(len(children))
        ]
    if parent.name and not any(on_own_line):
        return None
    pieces: list[_Element | _Atom | str] = []
    run: list[_Atom | _Element] = []
    for child, own in zip(children, on_own_line, strict=True):
        if own:
            _add_run(source, run, pieces)
            run = []
            pieces.append(child)
        else:
            run.append(child)
    _add_run(source, run, pieces)
    return pieces


def _own_line(
    source: str, parent: _Element, children: list[_Atom | _Element], index: int
) -> bool:
    """True when ``children[index]`` of an HTML ``parent`` gets its own line."""
    child = children[index]
    if isinstance(child, _Atom):
        if child.kind in (_DECLARATION, _PI) and not parent.name:
            block = True
        else:
            block = child.kind == _SELF_CLOSING and (
                child.name in _BLOCK or parent.name == "head"
            )
    else:
        block = child.name in _BLOCK or parent.name == "head"
    if not block:
        return False
    before = children[index - 1] if index else None
    after = children[index + 1] if index + 1 < len(children) else None
    return _blank_or_markup(source, before) and _blank_or_markup(source, after)


def _foreign_own_lines(source: str, parent: _Element) -> list[bool]:
    """Which children of an SVG or MathML ``parent`` get their own lines.

    As in XML, that is every child but text, and only when all the text
    between them is whitespace. Text elements such as ``text`` keep theirs.
    """
    children = parent.children
    if parent.name in _FOREIGN_TEXT or not all(
        _is_space(source, child) for child in children
    ):
        return [False] * len(children)
    return [
        not (isinstance(child, _Atom) and child.kind == _TEXT) for child in children
    ]


def _blank_or_markup(source: str, neighbor: _Atom | _Element | None) -> bool:
    if neighbor is None or not isinstance(neighbor, _Atom):
        return True
    return neighbor.kind != _TEXT or _is_space(source, neighbor)


def _is_space(source: str, child: _Atom | _Element) -> bool:
    """True for a text atom that is only whitespace. A marked section, such
    as ``<![CDATA[x]]>``, counts as text."""
    if not isinstance(child, _Atom):
        return True
    if child.kind == _MARKED:
        return False
    if child.kind != _TEXT:
        return True
    return _NON_SPACE.search(source, child.start, child.end) is None


def _add_run(
    source: str, run: list[_Atom | _Element], pieces: list[_Element | _Atom | str]
) -> None:
    """Add an inline run, without the whitespace at its two ends."""
    first = 0
    last = len(run)
    while first < last and _blank_text(source, run[first]):
        first += 1
    while last > first and _blank_text(source, run[last - 1]):
        last -= 1
    if first == last:
        return
    if last - first == 1 and isinstance(run[first], _Element):
        pieces.append(run[first])
        return
    text = source[run[first].start : run[last - 1].end].strip(_SPACE)
    if text:
        pieces.append(text)


def _blank_text(source: str, child: _Atom | _Element) -> bool:
    return isinstance(child, _Atom) and child.kind == _TEXT and _is_space(source, child)
