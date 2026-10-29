"""The indent size picker: its choices and the indent each one writes.

One setting drives both the indent Format writes and the unit Indent,
Outdent and the Tab key use. YAML can't indent with tabs, so where the
format is YAML, Tab writes two spaces and the picker keeps showing Tab.
"""

INDENT_CHOICES = ("2 spaces", "4 spaces", "Tab")
DEFAULT_INDENT_CHOICE = "2 spaces"
TAB_CHOICE = "Tab"
# The picker's tooltip on Tab while Output is YAML (Chief's brief).
YAML_TAB_REASON = "YAML indents with spaces."
_UNITS = {"2 spaces": "  ", "4 spaces": "    ", TAB_CHOICE: "\t"}
_YAML = "yaml"


def indent_unit(choice: str, format_name: str) -> str:
    """The indent ``choice`` writes in ``format_name`` (case-insensitive).

    An unknown choice is the default. Tab in YAML is the default's two
    spaces.
    """
    unit = _UNITS.get(choice, _UNITS[DEFAULT_INDENT_CHOICE])
    if unit == "\t" and format_name.strip().lower() == _YAML:
        return _UNITS[DEFAULT_INDENT_CHOICE]
    return unit


def is_indent_choice(choice: object) -> bool:
    """True when ``choice`` is one of :data:`INDENT_CHOICES`."""
    return isinstance(choice, str) and choice in _UNITS
