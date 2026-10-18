"""Convert a Python literal to JSON or YAML.

The text is parsed with ``ast.parse(..., mode="eval")`` and walked against
an allowlist. Values are built from those nodes. Nothing in the text is run.

``format_json`` rejects documents nested past ``MAX_DEPTH`` and does not cap
the number of characters. This walk uses that same depth limit. A
``RecursionError`` from the parser or a dump becomes a :class:`ConvertError`.
"""

import ast
import json
import math
from dataclasses import dataclass
from typing import assert_never

from ruamel.yaml.error import YAMLError

from galley.core.errors import MAX_DEPTH
from galley.core.format import _RUAMEL_SAFE_DEPTH, _dump_block, _dump_yaml_value

_TUPLE_ONE = "1 tuple was converted to a list."
_KEY_ONE = "1 dictionary key was converted to a string."
_COLLISION_ONE = "1 key matched another after conversion. The last value was kept."


@dataclass(frozen=True)
class ConvertNotice:
    """A conversion the user should see."""

    message: str


@dataclass(frozen=True)
class ConvertError:
    """A located problem. ``message`` names it and includes the line and column."""

    message: str
    lineno: int
    colno: int


@dataclass(frozen=True)
class ConvertResult:
    """JSON or YAML text, plus any conversion notices."""

    text: str
    notices: tuple[ConvertNotice, ...]


@dataclass
class _Visit:
    node: ast.AST
    depth: int
    as_key: bool


@dataclass
class _ReduceList:
    count: int


@dataclass
class _ReduceTuple:
    count: int


@dataclass
class _ReduceDict:
    count: int


_Step = _Visit | _ReduceList | _ReduceTuple | _ReduceDict

_REJECTED: dict[type[ast.AST], str] = {
    ast.Call: "Function calls can't be converted",
    ast.Name: "Names can't be converted",
    ast.Attribute: "Attribute access can't be converted",
    ast.Subscript: "Subscripts can't be converted",
    ast.ListComp: "Comprehensions can't be converted",
    ast.SetComp: "Comprehensions can't be converted",
    ast.DictComp: "Comprehensions can't be converted",
    ast.GeneratorExp: "Comprehensions can't be converted",
    ast.Lambda: "Lambda expressions can't be converted",
    ast.JoinedStr: "F-strings can't be converted",
    ast.FormattedValue: "F-strings can't be converted",
    ast.BinOp: "Binary operators can't be converted",
    ast.BoolOp: "Boolean operators can't be converted",
    ast.Compare: "Comparison operators can't be converted",
    ast.IfExp: "Conditional expressions can't be converted",
    ast.Set: "Sets can't be converted",
    ast.Starred: "Starred expressions can't be converted",
    ast.NamedExpr: "Assignment expressions can't be converted",
    ast.Await: "Await expressions can't be converted",
    ast.Yield: "Yield expressions can't be converted",
    ast.YieldFrom: "Yield expressions can't be converted",
}

_KEY_TYPES = "Dictionary keys must be strings, numbers, booleans, or None"
_UNPACKING = "Dictionary unpacking can't be converted"
_OPERATOR = "This operator can't be converted"
_NONFINITE = "NaN and Infinity can't be converted to JSON"
_BYTES = "Bytes can't be converted"
_COMPLEX = "Complex numbers can't be converted"
_UNKNOWN = "This expression can't be converted"
_VALUE = "This value can't be converted"


def convert_python(text: str, target: str = "json") -> ConvertResult | ConvertError:
    """Convert a Python literal in ``text`` to JSON or YAML.

    ``target`` is ``json`` or ``yaml``, compared without case. Tuples become
    lists, and ``int``, ``float``, ``bool``, and ``None`` dictionary keys
    become strings. A stringified key that lands on a key already in that
    dictionary keeps the last value. Each of those shows up as a notice.
    ``NaN`` and infinity are rejected for JSON and kept for YAML.
    """
    kind = _normalize_target(target)
    try:
        # The parser also rejects a parenthesis stack past its own cap, before
        # this walk can apply MAX_DEPTH. That failure is a located syntax error.
        parsed = ast.parse(text, mode="eval")
    except RecursionError:
        return _depth_error(1, 1)
    except SyntaxError as exc:
        return _syntax_error(exc)
    walker = _Walker(kind)
    try:
        error = walker.evaluate(parsed.body)
    except RecursionError:
        return _depth_error(1, 1)
    if error is not None:
        return error
    if len(walker.values) != 1:
        return _problem(_UNKNOWN, 1, 1)
    try:
        rendered = _render(walker.values[0], kind, walker.max_depth)
    except RecursionError:
        return _depth_error(1, 1)
    except (ValueError, TypeError, YAMLError):
        return _problem(_VALUE, 1, 1)
    return ConvertResult(rendered, walker.notices())


