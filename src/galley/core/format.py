"""JSON and YAML formatting.

Invalid UTF-8, documents nested past ``MAX_DEPTH``, and JSON lone-surrogate
escapes raise ``InputError``. Other syntax errors keep the line and column
from the parser. Banner sentences are not built here.
"""

import json
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
from ruamel.yaml.nodes import ScalarNode
from ruamel.yaml.reader import ReaderError
from ruamel.yaml.tokens import (
    BlockEndToken,
    BlockMappingStartToken,
    BlockSequenceStartToken,
    FlowMappingEndToken,
    FlowMappingStartToken,
    FlowSequenceEndToken,
    FlowSequenceStartToken,
)

from galley.core.errors import (
    MAX_DEPTH,
    InputError,
    escape_error,
    nesting_error,
    utf8_input_error,
)

# ruamel's loader and dumper recurse once per level, with several frames each.
# Past this depth a document that is still within MAX_DEPTH would hit
# Python's recursion limit, so it is built with an explicit stack instead.
_RUAMEL_SAFE_DEPTH = 256

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


def format_json(text: str | bytes) -> str:
    """Return ``text`` parsed as JSON and pretty-printed."""
    source = _decode_json(text)
    opened = _json_limits(source)
    try:
        parsed = json.loads(source)
        return json.dumps(parsed, indent=2, ensure_ascii=False) + "\n"
    except RecursionError:
        raise nesting_error(opened[0], opened[1], depth=opened[2]) from None


def format_yaml(text: str | bytes) -> str:
    """Return ``text`` parsed as YAML and dumped as a block document."""
    opened = _yaml_limits(text)
    if opened[2] > _RUAMEL_SAFE_DEPTH:
        try:
            parsed = _yaml_from_events(text)
            return _dump_block(parsed)
        except RecursionError:
            raise nesting_error(opened[0], opened[1], depth=opened[2]) from None
    yaml = YAML(typ="safe")
    yaml.default_flow_style = False
    try:
        parsed = yaml.load(text)
    except RecursionError:
        raise nesting_error(opened[0], opened[1], depth=opened[2]) from None
    except ReaderError as exc:
        error = _reader_utf8_error(text, exc)
        if error is not None:
            raise error from None
        raise
    buffer = StringIO()
    try:
        yaml.dump(parsed, buffer)
    except RecursionError:
        raise nesting_error(opened[0], opened[1], depth=opened[2]) from None
    return buffer.getvalue()


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


def _json_limits(text: str) -> tuple[int, int, int]:
    """Reject nesting past the limit and lone UTF-16 surrogates.

    Returns the 1-based line and column of the last container opened, and
    that container's depth, so a later ``RecursionError`` still has a place
    to point.
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


def _yaml_limits(text: str | bytes) -> tuple[int, int, int]:
    """Reject UTF-8 bytes and nesting past the limit before the composer runs.

    The scanner walks tokens in a loop. Stopping at ``MAX_DEPTH`` keeps the
    composer off the call stack, so a deep document cannot overflow the
    Windows stack. The returned line, column, and depth are the last
    container the scanner opened.
    """
    yaml = YAML(typ="safe")
    depth = 0
    deepest = 0
    opened = (1, 1, 0)
    try:
        for token in yaml.scan(text):
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
        return opened[0], opened[1], deepest
    except YAMLError:
        return opened[0], opened[1], deepest
    return opened[0], opened[1], deepest


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


def _yaml_from_events(text: str | bytes) -> object:
    """Build one document from parser events with an explicit stack."""
    yaml = YAML(typ="safe")
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
