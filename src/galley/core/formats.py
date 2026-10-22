"""The document formats Galley reads and writes, in picker order (#63).

One row per format. Format, Open, the pickers, and the size lookups all read
this table instead of their own list of names, so a later format is one new
row plus its parser. This module has no Qt and no message strings.
"""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class DocumentFormat:
    """One format Galley can format.

    ``name`` is the lowercase key ``format_document`` and the worker take.
    ``label`` is the picker item and the name Open reports. ``extensions``
    are lowercase suffixes, dot included, that Open maps to this format.
    ``sniff`` is True when the first meaningful line of a document looks
    like this format. ``loads_to_value`` is True when the text parses to a
    Python value, so it converts to and from every other such format.
    """

    name: str
    label: str
    extensions: tuple[str, ...]
    sniff: Callable[[str], bool]
    loads_to_value: bool


def _looks_like_json(line: str) -> bool:
    return line.startswith(("{", "["))


def _looks_like_yaml(line: str) -> bool:
    if line.startswith("---") or line.startswith("- "):
        return True
    key, colon, rest = line.partition(":")
    return bool(colon and key and (rest == "" or rest.startswith(" ")))


# ``.jsonc`` and ``.json5`` are not mapped, since the JSON parser rejects
# their comments and trailing commas.
JSON = DocumentFormat("json", "JSON", (".json", ".geojson"), _looks_like_json, True)
YAML = DocumentFormat("yaml", "YAML", (".yaml", ".yml"), _looks_like_yaml, True)

FORMATS: tuple[DocumentFormat, ...] = (JSON, YAML)
FORMAT_LABELS: tuple[str, ...] = tuple(fmt.label for fmt in FORMATS)


def format_named(name: str) -> DocumentFormat:
    """Return the format called ``name``, ignoring case and outer spaces.

    A label works as well as a name, since the two differ only in case.
    Any other name raises ``ValueError``.
    """
    kind = name.strip().lower()
    for fmt in FORMATS:
        if fmt.name == kind:
            return fmt
    raise ValueError(f"Unsupported format: {name}")


def format_for_extension(suffix: str) -> DocumentFormat | None:
    """Return the format Open maps ``suffix`` to, or None. Case is ignored."""
    suffix = suffix.casefold()
    for fmt in FORMATS:
        if suffix in fmt.extensions:
            return fmt
    return None


def sniff_format(text: str) -> DocumentFormat | None:
    """Return the format the first meaningful line of ``text`` looks like.

    Blank lines and ``#`` comment lines are skipped. Formats are tried in
    table order. None means that line settles nothing.
    """
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        for fmt in FORMATS:
            if fmt.sniff(line):
                return fmt
        return None
    return None


def targets_for(source: str) -> tuple[DocumentFormat, ...]:
    """Return the formats Output can show for a ``source`` document.

    A format that loads to a value can be written as any such format. Any
    other format is written only as itself.
    """
    fmt = format_named(source)
    if not fmt.loads_to_value:
        return (fmt,)
    return tuple(target for target in FORMATS if target.loads_to_value)
