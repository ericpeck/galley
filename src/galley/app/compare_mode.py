"""Compare mode: View › Compare, Next Change and Previous Change (#65 slice 3d).

Compare reuses the Input/Output splitter. The pane headers read Left and
Right. Left is the Input editor itself, read-only, so Input's text and undo
history are never touched. Right is a read-only editor of its own, shown in
the Output pane in place of the Output editor, which stays as it was. Right
keeps its text for the session. In 3d Paste fills Right; Open Right
Document… and drag and drop come in 3e.

The compare runs on the parse session's worker (#87), one job at a time,
with its Cancel. A side over 2 MB asks first. The Changes pane (#146) is a
tab of the results panel while Compare is on. ``ActionHold`` greys out the
commands that don't apply and gives each its own state back on leaving.

The window wires this in one block and hands over what it needs as a
``CompareHost``.
"""

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QKeyEvent, QPalette
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from galley.app.actions import ActionHold, ShellActions, set_not_available
from galley.app.changes_pane import (
    CHANGES_NOUNS,
    COMPARISON_CANCELED,
    COMPARISON_STOPPED,
    ChangesPane,
    difference_count_text,
)
from galley.app.editor import CHANGE_MARKERS, ChangeMarker, CodeEditor
from galley.app.find_bar import FindBar
from galley.app.limits import COMPARE_WARNING_BYTES
from galley.app.pane import PaneWidget
from galley.app.parse_session import CompareFailure, ParseSession
from galley.app.problem_text import DisplayProblem, announcement_text
from galley.app.problems_pane import ProblemsPane
from galley.app.problems_settings import STATE_OPEN
from galley.app.regex_session import RegexSession
from galley.app.status_bar import Busy, StatusBar
from galley.core.compare_report import ChangeRow, CompareReport, GroupRow, ReportRow
from galley.core.formats import format_named
from galley.core.worker import CompareEvent, CompareSide

LEFT_TITLE = "Left"
RIGHT_TITLE = "Right"
COMPARING_MESSAGE = "Comparing…"
COMPARING_ACCESSIBLE_NAME = "Comparing"
# The counts slot for identical documents. The Changes pane says "No
# differences." as a sentence.
NO_DIFFERENCES_COUNT = "No differences"
SIZE_WARNING_TEXT = "Comparing this much data may take a while."
COMPARE_ANYWAY = "Compare anyway"
# "Comparing…" and Cancel wait as long as Format's spinner (ADR 0001).
COMPARING_REVEAL_MS = 500


def compare_needs_size_warning(left_text: str, right_text: str) -> bool:
    """True when either side is over 2 MB in UTF-8, so Compare asks first."""
    return any(
        len(text.encode("utf-8")) > COMPARE_WARNING_BYTES
        for text in (left_text, right_text)
    )


@dataclass(frozen=True)
class CompareHost:
    """What Compare mode uses from the window.

    ``busy`` is True while a Format, Copy As, file read or sheet holds the
    window. ``format_reason`` is why JSON and YAML commands are greyed out
    for the Input format, or "". ``follow_editors`` gives the panes' editors the
    View settings, ``bind_menu`` gives a pane's editor the right-click menu,
    ``toolbar_sync`` redraws the toolbar's tooltips and ``resync`` brings
    every window control up to date again after Compare.
    """

    window: QWidget
    actions: ShellActions
    left: PaneWidget
    right: PaneWidget
    results: ProblemsPane
    status: StatusBar
    session: ParseSession
    regex_session: RegexSession
    held_actions: tuple[QAction, ...]
    held_widgets: tuple[QWidget, ...]
    busy: Callable[[], bool]
    format_reason: Callable[[], str]
    apply_problems: Callable[[tuple[DisplayProblem, ...]], None]
    clear_problems: Callable[[], None]
    problems_state: Callable[[], str]
    set_problems_state: Callable[[str], None]
    layout_problems: Callable[[], None]
    follow_editors: Callable[[], None]
    bind_menu: Callable[[PaneWidget], None]
    toolbar_sync: Callable[[], None]
    resync: Callable[[], None]


