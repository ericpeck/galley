"""One Input or Output pane: tab strip, editor, footer, empty state."""

from enum import Enum
from typing import Never

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from galley.app.editor import CodeEditor
from galley.app.fonts import ui_font
from galley.app.format_picker import FormatComboBox, PopOutButton
from galley.app.style import (
    link_button_style,
    muted_text_style,
    pane_style,
    primary_button_style,
    secondary_button_style,
)


class PaneRole(Enum):
    """Which side of the splitter a pane occupies."""

    INPUT = "input"
    OUTPUT = "output"


def _tab_and_format_label(role: PaneRole) -> tuple[str, str]:
    match role:
        case PaneRole.INPUT:
            return "Input 1", "Input as"
        case PaneRole.OUTPUT:
            return "Output 1", "Output as"
        case _:
            _never: Never = role
            raise AssertionError(_never)


class PaneWidget(QWidget):
    """Tab strip, format picker, editor, and footer for one side."""

    format_changed = Signal(str)

    def __init__(self, role: PaneRole, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.role = role
        title, format_label = _tab_and_format_label(role)
        self.setObjectName(f"{role.value}Pane")
        self.setMinimumWidth(240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(pane_style(self.objectName()))
        self.setAutoFillBackground(True)

        self.editor = CodeEditor()
        self.editor.setObjectName(f"{role.value}Editor")
        self.editor.setAccessibleName(f"{title} editor")

        self.tab_strip = QWidget()
        self.tab_strip.setObjectName(f"{role.value}TabStrip")
        self._build_tab_strip(title, format_label)

        self.empty_overlay = self._build_empty_state()
        self.editor.attach_overlay(self.empty_overlay)

        self.count_label = QLabel("Empty" if role is PaneRole.INPUT else "")
        self.count_label.setObjectName(f"{role.value}Count")
        self.count_label.setStyleSheet(muted_text_style())

        footer = self._build_footer()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        layout.addWidget(self.tab_strip)
        layout.addWidget(self.editor, 1)
        layout.addWidget(footer)

    def sizeHint(self) -> QSize:
        return QSize(480, 520)

    def format_name(self) -> str:
        return self.format_combo.currentText()

    def set_empty_visible(self, visible: bool) -> None:
        self.empty_overlay.setVisible(visible)
        if visible:
            self.editor.place_overlay()

    def set_keyboard_focus(self, focused: bool) -> None:
        self.setProperty("paneFocused", focused)
        self.style().unpolish(self)
        self.style().polish(self)

    def _build_tab_strip(self, title: str, format_label: str) -> None:
        tab = QLabel(title)
        tab.setObjectName(f"{self.role.value}TabTitle")
        tab.setStyleSheet("padding: 4px 8px;")
        tab_font = ui_font()
        tab_font.setBold(True)
        tab.setFont(tab_font)

        self.close_tab_button = QPushButton("×")
        self.close_tab_button.setAccessibleName("Close")
        self.close_tab_button.setToolTip("Close")
        self.close_tab_button.setFlat(True)
        self.close_tab_button.setFixedWidth(24)
        self.close_tab_button.setStyleSheet(muted_text_style())

        self.new_tab_button = QPushButton("+")
        self.new_tab_button.setAccessibleName("New tab")
        self.new_tab_button.setToolTip("New tab")
        self.new_tab_button.setFlat(True)
        self.new_tab_button.setFixedWidth(24)
        self.new_tab_button.setStyleSheet(muted_text_style())

        format_caption = QLabel(format_label)
        format_caption.setStyleSheet(muted_text_style())
        self.format_combo = self._format_combo(format_label)
        format_caption.setBuddy(self.format_combo)

        self.pop_out_button = PopOutButton()
        self.pop_out_button.setAccessibleName("Pop out into window")
        self.pop_out_button.setToolTip("Pop out into window")

        row = QHBoxLayout(self.tab_strip)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(4)
        row.addWidget(tab)
        row.addWidget(self.close_tab_button)
        row.addWidget(self.new_tab_button)
        row.addStretch(1)
        row.addWidget(format_caption)
        row.addWidget(self.format_combo)
        row.addWidget(self.pop_out_button)

    def _format_combo(self, accessible_name: str) -> QComboBox:
        self.format_combo = FormatComboBox()
        self.format_combo.setObjectName(f"{self.role.value}Format")
        self.format_combo.setAccessibleName(accessible_name)
        self.format_combo.addItems(["JSON", "YAML"])
        self.format_combo.currentTextChanged.connect(self.format_changed.emit)
        return self.format_combo

    def _build_empty_state(self) -> QWidget:
        match self.role:
            case PaneRole.INPUT:
                return self._input_empty_state()
            case PaneRole.OUTPUT:
                return self._output_empty_state()
            case _:
                _never: Never = self.role
                raise AssertionError(_never)

    def _input_empty_state(self) -> QWidget:
        overlay = QWidget()
        overlay.setObjectName("emptyInput")
        overlay.setStyleSheet("background: transparent;")
        heading = QLabel("Paste JSON or YAML, or drop a file here")
        heading.setObjectName("inputEmptyTitle")
        heading.setWordWrap(True)
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _style_empty_heading(heading)
        hint = QLabel("⌨ ⌘V to paste · Ctrl+V on Windows")
        hint.setObjectName("inputEmptyHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(muted_text_style())

        self.paste_button = QPushButton("Paste")
        self.paste_button.setObjectName("emptyPasteButton")
        self.paste_button.setStyleSheet(primary_button_style())
        self.open_button = QPushButton("Open file…")
        self.open_button.setObjectName("emptyOpenButton")
        self.open_button.setStyleSheet(secondary_button_style())

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        buttons.addWidget(self.paste_button)
        buttons.addWidget(self.open_button)
        buttons.addStretch(1)

        layout = QVBoxLayout(overlay)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(heading)
        layout.addWidget(hint)
        layout.addLayout(buttons)
        return overlay

    def _output_empty_state(self) -> QWidget:
        overlay = QWidget()
        overlay.setObjectName("emptyOutput")
        overlay.setStyleSheet("background: transparent;")
        overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        heading = QLabel("Formatted output will appear here.")
        heading.setObjectName("outputEmptyTitle")
        heading.setWordWrap(True)
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _style_empty_heading(heading)
        self.paste_button = None
        self.open_button = None
        layout = QVBoxLayout(overlay)
        layout.addWidget(heading)
        return overlay

    def _build_footer(self) -> QWidget:
        footer = QWidget()
        footer.setObjectName(f"{self.role.value}Footer")
        row = QHBoxLayout(footer)
        row.setContentsMargins(8, 2, 8, 2)
        row.setSpacing(8)
        match self.role:
            case PaneRole.INPUT:
                self._add_input_footer(row)
            case PaneRole.OUTPUT:
                self._add_output_footer(row)
            case _:
                _never: Never = self.role
                raise AssertionError(_never)
        return footer

    def _add_input_footer(self, row: QHBoxLayout) -> None:
        self.footer_paste_button = _link("Paste", "footerPasteButton")
        self.clear_button = _link("Clear", "clearButton")
        self.clear_button.setEnabled(False)
        self.options_button = None
        self.copy_button = None
        row.addWidget(self.footer_paste_button)
        row.addWidget(self.clear_button)
        row.addStretch(1)
        row.addWidget(self.count_label)

    def _add_output_footer(self, row: QHBoxLayout) -> None:
        self.footer_paste_button = None
        self.clear_button = None
        self.options_button = _link("Options", "outputOptionsButton")
        self.copy_button = _link("Copy", "outputCopyButton")
        self.copy_button.setEnabled(False)
        row.addWidget(self.options_button)
        row.addStretch(1)
        row.addWidget(self.count_label)
        row.addWidget(self.copy_button)


def _style_empty_heading(heading: QLabel) -> None:
    """Center a 15 px semibold heading and keep the wrap near 360 px."""
    font = ui_font()
    font.setPixelSize(15)
    font.setWeight(QFont.Weight.DemiBold)
    heading.setFont(font)
    heading.setMaximumWidth(360)


def _link(text: str, object_name: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName(object_name)
    button.setFlat(True)
    button.setStyleSheet(link_button_style())
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button
