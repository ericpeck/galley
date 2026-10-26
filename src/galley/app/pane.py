"""One editor pane: tab strip, editor, footer, empty state."""

from dataclasses import dataclass
from enum import Enum, IntEnum
from pathlib import Path
from typing import Never

from PySide6.QtCore import QEvent, QObject, QSize, Qt, Signal
from PySide6.QtGui import QFont, QKeySequence
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
from galley.app.open_file import InputDocument
from galley.app.style import (
    link_button_style,
    muted_text_style,
    pane_style,
    primary_button_style,
    secondary_button_style,
)
from galley.core.formats import FORMAT_LABELS


class PaneKind(Enum):
    """Which empty state and footer links a pane shows."""

    SOURCE = "source"
    RESULT = "result"


@dataclass(frozen=True)
class PaneSpec:
    """Everything that tells one pane apart from another.

    ``name`` prefixes the object names (``inputPane``, ``inputEditor``,
    ``inputFormat``, …), so tests and features can find the pane's parts.
    """

    name: str
    title: str
    format_label: str
    kind: PaneKind


class StripShed(IntEnum):
    """How much a narrow tab strip has given up, in the order it gives way.

    Each step keeps the earlier ones: the format caption hides first, then
    Pop out drops its text, then the tab title is shortened with "…".
    Tooltips and accessible names keep the full words at every step.
    """

    NONE = 0
    CAPTION = 1
    POP_OUT_TEXT = 2
    TITLE = 3


class PaneRole:
    """The two panes the main window shows today."""

    INPUT = PaneSpec("input", "Input 1", "Input as", PaneKind.SOURCE)
    OUTPUT = PaneSpec("output", "Output 1", "Output as", PaneKind.RESULT)


