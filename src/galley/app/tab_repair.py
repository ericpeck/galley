"""Replace leading YAML tabs with spaces as one editor undo step."""

from PySide6.QtGui import QTextCursor

from galley.app.editor import CodeEditor

SPACES_PER_TAB = 2


def replace_leading_tabs(
    editor: CodeEditor, spaces_per_tab: int = SPACES_PER_TAB
) -> None:
    """Turn indent tabs into spaces. One undo restores every line."""
    replacement = " " * spaces_per_tab
    cursor = editor.textCursor()
    cursor.beginEditBlock()
    block = editor.document().firstBlock()
    while block.isValid():
        original = block.text()
        updated = _replace_indent_tabs(original, replacement)
        following = block.next()
        if updated != original:
            cursor.setPosition(block.position())
            cursor.setPosition(
                block.position() + len(original),
                QTextCursor.MoveMode.KeepAnchor,
            )
            cursor.insertText(updated)
        block = following
    cursor.endEditBlock()


def _replace_indent_tabs(line: str, replacement: str) -> str:
    indent = 0
    while indent < len(line) and line[indent] in " \t":
        indent += 1
    prefix = line[:indent].replace("\t", replacement)
    return prefix + line[indent:]
