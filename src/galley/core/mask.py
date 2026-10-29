"""Mask values under sensitive key names, and values that are an email or phone.

This module does not import Qt. ``mask_tree`` replaces every scalar under a
matched key with ``"•••"`` and keeps the keys and structure, so the output
has the same shape as the unmasked output. It also replaces any string
that is, as a whole, an email address or a phone number, wherever it is,
including inside arrays with no keys. Text that only contains one, like
``"email me at pat@example.com"``, is kept. ``copy_as_tree`` builds the
three Copy As trees from the same rules. Serializing a tree is
``format.dump_value``. ``mask_document`` is Format with masking: it hands
``mask_tree`` to ``format_document`` as its ``prepare`` step.
``copy_as_text`` does the same with ``copy_as_result``.

Key names are split into lowercase words by ``split_key`` (see there for
the rule). A term in ``MASK_TERMS`` matches when one word, or several
adjacent words written together, spell it, so ``workEmail``, ``phone2``
and ``credit_card`` match and ``emailer`` does not. The tree is walked
with explicit stacks and never recursively. Any failure raises
``MaskError``, so a job with masking on fails whole and never shows
unmasked output.

A YAML alias is the same Python object as its anchor. A shared mapping or
sequence is masked everywhere when any place it appears is masked, so a
masked anchor does not show through an alias and the dumper writes the same
anchors as before. A shared string is masked everywhere for the same reason.
Strings shorter than two characters are skipped there, because CPython
shares those between unrelated values.
"""

import functools
import random
import re
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from galley.core.errors import MAX_DEPTH
from galley.core.fake import fake_value
from galley.core.format import DEFAULT_INDENT, format_document
from galley.core.formats import format_named

MASK = "•••"

Kind = Literal["email", "phone", "other"]
CopyAsMode = Literal["original", "masked", "fake"]
Scope = Literal["word", "key"]

# The default terms, one row each: (category, term, scope). A "word" term
# matches anywhere in a key, as one or more whole words. A "key" term
# matches only the whole key. Spaces in a term are for reading only:
# "credit card" also matches ``creditcard``, ``credit_card`` and
# ``creditCard``. Phrases such as "email address" whose words are already
# terms are listed so the all-lowercase spelling (``emailaddress``) matches.
MASK_TERMS: tuple[tuple[str, str, Scope], ...] = (
    ("contact", "email", "word"),
    ("contact", "email address", "word"),
    ("contact", "mail", "word"),
    ("contact", "phone", "word"),
    ("contact", "phone number", "word"),
    ("contact", "telephone", "word"),
    ("contact", "tel", "word"),
    ("contact", "mobile", "word"),
    ("contact", "mobile number", "word"),
    ("contact", "cell", "word"),
    ("contact", "cellphone", "word"),
    ("contact", "fax", "word"),
    ("contact", "address", "word"),
    ("contact", "street", "word"),
    ("contact", "street address", "word"),
    ("contact", "postcode", "word"),
    ("contact", "postal code", "word"),
    ("contact", "zip", "word"),
    ("contact", "zip code", "word"),
    ("identity", "name", "key"),
    ("identity", "first name", "word"),
    ("identity", "middle name", "word"),
    ("identity", "last name", "word"),
    ("identity", "full name", "word"),
    ("identity", "given name", "word"),
    ("identity", "family name", "word"),
    ("identity", "surname", "word"),
    ("identity", "user name", "word"),
    ("identity", "dob", "word"),
    ("identity", "date of birth", "word"),
    ("identity", "birthdate", "word"),
    ("identity", "birthday", "word"),
    ("identity", "passport", "word"),
    ("identity", "license number", "word"),
    ("identity", "licence number", "word"),
    ("identity", "driver license", "word"),
    ("identity", "drivers license", "word"),
    ("identity", "driving licence", "word"),
    ("identity", "national id", "word"),
    ("identity", "tax id", "word"),
    ("identity", "ssn", "word"),
    ("identity", "social security number", "word"),
    ("financial", "credit card", "word"),
    ("financial", "card number", "word"),
    ("financial", "cvv", "word"),
    ("financial", "cvc", "word"),
    ("financial", "iban", "word"),
    ("financial", "account number", "word"),
    ("financial", "bank account", "word"),
    ("financial", "routing number", "word"),
    ("network", "ip", "word"),
    ("network", "ip address", "word"),
    ("network", "ipv4", "word"),
    ("network", "ipv6", "word"),
    ("network", "mac address", "word"),
    ("secret", "password", "word"),
    ("secret", "passwd", "word"),
    ("secret", "pwd", "word"),
    ("secret", "passphrase", "word"),
    ("secret", "secret", "word"),
    ("secret", "secret key", "word"),
    ("secret", "token", "word"),
    ("secret", "access token", "word"),
    ("secret", "refresh token", "word"),
    ("secret", "auth token", "word"),
    ("secret", "api key", "word"),
    ("secret", "access key", "word"),
    ("secret", "private key", "word"),
    ("secret", "auth", "word"),
    ("secret", "authorization", "word"),
    ("secret", "credential", "word"),
)

