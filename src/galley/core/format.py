"""JSON, YAML, and XML formatting.

Invalid UTF-8, documents nested past ``MAX_DEPTH``, and JSON lone-surrogate
escapes raise ``InputError``. So does a YAML surrogate escape on its way to
JSON output. A JSON object that repeats a key raises
``JSONDuplicateKeyError`` instead of keeping the last value. Other syntax
errors keep the line and column from the parser. Banner sentences are not
built here.

Every YAML dump writes the bytes ruamel's pure-Python emitter writes, with
or without ``ruamel.yaml.clib``. With it, the C emitter does the writing,
with scalar styles set so its output matches (see ``_dump_yaml``). A
``!!set`` is written with its string members first, in code-point order,
then its other members grouped by type name and ordered by ``repr``, so the
order does not follow ``PYTHONHASHSEED``.

``sort_keys=True`` reorders the output only; the input text is never
changed. See :func:`sort_mapping_keys` for the ordering rule.

``prepare`` rewrites the loaded value before it is written; masking uses
it. It runs after the checks and before ``sort_keys``. With it set, a YAML
document that has an alias loads with the pure-Python parser, because the
C parser gives each alias of a scalar its own string object.

XML is formatted by ``galley.core.xml_format`` and is written only as XML.
It reaches ``format_document`` once the format table has an XML row, which
#63 slice 3 adds with the picker item.
"""

import datetime
import functools
import json
import os
import re
import time
from collections.abc import Callable
from io import StringIO

from ruamel.yaml import YAML
from ruamel.yaml.emitter import Emitter as PureEmitter
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
from ruamel.yaml.nodes import MappingNode, ScalarNode, SequenceNode
from ruamel.yaml.reader import ReaderError
from ruamel.yaml.representer import SafeRepresenter
from ruamel.yaml.tag import Tag
from ruamel.yaml.tokens import (
    AliasToken,
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
    NotJSONValueError,
    escape_error,
    nesting_error,
    utf8_input_error,
)
from galley.core.formats import format_named, targets_for
from galley.core.xml_format import format_xml

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

# Rewrites a loaded value before it is written. See the module docstring.
Prepare = Callable[[object], object]

# A surrogate in a YAML value, and the escape the pure-Python emitter writes
# for one. YAML ``\u`` and ``\U`` escapes are code points, so two escapes
# that would be a UTF-16 pair in JSON are two surrogates here.
_SURROGATE_RE = re.compile("[\ud800-\udfff]")
_RENDERED_SURROGATE_RE = re.compile(r"\\(?:u|U0000)[dD][89a-fA-F][0-9a-fA-F]{2}")
_YAML_HEX_ESCAPE_WIDTHS = {"x": 2, "u": 4, "U": 8}
_YAML_LINE_BREAKS = frozenset("\n\x85\u2028\u2029")

# A JSON string with the colon that makes it a key, or one bracket. In valid
# JSON a string followed by a colon is always a key of the innermost object.
_KEY_OR_BRACKET_RE = re.compile(
    r'("[^"\\]*(?:\\.[^"\\]*)*")[ \t\n\r]*(:)?|[][{}]', re.DOTALL
)


def format_json(
    text: str | bytes, *, sort_keys: bool = False, prepare: Prepare | None = None
) -> str:
    """Return ``text`` parsed as JSON and pretty-printed.

    ``sort_keys`` writes every object's keys in code-point order. Arrays
    keep their order. Repeated keys are still an error.

    Valid text is scanned for lone surrogates, parsed with ``json.loads``,
    and checked against the nesting limit. The character walk runs only when
    that path needs the walk's line and column. A repeated key also takes
    the walk, so a syntax or nesting error later in the text still wins.
    """
    source = _decode_json(text)
    parsed = _json_fast_value(source)
    if parsed is _CHARACTER_WALK:
        return _format_json_walk_first(source, sort_keys=sort_keys, prepare=prepare)
    try:
        return _dump_json(_apply(prepare, parsed), sort_keys=sort_keys)
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


def _pure_python_yaml(
    text: str | bytes, *, sort_keys: bool = False, prepare: Prepare | None = None
) -> str:
    """Re-parse with pure Python and return that result, success or error."""
    _wait_for_pure_retry_gate()
    return _format_yaml_impl(text, pure=True, sort_keys=sort_keys, **_given(prepare))


