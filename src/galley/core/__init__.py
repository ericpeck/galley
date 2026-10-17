"""Pure Python JSON and YAML helpers. This package does not import Qt."""

from galley.core.errors import MAX_DEPTH, InputError
from galley.core.format import format_json, format_yaml

__all__ = ["MAX_DEPTH", "InputError", "format_json", "format_yaml"]