DEFAULT_MASK_KEYS = tuple(term for _, term, scope in MASK_TERMS if scope == "word")
DEFAULT_WHOLE_KEYS = tuple(term for _, term, scope in MASK_TERMS if scope == "key")

# Key words that say what kind of value a fake should look like.
_EMAIL_WORDS = frozenset({"email", "mail"})
_PHONE_WORDS = frozenset(
    {"phone", "telephone", "tel", "mobile", "cell", "cellphone", "fax"}
)
_PLURAL_ENDINGS = ("s", "es")

_EMAIL_RE = re.compile(
    r"[\w.!#$%&'*+/=?^`{|}~-]+@(?:[^\W_](?:[\w-]*[^\W_])?\.)+[^\W\d_]{2,}"
)
_EMAIL_MAX_LENGTH = 254
# An optional "+", an optional "(area)" with a code before it, then digit
# groups with one space, dot or dash between each. ``looks_like_phone``
# checks the groups.
_PHONE_RE = re.compile(
    r"(?P<plus>\+)?"
    r"(?:(?:[0-9]{1,4}[ .-]?)?(?P<area>\([0-9]{1,4}\))[ .-]?)?"
    r"[0-9]+(?:[ .-][0-9]+)*"
)
_PHONE_MAX_LENGTH = 32
_PHONE_DIGITS = (7, 15)
_DIGIT_RUN_RE = re.compile(r"[0-9]+")
_PHONE_SEPARATOR_RE = re.compile(r"[ .-]")
_DATE_GROUPS = ([4, 2, 2], [2, 2, 4])

# Shorter strings are shared by CPython, so identity says nothing about aliases.
_ALIAS_MIN_LENGTH = 2

_Matches = Callable[[object], bool]
_Replace = Callable[[object, object], object]


class MaskError(Exception):
    """Masking could not finish, so the job fails and shows no output."""


def split_key(name: str) -> tuple[str, ...]:
    """Split a key name into lowercase words.

    Any character that is not a letter or a digit separates words, so
    ``_``, ``-``, ``.`` and spaces do. A word also ends where a lowercase
    letter meets an uppercase one (camelCase), and where letters meet
    digits. In a run of capitals followed by lowercase letters, the last
    capital starts the next word, unless the lowercase part is a lone
    plural ``s``. For example:

    - ``workEmail`` → ``work``, ``email``
    - ``E_MAIL_ADDRESS`` → ``e``, ``mail``, ``address``
    - ``phone2`` → ``phone``, ``2``
    - ``ssn_last4`` → ``ssn``, ``last``, ``4``
    - ``IPAddress`` → ``ip``, ``address``
    - ``APIKey`` → ``api``, ``key``
    - ``userSSNs`` → ``user``, ``ssns``
    - ``emailer`` and ``filename`` stay one word each.
    """
    words: list[str] = []
    word = ""
    previous = ""
    for index, char in enumerate(name):
        kind = _char_class(char)
        if not kind:
            words.append(word)
            word, previous = "", ""
            continue
        if previous and kind != previous:
            if previous == "D" or kind == "D" or (previous, kind) == ("L", "U"):
                words.append(word)
                word = ""
            elif len(word) > 1 and not _plural_acronym(name, index):
                words.append(word[:-1])
                word = word[-1]
        word += char
        previous = kind
    words.append(word)
    return tuple(word.lower() for word in words if word)


def _char_class(char: str) -> str:
    """Return "D" for a digit, "U" for a capital, "L" for another letter."""
    if char.isdigit():
        return "D"
    if char.isalpha():
        return "U" if char.isupper() or char.istitle() else "L"
    return ""


