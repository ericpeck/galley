"""Keep checking JSON after a syntax error.

``json.loads`` stops at its first error. ``scan_json`` reads the same
grammar as CPython's C scanner, so its first error has the message and
position ``json.loads`` reports. After each error it repairs the text in
its head and keeps reading. Nothing is run: the text is only matched with
regular expressions and indexes.

A repair is either sure (a comma is missing, a key has no quotes) or a
guess (where an unclosed string ends, which bracket a stray closer
closes). Errors found after a guess are flagged, because the guess may
have caused them.

After an error, later errors stay quiet until the scan reads a token that
parses, so one mistake gives one error rather than a cascade.

Speed: an object or array that ``json`` decodes on its own is skipped at
C speed. Only the brackets around an error are read here, a token at a
time.
"""

import json
import re
from dataclasses import dataclass

# The scan stops once it has found this many errors and repeated keys.
MAX_FINDINGS = 1000
# Brackets deeper than this are read here even when ``json`` could skip
# them. A failed try costs a read up to the error, and only the brackets
# around an error fail, so this caps the repeated reading.
_FAST_DEPTH = 16
# A fast try decodes a slice that starts at the bracket, so its error does
# not count lines from the top of the text. The slice starts this long and
# grows when it ends before the bracket closes.
_FAST_WINDOW = 4096
# A slice that ends inside a token, such as ``-Infinity``, fails this close
# to its end.
_FAST_TAIL = 16

BOM_MESSAGE = "Unexpected UTF-8 BOM (decode using utf-8-sig)"
EXPECT_VALUE = "Expecting value"
EXPECT_COMMA = "Expecting ',' delimiter"
EXPECT_COLON = "Expecting ':' delimiter"
EXPECT_KEY = "Expecting property name enclosed in double quotes"
EXTRA_DATA = "Extra data"
UNTERMINATED = "Unterminated string starting at"
CONTROL_CHARACTER = "Invalid control character at"
BAD_ESCAPE = "Invalid \\escape"
BAD_UNICODE_ESCAPE = "Invalid \\uXXXX escape"

_WS = re.compile(r"[ \t\n\r]*")
# A whole valid string, so a valid string costs one match.
_STRING = re.compile(
    r'"[^"\\\x00-\x1f]*(?:\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4})[^"\\\x00-\x1f]*)*"'
)
_CHUNK = re.compile(r'[^"\\\x00-\x1f]*')
_HEX4 = re.compile(r"[0-9a-fA-F]{4}")
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?")
# Literals ``json.loads`` accepts, keyed by first character.
_LITERALS = {
    "n": ("null",),
    "t": ("true",),
    "f": ("false",),
    "N": ("NaN",),
    "I": ("Infinity",),
    "-": ("-Infinity",),
}
# A bare word or other run that is not JSON, such as ``undefined`` or a key
# without quotes. It stops at whitespace, punctuation, and quotes.
_JUNK = re.compile(r"""[^ \t\n\r,:\[\]{}"'/]+""")
_SINGLE_QUOTED = re.compile(r"'(?:[^'\\\n\r]|\\[^\n\r])*'")
_LINE_END = re.compile(r"[^\n\r]*")
_CLOSER = {"[": "]", "{": "}"}
_ESCAPES = frozenset('"\\/bfnrt')
# Characters that can start the next value or key, so a missing comma is
# the likeliest repair.
_ITEM_START = frozenset("\"'{[-0123456789_$")

_VALUE, _FIRST_VALUE, _AFTER, _FIRST_KEY, _KEY, _COLON, _END = range(7)


@dataclass(frozen=True)
class Slip:
    """One syntax error, worded the way ``json.loads`` words it.

    ``opened`` is the innermost bracket still open at ``pos``, or None.
    ``start`` is the opening quote for a string error, otherwise ``pos``.
    ``after_guess`` is True when an earlier repair was a guess.
    """

    message: str
    pos: int
    opened: int | None
    start: int
    after_guess: bool


@dataclass(frozen=True)
class Repeat:
    """A key that repeats an earlier key of the same object, at ``pos``."""

    key: str
    pos: int
    after_guess: bool


