"""JSON and YAML formatting.

Invalid UTF-8, documents nested past ``MAX_DEPTH``, and JSON lone-surrogate
escapes raise ``InputError``. A JSON object that repeats a key raises
``JSONDuplicateKeyError`` instead of keeping the last value. Other syntax
errors keep the line and column from the parser. Banner sentences are not
built here.
"""

import json
import os
import re
import time
from io import StringIO

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.events import (
    AliasEvent,
    MappingEndEvent,
    MappingStartEvent,
    ScalarEvent,
    SequenceEndEvent,
    SequenceStartEvent,
)
from ruamel.yaml.main import CEmitter, CParser
from ruamel.yaml.nodes import ScalarNode
from ruamel.yaml.reader import ReaderError
from ruamel.yaml.tokens import (
    BlockEndToken,
    BlockMappingStartToken,
    BlockSequenceStartToken,
    DirectiveToken,
    FlowMappingEndToken,
    FlowMappingStartToken,
    FlowSequenceEndToken,
    FlowSequenceStartToken,
    ScalarToken,
    TagToken,
)

from galley.core.errors import (
    MAX_DEPTH,
    DuplicateKey,
    InputError,
    JSONDuplicateKeyError,
    escape_error,
    nesting_error,
    utf8_input_error,
)

# ruamel's loader and dumper recurse once per level, with several frames each.
# Past this depth a document that is still within MAX_DEPTH would hit
# Python's recursion limit, so it is built with an explicit stack instead.
_RUAMEL_SAFE_DEPTH = 256

# Line width for every YAML dump. ruamel wraps a scalar that contains spaces
# once a line passes ``width`` (80 by default), so masking a long value to
# "•••" would drop lines. The largest C int is the widest value the
# ruamel.yaml.clib emitter accepts, and no line in a document Galley opens
# reaches it, so neither emitter wraps. Format and masked output share it.
YAML_DUMP_WIDTH = 2**31 - 1

# Test seam for the pure-Python retry. Unset during normal use. The spinner
# test sets the force flag so the C attempt fails, and the gate path so that
# retry stays in progress long enough to observe the busy indicator.
YAML_FORCE_C_PARSER_ERROR = "GALLEY_YAML_FORCE_C_PARSER_ERROR"
YAML_PURE_RETRY_GATE = "GALLEY_YAML_PURE_RETRY_GATE"
_PURE_RETRY_GATE_TIMEOUT_S = 30.0

# YAML 1.1 core tags whose C load and pure dump already match pure Python.
# Other tags are decided in ``_tag_needs_reparse``. Probed against
# ruamel.yaml 0.19.1 and ruamel.yaml.clib 0.2.15.
_CORE_TAG_NAMES = frozenset({"str", "int", "float", "bool", "null", "binary"})
_QUOTED_SCALAR_STYLES = frozenset({'"', "'"})

_HEX = frozenset("0123456789abcdefABCDEF")
_HIGH_MIN = 0xD800
_HIGH_MAX = 0xDBFF
_LOW_MIN = 0xDC00
_LOW_MAX = 0xDFFF
_YAML_OPEN = (
    BlockMappingStartToken,
    BlockSequenceStartToken,
    FlowMappingStartToken,
    FlowSequenceStartToken,
)
_YAML_CLOSE = (BlockEndToken, FlowMappingEndToken, FlowSequenceEndToken)
_SCALAR_QUOTES = set(":#{}[]&*!|>'\"%@`,")

# Lone surrogates, as ``\u`` escapes or raw code units. A high unit immediately
# followed by a low unit is a pair and is not a hit, unless a backslash before
# that pair escapes the high unit and leaves the low unit lone.
_LONE_SURROGATE_RE = re.compile(
    r"(?<=\\)\\u[dD][89abAB][0-9a-fA-F]{2}\\u[dD][c-fC-F][0-9a-fA-F]{2}"
    r"|\\u[dD][89abAB][0-9a-fA-F]{2}(?!\\u[dD][c-fC-F][0-9a-fA-F]{2})"
    r"|(?<!\\u[dD][89abAB][0-9a-fA-F]{2})\\u[dD][c-fC-F][0-9a-fA-F]{2}"
    "|"
    "[\ud800-\udfff]"
)
_CHARACTER_WALK = object()

