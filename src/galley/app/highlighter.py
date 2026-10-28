"""JSON, YAML, XML, and HTML syntax colors for the code editors.

Colors follow spec §3: keys ``accent.ink``, strings ``string``, numbers and
literals ``text.code.value`` bold, punctuation and comments ``text.muted``.
YAML uses the same colors as JSON. A dark editor Base switches to the
``*_DARK`` set from ``theme.py``.

XML adds no colors (Bill, #63): element names use the key color, attribute
names and ``< > / =`` punctuation, attribute values the string color, and
comments, the doctype, PIs, the XML declaration, and the CDATA markers the
muted punctuation color. Text, the CDATA body, and entities stay plain.
HTML uses the same lexer and mapping (Bill, #63 slice 5). Tag names match
in any case, an unquoted attribute value is a string, and the bodies of
``script``, ``style``, and ``pre`` stay plain text until their end tag.

Highlighting works one line at a time with precompiled patterns. Between
lines it carries a YAML block scalar (``|`` or ``>``), whose indented lines
are string content, and for XML and HTML an open comment, CDATA section,
PI, doctype, start tag, quoted attribute value, or HTML raw body.
"""

import re
from typing import Final, Literal, NamedTuple

from PySide6.QtGui import (
    QColor,
    QFont,
    QSyntaxHighlighter,
    QTextBlock,
    QTextCharFormat,
    QTextDocument,
)

from galley.app.theme import (
    ACCENT_INK,
    ACCENT_INK_DARK,
    STRING,
    STRING_DARK,
    TEXT_CODE_VALUE,
    TEXT_CODE_VALUE_DARK,
    TEXT_MUTED,
    TEXT_MUTED_DARK,
)

SyntaxMode = Literal["JSON", "YAML", "XML", "HTML"]
SYNTAX_MODES: Final[tuple[SyntaxMode, ...]] = ("JSON", "YAML", "XML", "HTML")


class SyntaxColors(NamedTuple):
    key: str
    string: str
    value: str
    punctuation: str


LIGHT_SYNTAX: Final = SyntaxColors(ACCENT_INK, STRING, TEXT_CODE_VALUE, TEXT_MUTED)
DARK_SYNTAX: Final = SyntaxColors(
    ACCENT_INK_DARK, STRING_DARK, TEXT_CODE_VALUE_DARK, TEXT_MUTED_DARK
)
# Same 128 cut as theme.is_dark: below this lightness, Base is dark. Base,
# not the color scheme, decides, so the colors match the page actually
# drawn even when an app palette stays light under a dark scheme.
_DARK_LIGHTNESS: Final = 128

# Block states. A YAML block scalar stores its parent column + 2, so a
# top-level scalar (parent column -1) is still positive.
_PLAIN_STATE: Final = 0
# Qt's state for a block that was never highlighted. Every mode leaves a
# block it colored at a state of 0 or more, so a block still at this state
# is "cold": it has no colors yet. A large document is colored a chunk of
# cold blocks at a time (#117), and ``allow_cold_blocks`` sets the chunk.
COLD_STATE: Final = -1

_JSON_TOKEN: Final = re.compile(
    r"""
      (?P<string>"(?:[^"\\]|\\.?)*"?)
    | (?P<number>(?<![\w.])-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?![\w.]))
    | (?P<literal>(?<!\w)(?:true|false|null)(?!\w))
    | (?P<punct>[{}\[\],:])
    """,
    re.VERBOSE,
)