def _plural_acronym(name: str, index: int) -> bool:
    """Say whether ``name[index]`` is the ``s`` of a plural like ``SSNs``."""
    if name[index] != "s":
        return False
    following = name[index + 1 : index + 2]
    return not following or _char_class(following) != "L"


def _is_term(text: str, terms: frozenset[str]) -> bool:
    """Say whether ``text`` is a term or a term with an ``s`` or ``es`` added."""
    if text in terms:
        return True
    return any(
        text.endswith(ending) and text[: -len(ending)] in terms
        for ending in _PLURAL_ENDINGS
    )


def _term_text(term: str) -> str:
    return "".join(split_key(term))


@dataclass(frozen=True)
class MaskRules:
    """The terms to mask. Picklable, so a format job can carry it.

    ``keys`` match as whole words anywhere in a key name. A term matches
    when one word, or several adjacent words joined, spell the term with
    its spaces removed, optionally followed by a plural ``s`` or ``es``.
    ``whole_keys`` match only when all the words of the key name, joined,
    spell the term in the same way.
    """

    keys: tuple[str, ...] = DEFAULT_MASK_KEYS
    whole_keys: tuple[str, ...] = DEFAULT_WHOLE_KEYS
    _words: frozenset[str] = field(init=False, repr=False, compare=False)
    _whole: frozenset[str] = field(init=False, repr=False, compare=False)
    _longest: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        keys = tuple(self.keys)
        whole_keys = tuple(self.whole_keys)
        words = frozenset(text for text in map(_term_text, keys) if text)
        whole = frozenset(text for text in map(_term_text, whole_keys) if text)
        longest = max(map(len, words), default=0) + max(map(len, _PLURAL_ENDINGS))
        object.__setattr__(self, "keys", keys)
        object.__setattr__(self, "whole_keys", whole_keys)
        object.__setattr__(self, "_words", words)
        object.__setattr__(self, "_whole", whole)
        object.__setattr__(self, "_longest", longest)

    def matches(self, key: object) -> bool:
        """Return True when ``key`` is a string naming a masked key."""
        if not isinstance(key, str):
            return False
        words = split_key(key)
        if _is_term("".join(words), self._whole):
            return True
        for start in range(len(words)):
            joined = ""
            for word in words[start:]:
                joined += word
                if len(joined) > self._longest:
                    break
                if _is_term(joined, self._words):
                    return True
        return False


@dataclass(frozen=True)
class MaskResult:
    value: object
    masked_count: int


@dataclass(frozen=True)
class MaskedDocument:
    """Masked output text and how many values ``mask_tree`` replaced."""

    text: str
    masked_count: int


def looks_like_email(text: str) -> bool:
    """Say whether ``text``, apart from spaces at either end, is an email.

    It needs one ``@``, no spaces, and a domain ending in a dot and a
    top-level domain of two or more letters: ``pat@example.com`` is one,
    and ``user@localhost``, ``lodash@4.17.21`` and
    ``Pat <pat@example.com>`` are not.
    """
    text = text.strip()
    return len(text) <= _EMAIL_MAX_LENGTH and _EMAIL_RE.fullmatch(text) is not None


def looks_like_phone(text: str) -> bool:
    """Say whether ``text``, apart from spaces at either end, is a phone number.

    It has only digits, one optional leading ``+``, one optional
    ``(area)`` code and single spaces, dots or dashes between digit groups,
    with 7 to 15 digits. A ``+`` or an ``(area)`` code marks a phone
    number. Without one, the groups must look like a phone number too:

    - At least one separator, so a plain number like ``5550142`` is kept.
    - Two groups only as ``555-0142`` (3 then 4 digits, not split by a
      dot), so ``1990-2020`` and ``1234567.89`` are kept.
    - With three or more groups, the first has 1 to 4 digits and each
      other group 2 to 4, so ISBNs like ``978-0-306-40615-7`` are kept.
    - Not a date (``2026-10-20``, ``20.10.2026``), not an IPv4 address
      (``192.168.10.20``) and not a number with thousands separators
      (``12 345 678``).

    Letters, ``$``, commas, colons, slashes and ``x`` extensions never
    match, so IDs, amounts, UUIDs and timestamps are kept.
    """
    text = text.strip()
    if len(text) > _PHONE_MAX_LENGTH:
        return False
    match = _PHONE_RE.fullmatch(text)
    if match is None:
        return False
    sizes = [len(group) for group in _DIGIT_RUN_RE.findall(text)]
    if not _PHONE_DIGITS[0] <= sum(sizes) <= _PHONE_DIGITS[1]:
        return False
    if match["plus"] or match["area"]:
        return True
    return _phone_groups(sizes, set(_PHONE_SEPARATOR_RE.findall(text)))