@dataclass(frozen=True)
class _Saved:
    """What entering Compare set aside, for leaving to put back."""

    output_editor: CodeEditor
    output_find_bar: FindBar
    output_format: str
    left_read_only: bool
    left_flags: Qt.TextInteractionFlag
    left_position: int
    left_anchor: int
    left_scroll: int
    problems: tuple[DisplayProblem, ...]
    problems_state: str
    state_text: str
    counts_text: str


class CompareMode(QObject):
    """Turns Compare on and off and runs each compare."""

    def __init__(self, host: CompareHost) -> None:
        super().__init__(host.window)
        self._host = host
        actions = host.actions
        self._hold = ActionHold(host.held_actions, host.held_widgets)
        self._changes = ChangesPane()
        self._changes.activated.connect(self._show_row)
        self._changes.escape_pressed.connect(self._focus_side)
        self._changes.compare_again_requested.connect(self.run)
        self._right = self._build_right_editor()
        self._right_bar = FindBar(
            self._right, "right", regex_session=host.regex_session
        )
        self._share_toggles(host.left.find_bar, self._right_bar)
        self._right.textChanged.connect(self._sync_right_footer)
        self._right_format = ""
        self._saved: _Saved | None = None
        self._job_id: int | None = None
        self._size_box: QMessageBox | None = None
        self._menu_bound = False
        self._restoring = False
        self._reveal_timer = QTimer(self)
        self._reveal_timer.setSingleShot(True)
        self._reveal_timer.timeout.connect(self._show_comparing)

        actions.compare.toggled.connect(self._on_toggled)
        actions.next_change.triggered.connect(self._next_change)
        actions.previous_change.triggered.connect(self._previous_change)
        actions.paste.triggered.connect(self._paste)
        host.status.cancel_clicked.connect(self.cancel)
        host.results.jump_requested.connect(self._jump_in_right)
        host.left.format_changed.connect(self._on_format_changed)
        host.right.format_changed.connect(self._on_right_format_changed)
        session = host.session
        session.compare_finished.connect(self._on_finished)
        session.compare_failed.connect(self._on_failed)
        session.compare_stopped.connect(self._on_stopped)
        # A busy window or an XML or HTML Input greys Compare out. Each of
        # these changes when the window's own sync runs.
        for watched in (actions.format, actions.open_file, actions.copy_as_original):
            watched.changed.connect(self.sync_action)
        host.left.format_changed.connect(self.sync_action)
        self.sync_action()

    # --- State ------------------------------------------------------------

    def is_on(self) -> bool:
        return self._saved is not None

    def is_running(self) -> bool:
        return self._job_id is not None

    def changes_pane(self) -> ChangesPane:
        return self._changes

    def right_editor(self) -> CodeEditor:
        return self._right

    def sync_action(self) -> None:
        """Grey Compare out for XML or HTML Input, or while the window is busy.

        It stays on while Compare is on, so Compare can always be left.
        """
        action = self._host.actions.compare
        reason = "" if self.is_on() else self._host.format_reason()
        available = self.is_on() or (not reason and not self._host.busy())
        if action.isEnabled() != available:
            action.setEnabled(available)
        set_not_available(action, reason)

    # --- Entering and leaving ---------------------------------------------

    def _on_toggled(self, checked: bool) -> None:
        if checked == self.is_on():
            return
        if checked:
            if self._host.busy() or self._host.format_reason():
                self._set_checked(False)
                return
            self._enter()
        else:
            self._leave()

    def _set_checked(self, checked: bool) -> None:
        action = self._host.actions.compare
        action.blockSignals(True)
        action.setChecked(checked)
        action.blockSignals(False)

    def _enter(self) -> None:
        host = self._host
        left = host.left.editor
        cursor = left.textCursor()
        self._saved = _Saved(
            output_editor=host.right.editor,
            output_find_bar=host.right.find_bar,
            output_format=host.right.format_name(),
            left_read_only=left.isReadOnly(),
            left_flags=left.textInteractionFlags(),
            left_position=cursor.position(),
            left_anchor=cursor.anchor(),
            left_scroll=left.verticalScrollBar().value(),
            problems=host.results.given(),
            problems_state=host.problems_state(),
            state_text=host.status.state_text(),
            counts_text=host.status.counts_text(),
        )
        for pane in (host.left, host.right):
            pane.find_bar.close_bar()
        self._hold.set_held(True)
        host.toolbar_sync()
        _view_only(left)
        host.left.set_empty_visible(False)
        host.right.show_editor(self._right, self._right_bar)
        if not self._menu_bound:
            self._menu_bound = True
            host.bind_menu(host.right)
        host.follow_editors()
        host.left.set_title(LEFT_TITLE)
        host.right.set_title(RIGHT_TITLE)
        self._choose_right_format(self._right_format or self._saved.output_format)
        self._sync_right_footer()
        host.results.add_tab(
            self._changes, "Changes", CHANGES_NOUNS, _accent_fill, keep_open=True
        )
        host.results.set_current_page(self._changes)
        if host.problems_state() != STATE_OPEN:
            host.set_problems_state(STATE_OPEN)
        host.layout_problems()
        QWidget.setTabOrder(left, self._right)
        QWidget.setTabOrder(self._right, self._changes.list_view())
        for action in (host.actions.next_change, host.actions.previous_change):
            action.setEnabled(True)
        QApplication.instance().installEventFilter(self)
        self.sync_action()
        left.setFocus(Qt.FocusReason.OtherFocusReason)
        self.run()

    def _leave(self) -> None:
        saved = self._saved
        if saved is None:
            return
        host = self._host
        QApplication.instance().removeEventFilter(self)
        self._close_size_box()
        self._cancel_job()
        self._clear_marks()
        self._right.set_problem_lines(set())
        self._right_bar.close_bar()
        host.left.find_bar.close_bar()
        self._restoring = True
        try:
            host.right.show_editor(saved.output_editor, saved.output_find_bar)
            host.left.set_title(None)
            host.right.set_title(None)
            _choose_format(host.right, saved.output_format)
        finally:
            self._restoring = False
        left = host.left.editor
        left.setReadOnly(saved.left_read_only)
        left.setTextInteractionFlags(saved.left_flags)
        cursor = left.textCursor()
        cursor.setPosition(saved.left_anchor)
        cursor.setPosition(saved.left_position, cursor.MoveMode.KeepAnchor)
        left.setTextCursor(cursor)
        left.verticalScrollBar().setValue(saved.left_scroll)
        host.results.remove_tab(self._changes)
        self._saved = None
        for action in (host.actions.next_change, host.actions.previous_change):
            action.setEnabled(False)
        self._hold.set_held(False)
        host.toolbar_sync()
        if saved.problems:
            host.apply_problems(saved.problems)
        else:
            host.clear_problems()
            host.status.set_state(saved.state_text)
        host.status.set_counts(saved.counts_text)
        if host.problems_state() != saved.problems_state:
            host.set_problems_state(saved.problems_state)
        host.follow_editors()
        host.resync()
        self.sync_action()
        left.setFocus(Qt.FocusReason.OtherFocusReason)

    # --- Running a compare ------------------------------------------------

    def run(self) -> None:
        """Compare Left with Right now. A running compare is dropped first."""
        if not self.is_on():
            return
        self._close_size_box()
        self._cancel_job()
        self._clear_marks()
        left_text = self._host.left.editor.toPlainText()
        right_text = self._right.toPlainText()
        if _is_blank(left_text) or _is_blank(right_text):
            self._show_empty()
            return
        if compare_needs_size_warning(left_text, right_text):
            self._ask_size(left_text, right_text)
            return
        self._submit(left_text, right_text)

    def cancel(self) -> bool:
        """Stop the running compare, as Cancel and Esc do. False when idle."""
        if not self.is_running():
            return False
        self._cancel_job()
        self._clear_marks()
        self._show_canceled()
        self._host.status.set_state(COMPARISON_CANCELED)
        return True

    def _submit(self, left_text: str, right_text: str) -> None:
        host = self._host
        job_id = host.session.submit_compare(
            left_text,
            host.left.format_name(),
            right_text,
            host.right.format_name(),
        )
        if job_id is None:
            self._show_stopped()
            return
        self._job_id = job_id
        self._changes.show_busy()
        self._set_count(0)
        host.status.set_counts("")
        self._reveal_timer.start(COMPARING_REVEAL_MS)

    def _show_comparing(self) -> None:
        if not self.is_running():
            return
        status = self._host.status
        status.set_busy(Busy.SPINNER, COMPARING_ACCESSIBLE_NAME, cancel=True)
        status.set_state(COMPARING_MESSAGE)

    def _cancel_job(self) -> None:
        """Drop the running compare quietly. The caller says what happened."""
        if self._job_id is None:
            return
        self._end_job()
        self._host.session.cancel()

    def _end_job(self) -> None:
        self._job_id = None
        self._reveal_timer.stop()
        self._host.status.set_busy(Busy.IDLE)

    def _ask_size(self, left_text: str, right_text: str) -> None:
        """Ask before a compare with a side over 2 MB. One answer, one compare."""
        box = QMessageBox(self._host.window)
        box.setObjectName("compareSizeWarning")
        box.setWindowTitle("Galley")
        box.setIcon(QMessageBox.Icon.NoIcon)
        box.setWindowModality(Qt.WindowModality.WindowModal)
        box.setText(SIZE_WARNING_TEXT)
        anyway = box.addButton(COMPARE_ANYWAY, QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(anyway)
        box.setEscapeButton(cancel)
        self._size_box = box

        def _choose() -> None:
            if self._size_box is not box:
                return
            accepted = box.clickedButton() is anyway
            self._size_box = None
            box.deleteLater()
            if not self.is_on():
                return
            if accepted:
                self._submit(left_text, right_text)
            else:
                self._show_canceled()

        box.finished.connect(_choose)
        box.open()

    def _close_size_box(self) -> None:
        box = self._size_box
        if box is None:
            return
        self._size_box = None
        box.reject()
        box.deleteLater()

    # --- Results ----------------------------------------------------------

    def _on_finished(self, job_id: int, event: object) -> None:
        if job_id != self._job_id or not isinstance(event, CompareEvent):
            return
        self._end_job()
        host = self._host
        host.clear_problems()
        self._right.set_problem_lines(set())
        report = event.report
        self._changes.set_report(report)
        self._set_count(report.count)
        counts = (
            difference_count_text(report.count)
            if report.count
            else NO_DIFFERENCES_COUNT
        )
        host.status.set_counts(counts)
        host.status.set_state("Ready")
        host.status.announce(counts)
        self._mark(report)
        first = self._changes.model().row_at(self._changes.list_view().current_row())
        if first is not None:
            self._show_row(first)

    def _on_failed(self, job_id: int, failure: object) -> None:
        if job_id != self._job_id or not isinstance(failure, CompareFailure):
            return
        self._end_job()
        host = self._host
        self._changes.show_errors()
        self._set_count(0)
        host.status.set_counts("")
        host.clear_problems()
        self._right.set_problem_lines(set())
        if not failure.problems:
            host.status.set_state(failure.message)
            return
        if failure.side == "right":
            self._show_right_problems(failure.problems)
        else:
            host.apply_problems(failure.problems)
        host.results.tab_bar().setCurrentIndex(0)

    def _show_right_problems(self, rows: tuple[DisplayProblem, ...]) -> None:
        """List Right's problems and mark Right's gutter, as Input's are."""
        results = self._host.results
        results.set_problems(rows)
        shown = results.shown()
        messages: dict[int, list[str]] = {}
        for row in shown:
            messages.setdefault(row.lineno, []).append(row.full_message())
        self._right.set_problem_messages(
            {lineno: "\n".join(texts) for lineno, texts in messages.items()}
        )
        self._host.layout_problems()
        status = self._host.status
        status.set_problem_count(results.count())
        if shown:
            status.announce(announcement_text(results.count(), shown[0]))

    def _jump_in_right(self, lineno: int, colno: int) -> None:
        """A Problems row jumps in the side its problem is in."""
        if self._right.problem_lines():
            self._right.reveal_line(lineno, colno)

    def _on_stopped(self, job_id: int) -> None:
        if job_id != self._job_id:
            return
        self._end_job()
        self._show_stopped()

    def _show_empty(self) -> None:
        self._changes.show_empty_right()
        self._set_count(0)
        self._host.status.set_counts("")

    def _show_canceled(self) -> None:
        self._changes.show_canceled()
        self._set_count(0)
        self._host.status.set_counts("")

    def _show_stopped(self) -> None:
        self._changes.show_stopped()
        self._set_count(0)
        self._host.status.set_counts("")
        self._host.status.set_state(COMPARISON_STOPPED)

    def _set_count(self, count: int) -> None:
        if self.is_on():
            self._host.results.set_count(self._changes, count)

    # --- Markers and jumps ------------------------------------------------

    def _mark(self, report: CompareReport) -> None:
        names = _first_names(report.rows)
        for side, editor, marks in (
            ("left", self._host.left.editor, report.left),
            ("right", self._right, report.right),
        ):
            spoken = names[side]
            editor.set_line_markers(
                CHANGE_MARKERS,
                {
                    line: ChangeMarker(kind, spoken.get(line, ""))
                    for line, kind in marks.markers.items()
                },
            )
            editor.set_change_tints(side, marks.tints)

    def _clear_marks(self) -> None:
        sides: tuple[tuple[CompareSide, CodeEditor], ...] = (
            ("left", self._host.left.editor),
            ("right", self._right),
        )
        for side, editor in sides:
            editor.clear_line_markers(CHANGE_MARKERS)
            editor.set_change_tints(side, ())

    def _show_row(self, row: object) -> None:
        """Select a change's spans: Left, Right, or both. Focus stays put."""
        change = _first_change(row)
        if change is None:
            return
        if change.left_span is not None:
            self._host.left.editor.select_span(change.left_span)
        if change.right_span is not None:
            self._right.select_span(change.right_span)

    def _next_change(self) -> None:
        if self.is_on() and not self.is_running():
            self._changes.next_change()

    def _previous_change(self) -> None:
        if self.is_on() and not self.is_running():
            self._changes.previous_change()

    # --- Right ------------------------------------------------------------

    def _build_right_editor(self) -> CodeEditor:
        editor = CodeEditor()
        editor.setObjectName("rightEditor")
        editor.setAccessibleName(f"{RIGHT_TITLE} editor")
        _view_only(editor)
        editor.hide()
        return editor

    def _paste(self) -> None:
        """Paste replaces all of Right, wherever focus is (3d)."""
        if not self.is_on():
            return
        text = QApplication.clipboard().text()
        if not text:
            return
        self._right.setPlainText(text)
        self._choose_right_format(_format_guess(text))
        self.run()

    def _choose_right_format(self, label: str) -> None:
        if label not in self._host.right.format_choices():
            self._right_format = self._host.right.format_name()
            return
        self._right_format = label
        self._restoring = True
        try:
            _choose_format(self._host.right, label)
        finally:
            self._restoring = False

    def _on_right_format_changed(self, label: str) -> None:
        if not self.is_on() or self._restoring:
            return
        self._right_format = label
        self.run()

    def _on_format_changed(self, _label: str) -> None:
        if self.is_on() and not self._restoring:
            self.run()

    def _sync_right_footer(self) -> None:
        if not self.is_on():
            return
        pane = self._host.right
        text = self._right.toPlainText()
        pane.count_label.setText(_line_label(self._right.blockCount()) if text else "")
        if pane.copy_button is not None:
            pane.copy_button.setEnabled(bool(text))

    def _share_toggles(self, first: FindBar, second: FindBar) -> None:
        """Right's Regex and Whole word follow the other bars', as theirs do."""
        for name in ("regex_button", "whole_word_button"):
            one, two = (getattr(bar, name) for bar in (first, second))
            one.toggled.connect(two.setChecked)
            two.toggled.connect(one.setChecked)

    def _focus_side(self) -> None:
        """Esc in Changes goes back to the editor. Compare stays on."""
        self._host.left.editor.setFocus(Qt.FocusReason.OtherFocusReason)

    # --- Esc and drops ----------------------------------------------------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """While comparing, Esc closes a find bar, then cancels the compare.

        Idle, Esc goes on to the widget, so it never leaves Compare. Files
        dropped on the window are refused until 3e opens them.
        """
        if not isinstance(watched, QWidget) or not _is_inside(
            watched, self._host.window
        ):
            return False
        kind = event.type()
        if kind in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop):
            event.ignore()
            return True
        if (
            kind != QEvent.Type.KeyPress
            or not isinstance(event, QKeyEvent)
            or event.key() != Qt.Key.Key_Escape
            or QApplication.activeModalWidget() is not None
        ):
            return False
        for bar in (self._host.left.find_bar, self._right_bar):
            if bar.is_open():
                if _is_inside(watched, bar):
                    return False
                bar.close_bar()
                return True
        return self.cancel()


def _view_only(editor: CodeEditor) -> None:
    """Read-only, with the caret and selection still on the keyboard."""
    editor.setReadOnly(True)
    editor.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse
        | Qt.TextInteractionFlag.TextSelectableByKeyboard
    )


def _first_change(row: object) -> ChangeRow | None:
    if isinstance(row, ChangeRow):
        return row
    if isinstance(row, GroupRow):
        return next(
            (child for child in row.children if isinstance(child, ChangeRow)), None
        )
    return None


def _first_names(rows: tuple[ReportRow, ...]) -> dict[str, dict[int, str]]:
    """Each side's marked line and the spoken name of its first change."""
    names: dict[str, dict[int, str]] = {"left": {}, "right": {}}
    for change in _changes_in(rows):
        for side, line in (("left", change.left_line), ("right", change.right_line)):
            if line is not None:
                names[side].setdefault(line, change.accessible_name)
    return names


def _changes_in(rows: tuple[ReportRow, ...]) -> list[ChangeRow]:
    found: list[ChangeRow] = []
    for row in rows:
        if isinstance(row, ChangeRow):
            found.append(row)
        elif isinstance(row, GroupRow):
            found.extend(
                child for child in row.children if isinstance(child, ChangeRow)
            )
    return found


def _format_guess(text: str) -> str:
    """JSON when the text opens with ``{`` or ``[``, else YAML.

    The editor guesses its colors the same way.
    """
    stripped = text.lstrip()
    label = "JSON" if stripped[:1] in ("{", "[") else "YAML"
    return format_named(label).label


def _choose_format(pane: PaneWidget, label: str) -> None:
    combo = pane.format_combo
    index = combo.findText(label)
    if index >= 0:
        combo.setCurrentIndex(index)


def _is_blank(text: str) -> bool:
    return text.strip() == ""


def _line_label(count: int) -> str:
    return f"{count} line" if count == 1 else f"{count} lines"


def _is_inside(widget: QWidget | None, ancestor: QWidget) -> bool:
    while widget is not None:
        if widget is ancestor:
            return True
        widget = widget.parentWidget()
    return False


def _accent_fill(palette: QPalette) -> QColor:
    """The Changes badge: the accent, beside Problems' error fill."""
    return palette.color(QPalette.ColorRole.Accent)
