"""Mask values under sensitive key names.

This module does not import Qt. ``mask_tree`` replaces every scalar under a
matched key with ``"•••"`` and keeps the keys and structure, so the output
has the same shape as the unmasked output. ``copy_as_tree`` builds the three
Copy As trees from the same rules. Serializing a tree is ``format.dump_value``.

Keys match as whole names after lowercasing and dropping ``_``, ``-`` and
spaces, so ``email_address`` matches ``emailaddress`` and ``emailer`` does
not. The tree is walked with explicit stacks and never recursively. Any
failure raises ``MaskError``, so a job with masking on fails whole and never
shows unmasked output.

A YAML alias is the same Python object as its anchor. A shared mapping or
sequence is masked everywhere when any place it appears is masked, so a
masked anchor does not show through an alias and the dumper writes the same
anchors as before. A shared string is masked everywhere for the same reason.
Strings shorter than two characters are skipped there, because CPython
shares those between unrelated values.
"""

import datetime as dt
import random
import re
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from galley.core.errors import MAX_DEPTH
from galley.core.fake import (
    fake_value,
    random_date_like,
    random_digit,
    random_float_like,
    random_int_like,
)

MASK = "•••"

DEFAULT_MASK_KEYS = (
    "email",
    "emailaddress",
    "phone",
    "phonenumber",
    "mobile",
    "ssn",
    "socialsecuritynumber",
    "address",
    "streetaddress",
)

Kind = Literal["email", "phone", "other"]
CopyAsMode = Literal["original", "masked", "fake"]

_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_PHONE_RE = re.compile(r"\+?[0-9][0-9 ().-]*")
_ISO_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_PHONE_DIGITS = (7, 15)
_PHONE_KEPT_TAIL = 4
_OTHER_KEY_NAMES = frozenset(
    {"ssn", "socialsecuritynumber", "address", "streetaddress"}
)
# Shorter strings are shared by CPython, so identity says nothing about aliases.
_ALIAS_MIN_LENGTH = 2

_Replace = Callable[[object, object], object]


class MaskError(Exception):
    """Masking could not finish, so the job fails and shows no output."""


def normalize_key(name: str) -> str:
    """Lowercase ``name`` and drop ``_``, ``-`` and spaces."""
    return name.lower().replace("_", "").replace("-", "").replace(" ", "")


