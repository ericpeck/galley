"""Inline find bar over one editor (#52), with Keys only on Output (#62).

Left to right: the search field, the match count, Previous and Next, Match
case, Keys only (Output only), and close. Return finds the next match and
Shift+Return the previous one, both wrapping around. Esc closes the bar and
puts focus back in the editor with the current match selected.

Input's bar also has a replace row for Find and Replace: the replace field,
Replace and Replace All. Replace changes the current match and goes to the
next one. Replace All writes the whole new text in one edit, so one undo
restores it. Esc then selects the last replacement.

Regex and Whole word sit right of the search field on both bars, and both
bars share their state. With either on, the search runs in the find worker
(:mod:`galley.app.regex_session`), which may take up to its limit; the bar
keeps showing the last result until the new one comes back. An invalid
pattern or a search past the limit clears the matches, draws the error
border, and says so in the count label. A search past the limit is not
tried again until the query or a toggle changes.

The plain search is :mod:`galley.core.find`. The bar keeps the editor's
text and its key mask until the document changes, so typing a query only
runs the search. A bar with the replace row searches the raw text, which
keeps no-break spaces, so its count is what Replace All replaces. A
document over its #42 busy size waits ``FIND_DELAY_MS`` after the last
keystroke before it searches.
"""

from bisect import bisect_left, bisect_right
from collections.abc import Callable
from dataclasses import dataclass
from itertools import count as counter

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
    QShortcut,
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
from galley.app.regex_session import RegexSession
from galley.app.shortcuts import TOGGLE_REGEX, TOGGLE_WHOLE_WORD
from galley.app.style import find_toggle_style, muted_text_style
from galley.app.theme import error_color
from galley.core.find import (
    astral_offsets,
    find_starts,
    key_mask,
    key_spans,
    replace_all,
    utf16_offsets,
)
from galley.core.regex_find import (
    FindJob,
    Found,
    PatternFailed,
    ReplaceAllJob,
    ReplacedAll,
    ReplacedOne,
    ReplacementFailed,
    ReplaceOneJob,
    missing_group,
    parse_replacement,
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
REGEX = "Regex"
WHOLE_WORD = "Whole word"
REGEX_NAME = "Use regular expression"
WHOLE_WORD_NAME = "Match whole word"
INVALID_PATTERN = "Invalid pattern"
SEARCH_TIMED_OUT = "Search timed out"
SEARCH_TIMED_OUT_SPOKEN = "Search timed out. Try a simpler pattern."
INVALID_REPLACEMENT = "Invalid replacement"
MISSING_GROUP = "The pattern has no group {group}."
# Why the last Regex or Whole word search found nothing to show.
_INVALID = "invalid"
_TIMED_OUT = "timed out"
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


def pattern_error_text(message: str, column: int | None) -> str:
    """An invalid pattern's tooltip: ``Col 3: Missing ), unterminated subpattern``.

    The column is placed as a JSON error's is in the Problems pane.
    """
    if column is None:
        return message
    return f"Col {column}: {message}"


def replaced_announcement(count: int) -> str:
    """What screen readers hear after Replace All: ``Replaced 12 matches.``"""
    noun = "match" if count == 1 else "matches"
    return f"Replaced {count:,} {noun}."


class ErrorLineEdit(QLineEdit):
    """A field that can draw a 2 px error border.

    The border comes with a reason, which is the accessible description.
    The bar also says it in words, so the border is never the only cue.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._error = ""

    def error(self) -> str:
        """Why the border shows, or "" when it doesn't."""
        return self._error

    def set_error(self, reason: str) -> None:
        if reason == self._error:
            return
        self._error = reason
        self.setAccessibleDescription(reason)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if not self._error:
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


class FindField(ErrorLineEdit):
    """The search field. Return asks for the next match, Shift+Return the previous.

    With no match it draws the error border; the bar also shows the words
    ``No results``.
    """

    next_requested = Signal()
    previous_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText(FIND_PLACEHOLDER)
        self.setAccessibleName(FIND_PLACEHOLDER)
        self.setFont(code_font())
        self.setClearButtonEnabled(False)

    def no_results(self) -> bool:
        return self.error() == NO_RESULTS

    def set_no_results(self, no_results: bool) -> None:
        self.set_error(NO_RESULTS if no_results else "")

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


# Each read of a bar's text gets a new version, unique across both bars,
# so the find worker knows when it already holds the text.
_TEXT_VERSIONS = counter(1)


@dataclass(frozen=True)
class _Request:
    """A job on the find worker that the bar is waiting for.

    ``span`` is the UTF-16 match a Replace changes. ``move`` and
    ``show_count`` are the search's own arguments.
    """

    job_id: int
    version: int
    kind: str
    move: bool = True
    show_count: bool = True
    span: tuple[int, int] = (0, 0)


class FindBar(QWidget):
    """Find in ``editor``. Hidden until :meth:`open`.

    ``keys_option`` adds the Keys only box, which limits matches to object
    keys in JSON or YAML. Only Output has it: Input may not parse.
    ``replace_option`` adds the replace row, which ``open(replace=True)``
    shows. Only Input has it. ``regex_session`` runs Regex and Whole word;
    the window gives both bars the same one. Without one the bar starts its
    own on the first such search.
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
        regex_session: RegexSession | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._editor = editor
        self._session: RegexSession | None = None
        # The worker job the bar waits for, and what runs once it answers.
        self._request: _Request | None = None
        self._after_search: Callable[[], None] | None = None
        self._version = 0
        # _INVALID or _TIMED_OUT after a worker search that showed nothing.
        self._problem = ""
        # A toggle changed while the bar was closed.
        self._options_changed = False
        # The last worker search: its pattern's group count, and its spans
        # in code points, for Replace.
        self._groups = 0
        self._points: tuple[list[int], list[int]] = ([], [])
        self._keys: tuple[str, list[int], list[int]] | None = None
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
        self._heard = ""
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
        if regex_session is not None:
            self.set_regex_session(regex_session)
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

        self.regex_button = _toggle_button(
            REGEX, REGEX_NAME, TOGGLE_REGEX, f"{name}FindRegex"
        )
        self.whole_word_button = _toggle_button(
            WHOLE_WORD, WHOLE_WORD_NAME, TOGGLE_WHOLE_WORD, f"{name}FindWholeWord"
        )
        self._toggles = QWidget()
        toggles = QHBoxLayout(self._toggles)
        toggles.setContentsMargins(0, 0, 0, 0)
        toggles.setSpacing(2)
        self.toggle_shortcuts: list[QShortcut] = []
        for button, key in (
            (self.regex_button, TOGGLE_REGEX),
            (self.whole_word_button, TOGGLE_WHOLE_WORD),
        ):
            button.toggled.connect(self._on_option_changed)
            toggles.addWidget(button)
            # Only while this bar, or a control in it, has focus.
            shortcut = QShortcut(key, self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(button.toggle)
            self.toggle_shortcuts.append(shortcut)

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

        self.replace_field = ErrorLineEdit()
        self.replace_field.setObjectName(f"{name}ReplaceField")
        self.replace_field.setPlaceholderText(REPLACE_PLACEHOLDER)
        self.replace_field.setAccessibleName(REPLACE_FIELD_NAME)
        self.replace_field.returnPressed.connect(self._replace_on_return)
        self.replace_field.textChanged.connect(self._on_replacement_changed)
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
        self._first_row.addWidget(self._toggles)
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
            self.regex_button,
            self.whole_word_button,
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

    def uses_worker(self) -> bool:
        """Regex or Whole word is on, so the find worker searches."""
        return self.regex_button.isChecked() or self.whole_word_button.isChecked()

    def regex_session(self) -> RegexSession:
        """The find worker's session, started here if the bar has none."""
        if self._session is None:
            self.set_regex_session(RegexSession(self))
        assert self._session is not None
        return self._session

    def set_regex_session(self, session: RegexSession) -> None:
        if self._session is not None:
            self._session.finished.disconnect(self._on_worker_finished)
            self._session.timed_out.disconnect(self._on_worker_timed_out)
        self._session = session
        session.finished.connect(self._on_worker_finished)
        session.timed_out.connect(self._on_worker_timed_out)

    def is_searching(self) -> bool:
        """A worker job for this bar has not answered yet."""
        return self._request is not None

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
        self._cancel_request()
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
        if not self._may_replace() or self._waits_for_worker(self.replace_current):
            return
        self._catch_up()
        match = self.current_match()
        if match is None:
            return
        if self.regex_button.isChecked():
            self._replace_on_worker(match)
            return
        self._replace_span(match, self.replace_field.text())

    def replace_all(self) -> int:
        """Replace every match in one edit and return how many.

        The new text is built in one pass and goes in as one insert over
        the whole document, so one undo restores the original. The caret
        and scroll stay where they were. The label shows ``Replaced N``
        for :data:`REPLACED_MS`, then the live count.

        With Regex or Whole word on, the worker builds the new text and
        this returns 0 at once. The edit goes in when the worker answers,
        or never if it runs past its limit.
        """
        if not self._may_replace() or self._waits_for_worker(self.replace_all):
            return 0
        self._timer.stop()
        text, _astral = self._source_text()
        if self.uses_worker():
            job = ReplaceAllJob(
                **self._job_fields(),
                replacement=self.replace_field.text(),
                expand_groups=self.regex_button.isChecked(),
            )
            self._submit(job, "replace all")
            return 0
        replaced = replace_all(
            text,
            self.field.text(),
            self.replace_field.text(),
            match_case=self.match_case.isChecked(),
        )
        return self._apply_replace_all(replaced.text, replaced.spans)

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
            self._first_row.insertWidget(6, self._options)

    def _one_row_width(self) -> int:
        margins = self.layout().contentsMargins()
        widths = [
            _FIELD_MIN_WIDTH,
            self._toggles.sizeHint().width(),
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
            return
        # The other bar changed a shared toggle. This bar searches afresh
        # when it opens or steps.
        self._clear_problem()
        self._options_changed = True

    def _on_document_changed(self) -> None:
        self._text = None
        self._mask = None
        self._keys = None
        if not self._open or self._editing:
            return
        if self._problem:
            # Only a new query or a toggle searches again.
            return
        self._last_replacement = None
        # An edit searches again after a pause and leaves the caret alone.
        if not self._timer.isActive():
            self._move_on_search = False
        self._timer.start()

    def _search_due(self) -> bool:
        """The matches are out of date: a search waits, or never ran."""
        return self._timer.isActive() or self._text is None or self._options_changed

    def _search_pending(self) -> None:
        move = self._move_on_search
        self._move_on_search = True
        if not move:
            self._anchor = self._editor.textCursor().selectionStart()
        self._run_search(move=move)

    def _run_search(self, *, move: bool, show_count: bool = True) -> None:
        """Search and make the first match at or after the anchor current."""
        self._options_changed = False
        query = self.field.text()
        if not query:
            self._cancel_request()
            self._clear_problem()
            self._starts, self._ends = [], []
            self._current = -1
            self._editor.clear_find_matches()
            self.field.set_no_results(False)
            self._show_count("")
            self._sync_replace_buttons()
            return
        if self.uses_worker():
            self._submit(
                FindJob(**self._job_fields()),
                "find",
                move=move,
                show_count=show_count,
            )
            return
        self._cancel_request()
        self._clear_problem()
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
        if self._problem:
            return
        self._last_replacement = None
        if not self._open:
            self._open = True
            self.show()
            self._sync_layout()
        if self._waits_for_worker(lambda: self._step(forward=forward)):
            return
        if self._search_due():
            self._timer.stop()
            self._move_on_search = True
            self._anchor = self._editor.textCursor().selectionStart()
            self._run_search(move=False)
        if not self._starts:
            return
        caret = self._editor.textCursor().selectionStart()
        total = len(self._starts)
        if 0 <= self._current < total and caret == self._starts[self._current]:
            # From the current match by index: an empty match can share
            # its start with the match after it.
            index = (self._current + (1 if forward else -1)) % total
        elif forward:
            index = bisect_right(self._starts, caret)
            if index == total:
                index = 0
        else:
            index = bisect_left(self._starts, caret) - 1
            if index < 0:
                index = total - 1
        self._anchor = self._starts[index]
        self._set_current(index, move=True)

    def _set_current(self, index: int, *, move: bool, show_count: bool = True) -> None:
        self._current = index
        self._editor.set_find_matches(self._starts, self._ends, index)
        self.field.set_no_results(index < 0)
        self._check_replacement()
        if show_count:
            self._show_count(*self._live_count())
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
        if self._search_due():
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

    def _replace_span(self, match: tuple[int, int], replacement: str) -> None:
        """Put ``replacement`` over the UTF-16 ``match``, then search on."""
        start, end = match
        cursor = QTextCursor(self._editor.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        self._edit(cursor, replacement)
        span = (start, start + len(replacement.encode("utf-16-le")) // 2)
        self._last_replacement = span
        self._anchor = span[1]
        self._run_search(move=True)

    def _apply_replace_all(self, new_text: str, spans: list[tuple[int, int]]) -> int:
        """Write ``new_text`` in one edit. ``spans`` are its replacements."""
        count = len(spans)
        if not count:
            return 0
        editor = self._editor
        document = editor.document()
        caret = editor.textCursor().position()
        scrolls = (editor.horizontalScrollBar(), editor.verticalScrollBar())
        values = [bar.value() for bar in scrolls]
        cursor = QTextCursor(document)
        cursor.select(QTextCursor.SelectionType.Document)
        self._edit(cursor, new_text)
        moved = QTextCursor(document)
        moved.setPosition(min(caret, document.characterCount() - 1))
        editor.setTextCursor(moved)
        for bar, value in zip(scrolls, values, strict=True):
            bar.setValue(value)
        self._last_replacement = spans[-1]
        self._anchor = moved.position()
        self._run_search(move=False, show_count=False)
        self._show_count(replaced_text(count), replaced_announcement(count))
        self._replaced_timer.start()
        return count

    def _replace_on_return(self) -> None:
        """Return in the replace field is Replace, while Replace is enabled."""
        if self.replace_button.isEnabled():
            self.replace_current()

    def _end_replaced(self) -> None:
        """Swap ``Replaced N`` for the live count."""
        self._replaced_timer.stop()
        if self._problem:
            return
        if self.field.text():
            self._show_count(*self._live_count())
        else:
            self._show_count("")

    def _sync_replace_buttons(self) -> None:
        """Replace and Replace All need a query, a match, and no job.

        When they grey out under the keyboard, focus stays in the bar, in
        the replace field.
        """
        enabled = (
            bool(self.field.text())
            and bool(self._starts)
            and not self._replace_blocked
            and not self._problem
            and not self.replace_field.error()
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
            self._version = next(_TEXT_VERSIONS)
        return self._text, self._astral

    def _key_mask(self, text: str) -> str:
        syntax = self._editor.syntax_mode()
        if self._mask is None or self._mask[0] != syntax:
            self._mask = (syntax, key_mask(text, syntax))
        return self._mask[1]

    # Find worker --------------------------------------------------------

    def _job_fields(self) -> dict[str, object]:
        """What every worker job carries: the query, the toggles, the text."""
        self._source_text()
        return {
            "job_id": self.regex_session().next_job_id(),
            "version": self._version,
            "query": self.field.text(),
            "regex": self.regex_button.isChecked(),
            "whole_word": self.whole_word_button.isChecked(),
            "match_case": self.match_case.isChecked(),
        }

    def _submit(
        self,
        job: FindJob,
        kind: str,
        *,
        move: bool = True,
        show_count: bool = True,
        span: tuple[int, int] = (0, 0),
    ) -> None:
        """Send ``job`` and wait for it. It replaces any job still waiting."""
        self._request = _Request(job.job_id, job.version, kind, move, show_count, span)
        self.regex_session().submit(job, self._source_text()[0], owner=self)

    def _cancel_request(self) -> None:
        """Stop waiting for the worker. A late answer is ignored."""
        self._after_search = None
        if self._request is None:
            return
        self._request = None
        if self._session is not None:
            self._session.drop(self)

    def _waits_for_worker(self, action: Callable[[], object]) -> bool:
        """Run ``action`` after the worker search it needs, if one is due.

        Returns whether ``action`` now waits. A search still in its
        typing delay runs now, from the caret.
        """
        if not self.uses_worker():
            return False
        due = self._search_due()
        if self._request is None and not due:
            return False
        self._after_search = action
        if self._request is None:
            self._timer.stop()
            self._move_on_search = True
            self._anchor = self._editor.textCursor().selectionStart()
            self._run_search(move=False)
        return True

    def _replace_on_worker(self, match: tuple[int, int]) -> None:
        """Ask the worker for the current match's replacement, with groups."""
        starts, ends = self._points
        job = ReplaceOneJob(
            **self._job_fields(),
            replacement=self.replace_field.text(),
            expand_groups=True,
            start=starts[self._current],
            end=ends[self._current],
        )
        self._submit(job, "replace one", span=match)

    def _on_worker_finished(self, job_id: int, event: object) -> None:
        request = self._request
        if request is None or request.job_id != job_id:
            return
        self._request = None
        if isinstance(event, PatternFailed):
            reason = pattern_error_text(event.message, event.column)
            spoken = f"{INVALID_PATTERN}. {reason}"
            self._show_problem(_INVALID, reason, reason, INVALID_PATTERN, spoken)
            return
        if not self._open or self._text is None or request.version != self._version:
            # The document changed while the worker ran. Its own search follows.
            self._after_search = None
            return
        if isinstance(event, Found):
            self._apply_found(event, request)
        elif isinstance(event, ReplacedOne):
            self._apply_replaced_one(event, request)
        elif isinstance(event, ReplacedAll):
            if self._may_replace():
                astral = astral_offsets(event.text)
                starts = utf16_offsets(event.starts.tolist(), astral)
                ends = utf16_offsets(event.ends.tolist(), astral)
                spans = list(zip(starts, ends, strict=True))
                self._apply_replace_all(event.text, spans)
        elif isinstance(event, ReplacementFailed):
            self._groups = min(self._groups, event.group - 1)
            self._on_replacement_changed("")

    def _on_worker_timed_out(self, job_id: int) -> None:
        request = self._request
        if request is None or request.job_id != job_id:
            return
        self._request = None
        self._show_problem(
            _TIMED_OUT,
            SEARCH_TIMED_OUT_SPOKEN,
            "",
            SEARCH_TIMED_OUT,
            SEARCH_TIMED_OUT_SPOKEN,
        )

    def _apply_found(self, event: Found, request: _Request) -> None:
        starts = event.starts.tolist()
        ends = event.ends.tolist()
        if self.keys_only_active():
            starts, ends = self._inside_keys(starts, ends)
        _text, astral = self._source_text()
        self._clear_problem()
        self._groups = event.groups
        self._points = (starts, ends)
        self._starts = utf16_offsets(starts, astral)
        self._ends = utf16_offsets(ends, astral)
        current = -1
        if self._starts:
            current = bisect_left(self._starts, self._anchor)
            if current == len(self._starts):
                current = 0
        self._set_current(current, move=request.move, show_count=request.show_count)
        action = self._after_search
        self._after_search = None
        if action is not None:
            action()

    def _apply_replaced_one(self, event: ReplacedOne, request: _Request) -> None:
        if event.replacement is None or not self._may_replace():
            self._run_search(move=False)
            return
        self._replace_span(request.span, event.replacement)

    def _inside_keys(
        self, starts: list[int], ends: list[int]
    ) -> tuple[list[int], list[int]]:
        """Keep only the matches that lie inside one object key.

        An empty match at either edge of a key counts as inside it.
        """
        text, _astral = self._source_text()
        syntax = self._editor.syntax_mode()
        if self._keys is None or self._keys[0] != syntax:
            spans = key_spans(text, syntax)
            self._keys = (syntax, [s for s, _ in spans], [e for _, e in spans])
        _syntax, key_starts, key_ends = self._keys
        kept_starts: list[int] = []
        kept_ends: list[int] = []
        for start, end in zip(starts, ends, strict=True):
            index = bisect_right(key_starts, start) - 1
            if index >= 0 and end <= key_ends[index]:
                kept_starts.append(start)
                kept_ends.append(end)
        return kept_starts, kept_ends

    def _show_problem(
        self, kind: str, reason: str, tooltip: str, label: str, spoken: str
    ) -> None:
        """Clear the matches and say why, with the border and the label.

        Focus stays where it is, and nothing else pops up.
        """
        self._problem = kind
        self._after_search = None
        self._starts, self._ends = [], []
        self._points = ([], [])
        self._current = -1
        self._replaced_timer.stop()
        self._editor.clear_find_matches()
        self.field.set_error(reason)
        self.field.setToolTip(tooltip)
        self._check_replacement()
        self._show_count(label, spoken)
        self._sync_replace_buttons()

    def _clear_problem(self) -> None:
        if not self._problem:
            return
        self._problem = ""
        self.field.setToolTip("")

    def _check_replacement(self) -> None:
        """Mark a replacement that names a group the pattern lacks.

        Only with Regex on, a replace row and a pattern that compiled.
        This reads ``$`` signs and never compiles anything.
        """
        group = None
        if self._replace_shown and self.regex_button.isChecked() and not self._problem:
            parts = parse_replacement(self.replace_field.text())
            group = missing_group(parts, self._groups)
        reason = MISSING_GROUP.format(group=group) if group is not None else ""
        self.replace_field.set_error(reason)
        self.replace_field.setToolTip(reason)

    def _on_replacement_changed(self, _text: str) -> None:
        self._check_replacement()
        replaced = self._replaced_timer.isActive()
        if self.field.text() and not self._problem and not replaced:
            self._show_count(*self._live_count())
        self._sync_replace_buttons()

    def _live_count(self) -> tuple[str, str | None]:
        """The count label, or ``Invalid replacement`` while it applies."""
        reason = self.replace_field.error()
        if reason and self._starts:
            return INVALID_REPLACEMENT, f"{INVALID_REPLACEMENT}. {reason}"
        return count_text(self._current, len(self._starts)), None

    def _show_count(self, text: str, spoken: str | None = None) -> None:
        """Set the count. ``No results`` is muted; the bar announces changes.

        Screen readers hear ``spoken`` when it is given, else ``text``.
        """
        heard = text if spoken is None else spoken
        if (text, heard) == (self._count, self._heard):
            return
        self._count = text
        self._heard = heard
        self.count_label.setText(text)
        self.count_label.setStyleSheet(muted_text_style() if text == NO_RESULTS else "")
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


def _toggle_button(
    text: str, name: str, key: QKeySequence, object_name: str
) -> QPushButton:
    """A checkable text button with no icon. It shows pressed while on.

    ``name`` is its accessible name. The tooltip adds the shortcut.
    """
    button = QPushButton(text)
    button.setObjectName(object_name)
    button.setCheckable(True)
    button.setAccessibleName(name)
    shown = key.toString(QKeySequence.SequenceFormat.NativeText)
    button.setToolTip(f"{name} ({shown})")
    button.setAutoDefault(False)
    button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    button.setStyleSheet(find_toggle_style())
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
