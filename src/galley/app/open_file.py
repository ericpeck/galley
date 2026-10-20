"""Load one UTF-8 file into the Input editor.

The file dialog and message boxes live on the window. This module reads
bytes, chooses JSON or YAML, and remembers the last folder.
"""

from pathlib import Path

from galley.app.problems_settings import problems_settings

LAST_DIR_KEY = "open/lastDir"
OPEN_FILE_FILTER = "JSON and YAML (*.json *.yaml *.yml);;All files (*)"
REPLACE_INPUT_TEXT = "Replace the current input?"
REPLACE_INPUT_DETAIL = "Your current input will be replaced with the file's contents."
OPEN_ERROR_UTF8 = "This file isn't UTF-8 text."
OPEN_ERROR_UNREADABLE = "Galley doesn't have permission to read this file."

_JSON_SUFFIXES = frozenset({".json"})
_YAML_SUFFIXES = frozenset({".yaml", ".yml"})


class OpenFileError(Exception):
    """The file was not loaded. Input stays as it was.

    ``detail`` is the informative text under ``Couldn't open <name>.``
    """

    def __init__(self, file_name: str, detail: str) -> None:
        self.file_name = file_name
        self.detail = detail
        super().__init__(f"Couldn't open {file_name}.")


def input_window_title(file_name: str) -> str:
    """Window title for a file loaded into Input."""
    return f"{file_name} — Galley"


def input_needs_replace(current: str, opened: str | None) -> bool:
    """Return True when opening would discard unsaved Input text.

    Empty Input, and Input that still matches the file last loaded, open
    without asking.
    """
    if current == "":
        return False
    return current != opened


def read_utf8_file(path: Path) -> str:
    """Return ``path`` decoded as strict UTF-8.

    Newlines are normalized to line feeds. A missing or unreadable file,
    and any byte sequence that is not UTF-8, raises :class:`OpenFileError`.
    Callers leave Input unchanged.
    """
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise OpenFileError(path.name, OPEN_ERROR_UNREADABLE) from exc
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OpenFileError(path.name, OPEN_ERROR_UTF8) from exc
    return text.replace("\r\n", "\n").replace("\r", "\n")


def format_name_for_file(path: Path, text: str) -> str:
    """Return ``JSON`` or ``YAML`` for ``path``.

    ``.json``, ``.yaml``, and ``.yml`` win over the text. Any other suffix
    is JSON when the text starts with an object or array, and YAML otherwise.
    """
    suffix = path.suffix.casefold()
    if suffix in _JSON_SUFFIXES:
        return "JSON"
    if suffix in _YAML_SUFFIXES:
        return "YAML"
    return _detect_text_format(text)


def load_last_open_dir() -> str:
    """Return the last folder used by Open, or the home folder."""
    value = problems_settings().value(LAST_DIR_KEY, "")
    if isinstance(value, str) and value:
        directory = Path(value)
        if directory.is_dir():
            return str(directory)
    return str(Path.home())


def save_last_open_dir(directory: Path) -> None:
    """Remember ``directory`` as the folder Open starts in next time."""
    if not directory.is_dir():
        return
    settings = problems_settings()
    settings.setValue(LAST_DIR_KEY, str(directory))
    settings.sync()


def _detect_text_format(text: str) -> str:
    stripped = text.lstrip("\ufeff \t\r\n")
    if stripped.startswith(("{", "[")):
        return "JSON"
    return "YAML"