class PaneWidget(QWidget):
    """Tab strip, format picker, editor, and footer for one side."""

    format_changed = Signal(str)

    def __init__(self, spec: PaneSpec, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.spec = spec
        self.document: InputDocument | None = None
        self.source_path: Path | None = None
        self.setObjectName(f"{spec.name}Pane")
        self.setMinimumWidth(240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # A QWidget subclass skips stylesheet borders unless this is set.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(pane_style(self.objectName()))
        self.setAutoFillBackground(True)

        self.editor = CodeEditor()
        self.editor.setObjectName(f"{spec.name}Editor")
        self.editor.setAccessibleName(f"{spec.title} editor")

        self.tab_strip = QWidget()
        self.tab_strip.setObjectName(f"{spec.name}TabStrip")
        self._build_tab_strip()

        self.empty_overlay = self._build_empty_state()
        self.editor.attach_overlay(self.empty_overlay)

        self.count_label = QLabel("Empty" if spec.kind is PaneKind.SOURCE else "")
        self.count_label.setObjectName(f"{spec.name}Count")
        self.count_label.setStyleSheet(muted_text_style())

        footer = self._build_footer()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        layout.addWidget(self.tab_strip)
        layout.addWidget(self.editor, 1)
        layout.addWidget(footer)

    @property
    def title(self) -> str:
        return self.spec.title

    def sizeHint(self) -> QSize:
        return QSize(480, 520)

    def format_name(self) -> str:
        return self.format_combo.currentText()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.tab_strip and event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.FontChange,
            QEvent.Type.StyleChange,
        ):
            self.fit_tab_strip()
        return super().eventFilter(watched, event)

    def full_strip_width(self) -> int:
        """Pane width at which the tab strip sheds nothing."""
        return self._pane_width(self._strip_width(StripShed.NONE))

    def min_strip_width(self) -> int:
        """Narrowest pane width at which the fully shed strip, title "…", fits."""
        return self._pane_width(self._strip_width(StripShed.TITLE, "…"))

    def _pane_width(self, strip_width: int) -> int:
        margins = self.layout().contentsMargins()
        return strip_width + margins.left() + margins.right()

    def fit_tab_strip(self, width: int | None = None) -> StripShed:
        """Shed strip parts until the strip fits ``width`` (default: its own).

        Nothing here knows about the window, so any pane, including the
        two equal Compare panes, fits the same way.
        """
        available = self.tab_strip.width() if width is None else width
        shed = StripShed.NONE
        while shed < StripShed.TITLE and self._strip_width(shed) > available:
            shed = StripShed(shed + 1)
        self._apply_strip_shed(shed, available)
        return shed

    def strip_shed(self) -> StripShed:
        return self._strip_shed

    def _strip_width(self, shed: StripShed, title: str | None = None) -> int:
        """Strip width with ``shed`` applied and ``title`` (default: the full one)."""
        title_extra = self._tab_title.sizeHint().width() - _text_width(
            self._tab_title, self._tab_title.text()
        )
        parts = [
            _text_width(self._tab_title, title or self.spec.title) + title_extra,
            _hint_width(self.close_tab_button),
            _hint_width(self.new_tab_button),
            _hint_width(self.format_combo),
            self.pop_out_button.icon_width()
            if shed >= StripShed.POP_OUT_TEXT
            else self.pop_out_button.text_width(),
        ]
        if shed < StripShed.CAPTION:
            parts.append(_hint_width(self._format_caption))
        row = self.tab_strip.layout()
        margins = row.contentsMargins()
        spacing = row.spacing() * (len(parts) - 1)
        return margins.left() + margins.right() + spacing + sum(parts)

    def _apply_strip_shed(self, shed: StripShed, available: int) -> None:
        self._strip_shed = shed
        caption_shown = shed < StripShed.CAPTION
        self._format_caption.setVisible(caption_shown)
        self.format_combo.setToolTip("" if caption_shown else self.spec.format_label)
        self.pop_out_button.set_icon_only(shed >= StripShed.POP_OUT_TEXT)
        title = self.spec.title
        if shed >= StripShed.TITLE:
            spare = available - self._strip_width(shed)
            room = _text_width(self._tab_title, title) + spare
            title = self._elided_title(room)
        self._tab_title.setText(title)
        self._tab_title.setToolTip("" if title == self.spec.title else self.spec.title)
        # The strip's layout already placed its parts for this resize, so
        # place them again now rather than one event late.
        self.tab_strip.layout().activate()

    def _elided_title(self, room: int) -> str:
        """The longest "…" form of the title whose label fits ``room``."""
        metrics = self._tab_title.fontMetrics()
        title = self.spec.title
        while room > 0:
            elided = metrics.elidedText(title, Qt.TextElideMode.ElideRight, room)
            if elided and _text_width(self._tab_title, elided) <= room:
                return elided
            room -= 1
        return "…"

    def set_empty_visible(self, visible: bool) -> None:
        self.empty_overlay.setVisible(visible)
        if visible:
            self.editor.place_overlay()

    def set_keyboard_focus(self, focused: bool) -> None:
        self.setProperty("paneFocused", focused)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_document(self, document: InputDocument | None) -> None:
        """Record the file now in this pane, or None for typed or pasted text."""
        self.document = document
        self.set_source(None if document is None else document.path)

    def set_source(self, path: Path | None) -> None:
        """Remember where this pane's text came from.

        A later tab title and tooltip read it. Nothing on screen shows it yet.
        """
        self.source_path = path

    def _build_tab_strip(self) -> None:
        tab = QLabel(self.spec.title)
        tab.setObjectName(f"{self.spec.name}TabTitle")
        tab.setAccessibleName(self.spec.title)
        self._tab_title = tab
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

        format_caption = QLabel(self.spec.format_label)
        format_caption.setObjectName(f"{self.spec.name}FormatCaption")
        format_caption.setStyleSheet(muted_text_style())
        self._format_caption = format_caption
        self.format_combo = self._format_combo(self.spec.format_label)
        format_caption.setBuddy(self.format_combo)

        self.pop_out_button = PopOutButton()
        self.pop_out_button.setObjectName(f"{self.spec.name}PopOut")

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
        self._strip_shed = StripShed.NONE
        self.tab_strip.installEventFilter(self)

    def _format_combo(self, accessible_name: str) -> QComboBox:
        self.format_combo = FormatComboBox()
        self.format_combo.setObjectName(f"{self.spec.name}Format")
        self.format_combo.setAccessibleName(accessible_name)
        self.format_combo.addItems(FORMAT_LABELS)
        self.format_combo.currentTextChanged.connect(self.format_changed.emit)
        return self.format_combo

    def _build_empty_state(self) -> QWidget:
        match self.spec.kind:
            case PaneKind.SOURCE:
                return self._source_empty_state()
            case PaneKind.RESULT:
                return self._result_empty_state()
            case _:
                _never: Never = self.spec.kind
                raise AssertionError(_never)

    def _source_empty_state(self) -> QWidget:
        overlay = QWidget()
        overlay.setObjectName(f"empty{self.spec.name.capitalize()}")
        overlay.setStyleSheet("background: transparent;")
        heading = QLabel(_source_empty_heading())
        heading.setObjectName(f"{self.spec.name}EmptyTitle")
        heading.setWordWrap(True)
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _style_empty_heading(heading)
        hint = QLabel("⌨ ⌘V to paste · Ctrl+V on Windows")
        hint.setObjectName(f"{self.spec.name}EmptyHint")
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

    def _result_empty_state(self) -> QWidget:
        overlay = QWidget()
        overlay.setObjectName(f"empty{self.spec.name.capitalize()}")
        overlay.setStyleSheet("background: transparent;")
        overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        heading = QLabel("Formatted output will appear here.")
        heading.setObjectName(f"{self.spec.name}EmptyTitle")
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
        footer.setObjectName(f"{self.spec.name}Footer")
        row = QHBoxLayout(footer)
        row.setContentsMargins(8, 2, 8, 2)
        row.setSpacing(8)
        match self.spec.kind:
            case PaneKind.SOURCE:
                self._add_source_footer(row)
            case PaneKind.RESULT:
                self._add_result_footer(row)
            case _:
                _never: Never = self.spec.kind
                raise AssertionError(_never)
        return footer

    def _add_source_footer(self, row: QHBoxLayout) -> None:
        self.footer_paste_button = _link("Paste", "footerPasteButton")
        self.clear_button = _link("Clear", "clearButton")
        self.clear_button.setEnabled(False)
        self.options_button = None
        self.copy_button = None
        row.addWidget(self.footer_paste_button)
        row.addWidget(self.clear_button)
        row.addStretch(1)
        row.addWidget(self.count_label)

    def _add_result_footer(self, row: QHBoxLayout) -> None:
        self.footer_paste_button = None
        self.clear_button = None
        self.options_button = _link("Options", f"{self.spec.name}OptionsButton")
        self.copy_button = _link("Copy", f"{self.spec.name}CopyButton")
        self.copy_button.setEnabled(False)
        row.addWidget(self.options_button)
        row.addStretch(1)
        row.addWidget(self.count_label)
        row.addWidget(self.copy_button)


def _hint_width(widget: QWidget) -> int:
    """Width a box layout gives ``widget`` when there is room for it."""
    hint = widget.sizeHint().width()
    return max(widget.minimumWidth(), min(widget.maximumWidth(), hint))


def _text_width(label: QLabel, text: str) -> int:
    """Text width as QLabel sizes it. ``horizontalAdvance`` rounds differently."""
    return label.fontMetrics().size(0, text).width()


def _source_empty_heading() -> str:
    """Empty Input heading with this platform's Open shortcut."""
    shortcut = QKeySequence(QKeySequence.StandardKey.Open).toString(
        QKeySequence.SequenceFormat.NativeText
    )
    if not shortcut:
        return "Paste JSON or YAML, or drop a file here."
    return f"Paste JSON or YAML, or drop a file here. {shortcut} to open."


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