def _phone_groups(sizes: list[int], separators: set[str]) -> bool:
    if len(sizes) == 2:
        return sizes == [3, 4] and separators != {"."}
    if len(sizes) < 2 or sizes[0] > 4:
        return False
    if any(not 2 <= size <= 4 for size in sizes[1:]):
        return False
    if sizes in _DATE_GROUPS:
        return False
    if separators == {"."} and len(sizes) == 4 and max(sizes) <= 3:
        return False
    thousands = sizes[0] <= 3 and all(size == 3 for size in sizes[1:])
    return not (thousands and len(separators) == 1)


def looks_personal(value: object) -> bool:
    """Say whether ``value`` is a string that is an email or a phone number."""
    return isinstance(value, str) and (
        looks_like_email(value) or looks_like_phone(value)
    )


def detect_kind(key: object, value: object) -> Kind:
    """Say whether ``value`` reads as an email, a phone, or other text.

    A string that is an email or a phone number decides first. Otherwise
    a word of the nearest key name decides: ``email`` or ``mail`` makes an
    email, and ``phone``, ``tel``, ``mobile``, ``cell`` or ``fax`` (and
    their longer forms) a phone. The kind only shapes Random fake values.
    It never decides what is masked.
    """
    if isinstance(value, str):
        if looks_like_email(value):
            return "email"
        if looks_like_phone(value):
            return "phone"
    words = split_key(key) if isinstance(key, str) else ()
    if any(_is_term(word, _EMAIL_WORDS) for word in words):
        return "email"
    if any(_is_term(word, _PHONE_WORDS) for word in words):
        return "phone"
    return "other"


def mask_tree(value: object, rules: MaskRules | None = None) -> MaskResult:
    """Return a copy of ``value`` with matched values replaced by ``"•••"``.

    ``value`` is not changed. ``masked_count`` is the number of scalars
    replaced, including emails and phone numbers found by value. A mapping
    or sequence shared through a YAML alias is built, and counted, once.
    """
    return _rewrite(value, rules or MaskRules(), lambda _scalar, _key: MASK)


def mask_document(
    text: str | bytes,
    source: str,
    target: str,
    rules: MaskRules | None = None,
    *,
    sort_keys: bool = False,
    indent: str = DEFAULT_INDENT,
) -> MaskedDocument:
    """Format ``text`` from ``source`` into ``target`` with values masked.

    This is ``format_document`` with ``mask_tree`` as its ``prepare`` step,
    so the text passes the same checks and raises the same errors, and
    ``sort_keys`` and ``indent`` write the masked output the same way. A
    format that does not load to a value, such as XML, can't be masked and
    raises ``MaskError``, so it never shows unmasked.
    """
    return _rewrite_document(
        text,
        source,
        target,
        lambda value: mask_tree(value, rules),
        sort_keys,
        indent,
    )


def _rewrite_document(
    text: str | bytes,
    source: str,
    target: str,
    rewrite: Callable[[object], MaskResult],
    sort_keys: bool = False,
    indent: str = DEFAULT_INDENT,
) -> MaskedDocument:
    """Format ``text`` with ``rewrite`` as ``format_document``'s ``prepare`` step.

    ``mask_document`` and ``copy_as_text`` share it, so Format with masking
    and Copy As load, check and write a document the same way.
    """
    fmt = format_named(source)
    if not fmt.loads_to_value:
        raise MaskError(f"{fmt.label} can't be masked")
    counts: list[int] = []

    def prepare(value: object) -> object:
        result = rewrite(value)
        counts.append(result.masked_count)
        return result.value

    text_out = format_document(
        text, source, target, sort_keys=sort_keys, prepare=prepare, indent=indent
    )
    if not counts:
        raise MaskError("the document was not masked")
    # A YAML retry masks the document again. The last count is the one shown.
    return MaskedDocument(text_out, counts[-1])