def _normalize_target(target: str) -> str:
    kind = target.strip().lower()
    if kind not in {"json", "yaml"}:
        raise ValueError(f"Unsupported format: {target}")
    return kind


def _problem(problem: str, line: int, column: int) -> ConvertError:
    return ConvertError(
        message=f"{problem}: line {line} column {column}",
        lineno=line,
        colno=column,
    )


def _located(node: ast.AST, problem: str) -> ConvertError:
    line = node.lineno if isinstance(node.lineno, int) else 1
    offset = node.col_offset if isinstance(node.col_offset, int) else 0
    return _problem(problem, line, offset + 1)


def _depth_error(line: int, column: int) -> ConvertError:
    return _problem(
        f"This document is nested more than {MAX_DEPTH} levels deep",
        line,
        column,
    )


def _syntax_error(exc: SyntaxError) -> ConvertError:
    line = exc.lineno if isinstance(exc.lineno, int) else 1
    column = exc.offset if isinstance(exc.offset, int) and exc.offset > 0 else 1
    detail = exc.msg or "This isn't valid Python"
    return _problem(detail, line, column)


def _render(value: object, target: str, depth: int) -> str:
    if target == "json":
        rendered = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)
        return rendered + "\n"
    # ruamel recurses once per level. Past this depth format_yaml builds the
    # document with an explicit stack, and so does this dump.
    if depth > _RUAMEL_SAFE_DEPTH:
        return _dump_block(value)
    return _dump_yaml_value(value)


