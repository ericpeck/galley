"""Inline find bar over one editor (#52), with Keys only on Output (#62).

Left to right: the search field, the match count, Previous and Next, Match
case, Keys only (Output only), and close. Return finds the next match and
Shift+Return the previous one, both wrapping around. Esc closes the bar and
puts focus back in the editor with the current match selected.

Input's bar also has a replace row for Find and Replace: the replace field,
Replace and Replace All. Replace changes the current match and goes to the
next one. Replace All writes the whole new text in one edit, so one undo
restores it. Esc then selects the last replacement.

The search itself is :mod:`galley.core.find`. The bar keeps the editor's
text and its key mask until the document changes, so typing a query only
runs the search. A bar with the replace row searches the raw text, which
keeps no-break spaces, so its count is what Replace All replaces. A
document over its #42 busy size waits ``FIND_DELAY_MS`` after the last
keystroke before it searches.
"""

from bisect import bisect_left, bisect_right

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAccessible,
    QAccessibleAnnouncementEvent,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPaintEvent,
    QPen,
    QResizeEvent,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from galley.app.editor import CodeEditor
from galley.app.fonts import code_font
from galley.app.icons import toolbar_icon
from galley.app.limits import (
    HTML_BUSY_BYTES,
    JSON_BUSY_BYTES,
    XML_BUSY_BYTES,
    YAML_BUSY_BYTES,
)
from galley.app.style import muted_text_style
from galley.app.theme import error_color
from galley.core.find import (
    astral_offsets,
    find_starts,
    key_mask,
    replace_all,
    utf16_offsets,
)

FIND_DELAY_MS = 150
NO_RESULTS = "No results"
FIND_PLACEHOLDER = "Find"
PREVIOUS_MATCH = "Previous match"
NEXT_MATCH = "Next match"
CLOSE_FIND_BAR = "Close find bar"
MATCH_CASE = "Match case"
KEYS_ONLY = "Keys only"
KEYS_ONLY_TIP = "Match only object keys, not values"
# Keys only stays visible but greyed out for a format with no keys (#63).
KEYS_ONLY_NOT_APPLICABLE = "Keys only doesn't apply to {label}"
REPLACE_PLACEHOLDER = "Replace"
REPLACE_FIELD_NAME = "Replace with"
REPLACE = "Replace"
REPLACE_ALL = "Replace All"
# How long "Replaced N" shows after Replace All before the live count returns.
REPLACED_MS = 4000
# QTextDocument.toRawText() puts this between blocks.
_BLOCK_BREAK = "\u2029"
_FIELD_MIN_WIDTH = 120
_FIELD_MAX_WIDTH = 320
_ERROR_BORDER = 2


def count_text(current: int, total: int) -> str:
    """``3 of 12`` for the 0-based ``current`` match, or ``No results``."""
    if total == 0:
        return NO_RESULTS
    return f"{current + 1:,} of {total:,}"


def replaced_text(count: int) -> str:
    """The count label after Replace All: ``Replaced 12``."""
    return f"Replaced {count:,}"


def replaced_announcement(count: int) -> str:
    """What screen readers hear after Replace All: ``Replaced 12 matches.``"""
    noun = "match" if count == 1 else "matches"
    return f"Replaced {count:,} {noun}."


