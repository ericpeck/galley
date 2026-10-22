"""JSON and YAML syntax colors for the code editors.

Colors follow spec §3: keys ``accent.ink``, strings ``string``, numbers and
literals ``text.code.value`` bold, punctuation and comments ``text.muted``.
YAML uses the same colors as JSON. A dark editor Base switches to the
``*_DARK`` set from ``theme.py``.

Highlighting works one line at a time with precompiled patterns. The only
state carried between lines is a YAML block scalar (``|`` or ``>``), whose
indented lines are string content.
"""

import re
from typing import Final, Literal, NamedTuple

from PySide6.QtGui import (
    QColor,
    QFont,
    QSyntaxHighlighter,
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

SyntaxMode = Literal["JSON", "YAML"]
SYNTAX_MODES: Final[tuple[SyntaxMode, ...]] = ("JSON", "YAML")


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
    """Character formats for JSON or YAML. Never touches extra selections."""

    def __init__(self, document: QTextDocument, mode: SyntaxMode = "JSON") -> None:
        if mode not in SYNTAX_MODES:
            raise ValueError(f"unknown syntax mode: {mode!r}")
        super().__init__(document)
        self._target = document
        self._mode: SyntaxMode = mode
        self._enabled = True
        self._offsets: list[int] | None = None
        self._colors = LIGHT_SYNTAX
        self._use_colors(LIGHT_SYNTAX)

    def mode(self) -> SyntaxMode:
        return self._mode

    def set_mode(self, mode: SyntaxMode, *, rehighlight: bool = True) -> None:
        """Switch between ``"JSON"`` and ``"YAML"``.

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

    def _use_colors(self, colors: SyntaxColors) -> None:
        self._colors = colors
        self.key_format = _color_format(colors.key)
        self.string_format = _color_format(colors.string)
        self.value_format = _color_format(colors.value, bold=True)
        self.punctuation_format = _color_format(colors.punctuation)

    def highlightBlock(self, text: str) -> None:
        self._offsets = _utf16_offsets(text)
        if self._mode == "JSON":
            self._highlight_json(text)
        else:
            self._highlight_yaml(text)

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

    def _scalar_format(self, value: str) -> QTextCharFormat:
        if value in _YAML_LITERALS or _YAML_NUMBER.fullmatch(value):
            return self.value_format
        return self.string_format