class _Walker:
    """Build one value from an allowlisted expression, using an explicit stack."""

    def __init__(self, target: str) -> None:
        self.target = target
        self.values: list[object] = []
        self.max_depth = 0
        self._tuples = 0
        self._keys = 0
        self._collisions = 0

    def notices(self) -> tuple[ConvertNotice, ...]:
        found: list[ConvertNotice] = []
        if self._tuples == 1:
            found.append(ConvertNotice(_TUPLE_ONE))
        elif self._tuples > 1:
            found.append(
                ConvertNotice(f"{self._tuples} tuples were converted to lists.")
            )
        if self._keys == 1:
            found.append(ConvertNotice(_KEY_ONE))
        elif self._keys > 1:
            found.append(
                ConvertNotice(
                    f"{self._keys} dictionary keys were converted to strings."
                )
            )
        if self._collisions == 1:
            found.append(ConvertNotice(_COLLISION_ONE))
        elif self._collisions > 1:
            found.append(
                ConvertNotice(
                    f"{self._collisions} keys matched another after conversion. "
                    "The last value was kept."
                )
            )
        return tuple(found)

    def evaluate(self, node: ast.AST) -> ConvertError | None:
        work: list[_Step] = [_Visit(node, 0, False)]
        while work:
            step = work.pop()
            if isinstance(step, _Visit):
                error = self._visit(step, work)
            elif isinstance(step, _ReduceList):
                error = self._reduce_list(step.count)
            elif isinstance(step, _ReduceTuple):
                error = self._reduce_tuple(step.count)
            elif isinstance(step, _ReduceDict):
                error = self._reduce_dict(step.count)
            else:
                assert_never(step)
            if error is not None:
                return error
        return None

    def _visit(self, step: _Visit, work: list[_Step]) -> ConvertError | None:
        node = step.node
        rejected = _REJECTED.get(type(node))
        if rejected is not None:
            return _located(node, rejected)
        if step.as_key and isinstance(node, (ast.List, ast.Tuple, ast.Dict)):
            return _located(node, _KEY_TYPES)
        if isinstance(node, ast.Constant):
            return self._constant(node)
        if isinstance(node, ast.UnaryOp):
            return self._unary(node)
        if isinstance(node, (ast.List, ast.Tuple)):
            return self._open_sequence(node, step.depth, work)
        if isinstance(node, ast.Dict):
            return self._open_dict(node, step.depth, work)
        return _located(node, _UNKNOWN)

    def _constant(self, node: ast.Constant) -> ConvertError | None:
        value = node.value
        if value is None or type(value) is bool or type(value) is str:
            self.values.append(value)
            return None
        if type(value) is int:
            self.values.append(value)
            return None
        if type(value) is float:
            return self._float(node, value)
        if type(value) is bytes:
            return _located(node, _BYTES)
        if type(value) is complex:
            return _located(node, _COMPLEX)
        return _located(node, _UNKNOWN)

    def _float(self, node: ast.AST, value: float) -> ConvertError | None:
        if self.target == "json" and not math.isfinite(value):
            return _located(node, _NONFINITE)
        self.values.append(value)
        return None

    def _unary(self, node: ast.UnaryOp) -> ConvertError | None:
        if not isinstance(node.op, (ast.UAdd, ast.USub)):
            return _located(node, _OPERATOR)
        operand = node.operand
        if not isinstance(operand, ast.Constant):
            return _located(node, _OPERATOR)
        value = operand.value
        if type(value) is not int and type(value) is not float:
            return _located(node, _OPERATOR)
        if isinstance(node.op, ast.USub):
            value = -value
        if type(value) is float:
            return self._float(node, value)
        self.values.append(value)
        return None

    def _open_sequence(
        self,
        node: ast.List | ast.Tuple,
        parent_depth: int,
        work: list[_Step],
    ) -> ConvertError | None:
        opened = self._enter(node, parent_depth)
        if isinstance(opened, ConvertError):
            return opened
        if isinstance(node, ast.Tuple):
            work.append(_ReduceTuple(len(node.elts)))
        else:
            work.append(_ReduceList(len(node.elts)))
        for elt in reversed(node.elts):
            work.append(_Visit(elt, opened, False))
        return None

    def _open_dict(
        self, node: ast.Dict, parent_depth: int, work: list[_Step]
    ) -> ConvertError | None:
        opened = self._enter(node, parent_depth)
        if isinstance(opened, ConvertError):
            return opened
        pairs: list[tuple[ast.AST, ast.AST]] = []
        for key, value in zip(node.keys, node.values, strict=True):
            if key is None:
                return _located(node, _UNPACKING)
            pairs.append((key, value))
        work.append(_ReduceDict(len(pairs)))
        for key, value in reversed(pairs):
            work.append(_Visit(value, opened, False))
            work.append(_Visit(key, opened, True))
        return None

    def _enter(self, node: ast.AST, parent_depth: int) -> int | ConvertError:
        depth = parent_depth + 1
        if depth > MAX_DEPTH:
            line = node.lineno if isinstance(node.lineno, int) else 1
            offset = node.col_offset if isinstance(node.col_offset, int) else 0
            return _depth_error(line, offset + 1)
        if depth > self.max_depth:
            self.max_depth = depth
        return depth

    def _reduce_list(self, count: int) -> None:
        self.values.append(_pop_sequence(self.values, count))

    def _reduce_tuple(self, count: int) -> None:
        self.values.append(_pop_sequence(self.values, count))
        self._tuples += 1

    def _reduce_dict(self, count: int) -> ConvertError | None:
        pairs: list[tuple[object, object]] = []
        for _ in range(count):
            value = self.values.pop()
            key = self.values.pop()
            pairs.append((key, value))
        pairs.reverse()
        built: dict[str, object] = {}
        # Keys whose current value came from a non-string key. A later key
        # that spells the same text is a collision; a plain duplicate string
        # key is not.
        from_conversion: set[str] = set()
        for key, value in pairs:
            if type(key) is str:
                text = key
                converted = False
            else:
                spelled = _string_key(key)
                if spelled is None:
                    return _problem(_KEY_TYPES, 1, 1)
                text = spelled
                converted = True
                self._keys += 1
            if text in built and (converted or text in from_conversion):
                self._collisions += 1
            if converted:
                from_conversion.add(text)
            else:
                from_conversion.discard(text)
            built[text] = value
        self.values.append(built)
        return None


def _pop_sequence(values: list[object], count: int) -> list[object]:
    items = [values.pop() for _ in range(count)]
    items.reverse()
    return items


def _string_key(key: object) -> str | None:
    if key is None:
        return "null"
    if key is True:
        return "true"
    if key is False:
        return "false"
    if type(key) is int:
        return str(key)
    if type(key) is float:
        if math.isnan(key):
            return "nan"
        if math.isinf(key):
            return "-inf" if key < 0 else "inf"
        return json.dumps(key)
    return None