@dataclass(frozen=True)
class Scan:
    """Every finding in text order. ``stopped_at`` is set when the limit hit."""

    slips: tuple[Slip, ...]
    repeats: tuple[Repeat, ...]
    stopped_at: int | None


class _End(Exception):
    """The text ran out, or the scan reached its limit."""


class _RepeatedKey(Exception):
    """Stops a fast decode at an object that repeats a key."""


def _reject_repeated_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise _RepeatedKey
    return value


_DECODER = json.JSONDecoder(object_pairs_hook=_reject_repeated_keys)


def scan_json(text: str, limit: int = MAX_FINDINGS) -> Scan:
    """Return every syntax error and repeated key in ``text``."""
    scanner = _Scanner(text, limit)
    scanner.run()
    return Scan(tuple(scanner.slips), tuple(scanner.repeats), scanner.stopped_at)


class _Scanner:
    def __init__(self, text: str, limit: int) -> None:
        self.text = text
        self.size = len(text)
        self.limit = limit
        self.slips: list[Slip] = []
        self.repeats: list[Repeat] = []
        self.stopped_at: int | None = None
        # Open brackets: the character, where it opened, and an object's keys.
        self.kinds: list[str] = []
        self.opens: list[int] = []
        self.keys: list[set[str] | None] = []
        self.quiet = False
        self.guessed = False
        # An unclosed string that ended in a comma already separated items.
        self.implied_comma = False
        self.extra_seen = False

    def run(self) -> None:
        index = 0
        if self.text.startswith("﻿"):
            self.slip(BOM_MESSAGE, 0)
            index = 1
        state = _VALUE
        try:
            while True:
                index, state = self.step(index, state)
        except _End:
            return

    def step(self, index: int, state: int) -> tuple[int, int]:
        index = _WS.match(self.text, index).end()
        if state == _AFTER:
            return self.after(index)
        if state == _VALUE or state == _FIRST_VALUE:
            if state == _FIRST_VALUE and self.char(index) == "]":
                return self.close(index)
            return self.value(index)
        if state == _COLON:
            return self.colon(index)
        if state == _END:
            return self.end(index)
        return self.key(index, state)

    # Findings

    def slip(self, message: str, pos: int, start: int | None = None) -> None:
        if self.quiet:
            return
        self.count_one(pos)
        opened = self.opens[-1] if self.opens else None
        place = pos if start is None else start
        self.slips.append(Slip(message, pos, opened, place, self.guessed))
        self.quiet = True

    def repeat(self, key: str, pos: int) -> None:
        self.count_one(pos)
        self.repeats.append(Repeat(key, pos, self.guessed))

    def count_one(self, pos: int) -> None:
        if len(self.slips) + len(self.repeats) >= self.limit:
            self.stopped_at = pos
            raise _End

    def guess(self) -> None:
        self.guessed = True

    def parsed(self) -> None:
        """A token parsed as written, so the next error is reported."""
        self.quiet = False

    # Reading

    def char(self, index: int) -> str:
        return self.text[index] if index < self.size else ""

    def ensure_more(self, index: int, message: str) -> str:
        if index >= self.size:
            self.slip(message, index)
            raise _End
        return self.text[index]

    def after_value(self) -> int:
        return _AFTER if self.kinds else _END

    def value(self, index: int) -> tuple[int, int]:
        char = self.ensure_more(index, EXPECT_VALUE)
        if char == '"':
            return self.string(index)[0], self.after_value()
        if char == "{" or char == "[":
            end = self.decoded_end(index)
            self.parsed()
            if end is not None:
                return end, self.after_value()
            self.push(char, index)
            return index + 1, _FIRST_KEY if char == "{" else _FIRST_VALUE
        end = self.scalar_end(index, char)
        if end is not None:
            self.parsed()
            return end, self.after_value()
        self.slip(EXPECT_VALUE, index)
        return self.bad_value(index, char)

    def decoded_end(self, index: int) -> int | None:
        """Where the bracket at ``index`` closes, if ``json`` decodes it whole.

        A repeated key fails the decode, so this scan finds the repeat.
        """
        if len(self.kinds) >= _FAST_DEPTH:
            return None
        width = _FAST_WINDOW
        while True:
            piece = self.text[index : index + width]
            cut = index + width < self.size
            try:
                return index + _DECODER.raw_decode(piece)[1]
            except json.JSONDecodeError as exc:
                ran_out = exc.pos >= len(piece) - _FAST_TAIL or exc.msg == UNTERMINATED
                if not (cut and ran_out):
                    return None
            except (_RepeatedKey, RecursionError):
                return None
            width *= 4

    def scalar_end(self, index: int, char: str) -> int | None:
        for literal in _LITERALS.get(char, ()):
            if self.text.startswith(literal, index):
                return index + len(literal)
        match = _NUMBER.match(self.text, index)
        return match.end() if match else None

    def bad_value(self, index: int, char: str) -> tuple[int, int]:
        """Repair a value that does not parse, after its error."""
        if char in "]}" or char == ",":
            # A missing value. The comma or bracket is read next.
            return index, self.after_value()
        if char == ":":
            self.guess()
            return index + 1, _VALUE
        if char == "'":
            return self.single_quoted(index), self.after_value()
        if char == "/" and self.char(index + 1) in ("/", "*"):
            return self.comment(index), _VALUE
        return self.junk(index), self.after_value()

    def after(self, index: int) -> tuple[int, int]:
        implied, self.implied_comma = self.implied_comma, False
        char = self.ensure_more(index, EXPECT_COMMA)
        kind = self.kinds[-1]
        if char == _CLOSER[kind]:
            return self.close(index)
        if char == ",":
            self.parsed()
            return index + 1, _VALUE if kind == "[" else _KEY
        if implied and _starts_item(char):
            return index, _VALUE if kind == "[" else _KEY
        self.slip(EXPECT_COMMA, index)
        if char in "]}":
            return self.mismatch(index, char)
        if _starts_item(char):
            return index, _VALUE if kind == "[" else _KEY
        if char == ":":
            self.guess()
            return index + 1, _AFTER
        if char == "/" and self.char(index + 1) in ("/", "*"):
            return self.comment(index), _AFTER
        return self.junk(index), _AFTER

    def key(self, index: int, state: int) -> tuple[int, int]:
        char = self.ensure_more(index, EXPECT_KEY)
        if char == '"':
            end, key = self.string(index)
            if key is not None:
                self.check_repeat(key, index)
            return end, _COLON
        if char == "}" and state == _FIRST_KEY:
            return self.close(index)
        self.slip(EXPECT_KEY, index)
        if char == "}":
            # A trailing comma before the closing brace.
            return self.pop_to(index, 1)
        if char == "]":
            return self.mismatch(index, char)
        if char == ",":
            return index + 1, _KEY
        if char == "'":
            return self.single_quoted(index), _COLON
        if char == "/" and self.char(index + 1) in ("/", "*"):
            return self.comment(index), _KEY
        if char in "{[:":
            # A value with no key. Read it as the value.
            self.guess()
            return (index + 1 if char == ":" else index), _VALUE
        return self.junk(index), _COLON

    def colon(self, index: int) -> tuple[int, int]:
        char = self.ensure_more(index, EXPECT_COLON)
        if char == ":":
            self.parsed()
            return index + 1, _VALUE
        self.slip(EXPECT_COLON, index)
        if char in ",]}":
            # A key with no value. The comma or bracket is read next.
            return index, _AFTER
        if char == "/" and self.char(index + 1) in ("/", "*"):
            return self.comment(index), _COLON
        return index, _VALUE

    def end(self, index: int) -> tuple[int, int]:
        if index >= self.size:
            raise _End
        char = self.text[index]
        if not self.extra_seen or char in "]},:":
            self.slip(EXTRA_DATA, index)
        self.extra_seen = True
        if char in "]},:":
            return index + 1, _END
        if char == "/" and self.char(index + 1) in ("/", "*"):
            return self.comment(index), _END
        # Another document follows, as in JSON Lines. Check it too.
        return index, _VALUE

    # Brackets

    def push(self, char: str, index: int) -> None:
        self.kinds.append(char)
        self.opens.append(index)
        self.keys.append(set() if char == "{" else None)

    def close(self, index: int) -> tuple[int, int]:
        self.parsed()
        return self.pop_to(index, 1)

    def pop_to(self, index: int, count: int) -> tuple[int, int]:
        del self.kinds[-count:]
        del self.opens[-count:]
        del self.keys[-count:]
        return index + 1, self.after_value()

    def mismatch(self, index: int, char: str) -> tuple[int, int]:
        """A closer that does not match the innermost open bracket.

        It closes the nearest bracket it matches, and every bracket inside
        that one: the inner closers are missing. That bracket can be the
        outermost one only at the end of the text. Otherwise, or with no
        match, the closer was mistyped and closes the innermost bracket.
        """
        self.guess()
        kinds = self.kinds
        for depth in range(len(kinds) - 1, -1, -1):
            if _CLOSER[kinds[depth]] != char:
                continue
            if depth > 0 or _WS.match(self.text, index + 1).end() >= self.size:
                return self.pop_to(index, len(kinds) - depth)
            break
        return self.pop_to(index, 1)

    def check_repeat(self, key: str, index: int) -> None:
        seen = self.keys[-1]
        if seen is None:
            return
        if key in seen:
            self.repeat(key, index)
            return
        seen.add(key)

    # Tokens

    def string(self, index: int) -> tuple[int, str | None]:
        """Read the string that opens at ``index``. Return its end and value.

        The value is None when the string had an error.
        """
        text = self.text
        match = _STRING.match(text, index)
        if match is not None:
            self.parsed()
            end = match.end()
            raw = text[index:end]
            return end, (json.loads(raw) if "\\" in raw else raw[1:-1])
        cursor = index + 1
        while True:
            cursor = _CHUNK.match(text, cursor).end()
            if cursor >= self.size:
                self.slip(UNTERMINATED, index, index)
                raise _End
            char = text[cursor]
            if char == '"':
                # The errors inside were sure repairs, and the string closed.
                self.parsed()
                return cursor + 1, None
            if char == "\\":
                cursor = self.escape(index, cursor)
                if cursor < 0:
                    return -cursor, None
                continue
            self.slip(CONTROL_CHARACTER, cursor, index)
            if char in "\n\r":
                # The quote never closed. End the string at the line end.
                self.guess()
                self.implied_comma = text[index + 1 : cursor].rstrip().endswith(",")
                return cursor, None
            cursor += 1

    def escape(self, quote: int, cursor: int) -> int:
        """Read the escape at ``cursor``. A negative result ends the string."""
        text = self.text
        if cursor + 1 >= self.size:
            self.slip(UNTERMINATED, quote, quote)
            raise _End
        char = text[cursor + 1]
        if char in _ESCAPES:
            return cursor + 2
        if char == "u":
            # Like CPython, four digits that end the text are not an escape.
            if cursor + 6 < self.size and _HEX4.match(text, cursor + 2):
                return cursor + 6
            self.slip(BAD_UNICODE_ESCAPE, cursor + 1, quote)
            return cursor + 2
        self.slip(BAD_ESCAPE, cursor, quote)
        if char in "\n\r":
            # A backslash at the line end. The string ends there.
            self.guess()
            return -(cursor + 1)
        return cursor + 2

    def single_quoted(self, index: int) -> int:
        match = _SINGLE_QUOTED.match(self.text, index)
        if match is not None:
            return match.end()
        self.guess()
        return _LINE_END.match(self.text, index).end()

    def comment(self, index: int) -> int:
        if self.text[index + 1] == "/":
            return _LINE_END.match(self.text, index).end()
        end = self.text.find("*/", index + 2)
        return self.size if end < 0 else end + 2

    def junk(self, index: int) -> int:
        match = _JUNK.match(self.text, index)
        if match is None or match.end() == index:
            # A lone character such as "/" that starts nothing.
            return index + 1
        return match.end()


def _starts_item(char: str) -> bool:
    return char in _ITEM_START or char.isalnum()