@dataclass(frozen=True)
class MaskRules:
    """The key names to mask. Picklable, so a format job can carry it."""

    keys: tuple[str, ...] = DEFAULT_MASK_KEYS
    _names: frozenset[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        keys = tuple(self.keys)
        names = frozenset(name for name in map(normalize_key, keys) if name)
        object.__setattr__(self, "keys", keys)
        object.__setattr__(self, "_names", names)

    def matches(self, key: object) -> bool:
        """Return True when ``key`` is a string naming a masked key."""
        return isinstance(key, str) and normalize_key(key) in self._names


@dataclass(frozen=True)
class MaskResult:
    value: object
    masked_count: int


def detect_kind(key: object, value: object) -> Kind:
    """Say whether ``value`` reads as an email, a phone, or other text.

    The nearest key name decides first: a name containing ``email`` is an
    email, one containing ``phone`` or ``mobile`` is a phone, and the other
    default names are other text. For any other key a simple pattern on
    the value decides. The kind only shapes replacement values. It never
    decides what is masked.
    """
    name = normalize_key(key) if isinstance(key, str) else ""
    if "email" in name:
        return "email"
    if "phone" in name or "mobile" in name:
        return "phone"
    if name in _OTHER_KEY_NAMES or not isinstance(value, str):
        return "other"
    if _EMAIL_RE.fullmatch(value):
        return "email"
    digits = sum(char.isdigit() for char in value)
    if (
        _PHONE_RE.fullmatch(value)
        and _PHONE_DIGITS[0] <= digits <= _PHONE_DIGITS[1]
        and not _ISO_DATE_RE.fullmatch(value)
    ):
        return "phone"
    return "other"


def preserve_mask(
    value: object, kind: Kind, rng: random.Random | None = None
) -> object:
    """Return ``value`` disguised but in the same format.

    An email keeps its domain and its local part becomes ``x`` of the same
    length. A phone keeps its separators and leading digits, and its last
    four digits are random. Other text turns letters into ``x`` and digits
    into random digits, keeping punctuation, spaces and length. An int or
    float keeps its type, sign and digit count with random digits. A bool
    and ``None`` stay as they are. A date or datetime becomes a random one
    and bytes become random bytes of the same length. Any other type raises
    ``TypeError``.
    """
    chooser = rng if rng is not None else random.SystemRandom()
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return random_int_like(value, chooser)
    if isinstance(value, float):
        return random_float_like(value, chooser)
    if isinstance(value, str):
        return _preserve_text(value, kind, chooser)
    if isinstance(value, dt.date):
        return random_date_like(value, chooser)
    if isinstance(value, bytes):
        return chooser.randbytes(len(value))
    raise TypeError(f"cannot mask {type(value).__name__}")


def mask_tree(value: object, rules: MaskRules | None = None) -> MaskResult:
    """Return a copy of ``value`` with matched values replaced by ``"•••"``.

    ``value`` is not changed. ``masked_count`` is the number of scalars
    replaced. A mapping or sequence shared through a YAML alias is built,
    and counted, once.
    """
    return _rewrite(value, rules or MaskRules(), lambda _scalar, _key: MASK)


def copy_as_tree(
    value: object,
    mode: CopyAsMode,
    rules: MaskRules | None = None,
    rng: random.Random | None = None,
) -> object:
    """Return the tree Copy As serializes for ``mode``.

    ``original`` is ``value`` itself. ``masked`` passes each matched scalar
    through ``preserve_mask`` and ``fake`` through ``fake_value``. Both
    touch only the scalars ``mask_tree`` would replace.
    """
    if mode == "original":
        return value
    chooser = rng if rng is not None else random.SystemRandom()
    if mode == "masked":
        replace = _replacer(preserve_mask, chooser)
    elif mode == "fake":
        replace = _replacer(fake_value, chooser)
    else:
        raise ValueError(f"Unknown Copy As mode: {mode}")
    return _rewrite(value, rules or MaskRules(), replace).value


def _replacer(
    make: Callable[[object, Kind, random.Random], object], rng: random.Random
) -> _Replace:
    def replace(scalar: object, key: object) -> object:
        return make(scalar, detect_kind(key, scalar), rng)

    return replace


def _preserve_text(value: str, kind: Kind, rng: random.Random) -> str:
    if kind == "email" and "@" in value:
        local, _, domain = value.rpartition("@")
        if local:
            return "x" * len(local) + "@" + domain
    if kind == "phone":
        digits = [index for index, char in enumerate(value) if char.isdigit()]
        if digits:
            chars = list(value)
            for index in digits[-_PHONE_KEPT_TAIL:]:
                chars[index] = random_digit(rng)
            return "".join(chars)
    return "".join(_preserve_char(char, rng) for char in value)


def _preserve_char(char: str, rng: random.Random) -> str:
    if char.isdigit():
        return random_digit(rng)
    if char.isalpha():
        return "x"
    return char


def _rewrite(value: object, rules: MaskRules, replace: _Replace) -> MaskResult:
    """Find what to hide, then build the copy. Every failure is a MaskError."""
    try:
        roots = _matched_values(value, rules)
        hidden_nodes, hidden_strings = _hidden(roots)
        return _build(value, rules, replace, hidden_nodes, hidden_strings)
    except MaskError:
        raise
    except Exception as exc:
        raise MaskError(f"masking failed: {type(exc).__name__}: {exc}") from exc


def _matched_values(root: object, rules: MaskRules) -> list[object]:
    """Return every value under a matched key, visiting each container once.

    Breadth-first order reaches each container by its shortest path, so a
    document the loader accepted stays within ``MAX_DEPTH``.
    """
    matched: list[object] = []
    if not isinstance(root, (dict, list)):
        _reject_tuple(root)
        return matched
    seen = {id(root)}
    queue: deque[tuple[object, int]] = deque([(root, 1)])
    while queue:
        node, depth = queue.popleft()
        if depth > MAX_DEPTH:
            raise MaskError(f"nesting deeper than {MAX_DEPTH}")
        if isinstance(node, dict):
            children = node.values()
            matched.extend(child for key, child in node.items() if rules.matches(key))
        else:
            children = node
        for child in children:
            _reject_tuple(child)
            if isinstance(child, (dict, list)) and id(child) not in seen:
                seen.add(id(child))
                queue.append((child, depth + 1))
    return matched


def _hidden(roots: list[object]) -> tuple[set[int], set[int]]:
    """Return the ids of containers and strings inside matched values."""
    nodes: set[int] = set()
    strings: set[int] = set()
    stack = list(roots)
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            if len(node) >= _ALIAS_MIN_LENGTH:
                strings.add(id(node))
            continue
        if isinstance(node, (set, frozenset)):
            raise MaskError("cannot mask a YAML set: its members are keys")
        if not isinstance(node, (dict, list)) or id(node) in nodes:
            continue
        nodes.add(id(node))
        stack.extend(node.values() if isinstance(node, dict) else node)
    return nodes, strings


def _build(
    root: object,
    rules: MaskRules,
    replace: _Replace,
    hidden_nodes: set[int],
    hidden_strings: set[int],
) -> MaskResult:
    """Copy ``root``, replacing hidden scalars. Shared containers stay shared."""
    count = 0
    copies: dict[int, object] = {}
    pending: list[tuple[object, object, object]] = []

    def convert(node: object, key: object, hidden: bool) -> object:
        nonlocal count
        if isinstance(node, (dict, list)):
            existing = copies.get(id(node))
            if existing is not None:
                return existing
            copy = node.__class__()
            copies[id(node)] = copy
            pending.append((node, copy, key))
            return copy
        if hidden or (isinstance(node, str) and id(node) in hidden_strings):
            count += 1
            return replace(node, key)
        return node

    result = convert(root, None, False)
    while pending:
        source, copy, key = pending.pop()
        hidden = id(source) in hidden_nodes
        if isinstance(source, dict) and isinstance(copy, dict):
            for child_key, child in source.items():
                copy[child_key] = convert(
                    child, child_key, hidden or rules.matches(child_key)
                )
        elif isinstance(source, list) and isinstance(copy, list):
            copy.extend(convert(child, key, hidden) for child in source)
        else:
            raise MaskError(f"cannot copy {type(source).__name__}")
    return MaskResult(result, count)


def _reject_tuple(value: object) -> None:
    """A YAML ``!!pairs`` value loads as tuples, which have no keys to match."""
    if isinstance(value, tuple):
        raise MaskError("cannot mask a YAML !!pairs or tuple value")