def format_yaml(
    text: str | bytes, *, sort_keys: bool = False, prepare: Prepare | None = None
) -> str:
    """Return ``text`` parsed as YAML and dumped as a block document.

    When ``ruamel.yaml.clib`` is installed, the C loader runs first. The
    text shown to the user is written by ``_dump_yaml``, byte for byte what
    the pure-Python emitter writes. A tab
    anywhere in the text, a ``%YAML`` or ``%TAG`` directive, a non-core
    tag, a bare ``!`` that is not followed on the same line by a
    non-empty scalar or a flow collection, or a block scalar whose first
    non-empty content line is only whitespace with a later line indented
    further is parsed again with pure Python, and that result is
    returned, success or error. If the C parser raises, the pure-Python
    safe parser runs and its result is returned. Without the C library
    this is the pure-Python safe parser and it does not warn.

    ``sort_keys`` reorders every mapping with :func:`sort_mapping_keys`
    after the document loads, so duplicate keys are still an error.
    ``prepare`` runs on the loaded value before that. With ``prepare`` set,
    a document with an alias is parsed again with pure Python too.
    """
    retry = functools.partial(_pure_python_yaml, sort_keys=sort_keys, **_given(prepare))
    if not _clib_available():
        # The spinner test forces the retry even on the pure-Python CI leg.
        if _force_c_parser_error():
            return retry(text)
        return _format_yaml_impl(
            text, pure=False, sort_keys=sort_keys, **_given(prepare)
        )
    reparse = True
    loaded: object = None
    try:
        if _force_c_parser_error():
            raise YAMLError("forced C parser failure")
        opened, tokens, failed = _scan_yaml(text, pure=False, keep_tokens=True)
        if (
            not failed
            and not _needs_pure_reparse(text, tokens)
            and not (prepare is not None and _has_alias(tokens))
        ):
            loaded = _load_parsed(text, opened, pure=False)
            reparse = False
    except Exception:
        return retry(text)
    if reparse:
        return retry(text)
    # Outside the retry, so a masking failure is not retried in pure Python.
    loaded = _apply(prepare, loaded)
    try:
        return _dump_yaml(_ordered(loaded, sort_keys))
    except Exception:
        return retry(text)


def _format_yaml_impl(
    text: str | bytes,
    *,
    pure: bool,
    sort_keys: bool = False,
    prepare: Prepare | None = None,
) -> str:
    """Parse and dump ``text`` with safe YAML, optionally forcing pure Python."""
    opened = _yaml_limits(text, pure=pure)
    if opened[2] > _RUAMEL_SAFE_DEPTH:
        try:
            parsed = _apply(prepare, _yaml_from_events(text, pure=pure))
            return _dump_block(_ordered(parsed, sort_keys))
        except RecursionError:
            raise nesting_error(opened[0], opened[1], depth=opened[2]) from None
    try:
        loaded = _apply(prepare, _load_parsed(text, opened, pure=pure))
        return _dump_parsed(_ordered(loaded, sort_keys), pure=pure)
    except RecursionError:
        raise nesting_error(opened[0], opened[1], depth=opened[2]) from None


def _given(prepare: Prepare | None) -> dict[str, Prepare]:
    """``prepare`` as a keyword only when set, so calls without it are unchanged."""
    return {} if prepare is None else {"prepare": prepare}


def _has_alias(tokens: list[object]) -> bool:
    return any(isinstance(token, AliasToken) for token in tokens)


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


def _set_member_key(member: object) -> tuple[int, str, str]:
    """Sort key for a ``!!set`` member: strings by code point, then the rest."""
    if isinstance(member, str):
        return (0, "", member)
    return (1, type(member).__name__, repr(member))


@functools.cache
def _shared_tag(tag: str) -> Tag:
    """One ``Tag`` per tag string, so its URI is decoded once per process.

    ruamel builds a new ``Tag`` for every node, and each one decodes its
    URI the first time an emitter reads it. Only the parser changes a
    ``Tag``, so dumps can share them.
    """
    return Tag(suffix=tag)


