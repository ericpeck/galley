"""Plain-text find for the find bar (#52) and its Keys only option (#62).

``find_starts`` returns where a query starts in a text. The query is always
literal: no regex, no whole word. Match case off folds case one character at
a time, so a match is always as long as the query.

``key_mask`` keeps the text of each object key and turns every other
character into a newline. The mask is as long as the text, so a match found
in it is at the same offset in the text. A query never holds a newline, so
no match can cross from one key into the next.

Offsets here count code points. ``utf16_offsets`` turns them into the UTF-16
positions a Qt document uses.

This module does not import Qt.
"""

import logging
import re
from bisect import bisect_left
from typing import Literal

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.tokens import AnchorToken, KeyToken, ScalarToken, TagToken

FindSyntax = Literal["JSON", "YAML"]

_LOG = logging.getLogger(__name__)

# A JSON key: a string on one line, then a colon. Group 1 is the text between
# the quotes. Valid JSON never puts a colon after a value, and a scan that
# starts inside a string ends at that string's closing quote, so only keys
# match.
_JSON_KEY = re.compile(r'"([^"\\\n]*(?:\\.[^"\\\n]*)*)"[ \t]*:')
_ASTRAL = re.compile("[\U00010000-\U0010ffff]")
_QUOTED_STYLES = frozenset({"'", '"'})
_GAP = "\n"
_BOM = "\ufeff"


def find_starts(text: str, query: str, *, match_case: bool) -> list[int]:
    """Where ``query`` starts in ``text``, left to right, without overlaps.

    An empty query, or one with a newline, finds nothing.
    """
    if not query or _GAP in query:
        return []
    flags = 0 if match_case else re.IGNORECASE
    pattern = re.compile(re.escape(query), flags)
    return [match.start() for match in pattern.finditer(text)]


def key_spans(text: str, syntax: FindSyntax) -> list[tuple[int, int]]:
    """``(start, end)`` of each object key's text, in order.

    Quotes are not part of a key. The scan never raises: text that stops
    parsing keeps the keys found before that point.
    """
    if syntax == "YAML":
        return _yaml_keys(text)
    return [match.span(1) for match in _JSON_KEY.finditer(text)]


def key_mask(text: str, syntax: FindSyntax) -> str:
    """``text`` with every character outside a key turned into a newline."""
    parts: list[str] = []
    last = 0
    for start, end in key_spans(text, syntax):
        parts.append(_GAP * (start - last))
        parts.append(text[start:end])
        last = end
    parts.append(_GAP * (len(text) - last))
    return "".join(parts)


def astral_offsets(text: str) -> list[int]:
    """Offsets of the characters outside the Basic Multilingual Plane.

    Each one is two UTF-16 code units, so it moves every later offset.
    """
    if text.isascii():
        return []
    return [match.start() for match in _ASTRAL.finditer(text)]


def utf16_offsets(offsets: list[int], astral: list[int]) -> list[int]:
    """``offsets`` in code points, as UTF-16 positions.

    ``astral`` is :func:`astral_offsets` of the same text.
    """
    if not astral:
        return offsets
    return [offset + bisect_left(astral, offset) for offset in offsets]


def _yaml_keys(text: str, *, pure: bool = False) -> list[tuple[int, int]]:
    """Key scalars from the YAML scanner's tokens.

    A key is the scalar after a ``KeyToken``, past any anchor or tag. Both
    scanners count characters, but over a leading BOM the C scanner starts
    at 0 and the pure one at 1. The BOM is cut off first so both see the
    same text, and the spans move back by one. ``pure`` forces the Python
    scanner, which runs when ``ruamel.yaml.clib`` is not selected.
    """
    shift = 1 if text.startswith(_BOM) else 0
    spans: list[tuple[int, int]] = []
    after_key = False
    try:
        for token in YAML(typ="safe", pure=pure).scan(text[shift:]):
            if isinstance(token, KeyToken):
                after_key = True
                continue
            if after_key and isinstance(token, (AnchorToken, TagToken)):
                continue
            if after_key and isinstance(token, ScalarToken):
                start = token.start_mark.index + shift
                end = token.end_mark.index + shift
                if token.style in _QUOTED_STYLES:
                    start += 1
                    end -= 1
                spans.append((start, end))
            after_key = False
    except YAMLError:
        _LOG.debug("YAML scan stopped; Keys only uses the keys found so far.")
    return spans