class FindField(QLineEdit):
    """The search field. Return asks for the next match, Shift+Return the previous.

    With no match it draws a 2 px error border; the bar also shows the words
    ``No results``, so the border is never the only cue.
    """

    next_requested = Signal()
    previous_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._no_results = False
        self.setPlaceholderText(FIND_PLACEHOLDER)
        self.setAccessibleName(FIND_PLACEHOLDER)
        self.setFont(code_font())
        self.setClearButtonEnabled(False)

    def no_results(self) -> bool:
        return self._no_results

    def set_no_results(self, no_results: bool) -> None:
        if no_results == self._no_results:
            return
        self._no_results = no_results
        self.setAccessibleDescription(NO_RESULTS if no_results else "")
        self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            modifiers = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
            if modifiers == Qt.KeyboardModifier.ShiftModifier:
                self.previous_requested.emit()
                event.accept()
                return
            if modifiers == Qt.KeyboardModifier.NoModifier:
                self.next_requested.emit()
                event.accept()
                return
        super().keyPressEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if not self._no_results:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(error_color(self.palette()), _ERROR_BORDER)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        half = _ERROR_BORDER / 2
        painter.drawRoundedRect(
            self.rect().toRectF().adjusted(half, half, -half, -half), 3, 3
        )
        painter.end()