class _BlockRepresenter(SafeRepresenter):
    """Safe representer that writes ``!!set`` members in a fixed order."""

    def represent_scalar(
        self, tag: object, value: object, style: object = None, anchor: object = None
    ) -> ScalarNode:
        if isinstance(tag, str):
            tag = _shared_tag(tag)
        return super().represent_scalar(tag, value, style=style, anchor=anchor)

    def represent_sequence(
        self, tag: object, sequence: object, flow_style: object = None
    ) -> SequenceNode:
        if isinstance(tag, str):
            tag = _shared_tag(tag)
        return super().represent_sequence(tag, sequence, flow_style=flow_style)

    def represent_mapping(
        self, tag: object, mapping: object, flow_style: object = None
    ) -> MappingNode:
        if isinstance(tag, str):
            tag = _shared_tag(tag)
        return super().represent_mapping(tag, mapping, flow_style=flow_style)

    def represent_set(self, data: set[object]) -> MappingNode:
        members = dict.fromkeys(sorted(data, key=_set_member_key))
        return self.represent_mapping("tag:yaml.org,2002:set", members)


_BlockRepresenter.add_representer(set, _BlockRepresenter.represent_set)


def _block_dumper(yaml: YAML, representer: type = _BlockRepresenter) -> YAML:
    """Set the block style, key order, set order and width every dump shares."""
    yaml.Representer = representer
    yaml.default_flow_style = False
    yaml.representer.sort_base_mapping_type_on_output = False
    yaml.width = YAML_DUMP_WIDTH
    return yaml


# Characters the ruamel.yaml.clib emitter writes differently from the pure
# emitter: it escapes everything past U+FFFF ("\U0001F600"), it breaks a
# quoted line at NEL, U+2028 and U+2029, and it can't encode a lone
# surrogate. None of these has a setting.
_C_EMITTER_MISMATCH_RE = re.compile(
    "[\U00010000-\U0010ffff\x85\u2028\u2029\ud800-\udfff]"
)
_CORE_TAG_PREFIX = "tag:yaml.org,2002:"
_SIMPLE_KEY_LIMIT = 128
_STR_TAG = _CORE_TAG_PREFIX + "str"
# The pure emitter's own scalar analysis, used only to read ``allow_block_plain``.
_PURE_ANALYZER = PureEmitter(None, allow_unicode=True)
_PURE_ANALYZER.use_version = None


class _NeedsPureEmitter(Exception):
    """The C emitter can't write this value the way the pure emitter does."""


class _CBlockRepresenter(_BlockRepresenter):
    """Sets scalar styles so the C emitter writes the pure emitter's bytes.

    Both emitters pick plain when they can. After that, the pure emitter
    prefers double quotes for a string with ``'`` or a newline, and the C
    emitter takes single quotes, so those strings ask for ``"``. A simple
    key is shorter than 128 characters counting its ``!!str`` tag in pure
    Python, and at most 128 UTF-8 bytes in C; a key the two disagree on, a
    key with ``\\r`` (a line break only in C), binary data and the
    characters in ``_C_EMITTER_MISMATCH_RE`` raise ``_NeedsPureEmitter``.
    """

    def represent_str(self, data: str) -> ScalarNode:
        if _C_EMITTER_MISMATCH_RE.search(data):
            raise _NeedsPureEmitter
        style = None
        if "\n" in data or (
            "'" in data and not _PURE_ANALYZER.analyze_scalar(data).allow_block_plain
        ):
            style = '"'
        return self.represent_scalar(_STR_TAG, data, style=style)

    def represent_binary(self, data: bytes) -> ScalarNode:
        raise _NeedsPureEmitter

    def represent_key(self, data: object) -> object:
        node = self.represent_data(data)
        if isinstance(node, ScalarNode) and _keys_disagree(data, node):
            raise _NeedsPureEmitter
        return node


# The pure emitter counts a key's ``!!`` tag toward the simple-key limit.
# The tag comes from the key's type, which costs less than ``node.tag``.
# ``bool`` is listed before ``int``, its base class.
_KEY_TAG_LENGTHS = (
    (str, len("!!str")),
    (bool, len("!!bool")),
    (int, len("!!int")),
    (float, len("!!float")),
    (type(None), len("!!null")),
    (datetime.date, len("!!timestamp")),
)