def copy_as_tree(
    value: object,
    mode: CopyAsMode,
    rules: MaskRules | None = None,
    rng: random.Random | None = None,
) -> object:
    """Return the tree Copy As serializes for ``mode``.

    ``original`` is ``value`` itself. ``masked`` is the ``mask_tree``
    result, with ``"•••"`` like the view. ``fake`` passes each scalar
    ``mask_tree`` would replace through ``fake_value`` instead, emails and
    phone numbers found by value included.
    """
    return copy_as_result(value, mode, rules, rng).value


def copy_as_result(
    value: object,
    mode: CopyAsMode,
    rules: MaskRules | None = None,
    rng: random.Random | None = None,
) -> MaskResult:
    """Return ``copy_as_tree`` with the number of scalars it replaced.

    ``original`` replaces none, so its count is 0.
    """
    if mode == "original":
        return MaskResult(value, 0)
    if mode == "masked":
        return mask_tree(value, rules)
    if mode != "fake":
        raise ValueError(f"Unknown Copy As mode: {mode}")
    chooser = rng if rng is not None else random.SystemRandom()

    def replace(scalar: object, key: object) -> object:
        return fake_value(scalar, detect_kind(key, scalar), chooser)

    return _rewrite(value, rules or MaskRules(), replace)


@dataclass(frozen=True)
class CopyAsResult:
    """The text to put on the clipboard.

    ``count`` is the number of values hidden (Masked) or replaced (Random
    fake). It is 0 for Original.
    """

    mode: CopyAsMode
    text: str
    count: int


class CopyAsError(Exception):
    """Copy As built no text, so nothing may be copied.

    ``mode`` is the copy that failed. ``invalid`` is True when the text did
    not load as its format, and False when it loaded but masking or writing
    it failed. The message names only the mode, the format and the error
    type, never document text: a parse error quotes the line it failed on,
    which can hold a value Masked or Random fake must hide. The original
    error is ``__cause__``, for debugging only.
    """

    def __init__(self, mode: CopyAsMode, fmt: str, exc: Exception, *, invalid: bool):
        step = "did not load" if invalid else "could not be masked or written"
        super().__init__(
            f"Copy As {mode}: the {fmt} text {step} ({type(exc).__name__})"
        )
        self.mode: CopyAsMode = mode
        self.invalid = invalid


def copy_as_text(
    text: str,
    fmt: str,
    mode: CopyAsMode,
    rules: MaskRules | None = None,
    rng: random.Random | None = None,
) -> CopyAsResult:
    """Return the Copy As text for ``mode``. Every failure is a CopyAsError.

    ``text`` is the Output, or a selection of it, and ``fmt`` its format
    name (case-insensitive), for reading and writing. Original is ``text``
    as it is. Masked and Random fake format it from ``fmt`` to ``fmt`` the
    way ``mask_document`` does, with ``copy_as_result`` as the rewrite, so
    the copy keeps the structure and format, and passes the same checks.
    A format that does not load to a value, such as XML, fails. ``rng`` is
    for tests; without one Random fake uses ``random.SystemRandom``.
    """
    if mode == "original":
        return CopyAsResult(mode, text, 0)
    loaded = False

    def rewrite(value: object) -> MaskResult:
        nonlocal loaded
        loaded = True
        return copy_as_result(value, mode, rules, rng)

    try:
        copied = _rewrite_document(text, fmt, fmt, rewrite)
    except Exception as exc:
        invalid = not loaded and not isinstance(exc, MaskError)
        raise CopyAsError(mode, fmt, exc, invalid=invalid) from exc
    return CopyAsResult(mode, copied.text, copied.masked_count)


def _rewrite(value: object, rules: MaskRules, replace: _Replace) -> MaskResult:
    """Find what to hide, then build the copy. Every failure is a MaskError."""
    try:
        matches = functools.cache(rules.matches)
        roots = _matched_values(value, matches)
        hidden_nodes, hidden_strings = _hidden(roots)
        return _build(value, matches, replace, hidden_nodes, hidden_strings)
    except MaskError:
        raise
    except Exception as exc:
        raise MaskError(f"masking failed: {type(exc).__name__}: {exc}") from exc


def _matched_values(root: object, matches: _Matches) -> list[object]:
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
            matched.extend(child for key, child in node.items() if matches(key))
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
    matches: _Matches,
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
        if (
            hidden
            or (isinstance(node, str) and id(node) in hidden_strings)
            or looks_personal(node)
        ):
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
                    child, child_key, hidden or matches(child_key)
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