_COLON_AHEAD: Final = re.compile(r"[ \t]*:")
_SPACES: Final = re.compile(r"[ \t]*")
_DOC_MARKER: Final = re.compile(r"(?:---|\.\.\.)(?=\s|$)")
# ``-`` list items, ``?`` complex keys, ``:`` complex values.
_ENTRY_MARKER: Final = re.compile(r"[-?:](?=\s|$)")
_BLOCK_KEY: Final = re.compile(
    r"""
    (?P<key>
        "(?:[^"\\]|\\.)*"
      | '(?:[^']|'')*'
      | (?:[^\s\#'"{}\[\],&*!|>%@`?:-]|[?:-](?=\S))
        (?:[^\#]|(?<=\S)\#)*?
    )
    [ \t]*(?P<colon>:)(?=\s|$)
    """,
    re.VERBOSE,
)
_QUOTED: Final = re.compile(r""""(?:[^"\\]|\\.?)*"?|'(?:[^']|'')*'?""")
_NODE_PROPERTY: Final = re.compile(r"[&*!][^\s,\[\]{}]*")
_BLOCK_SCALAR: Final = re.compile(r"[|>][-+0-9]*(?=\s|$)")
_PLAIN_VALUE: Final = re.compile(r"(?:[^\#]|(?<=\S)\#)*")
_YAML_FLOW_TOKEN: Final = re.compile(
    r"""
      (?P<comment>(?<!\S)\#.*)
    | (?P<quoted>"(?:[^"\\]|\\.?)*"?|'(?:[^']|'')*'?)
    | (?P<punct>[{}\[\],:])
    | (?P<property>[&*!][^\s,\[\]{}]*)
    | (?P<plain>
        [^\s{}\[\],\#'"]
        (?:
            [^\s{}\[\],:]
          | :(?![\s,\[\]{}]|$)
          | [ \t]+(?=[^\s{}\[\],:\#]|:(?![\s,\[\]{}]|$))
        )*
      )
    """,
    re.VERBOSE,
)
_YAML_NUMBER: Final = re.compile(
    r"""
      [-+]?(?:\d[\d_]*(?:\.[\d_]*)?|\.\d[\d_]*)(?:[eE][-+]?\d+)?
    | 0x[0-9a-fA-F_]+
    | 0o[0-7_]+
    | [-+]?\.(?:inf|Inf|INF)
    | \.(?:nan|NaN|NAN)
    """,
    re.VERBOSE,
)
_YAML_LITERALS: Final = frozenset(
    {"true", "True", "TRUE", "false", "False", "FALSE", "null", "Null", "NULL", "~"}
)
_ASTRAL: Final = re.compile("[\U00010000-\U0010ffff]")

# XML and HTML block states: what is still open at the end of a line. The
# low four bits are the state. In HTML, the bits above them name the raw
# element a start tag opens, or whose body is open: 1 script, 2 style, 3 pre.
# YAML's states are positive too, but a highlighter colors one mode at a time.
_XML_COMMENT: Final = 1
_XML_CDATA: Final = 2
_XML_PI: Final = 3
_XML_DOCTYPE: Final = 4
_XML_SUBSET: Final = 5
_XML_TAG: Final = 6
_XML_TAG_DOUBLE: Final = 7
_XML_TAG_SINGLE: Final = 8
_HTML_RAW: Final = 9
_STATE_BITS: Final = 4
_STATE_MASK: Final = (1 << _STATE_BITS) - 1
_HTML_RAW_NAMES: Final = ("script", "style", "pre")
_HTML_RAW_ENDS: Final = tuple(
    re.compile(rf"</{name}(?=[\s>/]|$)", re.IGNORECASE) for name in _HTML_RAW_NAMES
)
_XML_CLOSERS: Final = {
    _XML_COMMENT: "-->",
    _XML_CDATA: "]]>",
    _XML_PI: "?>",
}
_XML_MARKUP: Final = re.compile(r"<(?:!--|!\[CDATA\[|\?|!|/|(?=[^\s<>/=!?]))")
_XML_NAME: Final = re.compile(r"[^\s<>/=\"']+")
_XML_TAG_PART: Final = re.compile(
    r"""
      (?P<space>\s+)
    | (?P<close>/?>)
    | (?P<equals>=)
    | (?P<quote>["'])
    | (?P<name>[^\s<>/=\"']+)
    | (?P<other>.)
    """,
    re.VERBOSE,
)
_XML_DOCTYPE_PART: Final = re.compile(r"[\[\]>]")


def _split_state(block_state: int) -> tuple[int, int]:
    """The XML or HTML state and raw element kept in a block state.

    Anything else, such as -1 before a first pass, is plain text.
    """
    if block_state <= 0:
        return _PLAIN_STATE, 0
    state = block_state & _STATE_MASK
    raw = block_state >> _STATE_BITS
    if state > _HTML_RAW or raw > len(_HTML_RAW_NAMES):
        return _PLAIN_STATE, 0
    return state, raw


def _raw_kind(name: str) -> int:
    """1, 2, or 3 for an HTML ``script``, ``style``, or ``pre`` tag, else 0."""
    lowered = name.casefold()
    if lowered in _HTML_RAW_NAMES:
        return _HTML_RAW_NAMES.index(lowered) + 1
    return 0


def _color_format(color: str, *, bold: bool = False) -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setForeground(QColor(color))
    if bold:
        fmt.setFontWeight(QFont.Weight.Bold)
    return fmt