def _keys_disagree(data: object, node: ScalarNode) -> bool:
    """Return True when only one emitter would write ``node`` as a simple key."""
    text = node.value
    if "\r" in text:
        return True
    tag_length = _key_tag_length(data, node)
    pure_length = len(text) + tag_length
    c_length = len(text.encode("utf-8", "surrogatepass"))
    return (pure_length < _SIMPLE_KEY_LIMIT) != (c_length <= _SIMPLE_KEY_LIMIT)


def _key_tag_length(data: object, node: ScalarNode) -> int:
    for kind, length in _KEY_TAG_LENGTHS:
        if isinstance(data, kind):
            return length
    tag = node.tag or ""
    return len(tag) - len(_CORE_TAG_PREFIX) + len("!!")


_CBlockRepresenter.add_representer(str, _CBlockRepresenter.represent_str)
_CBlockRepresenter.add_representer(bytes, _CBlockRepresenter.represent_binary)


def _dump_yaml(value: object) -> str:
    """Dump ``value`` as the block YAML Galley shows.

    With ``ruamel.yaml.clib`` the C emitter writes it, set up by
    ``_CBlockRepresenter`` to give the pure emitter's bytes. A value it
    can't match that way is written by the pure emitter. The output is
    never edited after either emitter writes it.
    """
    if _clib_available():
        yaml = _block_dumper(_safe_yaml(pure=False), _CBlockRepresenter)
        buffer = StringIO()
        try:
            yaml.dump(value, buffer)
        except _NeedsPureEmitter:
            pass
        else:
            return buffer.getvalue()
    return _dump_parsed(value, pure=True)


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


def _format_json_walk_first(
    text: str | bytes, *, sort_keys: bool = False, prepare: Prepare | None = None
) -> str:
    """Format JSON by walking characters before ``json.loads``.

    This is the path ``format_json`` used before the fast scan. It still
    places nesting and surrogate errors, and a parity test calls it with
    the same input it passes to ``format_json``.
    """
    source = _decode_json(text)
    opened = _json_limits(source)
    try:
        parsed = _apply(prepare, _json_walked_value(source, opened))
        return _dump_json(parsed, sort_keys=sort_keys)
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


def format_document(
    text: str | bytes,
    source: str,
    target: str,
    *,
    sort_keys: bool = False,
    prepare: Prepare | None = None,
) -> str:
    """Format ``text`` from ``source`` into ``target``.

    ``source`` and ``target`` are format names (case-insensitive). Same-format
    calls go through ``format_json``, ``format_yaml``, and ``format_xml``, so
    located ``InputError`` checks stay exactly those functions. A pair
    ``targets_for`` does not list raises ``ValueError``. ``sort_keys`` orders
    JSON and YAML output keys with :func:`sort_mapping_keys`; XML ignores it.
    ``prepare`` rewrites the loaded JSON or YAML value before it is written.
    XML does not load to a value, so it raises ``ValueError`` with ``prepare``.
    """
    source_kind = _normalize_format(source)
    target_kind = _normalize_format(target)
    if target_kind not in (fmt.name for fmt in targets_for(source_kind)):
        raise ValueError(f"Can't write {source.strip()} as {target.strip()}.")
    if prepare is not None:
        _value_format(source)
    if source_kind == "xml":
        return format_xml(text)
    if source_kind == "json" and target_kind == "json":
        return format_json(text, sort_keys=sort_keys, prepare=prepare)
    if source_kind == "yaml" and target_kind == "yaml":
        return format_yaml(text, sort_keys=sort_keys, prepare=prepare)
    if source_kind == "json":
        value = json.loads(format_json(text, sort_keys=sort_keys))
        return _dump_yaml_value(_apply(prepare, value))
    value = yaml_json_value(text, prepare=prepare)
    opened = _yaml_limits(text)
    try:
        return _dump_json(_ordered(value, sort_keys), default=_not_json_value)
    except RecursionError:
        raise nesting_error(opened[0], opened[1], depth=opened[2]) from None


