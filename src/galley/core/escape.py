"""Escape a document as one JSON string, and unescape it again.

Escape reads the document as JSON or YAML, writes the value as compact JSON
(no spaces after ``,`` or ``:``), and wraps that text in one JSON string
literal: ``{"a": "b"}`` becomes ``"{\\"a\\":\\"b\\"}"``. Quotes, backslashes,
and control characters are escaped. Other characters, emoji included, are
kept as they are (``ensure_ascii=False``, the same as Format).

Unescape reads the whole text as one JSON string literal, whatever the Input
format, parses the string's contents as JSON, and formats that value the way
Format writes the target. It peels exactly one layer: a string escaped twice
unescapes to the string escaped once.

Nothing here runs the text. Both directions use ``json`` and the formatter.
"""

import json
from typing import Literal

from galley.core.errors import (
    DuplicateKey,
    InputError,
    JSONDuplicateKeyError,
    NotJSONValueError,
)
from galley.core.format import (
    NotJSONKeyError,
    SelfReferenceError,
    _value_format,
    format_document,
    load_document,
    yaml_json_value,
)

UnescapeKind = Literal["not_string", "not_json", "duplicate_key"]


class EscapeError(NotJSONValueError):
    """The document parsed, but JSON can't hold one of its values.

    YAML can load a date, binary data, a set, a non-scalar key, or an alias
    that contains itself. Format to JSON fails on those as well. For a key
    JSON can't hold or an alias inside itself, the error's text is the kind
    ``yaml_json_value`` names: ``format.NOT_JSON_KEY`` or
    ``format.SELF_REFERENCE``.
    """


class UnescapeError(Exception):
    """The text isn't one escaped JSON string.

    ``kind`` is ``not_string`` when the text isn't a single JSON string
    literal, ``not_json`` when the string's contents aren't JSON, and
    ``duplicate_key`` when they are JSON but an object repeats a key.
    ``duplicates`` then lists every repeat, with lines and columns counted in
    the string's contents. The words shown to the user are chosen in
    ``galley.app``.
    """

    def __init__(
        self, kind: UnescapeKind, duplicates: tuple[DuplicateKey, ...] = ()
    ) -> None:
        super().__init__(kind)
        self.kind = kind
        self.duplicates = duplicates


def escape_document(text: str | bytes, source: str) -> str:
    """Return ``text`` as one JSON string literal holding its compact JSON.

    ``source`` is ``json`` or ``yaml``. The text passes the same checks as
    Format and raises the same errors. YAML is loaded the way Format to JSON
    loads it, so a surrogate escape raises the same ``bad_escape`` error.
    Any other format, such as XML, raises ``ValueError``.
    """
    if _value_format(source) == "json":
        value = load_document(text, "json")
    else:
        try:
            value = yaml_json_value(text)
        except (NotJSONKeyError, SelfReferenceError) as exc:
            raise EscapeError(str(exc)) from None
    return escape_value(value)


def escape_value(value: object) -> str:
    """Return ``value`` as compact JSON inside one JSON string literal.

    The literal ends with a newline, like Format's output.
    """
    try:
        inner = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError) as exc:
        raise EscapeError(str(exc)) from None
    return json.dumps(inner, ensure_ascii=False) + "\n"


def unescape_document(text: str, target: str) -> str:
    """Format the JSON inside the JSON string literal ``text`` as ``target``.

    Whitespace around the literal is allowed. The string's contents pass
    the same checks Format applies to JSON, nesting depth and lone
    surrogates included, and are written the way Format writes ``target``.
    A ``target`` that does not load to a value, such as XML, raises
    ``ValueError``.
    """
    _value_format(target)
    try:
        inner = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        raise UnescapeError("not_string") from None
    if not isinstance(inner, str):
        raise UnescapeError("not_string")
    try:
        return format_document(inner, "json", target)
    except JSONDuplicateKeyError as exc:
        raise UnescapeError("duplicate_key", exc.duplicates) from None
    except (json.JSONDecodeError, InputError):
        raise UnescapeError("not_json") from None