def _utf16_offsets(text: str) -> list[int] | None:
    """Map code-point indexes to the UTF-16 indexes ``setFormat`` expects.

    Returns None when every character is in the BMP and the two agree. The
    list is built only when the astral regex matches; ASCII text, the common
    case for JSON, skips the regex too.
    """
    if text.isascii() or _ASTRAL.search(text) is None:
        return None
    offsets = [0]
    for ch in text:
        offsets.append(offsets[-1] + (2 if ord(ch) > 0xFFFF else 1))
    return offsets


class SyntaxHighlighter(QSyntaxHighlighter):
    """Character formats for JSON, YAML, XML, or HTML. Never touches selections."""

    def __init__(self, document: QTextDocument, mode: SyntaxMode = "JSON") -> None:
        if mode not in SYNTAX_MODES:
            raise ValueError(f"unknown syntax mode: {mode!r}")
        super().__init__(document)
        self._target = document
        self._mode: SyntaxMode = mode
        self._enabled = True
        self._offsets: list[int] | None = None
        self._cold_budget: int | None = None
        self._colors = LIGHT_SYNTAX
        self._use_colors(LIGHT_SYNTAX)

    def mode(self) -> SyntaxMode:
        return self._mode

    def set_mode(self, mode: SyntaxMode, *, rehighlight: bool = True) -> None:
        """Switch between ``"JSON"``, ``"YAML"``, ``"XML"``, and ``"HTML"``.

        Pass ``rehighlight=False`` when the caller knows every block is about
        to be reformatted anyway.
        """
        if mode not in SYNTAX_MODES:
            raise ValueError(f"unknown syntax mode: {mode!r}")
        if mode == self._mode:
            return
        self._mode = mode
        if rehighlight and self._enabled:
            self.rehighlight()

    def colors(self) -> SyntaxColors:
        return self._colors

    def follow_base(self, base: QColor) -> None:
        """Pick the light or dark set for text drawn on ``base``."""
        colors = DARK_SYNTAX if base.lightness() < _DARK_LIGHTNESS else LIGHT_SYNTAX
        if colors == self._colors:
            return
        self._use_colors(colors)
        if self._enabled:
            self.rehighlight()

    def is_enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        """Turn colors on or off, for example for large documents.

        Off detaches from the document, which clears every format at once
        and stops per-edit work. On reattaches and recolors the document on
        the next pass of the event loop.
        """
        if enabled == self._enabled:
            return
        self._enabled = enabled
        self.setDocument(self._target if enabled else None)

    def allow_cold_blocks(self, count: int | None) -> None:
        """Color at most ``count`` cold blocks in the passes that follow.

        Qt colors every block an edit touched, then keeps going while a
        block's state changes. A block going from cold to colored is such a
        change, so one pass over a freshly set document would color all of
        it. Past ``count``, a cold block is left as it is, which also stops
        Qt's pass there; the editor colors the rest later, a chunk a time.
        Blocks that already have colors are always recolored. ``None`` lifts
        the limit.
        """
        self._cold_budget = count

    @staticmethod
    def is_cold(block: QTextBlock) -> bool:
        """True while ``block`` has never been colored."""
        return block.userState() == COLD_STATE

    def _use_colors(self, colors: SyntaxColors) -> None:
        self._colors = colors
        self.key_format = _color_format(colors.key)
        self.string_format = _color_format(colors.string)
        self.value_format = _color_format(colors.value, bold=True)
        self.punctuation_format = _color_format(colors.punctuation)

    def highlightBlock(self, text: str) -> None:
        if self.currentBlockState() == COLD_STATE and self._cold_budget is not None:
            if self._cold_budget <= 0:
                return
            self._cold_budget -= 1
        self._offsets = _utf16_offsets(text)
        if self._mode == "JSON":
            self._highlight_json(text)
        elif self._mode in ("XML", "HTML"):
            self._highlight_xml(text)
        else:
            self._highlight_yaml(text)
        if self.currentBlockState() == COLD_STATE:
            # JSON keeps no state between lines. Mark the block colored.
            self.setCurrentBlockState(_PLAIN_STATE)

    def _paint(self, start: int, end: int, fmt: QTextCharFormat) -> None:
        if end <= start:
            return
        if self._offsets is not None:
            start, end = self._offsets[start], self._offsets[end]
        self.setFormat(start, end - start, fmt)

    def _highlight_json(self, text: str) -> None:
        for match in _JSON_TOKEN.finditer(text):
            kind = match.lastgroup
            if kind == "string":
                is_key = _COLON_AHEAD.match(text, match.end()) is not None
                fmt = self.key_format if is_key else self.string_format
            elif kind == "punct":
                fmt = self.punctuation_format
            else:
                fmt = self.value_format
            self._paint(match.start(), match.end(), fmt)

    def _highlight_yaml(self, text: str) -> None:
        previous = self.previousBlockState()
        if previous > _PLAIN_STATE:
            parent = previous - 2
            indent = _SPACES.match(text).end()
            if indent == len(text) or indent > parent:
                self._paint(indent, len(text), self.string_format)
                self.setCurrentBlockState(previous)
                return
        self.setCurrentBlockState(_PLAIN_STATE)

        pos = _SPACES.match(text).end()
        parent = -1
        marker = _DOC_MARKER.match(text) if pos == 0 else None
        if marker:
            self._paint(0, marker.end(), self.punctuation_format)
            pos = _SPACES.match(text, marker.end()).end()
        else:
            parent = pos
            while marker := _ENTRY_MARKER.match(text, pos):
                parent = pos
                self._paint(pos, marker.end(), self.punctuation_format)
                pos = _SPACES.match(text, marker.end()).end()
            if key := _BLOCK_KEY.match(text, pos):
                parent = pos
                self._paint(key.start("key"), key.end("key"), self.key_format)
                colon = key.start("colon")
                self._paint(colon, colon + 1, self.punctuation_format)
                pos = key.end()
        self._highlight_yaml_value(text, pos, parent)

    def _highlight_yaml_value(self, text: str, pos: int, parent: int) -> None:
        end = len(text)
        while (pos := _SPACES.match(text, pos).end()) < end:
            ch = text[pos]
            if ch == "#":
                self._paint(pos, end, self.punctuation_format)
                return
            if ch in "[{":
                self._highlight_yaml_flow(text, pos)
                return
            if ch in "&*!":
                match = _NODE_PROPERTY.match(text, pos)
                self._paint(pos, match.end(), self.punctuation_format)
            elif ch in "|>" and (match := _BLOCK_SCALAR.match(text, pos)):
                self._paint(pos, match.end(), self.punctuation_format)
                self.setCurrentBlockState(parent + 2)
            elif ch in "\"'":
                match = _QUOTED.match(text, pos)
                self._paint(pos, match.end(), self.string_format)
            else:
                match = _PLAIN_VALUE.match(text, pos)
                value = text[pos : match.end()].rstrip()
                self._paint(pos, pos + len(value), self._scalar_format(value))
            pos = match.end()

    def _highlight_yaml_flow(self, text: str, pos: int) -> None:
        for match in _YAML_FLOW_TOKEN.finditer(text, pos):
            kind = match.lastgroup
            if kind in ("comment", "punct", "property"):
                fmt = self.punctuation_format
            elif _COLON_AHEAD.match(text, match.end()) is not None:
                fmt = self.key_format
            elif kind == "quoted":
                fmt = self.string_format
            else:
                fmt = self._scalar_format(match.group())
            self._paint(match.start(), match.end(), fmt)

    def _highlight_xml(self, text: str) -> None:
        """XML, or HTML with raw bodies, one line at a time."""
        state, raw = _split_state(self.previousBlockState())
        pos = 0
        if state in (_XML_TAG_DOUBLE, _XML_TAG_SINGLE):
            pos, state, raw = self._xml_value(text, 0, state, raw)
        elif state == _HTML_RAW:
            pos, state, raw = self._html_raw(text, 0, raw)
        while pos < len(text):
            if state == _PLAIN_STATE:
                found = _XML_MARKUP.search(text, pos)
                if found is None:
                    break
                pos, state, raw = self._xml_markup(text, found)
            elif state in _XML_CLOSERS:
                pos, state = self._xml_until(text, pos, state)
            elif state in (_XML_DOCTYPE, _XML_SUBSET):
                pos, state = self._xml_doctype(text, pos, state)
            elif state == _HTML_RAW:
                pos, state, raw = self._html_raw(text, pos, raw)
            else:
                pos, state, raw = self._xml_tag(text, pos, raw)
        self.setCurrentBlockState(state | raw << _STATE_BITS)

    def _xml_markup(self, text: str, found: re.Match[str]) -> tuple[int, int, int]:
        """Color the opener ``found`` points at and return where to go on."""
        start = found.start()
        opener = found.group()
        if opener == "<!--":
            return (*self._xml_until(text, start, _XML_COMMENT), 0)
        if opener == "<![CDATA[":
            self._paint(start, found.end(), self.punctuation_format)
            return found.end(), _XML_CDATA, 0
        if opener == "<?":
            return (*self._xml_until(text, start, _XML_PI), 0)
        if opener == "<!":
            return (*self._xml_doctype(text, start, _XML_DOCTYPE), 0)
        self._paint(start, found.end(), self.punctuation_format)
        name = _XML_NAME.match(text, found.end())
        if name is None:
            return found.end(), _XML_TAG, 0
        self._paint(name.start(), name.end(), self.key_format)
        raw = 0
        if self._mode == "HTML" and opener == "<":
            raw = _raw_kind(name.group())
        return name.end(), _XML_TAG, raw

    def _xml_until(self, text: str, pos: int, state: int) -> tuple[int, int]:
        """A comment, PI, or CDATA end from ``pos``. The CDATA body is plain."""
        closer = _XML_CLOSERS[state]
        close = text.find(closer, pos)
        stop = len(text) if close < 0 else close + len(closer)
        if state == _XML_CDATA:
            if close >= 0:
                self._paint(close, stop, self.punctuation_format)
        else:
            self._paint(pos, stop, self.punctuation_format)
        return stop, state if close < 0 else _PLAIN_STATE

    def _xml_doctype(self, text: str, pos: int, state: int) -> tuple[int, int]:
        """A doctype, muted, until the ``>`` outside its internal subset."""
        start = pos
        for found in _XML_DOCTYPE_PART.finditer(text, pos):
            char = found.group()
            if char == "[":
                state = _XML_SUBSET
            elif char == "]":
                state = _XML_DOCTYPE
            elif state == _XML_DOCTYPE:
                self._paint(start, found.end(), self.punctuation_format)
                return found.end(), _PLAIN_STATE
        self._paint(start, len(text), self.punctuation_format)
        return len(text), state

    def _xml_tag(self, text: str, pos: int, raw: int) -> tuple[int, int, int]:
        """Attributes and the end of a start or end tag, from ``pos``.

        ``raw`` is the HTML raw element this start tag opens, or 0. Its body
        starts at ``>``. A value after ``=`` without quotes is a string.
        """
        after_equals = False
        while pos < len(text):
            part = _XML_TAG_PART.match(text, pos)
            if part is None:
                break
            kind = part.lastgroup
            if kind == "close":
                self._paint(part.start(), part.end(), self.punctuation_format)
                if raw and part.group() == ">":
                    return part.end(), _HTML_RAW, raw
                return part.end(), _PLAIN_STATE, 0
            if kind == "quote":
                quoted = _XML_TAG_DOUBLE if part.group() == '"' else _XML_TAG_SINGLE
                return self._xml_value(text, part.start(), quoted, raw, opening=True)
            if kind == "name" and after_equals:
                self._paint(part.start(), part.end(), self.string_format)
            elif kind in ("name", "equals"):
                self._paint(part.start(), part.end(), self.punctuation_format)
            if kind != "space":
                after_equals = kind == "equals"
            pos = part.end()
        return len(text), _XML_TAG, raw

    def _xml_value(
        self, text: str, pos: int, state: int, raw: int, *, opening: bool = False
    ) -> tuple[int, int, int]:
        """A quoted attribute value from ``pos``, quotes included."""
        quote = '"' if state == _XML_TAG_DOUBLE else "'"
        close = text.find(quote, pos + 1 if opening else pos)
        if close < 0:
            self._paint(pos, len(text), self.string_format)
            return len(text), state, raw
        self._paint(pos, close + 1, self.string_format)
        return close + 1, _XML_TAG, raw

    def _html_raw(self, text: str, pos: int, raw: int) -> tuple[int, int, int]:
        """The plain body of a ``script``, ``style``, or ``pre`` from ``pos``.

        Nothing in it is colored. Its end tag, in any case, goes back to
        markup.
        """
        end = _HTML_RAW_ENDS[raw - 1].search(text, pos)
        if end is None:
            return len(text), _HTML_RAW, raw
        return end.start(), _PLAIN_STATE, 0

    def _scalar_format(self, value: str) -> QTextCharFormat:
        if value in _YAML_LITERALS or _YAML_NUMBER.fullmatch(value):
            return self.value_format
        return self.string_format