# A JSON string with the colon that makes it a key, or one bracket. In valid
# JSON a string followed by a colon is always a key of the innermost object.
_KEY_OR_BRACKET_RE = re.compile(
    r'("[^"\\]*(?:\\.[^"\\]*)*")[ \t\n\r]*(:)?|[][{}]', re.DOTALL
)


def format_json(text: str | bytes) -> str:
    """Return ``text`` parsed as JSON and pretty-printed.

    Valid text is scanned for lone surrogates, parsed with ``json.loads``,
    and checked against the nesting limit. The character walk runs only when
    that path needs the walk's line and column. A repeated key also takes
    the walk, so a syntax or nesting error later in the text still wins.
    """
    source = _decode_json(text)
    parsed = _json_fast_value(source)
    if parsed is _CHARACTER_WALK:
        return _format_json_walk_first(source)
    try:
        return _dump_json(parsed)
    except RecursionError:
        opened = _json_limits(source)
        raise nesting_error(opened[0], opened[1], depth=opened[2]) from None


def _detect_clib() -> bool:
    """Return True when safe mode selects the C parser and emitter."""
    if CParser is None or CEmitter is None:
        return False
    probe = YAML(typ="safe")
    return probe.Parser is CParser and probe.Emitter is CEmitter


_CLIB_READY = _detect_clib()


def _clib_available() -> bool:
    """Return True when ``ruamel.yaml.clib`` is installed and selected."""
    return _CLIB_READY


def _safe_yaml(*, pure: bool) -> YAML:
    """Safe YAML. ``pure`` forces the Python parser and emitter."""
    if pure:
        return YAML(typ="safe", pure=True)
    return YAML(typ="safe")


def _force_c_parser_error() -> bool:
    return bool(os.environ.get(YAML_FORCE_C_PARSER_ERROR))


