"""Qt tests draw code in DejaVu Sans Mono on every platform (#145)."""

from PySide6.QtGui import QFontInfo

from galley.app.editor import CodeEditor
from galley.app.fonts import code_font


def test_qt_tests_draw_code_in_dejavu_sans_mono(qtbot, code_test_font: str) -> None:
    assert code_test_font == "DejaVu Sans Mono"
    assert QFontInfo(code_font()).family() == "DejaVu Sans Mono"
    editor = CodeEditor()
    qtbot.addWidget(editor)
    assert editor.fontInfo().family() == "DejaVu Sans Mono"
    assert editor.fontInfo().fixedPitch()