class FindBar(QWidget):
    """Find in ``editor``. Hidden until :meth:`open`.

    ``keys_option`` adds the Keys only box, which limits matches to object
    keys in JSON or YAML. Only Output has it: Input may not parse.
    ``replace_option`` adds the replace row, which ``open(replace=True)``
    shows. Only Input has it.
    """

    # The count text after each change, as screen readers hear it.
    count_changed = Signal(str)
    closed = Signal()

    def __init__(
        self,
        editor: CodeEditor,
        name: str,
        *,
        keys_option: bool = False,
        replace_option: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._editor = editor
        self._open = False
        self._replace_option = replace_option
        self._replace_shown = False
        # A job is running, so nothing may change the text.
        self._replace_blocked = False
        # UTF-16 span of the last Replace or Replace All, for Esc to select.
        self._last_replacement: tuple[int, int] | None = None
        # The bar's own edit is in progress; it searches again itself.
        self._editing = False
        self._starts: list[int] = []
        self._ends: list[int] = []
        self._current = -1
        # Where a typed query looks for its first match: the caret when the
        # bar opened, then the start of the match Next or Previous chose.
        self._anchor = 0
        self._text: str | None = None
        self._astral: list[int] = []
        self._mask: tuple[str, str] | None = None
        self._move_on_search = True
        self._count = ""
        # The format Keys only doesn't apply to, or "" when it applies.
        self._keys_unavailable = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(FIND_DELAY_MS)
        self._timer.timeout.connect(self._search_pending)
        self._replaced_timer = QTimer(self)
        self._replaced_timer.setSingleShot(True)
        self._replaced_timer.setInterval(REPLACED_MS)
        self._replaced_timer.timeout.connect(self._end_replaced)
        self.setObjectName(f"{name}FindBar")
        self.setAccessibleName("Find bar")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._build(name, keys_option, replace_option)
        self.hide()
        editor.document().contentsChanged.connect(self._on_document_changed)

    def _build(self, name: str, keys_option: bool, replace_option: bool) -> None:
        self.field = FindField()
        self.field.setObjectName(f"{name}FindField")
        self.field.setMinimumWidth(_FIELD_MIN_WIDTH)
        self.field.setMaximumWidth(_FIELD_MAX_WIDTH)
        self.field.textChanged.connect(self._on_query_changed)
        self.field.next_requested.connect(self.find_next)
        self.field.previous_requested.connect(self.find_previous)

        self.count_label = QLabel("")
        self.count_label.setObjectName(f"{name}FindCount")
        self.count_label.setAccessibleDescription("Match count")

        self.previous_button = _icon_button(
            PREVIOUS_MATCH, f"{name}FindPrevious", Qt.KeyboardModifier.ShiftModifier
        )
        self.previous_button.clicked.connect(self.find_previous)
        self.next_button = _icon_button(
            NEXT_MATCH, f"{name}FindNext", Qt.KeyboardModifier.NoModifier
        )
        self.next_button.clicked.connect(self.find_next)

        self.match_case = QCheckBox(MATCH_CASE)
        self.match_case.setObjectName(f"{name}FindMatchCase")
        self.match_case.setAccessibleName(MATCH_CASE)
        self.match_case.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.match_case.toggled.connect(self._on_option_changed)

        self.keys_only: QCheckBox | None = None
        if keys_option:
            self.keys_only = QCheckBox(KEYS_ONLY)
            self.keys_only.setObjectName(f"{name}FindKeysOnly")
            self.keys_only.setAccessibleName(KEYS_ONLY)
            self.keys_only.setToolTip(KEYS_ONLY_TIP)
            self.keys_only.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self.keys_only.toggled.connect(self._on_option_changed)

        self.close_button = _icon_button(CLOSE_FIND_BAR, f"{name}FindClose", None)
        self.close_button.clicked.connect(self.close_bar)

        self.replace_field = QLineEdit()
        self.replace_field.setObjectName(f"{name}ReplaceField")
        self.replace_field.setPlaceholderText(REPLACE_PLACEHOLDER)
        self.replace_field.setAccessibleName(REPLACE_FIELD_NAME)
        self.replace_field.returnPressed.connect(self._replace_on_return)
        self.replace_field.setFont(code_font())
        # As wide as the find field above it, so the two line up.
        self.replace_field.setFixedWidth(_FIELD_MIN_WIDTH)
        self.field.installEventFilter(self)
        self.replace_button = _text_button(REPLACE, f"{name}Replace")
        self.replace_button.clicked.connect(self.replace_current)
        self.replace_all_button = _text_button(REPLACE_ALL, f"{name}ReplaceAll")
        self.replace_all_button.clicked.connect(self.replace_all)
        self._replace_row = QWidget(self)
        replace_row = QHBoxLayout(self._replace_row)
        replace_row.setContentsMargins(0, 0, 0, 0)
        replace_row.setSpacing(4)
        replace_row.addWidget(self.replace_field)
        replace_row.addWidget(self.replace_button)
        replace_row.addWidget(self.replace_all_button)
        replace_row.addStretch(1)
        self._replace_row.hide()

        # The options sit on the first row, or on a second row when the
        # pane is too narrow for one.
        self._options = QWidget()
        options = QHBoxLayout(self._options)
        options.setContentsMargins(0, 0, 0, 0)
        options.setSpacing(8)
        options.addWidget(self.match_case)
        if self.keys_only is not None:
            options.addWidget(self.keys_only)

        self._first_row = QHBoxLayout()
        self._first_row.setSpacing(4)
        self._first_row.addWidget(self.field, 1)
        self._first_row.addWidget(self.count_label)
        self._first_row.addWidget(self.previous_button)
        self._first_row.addWidget(self.next_button)
        self._first_row.addSpacing(4)
        self._first_row.addWidget(self._options)
        self._first_row.addStretch(1)
        self._first_row.addWidget(self.close_button)
        self._second_row = QHBoxLayout()
        self._second_row.setContentsMargins(0, 0, 0, 0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)
        layout.addLayout(self._first_row)
        if replace_option:
            layout.addWidget(self._replace_row)
        layout.addLayout(self._second_row)

        order = [
            self.field,
            self.replace_field,
            self.replace_button,
            self.replace_all_button,
            self.previous_button,
            self.next_button,
            self.match_case,
            *([self.keys_only] if self.keys_only is not None else []),
            self.close_button,
        ]
        for first, second in zip(order, order[1:], strict=False):
            QWidget.setTabOrder(first, second)
        self._show_count("")
        self._sync_replace_buttons()

    # Public API ---------------------------------------------------------

    def is_open(self) -> bool:
        return self._open

    def editor(self) -> CodeEditor:
        return self._editor

    def open(self, *, fill_from_selection: bool = True, replace: bool = False) -> None:
        """Show the bar and focus the field with all of its text selected.

        A one-line selection in the editor becomes the query. ``replace``
        shows the replace row, which stays until the bar closes. With the
        row, a find field that already had text puts focus in the replace
        field instead.
        """
        cursor = self._editor.textCursor()
        self._anchor = cursor.selectionStart()
        had_query = bool(self.field.text())
        selected = cursor.selectedText()
        if fill_from_selection and selected and " " not in selected:
            self.field.blockSignals(True)
            self.field.setText(selected)
            self.field.blockSignals(False)
        was_open = self._open
        self._open = True
        if replace and self._replace_option:
            self._replace_shown = True
            self._replace_row.show()
        self.show()
        self._sync_layout()
        if replace and self._replace_shown and had_query:
            self.replace_field.setFocus(Qt.FocusReason.ShortcutFocusReason)
            self.replace_field.selectAll()
        else:
            self.field.setFocus(Qt.FocusReason.ShortcutFocusReason)
            self.field.selectAll()
        if not was_open or fill_from_selection and selected:
            self.search()

    def close_bar(self) -> None:
        """Hide the bar and focus the editor with the current match selected."""
        if not self._open:
            return
        self._open = False
        self._timer.stop()
        self._replaced_timer.stop()
        match = self._last_replacement or self.current_match()
        self._last_replacement = None
        self._replace_shown = False
        self._replace_row.hide()
        self.hide()
        self._editor.clear_find_matches()
        if match is not None:
            self._select(*match)
        self._editor.setFocus(Qt.FocusReason.OtherFocusReason)
        self.closed.emit()

    def find_next(self) -> None:
        """Go to the first match after the caret, wrapping to the first."""
        self._step(forward=True)

    def find_previous(self) -> None:
        """Go to the last match before the caret, wrapping to the last."""
        self._step(forward=False)

    def replace_current(self) -> None:
        """Replace the current match, then go to the next one, wrapping.

        Each Replace is one undo step. The search runs again from the end
        of the replacement, so text it put in is not matched in place. Only
        a wrap can come back to it, when no other match is left before it.
        """
        if not self._may_replace():
            return
        self._catch_up()
        match = self.current_match()
        if match is None:
            return
        start, end = match
        replacement = self.replace_field.text()
        cursor = QTextCursor(self._editor.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        self._edit(cursor, replacement)
        span = (start, start + len(replacement.encode("utf-16-le")) // 2)
        self._last_replacement = span
        self._anchor = span[1]
        self._run_search(move=True)

    def replace_all(self) -> int:
        """Replace every match in one edit and return how many.

        The new text is built in one pass and goes in as one insert over
        the whole document, so one undo restores the original. The caret
        and scroll stay where they were. The label shows ``Replaced N``
        for :data:`REPLACED_MS`, then the live count.
        """
        if not self._may_replace():
            return 0
        self._timer.stop()
        editor = self._editor
        document = editor.document()
        text, _astral = self._source_text()
        replaced = replace_all(
            text,
            self.field.text(),
            self.replace_field.text(),
            match_case=self.match_case.isChecked(),
        )
        count = len(replaced.spans)
        if not count:
            return 0
        caret = editor.textCursor().position()
        scrolls = (editor.horizontalScrollBar(), editor.verticalScrollBar())
        values = [bar.value() for bar in scrolls]
        cursor = QTextCursor(document)
        cursor.select(QTextCursor.SelectionType.Document)
        self._edit(cursor, replaced.text)
        moved = QTextCursor(document)
        moved.setPosition(min(caret, document.characterCount() - 1))
        editor.setTextCursor(moved)
        for bar, value in zip(scrolls, values, strict=True):
            bar.setValue(value)
        self._last_replacement = replaced.spans[-1]
        self._anchor = moved.position()
        self._run_search(move=False, show_count=False)
        self._show_count(replaced_text(count), replaced_announcement(count))
        self._replaced_timer.start()
        return count

    def replace_row_shown(self) -> bool:
        return self._replace_shown

    def replaced_timer(self) -> QTimer:
        """Ends the ``Replaced N`` label."""
        return self._replaced_timer

    def set_replace_blocked(self, blocked: bool) -> None:
        """Grey out Replace and Replace All while a job runs."""
        if blocked == self._replace_blocked:
            return
        self._replace_blocked = blocked
        self._sync_replace_buttons()

    def search(self) -> None:
        """Search now and make the first match at or after the anchor current."""
        self._timer.stop()
        self._run_search(move=True)

    def match_count(self) -> int:
        return len(self._starts)

    def current_index(self) -> int:
        """0-based index of the current match, or -1."""
        return self._current

    def current_match(self) -> tuple[int, int] | None:
        """UTF-16 ``(start, end)`` of the current match."""
        if self._current < 0:
            return None
        return self._starts[self._current], self._ends[self._current]

    def count_label_text(self) -> str:
        return self._count

    def is_large(self) -> bool:
        """Whether the document is over its #42 busy size, so typing waits."""
        limits = {
            "YAML": YAML_BUSY_BYTES,
            "XML": XML_BUSY_BYTES,
            "HTML": HTML_BUSY_BYTES,
        }
        limit = limits.get(self._editor.syntax_mode(), JSON_BUSY_BYTES)
        return self._editor.document().characterCount() > limit

    def keys_only_active(self) -> bool:
        """Keys only is on and applies. A greyed-out box keeps its check."""
        box = self.keys_only
        return box is not None and box.isChecked() and not self._keys_unavailable

    def set_keys_only_unavailable(self, label: str) -> None:
        """Grey out Keys only for format ``label``, or offer it again with "".

        The box stays visible and keeps its check for when it applies again.
        Its tooltip and accessible description say why it is greyed out.
        """
        box = self.keys_only
        if box is None or label == self._keys_unavailable:
            return
        self._keys_unavailable = label
        reason = KEYS_ONLY_NOT_APPLICABLE.format(label=label) if label else ""
        box.setEnabled(not label)
        box.setToolTip(reason or KEYS_ONLY_TIP)
        box.setAccessibleDescription(reason)
        if box.isChecked() and self._open:
            self.search()

    # Events -------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape and event.modifiers() in (
            Qt.KeyboardModifier.NoModifier,
            Qt.KeyboardModifier.KeypadModifier,
        ):
            self.close_bar()
            event.accept()
            return
        super().keyPressEvent(event)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            for button in (self.previous_button, self.next_button, self.close_button):
                button.setIcon(toolbar_icon(button.accessibleName()))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.field and event.type() == QEvent.Type.Resize:
            self.replace_field.setFixedWidth(self.field.width())
        return super().eventFilter(watched, event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._sync_layout()

    # Internals ----------------------------------------------------------

    def _sync_layout(self) -> None:
        """Move the options to a second row when one row does not fit."""
        one_row = self._one_row_width()
        wrapped = self._second_row.indexOf(self._options) >= 0
        narrow = self.width() < one_row
        if narrow and not wrapped:
            self._first_row.removeWidget(self._options)
            self._second_row.addWidget(self._options)
            self._second_row.addStretch(1)
        elif not narrow and wrapped:
            while self._second_row.count():
                self._second_row.takeAt(0)
            self._first_row.insertWidget(5, self._options)

    def _one_row_width(self) -> int:
        margins = self.layout().contentsMargins()
        widths = [
            _FIELD_MIN_WIDTH,
            self.count_label.sizeHint().width(),
            self.previous_button.sizeHint().width(),
            self.next_button.sizeHint().width(),
            4,
            self._options.sizeHint().width(),
            self.close_button.sizeHint().width(),
        ]
        spacing = self._first_row.spacing() * (len(widths) - 1)
        return sum(widths) + spacing + margins.left() + margins.right()

    def _on_query_changed(self, _text: str) -> None:
        if not self._open:
            return
        self._last_replacement = None
        if self._replaced_timer.isActive():
            self._end_replaced()
        if self.is_large():
            self._move_on_search = True
            self._timer.start()
            return
        self.search()

    def _on_option_changed(self, _checked: bool) -> None:
        if self._open:
            self._last_replacement = None
            self.search()

    def _on_document_changed(self) -> None:
        self._text = None
        self._mask = None
        if not self._open or self._editing:
            return
        self._last_replacement = None
        # An edit searches again after a pause and leaves the caret alone.
        if not self._timer.isActive():
            self._move_on_search = False
        self._timer.start()

    def _search_pending(self) -> None:
        move = self._move_on_search
        self._move_on_search = True
        if not move:
            self._anchor = self._editor.textCursor().selectionStart()
        self._run_search(move=move)

    def _run_search(self, *, move: bool, show_count: bool = True) -> None:
        """Search and make the first match at or after the anchor current."""
        query = self.field.text()
        if not query:
            self._starts, self._ends = [], []
            self._current = -1
            self._editor.clear_find_matches()
            self.field.set_no_results(False)
            self._show_count("")
            self._sync_replace_buttons()
            return
        text, astral = self._source_text()
        source = self._key_mask(text) if self.keys_only_active() else text
        found = find_starts(source, query, match_case=self.match_case.isChecked())
        length = len(query)
        self._starts = utf16_offsets(found, astral)
        self._ends = utf16_offsets([start + length for start in found], astral)
        current = -1
        if self._starts:
            current = bisect_left(self._starts, self._anchor)
            if current == len(self._starts):
                current = 0
        self._set_current(current, move=move, show_count=show_count)

    def _step(self, *, forward: bool) -> None:
        if not self.field.text():
            self.open(fill_from_selection=False)
            return
        self._last_replacement = None
        if not self._open:
            self._open = True
            self.show()
            self._sync_layout()
        if self._timer.isActive() or self._text is None:
            self._timer.stop()
            self._move_on_search = True
            self._anchor = self._editor.textCursor().selectionStart()
            self._run_search(move=False)
        if not self._starts:
            return
        caret = self._editor.textCursor().selectionStart()
        if forward:
            index = bisect_right(self._starts, caret)
            if index == len(self._starts):
                index = 0
        else:
            index = bisect_left(self._starts, caret) - 1
            if index < 0:
                index = len(self._starts) - 1
        self._anchor = self._starts[index]
        self._set_current(index, move=True)

    def _set_current(self, index: int, *, move: bool, show_count: bool = True) -> None:
        self._current = index
        self._editor.set_find_matches(self._starts, self._ends, index)
        self.field.set_no_results(index < 0)
        if show_count:
            self._show_count(count_text(index, len(self._starts)))
        self._sync_replace_buttons()
        if index >= 0 and move:
            self._reveal(self._starts[index], self._ends[index])

    def _may_replace(self) -> bool:
        return (
            self._open
            and self._replace_shown
            and not self._replace_blocked
            and not self._editor.isReadOnly()
            and bool(self.field.text())
        )

    def _catch_up(self) -> None:
        """Run a search that is still waiting, from the caret."""
        if self._timer.isActive() or self._text is None:
            self._timer.stop()
            self._move_on_search = True
            self._anchor = self._editor.textCursor().selectionStart()
            self._run_search(move=False)

    def _edit(self, cursor: QTextCursor, text: str) -> None:
        """Put ``text`` over ``cursor``'s selection as one undo step."""
        self._editing = True
        try:
            cursor.beginEditBlock()
            cursor.insertText(text)
            cursor.endEditBlock()
        finally:
            self._editing = False

    def _replace_on_return(self) -> None:
        """Return in the replace field is Replace, while Replace is enabled."""
        if self.replace_button.isEnabled():
            self.replace_current()

    def _end_replaced(self) -> None:
        """Swap ``Replaced N`` for the live count."""
        self._replaced_timer.stop()
        live = count_text(self._current, len(self._starts))
        self._show_count(live if self.field.text() else "")

    def _sync_replace_buttons(self) -> None:
        """Replace and Replace All need a query, a match, and no job.

        When they grey out under the keyboard, focus stays in the bar, in
        the replace field.
        """
        enabled = (
            bool(self.field.text()) and bool(self._starts) and not self._replace_blocked
        )
        buttons = (self.replace_button, self.replace_all_button)
        focused = any(button.hasFocus() for button in buttons)
        for button in buttons:
            button.setEnabled(enabled)
        if focused and not enabled:
            self.replace_field.setFocus(Qt.FocusReason.OtherFocusReason)

    def _reveal(self, start: int, end: int) -> None:
        """Unfold what hides the match, then put the caret at its start."""
        editor = self._editor
        document = editor.document()
        editor.ensure_line_visible(document.findBlock(start).blockNumber() + 1)
        cursor = QTextCursor(document)
        cursor.setPosition(end)
        editor.setTextCursor(cursor)
        editor.ensureCursorVisible()
        cursor.setPosition(start)
        editor.setTextCursor(cursor)
        editor.ensureCursorVisible()

    def _select(self, start: int, end: int) -> None:
        editor = self._editor
        editor.ensure_line_visible(editor.document().findBlock(start).blockNumber() + 1)
        cursor = QTextCursor(editor.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(cursor)
        editor.ensureCursorVisible()

    def _source_text(self) -> tuple[str, list[int]]:
        """The text the search runs on, and its astral offsets.

        With the replace row it is the raw text, block breaks as newlines.
        toPlainText() would turn no-break spaces into spaces, so a query
        with a space would count matches Replace All never writes back.
        Both have the same length, so offsets don't move.
        """
        if self._text is None:
            if self._replace_option:
                raw = self._editor.document().toRawText()
                self._text = raw.replace(_BLOCK_BREAK, "\n")
            else:
                self._text = self._editor.toPlainText()
            self._astral = astral_offsets(self._text)
        return self._text, self._astral

    def _key_mask(self, text: str) -> str:
        syntax = self._editor.syntax_mode()
        if self._mask is None or self._mask[0] != syntax:
            self._mask = (syntax, key_mask(text, syntax))
        return self._mask[1]

    def _show_count(self, text: str, spoken: str | None = None) -> None:
        """Set the count. ``No results`` is muted; the bar announces changes.

        Screen readers hear ``spoken`` when it is given, else ``text``.
        """
        if text == self._count:
            return
        self._count = text
        self.count_label.setText(text)
        self.count_label.setStyleSheet(muted_text_style() if text == NO_RESULTS else "")
        heard = text if spoken is None else spoken
        if heard and QAccessible.isActive():
            event = QAccessibleAnnouncementEvent(self.count_label, heard)
            event.setPoliteness(QAccessible.AnnouncementPoliteness.Polite)
            QAccessible.updateAccessibility(event)
        self.count_changed.emit(heard)


def _text_button(text: str, object_name: str) -> QPushButton:
    """A text button with no icon. ``text`` is its accessible name too."""
    button = QPushButton(text)
    button.setObjectName(object_name)
    button.setAccessibleName(text)
    button.setAutoDefault(False)
    button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    return button


def _icon_button(
    name: str, object_name: str, return_modifier: Qt.KeyboardModifier | None
) -> QToolButton:
    """Icon button with ``name`` as its accessible name and tooltip.

    A button with a key in the field adds that key to the tooltip.
    """
    button = QToolButton()
    button.setObjectName(object_name)
    button.setIcon(toolbar_icon(name))
    button.setAccessibleName(name)
    tip = name
    if return_modifier is not None:
        key = QKeySequence(return_modifier | Qt.Key.Key_Return).toString(
            QKeySequence.SequenceFormat.NativeText
        )
        tip = f"{name} ({key})"
    elif name == CLOSE_FIND_BAR:
        tip = f"{name} (Esc)"
    button.setToolTip(tip)
    button.setAutoRaise(True)
    button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    return button
