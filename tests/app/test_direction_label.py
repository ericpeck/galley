"""The direction label shows the result on screen, not the pickers (#91 D4).

While Output is empty it follows the pickers. Once a Format or Minify
writes Output, it names that result's direction until Output changes
result or empties. A picker change after a result leaves it alone; the
Stale cue for that is D2.
"""

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QComboBox, QLabel

from galley.app.main import create_main_window

# Fake data only.
_JSON = '{"name": "Ada Example", "id": 7}'
_BROKEN = '{"name": "Ada Example",'
_PYTHON = "{'name': 'Ada Example', 'id': 7}"


def _show(qtbot):
    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def _action(window, text: str) -> QAction:
    matches = [item for item in window.findChildren(QAction) if item.text() == text]
    assert len(matches) == 1, text
    return matches[0]


def _direction(window) -> str:
    label = window.findChild(QLabel, "directionLabel")
    assert label is not None
    return label.text()


def _picker(window, name: str) -> QComboBox:
    combo = window.findChild(QComboBox, name)
    assert isinstance(combo, QComboBox), name
    return combo


def _run(qtbot, window, command: str) -> None:
    _action(window, command).trigger()
    qtbot.waitUntil(lambda: not window._formatting, timeout=15000)


def _format_to_yaml(qtbot, window) -> None:
    window._input_pane.editor.setPlainText(_JSON)
    _picker(window, "outputFormat").setCurrentText("YAML")
    _run(qtbot, window, "Format")
    assert window._output_pane.own_editor.toPlainText() != ""
    assert _direction(window) == "JSON → YAML"


def test_a_picker_change_after_a_result_keeps_the_result_direction(qtbot) -> None:
    window = _show(qtbot)
    _format_to_yaml(qtbot, window)
    _picker(window, "outputFormat").setCurrentText("JSON")
    # Output still holds YAML.
    assert _direction(window) == "JSON → YAML"


def test_an_empty_output_follows_the_pickers_again(qtbot) -> None:
    window = _show(qtbot)
    _format_to_yaml(qtbot, window)
    _picker(window, "outputFormat").setCurrentText("JSON")
    window._output_pane.own_editor.clear()
    assert _direction(window) == "JSON → JSON"
    _picker(window, "outputFormat").setCurrentText("YAML")
    assert _direction(window) == "JSON → YAML"


def test_a_new_format_names_its_own_direction(qtbot) -> None:
    window = _show(qtbot)
    _format_to_yaml(qtbot, window)
    _picker(window, "outputFormat").setCurrentText("JSON")
    _run(qtbot, window, "Format")
    assert window._output_pane.own_editor.toPlainText().startswith("{")
    assert _direction(window) == "JSON → JSON"


def test_minify_names_json_as_its_result(qtbot) -> None:
    window = _show(qtbot)
    _format_to_yaml(qtbot, window)
    _run(qtbot, window, "Minify")
    assert window._output_pane.own_editor.toPlainText().startswith("{")
    assert _direction(window) == "JSON → JSON"


def test_a_failed_format_keeps_the_result_on_screen(qtbot) -> None:
    window = _show(qtbot)
    _format_to_yaml(qtbot, window)
    yaml_output = window._output_pane.own_editor.toPlainText()
    window._input_pane.editor.setPlainText(_BROKEN)
    _picker(window, "outputFormat").setCurrentText("JSON")
    _run(qtbot, window, "Format")
    assert window._output_pane.own_editor.toPlainText() == yaml_output
    assert _direction(window) == "JSON → YAML"


def test_validate_leaves_output_and_its_direction(qtbot) -> None:
    window = _show(qtbot)
    _format_to_yaml(qtbot, window)
    _picker(window, "outputFormat").setCurrentText("JSON")
    _run(qtbot, window, "Validate")
    assert _direction(window) == "JSON → YAML"


def test_convert_from_python_shows_the_pickers_until_bill_decides(qtbot) -> None:
    """Asked on #91: Convert from Python, Escape and Unescape keep the
    picker label for now, so a Format's direction doesn't outlive its text.
    """
    window = _show(qtbot)
    _format_to_yaml(qtbot, window)
    _picker(window, "outputFormat").setCurrentText("JSON")
    window._input_pane.editor.setPlainText(_PYTHON)
    _action(window, "Convert from Python").trigger()
    assert window._output_pane.own_editor.toPlainText().startswith("{")
    assert _direction(window) == "JSON → JSON"