def _wait_for_pure_retry_gate() -> None:
    """Block the pure-Python retry while a test holds the gate file."""
    gate = os.environ.get(YAML_PURE_RETRY_GATE)
    if not gate:
        return
    ready = gate + ".ready"
    go = gate + ".go"
    try:
        descriptor = os.open(ready, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except OSError:
        descriptor = -1
    if descriptor >= 0:
        os.close(descriptor)
    deadline = time.monotonic() + _PURE_RETRY_GATE_TIMEOUT_S
    while not os.path.exists(go):
        if time.monotonic() >= deadline:
            return
        time.sleep(0.01)


def _pure_python_yaml(text: str | bytes) -> str:
    """Re-parse with pure Python and return that result, success or error."""
    _wait_for_pure_retry_gate()
    return _format_yaml_impl(text, pure=True)


def format_yaml(text: str | bytes) -> str:
    """Return ``text`` parsed as YAML and dumped as a block document.

    When ``ruamel.yaml.clib`` is installed, the C loader runs first. The
    text shown to the user is written by the pure-Python emitter. A tab
    anywhere in the text, a ``%YAML`` or ``%TAG`` directive, a non-core
    tag, a bare ``!`` that is not followed on the same line by a
    non-empty scalar or a flow collection, or a block scalar whose first
    non-empty content line is only whitespace with a later line indented
    further is parsed again with pure Python, and that result is
    returned, success or error. If the C parser raises, the pure-Python
    safe parser runs and its result is returned. Without the C library
    this is the pure-Python safe parser and it does not warn.
    """
    if not _clib_available():
        # The spinner test forces the retry even on the pure-Python CI leg.
        if _force_c_parser_error():
            return _pure_python_yaml(text)
        return _format_yaml_impl(text, pure=False)
    reparse = True
    loaded: object = None
    try:
        if _force_c_parser_error():
            raise YAMLError("forced C parser failure")
        opened, tokens, failed = _scan_yaml(text, pure=False, keep_tokens=True)
        if not failed and not _needs_pure_reparse(text, tokens):
            loaded = _load_parsed(text, opened, pure=False)
            reparse = False
    except Exception:
        return _pure_python_yaml(text)
    if reparse:
        return _pure_python_yaml(text)
    try:
        return _dump_parsed(loaded, pure=True)
    except Exception:
        return _pure_python_yaml(text)


def _format_yaml_impl(text: str | bytes, *, pure: bool) -> str:
    """Parse and dump ``text`` with safe YAML, optionally forcing pure Python."""
    opened = _yaml_limits(text, pure=pure)
    if opened[2] > _RUAMEL_SAFE_DEPTH:
        try:
            parsed = _yaml_from_events(text, pure=pure)
            return _dump_block(parsed)
        except RecursionError:
            raise nesting_error(opened[0], opened[1], depth=opened[2]) from None
    try:
        return _dump_parsed(_load_parsed(text, opened, pure=pure), pure=pure)
    except RecursionError:
        raise nesting_error(opened[0], opened[1], depth=opened[2]) from None


def _load_parsed(
    text: str | bytes, opened: tuple[int, int, int], *, pure: bool
) -> object:
    """Load one document. Deep documents use an explicit stack."""
    if opened[2] > _RUAMEL_SAFE_DEPTH:
        return _yaml_from_events(text, pure=pure)
    yaml = _safe_yaml(pure=pure)
    try:
        return yaml.load(text)
    except RecursionError:
        raise nesting_error(opened[0], opened[1], depth=opened[2]) from None
    except ReaderError as exc:
        error = _reader_utf8_error(text, exc)
        if error is not None:
            raise error from None
        raise


def _dump_parsed(value: object, *, pure: bool) -> str:
    """Dump ``value`` with safe YAML, using the pure emitter when asked."""
    yaml = _block_dumper(_safe_yaml(pure=pure))
    buffer = StringIO()
    yaml.dump(value, buffer)
    return buffer.getvalue()


def _block_dumper(yaml: YAML) -> YAML:
    """Set the block style, key order and width every YAML dump shares."""
    yaml.default_flow_style = False
    yaml.representer.sort_base_mapping_type_on_output = False
    yaml.width = YAML_DUMP_WIDTH
    return yaml


def _has_tab(text: str | bytes) -> bool:
    """Return True when ``text`` contains a tab."""
    return (b"\t" if isinstance(text, bytes) else "\t") in text


def _needs_pure_reparse(text: str | bytes, tokens: list[object]) -> bool:
    """Return True when the C tokens are one of the pure-Python mismatches.

    A tab anywhere in ``text`` forces that reparse. The other cases are a
    ``%YAML`` or ``%TAG`` directive, a tag ``_tag_needs_reparse`` rejects,
    and a block scalar whose first non-empty content line is whitespace
    with a later line indented further. Everything else keeps the C value
    and is dumped in pure Python.

    Token indexes from the C scanner count characters and skip a leading
    BOM, so the check runs on decoded text with that BOM removed.
    """
    if _has_tab(text):
        return True
    source = _scanner_text(text)
    cursor = 0
    for index, token in enumerate(tokens):
        start = token.start_mark.index
        end = token.end_mark.index
        if _gap_has_tab_outside_comment(source, cursor, start):
            return True
        if isinstance(token, DirectiveToken) and token.name in {"YAML", "TAG"}:
            return True
        if isinstance(token, TagToken) and _tag_needs_reparse(token, tokens, index):
            return True
        if isinstance(token, ScalarToken):
            raw = source[start:end]
            if _scalar_tab_needs_reparse(token, raw) or _block_scalar_needs_reparse(
                token, raw
            ):
                return True
        cursor = max(cursor, end)
    return _gap_has_tab_outside_comment(source, cursor, len(source))


def _scanner_text(text: str | bytes) -> str:
    """Return the character string C token indexes point into.

    ``ruamel.yaml.clib`` counts characters, not UTF-8 bytes, and its
    marks skip a leading BOM. Slicing the original bytes with those
    indexes moves a tab that follows a multibyte key.
    """
    if isinstance(text, bytes):
        decoded = text.decode("utf-8")
    else:
        decoded = text
    if decoded.startswith("\ufeff"):
        return decoded[1:]
    return decoded


def _scalar_tab_needs_reparse(token: ScalarToken, raw: str) -> bool:
    """Return True when a tab in this scalar still needs pure Python.

    A tab inside a single-line quoted scalar (style ``"`` or ``'``, and
    ``start_mark.line == end_mark.line``) matches pure Python, so it
    stays on the C value. A tab between tokens is not inside the scalar
    and is caught by the gap check. A tab in a multi-line quoted scalar,
    or in any other scalar, is parsed again.
    """
    if "\t" not in raw:
        return False
    if (
        token.style in _QUOTED_SCALAR_STYLES
        and token.start_mark.line == token.end_mark.line
    ):
        return False
    return True


def _is_core_tag(value: object) -> bool:
    if not isinstance(value, tuple) or len(value) != 2:
        return False
    handle, name = value
    return handle == "!!" and name in _CORE_TAG_NAMES


def _tag_needs_reparse(token: TagToken, tokens: list[object], index: int) -> bool:
    """Return True when this tag's C value can differ from pure Python."""
    if _is_core_tag(token.value):
        return False
    follower = tokens[index + 1] if index + 1 < len(tokens) else None
    return not _bare_tag_keeps_c_value(token, follower)


def _bare_tag_keeps_c_value(token: TagToken, follower: object | None) -> bool:
    """Return True when a bare ``!`` is safe to keep from the C parser.

    ``!`` and ``!<!>`` are both the token ``(None, "!")``. Keep the C
    value only when that tag is followed on the same line by a non-empty
    scalar, a flow-sequence start, or a flow-mapping start. An empty
    node, a block collection on the next line, and a local tag such as
    ``!foo`` are parsed again.

    This resolution is not YAML-spec behavior. The spec would make
    ``! 123``, ``! true``, and ``! null`` strings. ruamel.yaml 0.19.1
    and ruamel.yaml.clib 0.2.15 both resolve those three as the integer
    123, boolean true, and null, and the dumped text matches, so the C
    value is kept. The same pair also matches for ``! 1.5`` (float),
    ``! 2026-10-18`` (date), ``! ~`` (null), ``! "x"`` (string), and
    ``a: ! |`` with an indented line (block scalar). Those stay on the
    C value too.
    """
    if token.value != (None, "!"):
        return False
    if follower is None or not _same_line(token, follower):
        return False
    if isinstance(follower, (FlowSequenceStartToken, FlowMappingStartToken)):
        return True
    return (
        isinstance(follower, ScalarToken)
        and isinstance(follower.value, str)
        and follower.value != ""
    )


def _same_line(token: object, follower: object) -> bool:
    token_mark = getattr(token, "start_mark", None)
    follower_mark = getattr(follower, "start_mark", None)
    token_line = getattr(token_mark, "line", None)
    follower_line = getattr(follower_mark, "line", None)
    return isinstance(token_line, int) and token_line == follower_line


def _block_scalar_needs_reparse(token: ScalarToken, raw: str | bytes) -> bool:
    """Return True for the block indent pure Python rejects and C accepts."""
    if token.style not in {"|", ">"}:
        return False
    if isinstance(raw, str):
        normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
        newline = "\n"
        space = " "
        empty = ""
    else:
        normalized = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        newline = b"\n"
        space = b" "
        empty = b""
    split_at = normalized.find(newline)
    if split_at < 0:
        return False
    first_indent: int | None = None
    for line in normalized[split_at + 1 :].split(newline):
        if line == empty:
            continue
        if first_indent is None:
            if line.strip(space) != empty:
                return False
            first_indent = len(line)
            continue
        stripped = line.lstrip(space)
        indent = len(line) - len(stripped)
        if indent > first_indent and stripped != empty:
            return True
    return False


def _gap_has_tab_outside_comment(text: str | bytes, start: int, end: int) -> bool:
    """Return True when ``text[start:end]`` has a tab before any comment."""
    index = start
    while index < end:
        comment = _is_char(text, index, "#") and (
            index == start or _is_space(text, index - 1)
        )
        if comment:
            index += 1
            while index < end and not _is_char(text, index, "\n"):
                index += 1
            continue
        if _is_char(text, index, "\t"):
            return True
        index += 1
    return False


def _is_char(text: str | bytes, index: int, char: str) -> bool:
    found = text[index]
    if isinstance(found, int):
        return found == ord(char)
    return found == char


def _is_space(text: str | bytes, index: int) -> bool:
    return (
        _is_char(text, index, " ")
        or _is_char(text, index, "\t")
        or _is_char(text, index, "\n")
    )


def _decode_json(text: str | bytes) -> str:
    """Return JSON text, stripping a leading UTF-8 BOM from bytes.

    ``json.loads`` accepts a BOM on bytes and rejects it on text. Decoding
    here keeps that split, and turns a bad byte into a located error.
    """
    if isinstance(text, str):
        return text
    try:
        decoded = text.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise utf8_input_error(text, exc.start) from None
    if decoded.startswith("\ufeff"):
        return decoded[1:]
    return decoded


def _format_json_walk_first(text: str | bytes) -> str:
    """Format JSON by walking characters before ``json.loads``.

    This is the path ``format_json`` used before the fast scan. It still
    places nesting and surrogate errors, and a parity test calls it with
    the same input it passes to ``format_json``.
    """
    source = _decode_json(text)
    opened = _json_limits(source)
    try:
        return _dump_json(_json_walked_value(source, opened))
    except RecursionError:
        raise nesting_error(opened[0], opened[1], depth=opened[2]) from None


def _json_walked_value(source: str, opened: tuple[int, int, int]) -> object:
    """Parse ``source`` after ``_json_limits`` accepted it and returned ``opened``.

    Syntax errors raise ``json.JSONDecodeError`` before repeated keys are
    reported, so the parser's first complaint is the one shown. A document
    that is otherwise valid and repeats a key raises
    ``JSONDuplicateKeyError`` with every repeat.
    """
    try:
        return _json_loads(source)
    except RecursionError:
        raise nesting_error(opened[0], opened[1], depth=opened[2]) from None
    except _RepeatedKey:
        pass
    try:
        json.loads(source)
    except RecursionError:
        raise nesting_error(opened[0], opened[1], depth=opened[2]) from None
    raise _duplicate_key_error(source)


class _RepeatedKey(Exception):
    """Stops ``json.loads`` at the first object that repeats a key."""


def _reject_repeated_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise _RepeatedKey
    return value


def _json_loads(source: str) -> object:
    """``json.loads`` that raises ``_RepeatedKey`` instead of keeping the last value.

    The C scanner still parses. The hook only builds each object's dict,
    about 2 ms on top of a 2 MB document's 85 ms format.
    """
    return json.loads(source, object_pairs_hook=_reject_repeated_keys)


def _duplicate_key_error(source: str) -> JSONDuplicateKeyError:
    """Locate every repeated key in ``source``, which is valid JSON.

    This runs only after ``_json_loads`` found a repeat, so valid documents
    never pay for it. One pass keeps a set of keys per open object. Keys
    compare after escapes are decoded, so ``"a"`` and ``"\\u0061"`` repeat.
    Valid JSON strings hold no raw newline, so lines are counted between
    tokens.
    """
    found: list[DuplicateKey] = []
    objects: list[set[str] | None] = []
    line = 1
    line_start = 0
    cursor = 0
    for match in _KEY_OR_BRACKET_RE.finditer(source):
        token = match.group()
        if token in ("{", "["):
            objects.append(set() if token == "{" else None)
            continue
        if token in ("}", "]"):
            objects.pop()
            continue
        if match.group(2) is None:
            continue
        raw = match.group(1)
        key = raw[1:-1] if "\\" not in raw else json.loads(raw)
        seen = objects[-1]
        if seen is None:
            continue
        if key not in seen:
            seen.add(key)
            continue
        position = match.start()
        breaks = source.count("\n", cursor, position)
        if breaks:
            line += breaks
            line_start = source.rfind("\n", cursor, position) + 1
        cursor = position
        found.append(DuplicateKey(key, line, position - line_start + 1))
    return JSONDuplicateKeyError(tuple(found))


def _json_fast_value(source: str) -> object:
    """Return the parsed value, or ``_CHARACTER_WALK`` when the walk must run."""
    if _has_lone_surrogate(source):
        return _CHARACTER_WALK
    try:
        parsed = _json_loads(source)
    except RecursionError:
        return _CHARACTER_WALK
    except (json.JSONDecodeError, _RepeatedKey):
        return _CHARACTER_WALK
    if _json_nesting_exceeds(parsed, MAX_DEPTH):
        return _CHARACTER_WALK
    return parsed


def _has_lone_surrogate(text: str) -> bool:
    """Return True if ``text`` has a surrogate the character walk rejects."""
    start = 0
    while True:
        match = _LONE_SURROGATE_RE.search(text, start)
        if match is None:
            return False
        token = match.group()
        if _lone_surrogate_match(text, match.start(), token):
            return True
        end = match.end()
        if end <= start:
            end = start + 1
        start = end


def _lone_surrogate_match(text: str, index: int, token: str) -> bool:
    """Return True when a regex hit is a surrogate the walk rejects.

    A backslash that is itself escaped is literal text, so two backslashes
    before ``uD800`` are not a surrogate escape. If that prefix sits on a
    high+low pair, the low unit is still lone.
    """
    real = _backslash_starts_escape(text, index)
    if len(token) == 12:
        return not real
    return real


def _backslash_starts_escape(text: str, index: int) -> bool:
    """Return True when ``text[index]`` is not escaped by a backslash.

    Inside a string the walk pairs backslashes from the left. An even run
    of backslashes immediately before ``index`` is that same pairing.
    """
    count = 0
    cursor = index - 1
    while cursor >= 0 and text[cursor] == "\\":
        count += 1
        cursor -= 1
    return count % 2 == 0


def _json_nesting_exceeds(value: object, limit: int) -> bool:
    """Return True when lists or dicts nest more containers than ``limit``.

    An explicit stack keeps this off the call stack. A scalar has depth 0.
    The outermost container is depth 1, which is what ``_json_limits`` counts.
    """
    if not isinstance(value, (dict, list)):
        return False
    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > limit:
            return True
        if isinstance(node, dict):
            nested = node.values()
        else:
            nested = node
        child_depth = depth + 1
        for child in nested:
            if not isinstance(child, (dict, list)):
                continue
            if child_depth > limit:
                return True
            stack.append((child, child_depth))
    return False


def _json_limits(text: str) -> tuple[int, int, int]:
    """Reject nesting past the limit and lone UTF-16 surrogates.

    ``format_json`` calls this only when the fast path needs a location.
    The 1-based line and column are the last container opened, and the
    third value is that container's depth, so a later ``RecursionError``
    still has a place to point.
    """
    depth = 0
    line = 1
    column = 1
    index = 0
    size = len(text)
    in_string = False
    opened = (1, 1, 0)
    while index < size:
        char = text[index]
        if in_string:
            if char == "\\":
                escape = _unicode_escape(text, index)
                if escape is not None:
                    literal, unit = escape
                    if _HIGH_MIN <= unit <= _HIGH_MAX:
                        pair = _unicode_escape(text, index + 6)
                        if pair is not None and _LOW_MIN <= pair[1] <= _LOW_MAX:
                            index, line, column = _advance(
                                text, index, line, column, 12
                            )
                            continue
                    if _HIGH_MIN <= unit <= _LOW_MAX:
                        raise escape_error(line, column, literal)
                    index, line, column = _advance(text, index, line, column, 6)
                    continue
                step = 2 if index + 1 < size else 1
                index, line, column = _advance(text, index, line, column, step)
                continue
            if char == '"':
                in_string = False
            elif _HIGH_MIN <= ord(char) <= _LOW_MAX:
                raise escape_error(line, column, f"\\u{ord(char):04X}")
            index, line, column = _advance(text, index, line, column, 1)
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            depth += 1
            opened = (line, column, depth)
            if depth > MAX_DEPTH:
                raise nesting_error(line, column, depth=depth)
        elif char in "}]":
            depth = max(0, depth - 1)
        index, line, column = _advance(text, index, line, column, 1)
    return opened


def _unicode_escape(text: str, index: int) -> tuple[str, int] | None:
    """Return the ``\\uXXXX`` escape and its code unit at ``index``."""
    if (
        index + 6 > len(text)
        or text[index] != "\\"
        or text[index + 1] != "u"
        or any(char not in _HEX for char in text[index + 2 : index + 6])
    ):
        return None
    literal = text[index : index + 6]
    return literal, int(literal[2:], 16)


def _advance(
    text: str, index: int, line: int, column: int, count: int
) -> tuple[int, int, int]:
    end = index + count
    while index < end:
        if text[index] == "\n":
            line += 1
            column = 1
        else:
            column += 1
        index += 1
    return index, line, column


def _yaml_limits(text: str | bytes, *, pure: bool = False) -> tuple[int, int, int]:
    """Reject UTF-8 bytes and nesting past the limit before the composer runs.

    The scanner walks tokens in a loop. Stopping at ``MAX_DEPTH`` keeps the
    composer off the call stack, so a deep document cannot overflow the
    Windows stack. The returned line, column, and depth are the last
    container the scanner opened.
    """
    opened, _tokens, _failed = _scan_yaml(text, pure=pure, keep_tokens=False)
    return opened


def _scan_yaml(
    text: str | bytes, *, pure: bool, keep_tokens: bool
) -> tuple[tuple[int, int, int], list[object], bool]:
    """Scan ``text``. The bool is True when a YAML error ended the scan."""
    yaml = _safe_yaml(pure=pure)
    depth = 0
    deepest = 0
    opened = (1, 1, 0)
    tokens: list[object] = []
    try:
        for token in yaml.scan(text):
            if keep_tokens:
                tokens.append(token)
            if isinstance(token, _YAML_OPEN):
                depth += 1
                if depth > deepest:
                    deepest = depth
                opened = (*_mark(token, opened[:2]), depth)
                if depth > MAX_DEPTH:
                    raise nesting_error(opened[0], opened[1], depth=depth)
            elif isinstance(token, _YAML_CLOSE):
                depth = max(0, depth - 1)
    except InputError:
        raise
    except RecursionError:
        raise nesting_error(opened[0], opened[1], depth=opened[2]) from None
    except ReaderError as exc:
        error = _reader_utf8_error(text, exc)
        if error is not None:
            raise error from None
        return opened, tokens, True
    except YAMLError:
        return opened, tokens, True
    return opened, tokens, False


def _mark(token: object, fallback: tuple[int, int]) -> tuple[int, int]:
    mark = getattr(token, "start_mark", None)
    line = getattr(mark, "line", None)
    column = getattr(mark, "column", None)
    if isinstance(line, int) and isinstance(column, int):
        return line + 1, column + 1
    return fallback


def _reader_utf8_error(text: str | bytes, exc: ReaderError) -> InputError | None:
    if not isinstance(text, bytes) or exc.encoding != "utf-8":
        return None
    position = exc.position
    if not isinstance(position, int):
        return None
    return utf8_input_error(text, position)


def _yaml_from_events(text: str | bytes, *, pure: bool = False) -> object:
    """Build one document from parser events with an explicit stack."""
    yaml = _safe_yaml(pure=pure)
    anchors: dict[str, object] = {}
    stack: list[list[object]] = []
    root: object = None

    def attach(value: object) -> None:
        nonlocal root
        if not stack:
            root = value
            return
        frame = stack[-1]
        state = frame[0]
        if state == "seq":
            container = frame[1]
            if isinstance(container, list):
                container.append(value)
            return
        if state == "key":
            frame[0] = "value"
            frame[2] = value
            return
        container = frame[1]
        if isinstance(container, dict):
            container[frame[2]] = value
        frame[0] = "key"
        frame[2] = None

    for event in yaml.parse(text):
        if isinstance(event, MappingStartEvent):
            node: object = {}
            if event.anchor:
                anchors[event.anchor] = node
            attach(node)
            stack.append(["key", node, None])
        elif isinstance(event, MappingEndEvent):
            stack.pop()
        elif isinstance(event, SequenceStartEvent):
            node = []
            if event.anchor:
                anchors[event.anchor] = node
            attach(node)
            stack.append(["seq", node, None])
        elif isinstance(event, SequenceEndEvent):
            stack.pop()
        elif isinstance(event, AliasEvent):
            attach(anchors[event.anchor])
        elif isinstance(event, ScalarEvent):
            attach(_scalar_value(yaml, event))
    return root


def _scalar_value(yaml: YAML, event: ScalarEvent) -> object:
    tag = event.tag
    if tag is None:
        tag = yaml.resolver.resolve(ScalarNode, event.value, event.implicit)
    node = ScalarNode(
        tag,
        event.value,
        event.start_mark,
        event.end_mark,
        style=event.style,
    )
    return yaml.constructor.construct_object(node)


def _dump_block(value: object) -> str:
    """Dump ``value`` as a block document without recursing per level."""
    if not isinstance(value, (dict, list)):
        return _scalar_text(value) + "\n"
    lines: list[str] = []
    jobs: list[tuple[object, ...]] = []
    if isinstance(value, dict):
        jobs.append(("map", value, 0, list(value), 0))
    else:
        jobs.append(("seq", value, 0, 0))
    while jobs:
        job = jobs[-1]
        if job[0] == "map":
            _dump_map_step(lines, jobs)
        else:
            _dump_seq_step(lines, jobs)
    return "\n".join(lines) + "\n"


def _dump_map_step(lines: list[str], jobs: list[tuple[object, ...]]) -> None:
    _, mapping, indent, keys, index = jobs[-1]
    if (
        not isinstance(mapping, dict)
        or not isinstance(keys, list)
        or not isinstance(index, int)
        or not isinstance(indent, int)
    ):
        jobs.pop()
        return
    if index >= len(keys):
        jobs.pop()
        return
    key = keys[index]
    child = mapping[key]
    jobs[-1] = ("map", mapping, indent, keys, index + 1)
    pad = " " * indent
    key_text = _scalar_text(key)
    if isinstance(child, dict):
        if child:
            lines.append(f"{pad}{key_text}:")
            jobs.append(("map", child, indent + 2, list(child), 0))
        else:
            lines.append(f"{pad}{key_text}: {{}}")
    elif isinstance(child, list):
        if child:
            lines.append(f"{pad}{key_text}:")
            jobs.append(("seq", child, indent + 2, 0))
        else:
            lines.append(f"{pad}{key_text}: []")
    else:
        lines.append(f"{pad}{key_text}: {_scalar_text(child)}")


def _dump_seq_step(lines: list[str], jobs: list[tuple[object, ...]]) -> None:
    _, seq, indent, index = jobs[-1]
    if (
        not isinstance(seq, list)
        or not isinstance(index, int)
        or not isinstance(indent, int)
    ):
        jobs.pop()
        return
    if index >= len(seq):
        jobs.pop()
        return
    child = seq[index]
    jobs[-1] = ("seq", seq, indent, index + 1)
    pad = " " * indent
    if isinstance(child, dict):
        if child:
            lines.append(f"{pad}-")
            jobs.append(("map", child, indent + 2, list(child), 0))
        else:
            lines.append(f"{pad}- {{}}")
    elif isinstance(child, list):
        if child:
            lines.append(f"{pad}-")
            jobs.append(("seq", child, indent + 2, 0))
        else:
            lines.append(f"{pad}- []")
    else:
        lines.append(f"{pad}- {_scalar_text(child)}")


def _scalar_text(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        if (
            value == ""
            or any(char.isspace() for char in value)
            or any(char in _SCALAR_QUOTES for char in value)
        ):
            return json.dumps(value, ensure_ascii=False)
        return value
    return json.dumps(str(value), ensure_ascii=False)


def format_document(text: str | bytes, source: str, target: str) -> str:
    """Format ``text`` from ``source`` into ``target``.

    ``source`` and ``target`` are ``json`` or ``yaml`` (case-insensitive).
    Same-format calls go through ``format_json`` and ``format_yaml``, so
    located ``InputError`` checks stay exactly those functions.
    """
    source_kind = _normalize_format(source)
    target_kind = _normalize_format(target)
    if source_kind == "json" and target_kind == "json":
        return format_json(text)
    if source_kind == "yaml" and target_kind == "yaml":
        return format_yaml(text)
    if source_kind == "json":
        value = json.loads(format_json(text))
        return _dump_yaml_value(value)
    rendered = format_yaml(text)
    opened = _yaml_limits(text)
    try:
        return _dump_json(_load_rendered_yaml(text, rendered))
    except RecursionError:
        raise nesting_error(opened[0], opened[1], depth=opened[2]) from None


def load_document(text: str | bytes, source: str) -> object:
    """Return the value ``format_document`` formats from ``text``.

    The text passes the same checks as ``format_json`` or ``format_yaml``
    and raises the same errors. YAML is then loaded from ``text`` itself
    with the pure-Python parser, not from the rendered text, so an anchor
    and each of its aliases are one shared object, scalars included.
    """
    if _normalize_format(source) == "json":
        return json.loads(format_json(text))
    format_yaml(text)
    return _load_parsed(text, _yaml_limits(text, pure=True), pure=True)


def dump_value(value: object, target: str) -> str:
    """Serialize ``value`` the way ``format_document`` writes ``target``.

    JSON uses the JSON dumper. YAML uses the pure-Python safe emitter that
    ``format_yaml`` shows, and falls back to the explicit-stack block dump
    for a value too deep for ruamel.
    """
    if _normalize_format(target) == "json":
        return _dump_json(value)
    try:
        return _dump_parsed(value, pure=True)
    except RecursionError:
        return _dump_block(value)


def _normalize_format(name: str) -> str:
    kind = name.strip().lower()
    if kind not in {"json", "yaml"}:
        raise ValueError(f"Unsupported format: {name}")
    return kind


def _dump_json(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def _dump_yaml_value(value: object) -> str:
    """Dump ``value`` with the same safe block style ``format_yaml`` uses."""
    yaml = _block_dumper(YAML(typ="safe"))
    buffer = StringIO()
    try:
        yaml.dump(value, buffer)
    except RecursionError:
        return _dump_block(value)
    dumped = buffer.getvalue()
    if dumped and not dumped.endswith("\n"):
        return dumped + "\n"
    return dumped


def _load_rendered_yaml(original: str | bytes, rendered: str) -> object:
    """Load YAML text ``format_yaml`` already accepted."""
    yaml = YAML(typ="safe")
    try:
        return yaml.load(rendered)
    except RecursionError:
        return _yaml_from_events(original)