def load_document(text: str | bytes, source: str) -> object:
    """Return the value ``format_document`` formats from ``text``.

    The text passes the same checks as ``format_json`` or ``format_yaml``
    and raises the same errors. YAML is then loaded from ``text`` itself
    with the pure-Python parser, not from the rendered text, so an anchor
    and each of its aliases are one shared object, scalars included. A
    format that does not load to a value, such as XML, raises ``ValueError``.
    """
    if _value_format(source) == "json":
        return json.loads(format_json(text))
    format_yaml(text)
    return _load_parsed(text, _yaml_limits(text, pure=True), pure=True)


def dump_value(value: object, target: str) -> str:
    """Serialize ``value`` the way ``format_document`` writes ``target``.

    JSON uses the JSON dumper. YAML is written by ``_dump_yaml``, as
    ``format_yaml`` writes it, and falls back to the explicit-stack block dump
    for a value too deep for ruamel. A format that does not load to a value
    raises ``ValueError``.
    """
    if _value_format(target) == "json":
        return _dump_json(value)
    try:
        return _dump_yaml(value)
    except RecursionError:
        return _dump_block(value)


def sort_mapping_keys(value: object) -> object:
    """Reorder every mapping in ``value`` by key, in place, and return ``value``.

    Keys are compared as the text Galley writes for them, in Unicode
    code-point order: the order ``json.dumps(sort_keys=True)`` and
    ``jq -S`` use. Uppercase letters come before lowercase, and ``"é"``
    comes after ``"z"``. A YAML key that is not a string compares as its
    JSON text, so ``10`` comes before ``9``, ``false`` and ``null`` sort
    as those words, and a date as ``2024-01-01``. A list used as a key
    compares as its Python text. Keys whose text ties, such as ``1`` and
    ``"1"`` in YAML, keep their input order. Merge keys (``<<``) were
    already expanded when the document loaded, so merged keys sort with
    the mapping's own keys.

    Lists keep their order, and the mappings inside them are sorted. A
    mapping or list reached twice through YAML aliases is sorted once and
    stays one shared object, so the dump still writes an anchor and its
    aliases. Nesting is walked with an explicit stack, not recursion.
    """
    seen: set[int] = set()
    stack = [value]
    while stack:
        node = stack.pop()
        if id(node) in seen:
            continue
        if isinstance(node, dict):
            seen.add(id(node))
            items = sorted(node.items(), key=_key_order)
            node.clear()
            node.update(items)
            children = node.values()
        elif isinstance(node, list):
            seen.add(id(node))
            children = node
        else:
            continue
        stack.extend(child for child in children if isinstance(child, (dict, list)))
    return value


def _ordered(value: object, sort_keys: bool) -> object:
    return sort_mapping_keys(value) if sort_keys else value


def _apply(prepare: Prepare | None, value: object) -> object:
    return value if prepare is None else prepare(value)


def _key_order(item: tuple[object, object]) -> str:
    key = item[0]
    if isinstance(key, str):
        return key
    if key is None:
        return "null"
    if isinstance(key, bool):
        return "true" if key else "false"
    if isinstance(key, (int, float)):
        # The text json.dumps writes for a number key.
        return repr(key)
    return str(key)


def _normalize_format(name: str) -> str:
    return format_named(name).name


def _value_format(name: str) -> str:
    """The name of a format that loads to a value. Others raise ``ValueError``."""
    fmt = format_named(name)
    if not fmt.loads_to_value:
        raise ValueError(f"{fmt.label} doesn't load to a value.")
    return fmt.name


def _dump_json(
    value: object,
    *,
    sort_keys: bool = False,
    default: Callable[[object], object] | None = None,
) -> str:
    """``sort_keys`` is for JSON input, whose keys are all strings."""
    return (
        json.dumps(
            value, indent=2, ensure_ascii=False, sort_keys=sort_keys, default=default
        )
        + "\n"
    )


def _not_json_value(value: object) -> object:
    """``json.dumps`` hook for a YAML value JSON can't hold, at any depth."""
    raise NotJSONValueError(f"JSON can't hold a {type(value).__name__} value")


def _dump_yaml_value(value: object) -> str:
    """Dump ``value`` with ``_dump_yaml``, as ``format_yaml`` writes it."""
    try:
        dumped = _dump_yaml(value)
    except RecursionError:
        return _dump_block(value)
    if dumped and not dumped.endswith("\n"):
        return dumped + "\n"
    return dumped


def yaml_json_value(text: str | bytes, *, prepare: Prepare | None = None) -> object:
    """Return the value Format to JSON writes for YAML ``text``.

    The text passes ``format_yaml``'s checks and raises its errors. The
    rendered text is loaded back, and a surrogate in that value raises a
    ``bad_escape`` ``InputError`` at its escape in ``text``. JSON output
    can't hold a lone surrogate, and JSON input rejects one the same way.
    ``prepare`` goes to ``format_yaml``, so the rendered text is rewritten.
    A reload too deep for ruamel then walks the rendered text, not ``text``,
    so a value ``prepare`` replaced can't come back.
    """
    rendered = format_yaml(text, prepare=prepare)
    value = _load_rendered_yaml(text if prepare is None else rendered, rendered)
    if _RENDERED_SURROGATE_RE.search(rendered) and _value_has_surrogate(value):
        raise _yaml_surrogate_error(text)
    return value


def _load_rendered_yaml(original: str | bytes, rendered: str) -> object:
    """Load YAML text ``format_yaml`` already accepted.

    The C parser rejects some pure-Python emitter output, such as a
    surrogate escape or a root ``!!binary`` block. Then pure Python loads
    it, as ``format_yaml`` retries a C parser error.
    """
    try:
        return _load_rendered_with(original, rendered, pure=False)
    except YAMLError:
        if not _clib_available():
            raise
    return _load_rendered_with(original, rendered, pure=True)


def _load_rendered_with(original: str | bytes, rendered: str, *, pure: bool) -> object:
    try:
        return _safe_yaml(pure=pure).load(rendered)
    except RecursionError:
        return _yaml_from_events(original, pure=pure)


def _value_has_surrogate(value: object) -> bool:
    """Return True when a string key or value in ``value`` holds a surrogate.

    An alias can make a container hold itself, so each one is visited once.
    """
    stack = [value]
    seen: set[int] = set()
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            if _SURROGATE_RE.search(node):
                return True
            continue
        if not isinstance(node, (dict, list, set)) or id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, dict):
            stack.extend(node.keys())
            stack.extend(node.values())
        else:
            stack.extend(node)
    return False


def _yaml_surrogate_error(text: str | bytes) -> InputError:
    """Locate the first surrogate escape in a double-quoted scalar of ``text``.

    Surrogates reach a YAML value only through ``\\u``, ``\\U`` escapes in
    double-quoted scalars, since the reader rejects raw ones. Pure-Python
    marks count characters of the decoded text, a leading BOM included.
    """
    source = text.decode("utf-8") if isinstance(text, bytes) else text
    for token in _safe_yaml(pure=True).scan(text):
        if not isinstance(token, ScalarToken) or token.style != '"':
            continue
        if not _SURROGATE_RE.search(token.value):
            continue
        start = token.start_mark
        raw = source[start.index : token.end_mark.index]
        found = _surrogate_escape(raw)
        if found is None:
            continue
        offset, literal = found
        line, column = _yaml_position(raw, offset, start.line, start.column)
        return escape_error(line + 1, column + 1, literal)
    raise AssertionError("surrogate value without a surrogate escape")


def _surrogate_escape(raw: str) -> tuple[int, str] | None:
    """Return the offset and text of the first surrogate escape in ``raw``."""
    index = 1
    while index < len(raw):
        if raw[index] != "\\":
            index += 1
            continue
        width = _YAML_HEX_ESCAPE_WIDTHS.get(raw[index + 1 : index + 2])
        if width is None:
            index += 2
            continue
        literal = raw[index : index + 2 + width]
        if _HIGH_MIN <= int(literal[2:], 16) <= _LOW_MAX:
            return index, literal
        index += len(literal)
    return None


def _yaml_position(raw: str, offset: int, line: int, column: int) -> tuple[int, int]:
    """Advance a 0-based ruamel mark over ``raw[:offset]``, as its reader does."""
    for index in range(offset):
        char = raw[index]
        if char in _YAML_LINE_BREAKS or (
            char == "\r" and raw[index + 1 : index + 2] != "\n"
        ):
            line += 1
            column = 0
        elif char != "\ufeff":
            column += 1
    return line, column
