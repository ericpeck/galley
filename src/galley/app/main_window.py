"""Galley main window: menus, toolbar, panes, and status bar."""

import html
import time
from pathlib import Path
from typing import NamedTuple

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QKeyEvent,
    QKeySequence,
    QResizeEvent,
    QShowEvent,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMenuBar,
    QMessageBox,
    QPushButton,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from galley.app.about import show_about_dialog
from galley.app.actions import ShellActions, coming_soon_wrapper, is_unbuilt
from galley.app.appearance import (
    MODE_DARK,
    MODE_LIGHT,
    MODE_SYSTEM,
    application_appearance,
)
from galley.app.editor import CodeEditor, fold_status_text
from galley.app.escape_string import (
    StringResult,
    convert_python_too_large_message,
    escape_input,
    escape_too_large_message,
    unescape_input,
    unescape_too_large_message,
)
from galley.app.fonts import CODE_POINT_SIZE, ui_font
from galley.app.icons import toolbar_icon

# The size thresholds live in limits.py so the editor can read them too.
# Tests and callers still import them from here.
from galley.app.limits import (
    _SI_KB,
    _SI_MB,
    JSON_BUSY_BYTES,
    JSON_PROGRESS_BYTES,
    JSON_WARNING_BYTES,
    PYTHON_TO_JSON_BYTES,
    PYTHON_TO_YAML_BYTES,
    YAML_BUSY_BYTES,
    YAML_PROGRESS_BYTES,
    YAML_TAB_BUSY_BYTES,
    YAML_TAB_PROGRESS_BYTES,
    YAML_WARNING_BYTES,
)
from galley.app.open_file import (
    OPEN_DIALOG_CAPTION,
    OPEN_FILE_FILTER,
    OPEN_WHILE_FORMATTING_TOOLTIP,
    OPENED_STATUS_MS,
    REPLACE_BUTTON,
    FileReader,
    InputDocument,
    input_needs_replace,
    load_last_open_dir,
    save_last_open_dir,
)
from galley.app.open_text import (
    input_window_title,
    open_error_text,
    opened_text,
    opening_text,
    replace_input_text,
)
from galley.app.pane import PaneKind, PaneRole, PaneWidget
from galley.app.parse_session import ParseSession
from galley.app.problem_text import (
    DisplayProblem,
    announcement_text,
    problem_count_text,
)
from galley.app.problems_pane import SHOW_REPLACE_TABS_BUTTON, ProblemsPane
from galley.app.problems_settings import (
    MIN_WIDTH,
    STATE_CLOSED,
    STATE_MINIMIZED,
    STATE_OPEN,
    STRIP_WIDTH,
    load_problems_preferences,
    load_sort_keys,
    load_split_sizes,
    load_stacked_panes,
    save_problems_preferences,
    save_sort_keys,
    save_split_sizes,
    save_stacked_panes,
)
from galley.app.shortcuts import COLLAPSE_ALL, EXPAND_ALL, FOLD, UNFOLD
from galley.app.shortcuts import native_chord_text as chord_text
from galley.app.splitter import PaneSplitter

# Re-exported: tests and callers import the slot widths from here.
from galley.app.status_bar import PROGRESS_WIDTH_PX as PROGRESS_WIDTH_PX
from galley.app.status_bar import SPINNER_WIDTH_PX as SPINNER_WIDTH_PX
from galley.app.status_bar import Busy, StatusBar
from galley.app.style import (
    menu_style,
    muted_text_style,
    primary_button_style,
)
from galley.app.tab_repair import replace_leading_tabs
from galley.app.toolbar import MainToolbar
from galley.core.convert_python import ConvertError, convert_python
from galley.core.folds import FoldRange, unpack_ranges
from galley.core.format import _has_tab
from galley.core.formats import YAML, format_named
from galley.core.load import LoadedDocument, LoadError

NOT_YET_MESSAGE = "Not yet available."
NOTHING_TO_FOLD = "nothing to fold"
CANCELLED_MESSAGE = "Formatting canceled."
FORMATTING_MESSAGE = "Formatting…"
# Words, not only the pressed Sort keys button, say the output was sorted.
SORTED_KEYS_SUFFIX = "· keys sorted A–Z"
FORMAT_ACCESSIBLE_NAME = "Format"
FORMAT_BUSY_DESCRIPTION = "Format is running. Press Cancel to stop."
FORMAT_BUSY_TOOLTIP = "Formatting… press Cancel to stop"
FORMAT_NOT_STARTED_MESSAGE = (
    "Formatting didn't start. The formatter is shut down or already running."
)
SIZE_WARNING_DETAIL = (
    "Large documents can take a while to format. You can cancel while it runs."
)
_NOT_YET_MS = 4000

# Final timings. The busy indicator waits 500 ms, the progress bar and
# Cancel wait 2 s, and the status text counts up from 10 s.
SPINNER_REVEAL_MS = 500
PROGRESS_BAR_REVEAL_MS = 2000
ELAPSED_STATUS_MS = 10_000
CANCELED_STATUS_MS = 3_000
# "Opening file.json…" waits as long as the format spinner (ADR 0001).
OPEN_REVEAL_MS = SPINNER_REVEAL_MS
OPENING_ACCESSIBLE_NAME = "Opening"


class _EditorView(NamedTuple):
    position: int
    anchor: int
    vertical: int
    horizontal: int


class _FormatButton(QPushButton):
    """Format button sized for its widest label.

    The toolbar measures Format when it reflows. Sizing for both labels
    keeps the toolbar still when a resize lands mid-job.
    """

    def __init__(self) -> None:
        super().__init__()
        self._labels: tuple[str, ...] = ()

    def set_labels(self, labels: tuple[str, ...]) -> None:
        self._labels = labels
        self.updateGeometry()

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        metrics = self.fontMetrics()
        current = metrics.horizontalAdvance(self.text())
        widest = max(
            (metrics.horizontalAdvance(label) for label in self._labels),
            default=current,
        )
        if widest > current:
            hint.setWidth(hint.width() + widest - current)
        return hint


class MainWindow(QMainWindow):
    """The single Galley window in its empty, ready state.

    ``open_started``, ``open_finished(InputDocument)`` and
    ``open_failed(kind, name)`` report each Open for loading and error
    visuals. ``kind`` is a :class:`~galley.core.load.LoadErrorKind` value.
    """

    open_started = Signal()
    open_finished = Signal(object)
    open_failed = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self._initial_show_done = False
        self._split_settled = False
        self._applying_split = False
        self._following_palette = False
        self._splitter: PaneSplitter | None = None
        self._formatting = False
        self._format_started_s = 0.0
        self._size_forces_busy = False
        self._size_forces_progress = False
        self._accepted_job_id: int | None = None
        self._size_warning_accepted = False
        self._size_warning: QMessageBox | None = None
        self._replace_box: QMessageBox | None = None
        self._loading_path: Path | None = None
        self._open_job = 0
        self._reader = FileReader(self)
        self._reader.finished.connect(self._on_file_read)
        self._open_reveal_timer = QTimer(self)
        self._open_reveal_timer.setSingleShot(True)
        self._open_reveal_timer.timeout.connect(self._show_opening)
        self._opened_status_timer = QTimer(self)
        self._opened_status_timer.setSingleShot(True)
        self._opened_status_timer.timeout.connect(self._clear_opened_status)
        self._opened_status = ""
        self._status_before_format = "Ready"
        self._reveal_timer = QTimer(self)
        self._reveal_timer.setSingleShot(True)
        self._reveal_timer.timeout.connect(self._apply_deferred_reveal)
        self._cancel_status_timer = QTimer(self)
        self._cancel_status_timer.setSingleShot(True)
        self._cancel_status_timer.timeout.connect(self._clear_canceled_status)
        self._output_before_format = ""
        self._format_source = "JSON"
        self._format_target = "JSON"
        self._format_sorted = False
        self._format_idle_text = ""
        self._format_idle_tooltip = ""
        self._session = ParseSession(self)
        self.setWindowTitle("Galley")
        self.setFont(ui_font())
        self.resize(1280, 800)
        self.setMinimumSize(900, 600)
        self.setAutoFillBackground(True)
        self._actions = ShellActions(self)
        self._build_menus()
        self._input_pane = PaneWidget(PaneRole.INPUT)
        self._output_pane = PaneWidget(PaneRole.OUTPUT)
        # Only Output folds in v1 (#64).
        self._output_pane.editor.set_folding_enabled(True)
        self._active_editor = self._input_pane.editor
        self._splitter = self._build_splitter()
        self._problems_state, self._problems_width = load_problems_preferences()
        self._applying_problems_layout = False
        self._problems_pane = ProblemsPane()
        self._problems_splitter = self._build_problems_splitter()
        self._sync_problems_action()
        self._editing = self._build_caption()
        self._format_button = self._build_format_button()
        toolbar = self._build_toolbar()
        self._status = self._build_status_bar()
        self._assemble(toolbar)
        self._connect()
        self._set_syntax_colors(True)
        self._sync_input_state()
        self._sync_output_state()
        self._sync_direction()
        self._apply_saved_split()
        self._layout_problems()
        self._restore_stacked_panes()
        self._restore_sort_keys()
        self._follow_editor_palette()

    def start_parse_worker(self) -> None:
        """Start the warm formatter after runtime configuration."""
        self._session.start()

    def shutdown_parse_worker(self) -> None:
        """Reap the formatter when the window is done with it."""
        self._session.shutdown()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.cancel_open()
        self._session.shutdown()
        super().closeEvent(event)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self._initial_show_done:
            return
        self._initial_show_done = True
        self._focus_input()
        self._settle_split()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._settle_split()
        self._layout_problems()

    def _settle_split(self) -> None:
        splitter = self._splitter
        if splitter is None or self._split_settled or splitter.width() <= 200:
            return
        self._split_settled = True
        self._apply_saved_split()

    def _apply_saved_split(self) -> None:
        """Restore the last split ratio, or an even split (spec §8)."""
        splitter = self._splitter
        if splitter is None:
            return
        self._applying_split = True
        try:
            sizes = load_split_sizes()
            if sizes is None:
                splitter.apply_even_split()
            else:
                splitter.setSizes(sizes)
        finally:
            self._applying_split = False

    def _remember_split(self) -> None:
        """Save the split once it has settled. Restoring never saves."""
        splitter = self._splitter
        if splitter is None or self._applying_split or not self._split_settled:
            return
        sizes = splitter.sizes()
        if len(sizes) == 2 and min(sizes) > 0:
            save_split_sizes(sizes)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self._follow_editor_palette()
            self._refresh_toolbar_icons()
        if event.type() != QEvent.Type.WindowStateChange:
            return
        fullscreen = self.isFullScreen()
        action = self._actions.full_screen
        if action.isChecked() != fullscreen:
            action.setChecked(fullscreen)

    def _follow_editor_palette(self) -> None:
        """Keep each editor on the application Base after a scheme change."""
        if self._following_palette or not hasattr(self, "_output_pane"):
            return
        self._following_palette = True
        try:
            for editor in self._editors():
                editor.follow_application_palette()
        finally:
            self._following_palette = False

    def _refresh_toolbar_icons(self) -> None:
        """Redraw the toolbar glyphs, which keep the ink they were drawn with."""
        if not hasattr(self, "_format_button"):
            return
        for button in self.findChildren(QToolButton):
            action = button.defaultAction()
            if action is None or action.icon().isNull():
                continue
            icon_only = (
                button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
            )
            icon = toolbar_icon(action.text(), pad=not icon_only)
            action.setIcon(icon)
            button.setIcon(icon)
        self._format_button.setIcon(toolbar_icon("Format", pad=True))

    def _build_menus(self) -> None:
        actions = self._actions
        bar = self.menuBar()
        bar.setStyleSheet(menu_style())

        galley = _add_menu(bar, "Galley")
        galley.addAction(actions.preferences)
        galley.addSeparator()
        galley.addAction(actions.quit)

        file_menu = _add_menu(bar, "File")
        file_menu.setToolTipsVisible(True)
        file_menu.addAction(actions.open_file)

        edit = _add_menu(bar, "Edit")
        edit.addAction(actions.undo)
        edit.addAction(actions.redo)
        edit.addSeparator()
        edit.addAction(actions.cut)
        edit.addAction(actions.copy)
        edit.addAction(actions.paste)
        edit.addAction(actions.select_all)
        edit.addSeparator()
        edit.addAction(actions.find)
        edit.addAction(actions.find_replace)
        edit.addSeparator()
        edit.addAction(actions.escape_string)
        edit.addAction(actions.unescape_string)
        edit.addSeparator()
        edit.addAction(actions.format)
        edit.addAction(actions.convert_python)

        view = _add_menu(bar, "View")
        font_menu = view.addMenu("Font")
        font_menu.setObjectName("menuFont")
        view.addAction(actions.bigger)
        view.addAction(actions.smaller)
        view.addAction(actions.actual_size)
        view.addSeparator()
        view.addAction(actions.word_wrap)
        view.addAction(actions.show_whitespace)
        guides = view.addMenu("Indent Guides")
        guides.setObjectName("menuIndentGuides")
        guides.addAction(actions.guides_off)
        guides.addAction(actions.guides_lines)
        guides.addAction(actions.guides_depth)
        view.addAction(actions.show_problems)
        view.addAction(actions.next_problem)
        view.addAction(actions.previous_problem)
        view.addSeparator()
        view.addAction(actions.expand_all)
        view.addAction(actions.collapse_all)
        view.addAction(actions.fold)
        view.addAction(actions.unfold)
        view.addSeparator()
        view.addAction(actions.stack_panes)
        view.addSeparator()
        self._build_appearance_menu(view)
        if _shows_own_full_screen_item():
            view.addSeparator()
            view.addAction(actions.full_screen)

        window_menu = _add_menu(bar, "Window")
        window_menu.addAction(actions.pop_out)
        window_menu.addAction(actions.show_window)
        window_menu.addAction(actions.bring_back)

        # Last on Help. AboutRole moves it into the application menu
        # where Qt relocates that role, and leaves it on Help otherwise.
        help_menu = _add_menu(bar, "Help")
        help_menu.addAction(actions.about)

        self._tab_menu = QMenu(self)
        self._tab_menu.setObjectName("tabMenu")
        self._tab_menu.addAction(actions.rename)
        self._tab_menu.addAction(actions.duplicate)
        self._tab_menu.addAction(actions.pop_out)
        self._tab_menu.addSeparator()
        self._tab_menu.addAction(actions.close_tab)
        self._tab_menu.addAction(actions.close_others)
        self._tab_menu.addAction(actions.close_tabs_right)

        self._editor_menu = QMenu(self)
        self._editor_menu.setObjectName("editorMenu")
        self._editor_menu.addAction(actions.undo)
        self._editor_menu.addAction(actions.redo)
        self._editor_menu.addSeparator()
        self._editor_menu.addAction(actions.cut)
        self._editor_menu.addAction(actions.copy)
        self._editor_menu.addAction(actions.paste)
        self._editor_menu.addAction(actions.select_all)

        # Output's menu adds the fold commands; Input does not fold.
        self._output_editor_menu = QMenu(self)
        self._output_editor_menu.setObjectName("outputEditorMenu")
        self._output_editor_menu.addActions(self._editor_menu.actions())
        self._output_editor_menu.addSeparator()
        self._output_editor_menu.addAction(actions.fold)
        self._output_editor_menu.addAction(actions.unfold)
        self._output_editor_menu.addAction(actions.expand_all)
        self._output_editor_menu.addAction(actions.collapse_all)

    def _build_appearance_menu(self, view: QMenu) -> None:
        """View › Appearance: System, Light, Dark. Shared by every window."""
        menu = view.addMenu("Appearance")
        menu.setObjectName("menuAppearance")
        modes = {
            MODE_SYSTEM: self._actions.appearance_system,
            MODE_LIGHT: self._actions.appearance_light,
            MODE_DARK: self._actions.appearance_dark,
        }
        for mode, action in modes.items():
            menu.addAction(action)
            action.triggered.connect(
                lambda _checked=False, chosen=mode: self._set_appearance(chosen)
            )
        self._appearance_actions = modes
        appearance = application_appearance()
        if appearance is not None:
            appearance.mode_changed.connect(self._sync_appearance_actions)
            self._sync_appearance_actions(appearance.mode())

    def _set_appearance(self, mode: str) -> None:
        appearance = application_appearance()
        if appearance is not None:
            appearance.set_mode(mode)

    def _sync_appearance_actions(self, mode: str) -> None:
        action = self._appearance_actions.get(mode)
        if action is not None and not action.isChecked():
            action.setChecked(True)

    def _build_splitter(self) -> PaneSplitter:
        splitter = PaneSplitter()
        splitter.addWidget(self._input_pane)
        splitter.addWidget(self._output_pane)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.split_changed.connect(self._remember_split)
        return splitter

    def _build_problems_splitter(self) -> QSplitter:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("problemsSplitter")
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._problems_pane)
        splitter.addWidget(self._splitter)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.splitterMoved.connect(self._on_problems_splitter_moved)
        self._problems_pane.width_changed.connect(self._remember_problems_width)
        return splitter

    def _build_caption(self) -> QLabel:
        caption = QLabel(_editing_text(self._input_pane.title))
        caption.setObjectName("editingCaption")
        caption.setTextFormat(Qt.TextFormat.RichText)
        caption.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        caption.setStyleSheet(muted_text_style())
        font = ui_font()
        font.setPixelSize(11)
        caption.setFont(font)
        width = max(
            caption.fontMetrics().horizontalAdvance(f"Editing {pane.title}")
            for pane in (self._input_pane, self._output_pane)
        )
        caption.setMinimumWidth(width + 8)
        caption.setMinimumHeight(caption.fontMetrics().height() + 2)
        return caption

    def _build_format_button(self) -> QPushButton:
        button = _FormatButton()
        button.setObjectName("formatButton")
        button.setAccessibleName(FORMAT_ACCESSIBLE_NAME)
        button.setIcon(toolbar_icon("Format", pad=True))
        button.setStyleSheet(primary_button_style())
        button.setMinimumHeight(28)
        hint = self._actions.format.shortcut().toString(
            QKeySequence.SequenceFormat.NativeText
        )
        self._format_idle_text = f"{{·}} Format {hint}".rstrip()
        self._format_idle_tooltip = f"Format ({hint})" if hint else "Format"
        button.setText(self._format_idle_text)
        button.setToolTip(self._format_idle_tooltip)
        button.set_labels((self._format_idle_text, FORMATTING_MESSAGE))
        self._format_button = button
        self._actions.format.enabledChanged.connect(self._on_format_action_enabled)
        button.setEnabled(self._actions.format.isEnabled())
        button.clicked.connect(self._actions.format.trigger)
        return button

    def _build_toolbar(self) -> QWidget:
        return MainToolbar(self._actions, self._format_button, self._editing)

    def _build_status_bar(self) -> StatusBar:
        status = StatusBar()
        self.setStatusBar(status)
        status.state_clicked.connect(self._on_status_clicked)
        return status

    def _assemble(self, toolbar: QWidget) -> None:
        central = QWidget()
        central.setObjectName("central")
        central.setAutoFillBackground(True)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(toolbar)
        host = QWidget()
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(16, 8, 16, 8)
        host_layout.addWidget(self._problems_splitter)
        layout.addWidget(host, 1)
        self.setCentralWidget(central)

    def _connect(self) -> None:
        actions = self._actions
        actions.about.triggered.connect(self._show_about)
        actions.open_file.triggered.connect(self._open_file)
        for stub in actions.stubs():
            stub.triggered.connect(self._not_yet)
        actions.quit.triggered.connect(QApplication.quit)
        actions.undo.triggered.connect(self._undo)
        actions.redo.triggered.connect(self._redo)
        actions.cut.triggered.connect(self._cut)
        actions.copy.triggered.connect(self._copy)
        actions.paste.triggered.connect(self._paste)
        actions.select_all.triggered.connect(self._select_all)
        actions.word_wrap.toggled.connect(self._set_word_wrap)
        actions.show_whitespace.toggled.connect(self._set_show_whitespace)
        actions.bigger.triggered.connect(self._zoom_in)
        actions.smaller.triggered.connect(self._zoom_out)
        actions.actual_size.triggered.connect(self._actual_size)
        actions.full_screen.toggled.connect(self._set_full_screen)
        actions.format.triggered.connect(self._format)
        actions.convert_python.triggered.connect(self._convert_python)
        actions.escape_string.triggered.connect(self._escape_string)
        actions.unescape_string.triggered.connect(self._unescape_string)
        actions.show_problems.toggled.connect(self._on_show_problems_toggled)
        actions.stack_panes.toggled.connect(self._on_stack_panes_toggled)
        actions.sort_keys.toggled.connect(save_sort_keys)
        actions.next_problem.triggered.connect(self._next_problem)
        actions.previous_problem.triggered.connect(self._previous_problem)
        actions.expand_all.triggered.connect(self._expand_all)
        actions.collapse_all.triggered.connect(self._collapse_all)
        actions.fold.triggered.connect(self._output_pane.editor.fold_at_cursor)
        actions.unfold.triggered.connect(self._output_pane.editor.unfold_at_cursor)
        self._output_pane.editor.folds_changed.connect(self._sync_fold_actions)
        self._output_pane.editor.fold_toggled.connect(self._announce_fold)
        self._status.cancel_clicked.connect(self._cancel_format)
        self._session.write_progress.connect(self._on_write_progress)
        self._session.succeeded.connect(self._on_format_succeeded)
        self._session.failed.connect(self._on_format_failed)
        self._session.problems_found.connect(self._on_problems)
        self._problems_pane.jump_requested.connect(self._jump_to_problem)
        self._problems_pane.close_requested.connect(self._close_problems)
        self._problems_pane.minimize_requested.connect(self._minimize_problems)
        self._problems_pane.expand_requested.connect(self._expand_problems)
        self._problems_pane.replace_tabs_requested.connect(self._replace_tabs)
        self._problems_pane.focus_editor_requested.connect(self._focus_input)
        self._input_pane.editor.problem_marker_clicked.connect(
            self._problems_pane.select_line
        )
        self._order_focus()

        self._input_pane.editor.textChanged.connect(self._sync_input_state)
        self._output_pane.editor.textChanged.connect(self._sync_output_state)
        self._input_pane.format_changed.connect(self._sync_direction)
        self._output_pane.format_changed.connect(self._sync_direction)
        self._bind_pane(self._input_pane)
        self._bind_pane(self._output_pane)
        for editor in self._editors():
            editor.undoAvailable.connect(self._sync_undo_actions)
            editor.redoAvailable.connect(self._sync_undo_actions)
        self._sync_undo_actions()

        input_paste = self._input_pane.paste_button
        footer_paste = self._input_pane.footer_paste_button
        open_button = self._input_pane.open_button
        clear_button = self._input_pane.clear_button
        if input_paste is not None:
            input_paste.clicked.connect(self._paste_input)
        if footer_paste is not None:
            footer_paste.clicked.connect(self._paste_input)
        if open_button is not None:
            open_button.clicked.connect(actions.open_file.trigger)
        if clear_button is not None:
            clear_button.clicked.connect(self._clear_input)

        options = self._output_pane.options_button
        copy_output = self._output_pane.copy_button
        if options is not None:
            options.clicked.connect(self._not_yet)
        if copy_output is not None:
            copy_output.clicked.connect(self._copy_output)

        self._shield_unbuilt_controls()
        application = QApplication.instance()
        if isinstance(application, QApplication):
            application.focusChanged.connect(self._on_focus_changed)
            application.installEventFilter(self)

    def _shield_unbuilt_controls(self) -> None:
        """Keep Coming soon on disabled buttons that are not toolbar actions."""
        actions = self._actions
        open_button = self._input_pane.open_button
        if open_button is not None:
            coming_soon_wrapper(open_button, actions.open_file)
        for pane in (self._input_pane, self._output_pane):
            coming_soon_wrapper(pane.new_tab_button, actions.new_tab)
            coming_soon_wrapper(pane.close_tab_button, actions.close_tab)
            coming_soon_wrapper(pane.pop_out_button, actions.pop_out)
            pane.pop_out_button.sync_tool_tip()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Esc cancels the running format from whichever editor has focus."""
        if not isinstance(watched, QObject):
            return False
        if (
            self._formatting
            and event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
            and event.key() == Qt.Key.Key_Escape
            and _is_inside(watched if isinstance(watched, QWidget) else None, self)
        ):
            self._cancel_format()
            return True
        return super().eventFilter(watched, event)

    def _bind_pane(self, pane: PaneWidget) -> None:
        pane.tab_strip.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        pane.tab_strip.customContextMenuRequested.connect(self._show_tab_menu)
        pane.new_tab_button.clicked.connect(self._actions.new_tab.trigger)
        pane.close_tab_button.clicked.connect(self._actions.close_tab.trigger)
        pane.pop_out_button.clicked.connect(self._actions.pop_out.trigger)
        viewport = pane.editor.viewport()
        viewport.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        viewport.customContextMenuRequested.connect(self._show_editor_menu)

    def _show_tab_menu(self, pos: QPoint) -> None:
        source = self.sender()
        if isinstance(source, QWidget):
            self._tab_menu.popup(source.mapToGlobal(pos))

    def _show_editor_menu(self, pos: QPoint) -> None:
        source = self.sender()
        if not isinstance(source, QWidget):
            return
        menu = self._editor_menu
        if source is self._output_pane.editor.viewport():
            menu = self._output_editor_menu
        menu.popup(source.mapToGlobal(pos))

    def _show_about(self) -> None:
        show_about_dialog(self)

    def _not_yet(self) -> None:
        self.statusBar().showMessage(NOT_YET_MESSAGE, _NOT_YET_MS)

    def _format(self) -> None:
        if self._formatting or self._size_warning is not None:
            return
        if self._loading_path is not None:
            return
        text = self._input_pane.editor.toPlainText()
        if _is_blank(text):
            return
        source = self._input_pane.format_name()
        size = _utf8_size(text)
        if size >= _warning_bytes(source) and not self._size_warning_accepted:
            self._open_size_warning(text, size)
            return
        self._start_format(text)

    def _open_size_warning(self, text: str, size: int) -> None:
        """Ask once, with a window-modal sheet, before a large format."""
        box = QMessageBox(self)
        box.setObjectName("sizeWarning")
        box.setWindowTitle("Galley")
        box.setIcon(QMessageBox.Icon.NoIcon)
        box.setWindowModality(Qt.WindowModality.WindowModal)
        box.setText(f"Format this {si_size(size)} document?")
        box.setInformativeText(SIZE_WARNING_DETAIL)
        anyway = box.addButton("Format anyway", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(anyway)
        box.setEscapeButton(cancel)
        self._size_warning = box

        def _choose() -> None:
            accepted = box.clickedButton() is anyway
            self._size_warning = None
            box.deleteLater()
            if not accepted:
                return
            self._size_warning_accepted = True
            if _is_blank(self._input_pane.editor.toPlainText()):
                return
            self._start_format(text)

        box.finished.connect(_choose)
        box.open()

    def _start_format(self, text: str) -> None:
        self._cancel_status_timer.stop()
        self._formatting = True
        self._format_started_s = _monotonic()
        self._reveal_timer.stop()
        self._status_before_format = self._status.state_text()
        self._output_before_format = self._output_pane.editor.toPlainText()
        self._format_source = self._input_pane.format_name()
        self._format_target = self._output_pane.format_name()
        busy, progress = _busy_progress_bytes(self._format_source, text)
        size = _utf8_size(text)
        self._size_forces_busy = size >= busy
        self._size_forces_progress = size >= progress
        self._set_editors_locked(True)
        self._status.set_busy(Busy.IDLE)
        self._sync_format_button()
        self._apply_deferred_reveal()
        self._accepted_job_id = None
        self._session.start()
        self._format_sorted = self._actions.sort_keys.isChecked()
        self._session.submit(
            text,
            self._format_source,
            self._format_target,
            sort_keys=self._format_sorted,
        )
        self._accepted_job_id = self._session.active_job_id
        if self._accepted_job_id is None:
            self._finish_format(FORMAT_NOT_STARTED_MESSAGE)

    def _convert_python(self) -> None:
        """Convert the input literal into the output editor and show notices.

        This runs on the UI thread, so Input must be no larger than the limit
        for the Output format. Above it, Output is left unchanged.
        """
        if self._formatting:
            return
        text = self._input_pane.editor.toPlainText()
        if _is_blank(text):
            return
        target = self._output_pane.format_name()
        size = _utf8_size(text)
        yaml_target = target.casefold() == "yaml"
        limit = PYTHON_TO_YAML_BYTES if yaml_target else PYTHON_TO_JSON_BYTES
        if size > limit:
            self._status.set_state(convert_python_too_large_message(si_size(size)))
            return
        result = convert_python(text, target)
        if isinstance(result, ConvertError):
            self._status.set_state(result.message)
            return
        self._output_pane.editor.setPlainText(result.text)
        if result.notices:
            self._status.set_state(
                " ".join(notice.message for notice in result.notices)
            )
            return
        self._status.set_state(f"Converted to {target}.")

    def _escape_string(self) -> None:
        """Write Input as one escaped JSON string to Output. Input is unchanged.

        This runs on the UI thread, so Input must be smaller than the size
        where Format shows its busy indicator for the Input format.
        """
        if self._formatting:
            return
        text = self._input_pane.editor.toPlainText()
        if text == "":
            return
        source = self._input_pane.format_name()
        size = _utf8_size(text)
        limit = _busy_progress_bytes(source, text)[0]
        if size >= limit:
            self._status.set_state(
                escape_too_large_message(si_size(size), si_size(limit))
            )
            return
        self._show_string_result(escape_input(text, source))

    def _unescape_string(self) -> None:
        """Format the JSON inside an escaped Input string into Output.

        This runs on the UI thread, so Input must be smaller than the size
        where Format shows its busy indicator for the Output format.
        """
        if self._formatting:
            return
        text = self._input_pane.editor.toPlainText()
        if text == "":
            return
        target = self._output_pane.format_name()
        size = _utf8_size(text)
        limit = _busy_progress_bytes(target, text)[0]
        if size >= limit:
            self._status.set_state(
                unescape_too_large_message(si_size(size), si_size(limit))
            )
            return
        self._show_string_result(unescape_input(text, target))

    def _show_string_result(self, result: StringResult) -> None:
        if result.problems:
            self._apply_problems(result.problems)
            return
        if result.output is None:
            self._status.set_state(result.status)
            return
        _replace_as_one_undo(self._output_pane.editor, result.output)
        self._clear_problems()
        self._status.set_state(result.status)

    def _cancel_format(self) -> None:
        """Drop the result, restore the previous output, then kill the worker."""
        if not self._formatting:
            return
        self._restore_output()
        self._status.set_state(CANCELLED_MESSAGE)
        self._cancel_status_timer.start(CANCELED_STATUS_MS)
        self._status.set_busy(Busy.IDLE)
        self._formatting = False
        self._size_forces_busy = False
        self._size_forces_progress = False
        self._reveal_timer.stop()
        self._set_editors_locked(False)
        self._sync_format_button()
        self._session.cancel()

    def _clear_canceled_status(self) -> None:
        if self._formatting or self._status.state_text() != CANCELLED_MESSAGE:
            return
        restored = self._status_before_format
        count = self._problems_pane.count()
        if count and restored == problem_count_text(count):
            self._status.set_problem_count(count)
            return
        self._status.set_state(restored)

    def _on_write_progress(self, _written: int, _total: int) -> None:
        if not self._formatting:
            return
        self._apply_deferred_reveal()

    def _apply_deferred_reveal(self) -> None:
        """Show the busy indicator, then the progress bar, on time or size."""
        if not self._formatting:
            return
        elapsed = _elapsed_ms(self._format_started_s)
        progress_due = self._size_forces_progress or _reveal_due(
            PROGRESS_BAR_REVEAL_MS, self._format_started_s
        )
        busy_due = self._size_forces_busy or _reveal_due(
            SPINNER_REVEAL_MS, self._format_started_s
        )
        if progress_due:
            self._status.set_busy(Busy.PROGRESS)
            self._status.set_state(_formatting_status(elapsed))
        elif busy_due:
            self._status.set_busy(Busy.SPINNER)
            self._status.set_state(FORMATTING_MESSAGE)
        self._arm_reveal_timer()

    def _arm_reveal_timer(self) -> None:
        if not self._formatting or self._reveal_timer.isActive():
            return
        elapsed = _elapsed_ms(self._format_started_s)
        remaining: list[float] = []
        if not (
            self._size_forces_busy
            or _reveal_due(SPINNER_REVEAL_MS, self._format_started_s)
        ):
            remaining.append(SPINNER_REVEAL_MS - elapsed)
        if not (
            self._size_forces_progress
            or _reveal_due(PROGRESS_BAR_REVEAL_MS, self._format_started_s)
        ):
            remaining.append(PROGRESS_BAR_REVEAL_MS - elapsed)
        if elapsed < ELAPSED_STATUS_MS:
            remaining.append(ELAPSED_STATUS_MS - elapsed)
        elif self._status.progress_visible():
            next_second = (int(elapsed // 1000) + 1) * 1000
            remaining.append(next_second - elapsed)
        if not remaining:
            return
        self._reveal_timer.start(max(0, int(min(remaining))))

    def _on_format_succeeded(self, text: str) -> None:
        if not self._formatting:
            return
        if self._session.finished_job_id != self._accepted_job_id:
            return
        self._set_editors_locked(False)
        output = self._output_pane.editor
        _replace_as_one_undo(output, text)
        # The worker found the fold ranges with the text (Erik's #64 plan).
        output.set_fold_ranges(*unpack_ranges(self._session.finished_folds))
        self._clear_problems()
        self._finish_format(self._success_status())

    def _on_format_failed(self, message: str) -> None:
        if not self._formatting:
            return
        # Format was pressed while a cancelled worker was still being reaped.
        # The banner answers that press. The job is sent to the replacement
        # and still owns this format, so a later success can replace the banner.
        if self._session.has_pending_job:
            self._reveal_timer.stop()
            self._status.set_state(message)
            return
        self._finish_format(message)

    def _on_problems(self, problems: object) -> None:
        if not self._formatting or not isinstance(problems, tuple):
            return
        rows = tuple(item for item in problems if isinstance(item, DisplayProblem))
        if not rows:
            return
        if self._session.has_pending_job:
            self._reveal_timer.stop()
            self._apply_problems(rows)
            return
        count = sum(1 for row in rows if not row.is_note())
        self._finish_format(problem_count_text(count))
        self._apply_problems(rows)

    def _finish_format(self, status: str) -> None:
        self._cancel_status_timer.stop()
        self._formatting = False
        self._size_forces_busy = False
        self._size_forces_progress = False
        self._reveal_timer.stop()
        self._status.set_state(status)
        self._status.set_busy(Busy.IDLE)
        self._set_editors_locked(False)
        self._sync_format_button()

    def _restore_output(self) -> None:
        editor = self._output_pane.editor
        if editor.toPlainText() == self._output_before_format:
            return
        editor.setPlainText(self._output_before_format)

    def _set_editors_locked(self, locked: bool) -> None:
        self._input_pane.editor.setReadOnly(locked)
        self._output_pane.editor.setReadOnly(locked)

    def _apply_problems(self, rows: tuple[DisplayProblem, ...]) -> None:
        """Fill the pane, mark the gutter, count, and tell screen readers.

        Only listed rows get a gutter marker, so every marker has a row to
        select. The count is every problem.
        """
        pane = self._problems_pane
        pane.set_problems(rows)
        shown = pane.shown()
        messages: dict[int, list[str]] = {}
        for row in shown:
            messages.setdefault(row.lineno, []).append(row.full_message())
        self._input_pane.editor.set_problem_messages(
            {lineno: "\n".join(texts) for lineno, texts in messages.items()}
        )
        self._layout_problems()
        self._status.set_problem_count(pane.count())
        if shown:
            self._status.announce(announcement_text(pane.count(), shown[0]))

    def _clear_problems(self) -> None:
        self._problems_pane.set_problems(())
        self._input_pane.editor.set_problem_lines(set())
        self._layout_problems()

    def _next_problem(self) -> None:
        self._step_problem(forward=True)

    def _previous_problem(self) -> None:
        self._step_problem(forward=False)

    def _step_problem(self, *, forward: bool) -> None:
        """Go to the listed problem after or before the Input cursor, wrapping."""
        shown = self._problems_pane.shown()
        if not shown:
            return
        here = self._input_pane.editor.cursor_place()
        places = [(row.lineno, max(row.colno, 1)) for row in shown]
        if forward:
            later = [index for index, place in enumerate(places) if place > here]
            index = later[0] if later else 0
        else:
            earlier = [index for index, place in enumerate(places) if place < here]
            index = earlier[-1] if earlier else len(places) - 1
        self._problems_pane.select_row(index)
        self._jump_to_problem(shown[index].lineno, shown[index].colno)

    def _jump_to_problem(self, lineno: int, colno: int = 0) -> None:
        editor = self._input_pane.editor
        editor.reveal_line(lineno, colno)
        self._active_editor = editor
        self._update_caption()
        self._update_focus_ring()

    def _close_problems(self) -> None:
        self._set_problems_state(STATE_CLOSED)

    def _minimize_problems(self) -> None:
        self._set_problems_state(STATE_MINIMIZED)

    def _expand_problems(self) -> None:
        self._set_problems_state(STATE_OPEN)

    def _on_show_problems_toggled(self, checked: bool) -> None:
        self._set_problems_state(STATE_OPEN if checked else STATE_CLOSED)

    def _restore_stacked_panes(self) -> None:
        stacked = load_stacked_panes()
        action = self._actions.stack_panes
        action.blockSignals(True)
        action.setChecked(stacked)
        action.blockSignals(False)
        self._apply_stacked_layout(stacked)

    def _restore_sort_keys(self) -> None:
        action = self._actions.sort_keys
        action.blockSignals(True)
        action.setChecked(load_sort_keys())
        action.blockSignals(False)

    def _on_stack_panes_toggled(self, stacked: bool) -> None:
        self._apply_stacked_layout(stacked)
        save_stacked_panes(stacked)
        self._layout_problems()

    def _apply_stacked_layout(self, stacked: bool) -> None:
        splitter = self._splitter
        if splitter is None:
            return
        target = Qt.Orientation.Vertical if stacked else Qt.Orientation.Horizontal
        if splitter.orientation() == target:
            return
        views = tuple(_capture_editor(editor) for editor in self._editors())
        splitter.apply_stacked(stacked)
        self._restore_editor_views(views)
        # A later resize scrolls the cursor into view. Put the scroll back
        # once that layout pass has finished.
        QTimer.singleShot(0, self, lambda: self._restore_editor_views(views))

    def _restore_editor_views(self, views: tuple[_EditorView, ...]) -> None:
        for editor, view in zip(self._editors(), views, strict=True):
            _restore_editor(editor, view)

    def _on_status_clicked(self) -> None:
        if self._problems_pane.count() == 0:
            return
        self._set_problems_state(STATE_OPEN)

    def _replace_tabs(self) -> None:
        if not SHOW_REPLACE_TABS_BUTTON:
            return
        replace_leading_tabs(self._input_pane.editor)

    def _set_problems_state(self, state: str) -> None:
        self._problems_state = state
        save_problems_preferences(state, self._problems_width)
        self._sync_problems_action()
        self._layout_problems()

    def _sync_problems_action(self) -> None:
        action = self._actions.show_problems
        checked = self._problems_state != STATE_CLOSED
        if action.isChecked() == checked:
            return
        action.blockSignals(True)
        action.setChecked(checked)
        action.blockSignals(False)

    def _layout_problems(self) -> None:
        self._applying_problems_layout = True
        try:
            self._place_problems_pane()
        finally:
            self._applying_problems_layout = False

    def _place_problems_pane(self) -> None:
        pane = self._problems_pane
        splitter = self._problems_splitter
        handle = splitter.handle(1)
        visible = pane.count() > 0 and self._problems_state != STATE_CLOSED
        minimized = visible and self._problems_state == STATE_MINIMIZED
        pane.set_minimized(minimized)
        if not visible:
            pane.setMinimumWidth(0)
            pane.setMaximumWidth(0)
            pane.hide()
            handle.hide()
            return
        pane.show()
        handle.show()
        total = max(splitter.width(), 1)
        if minimized:
            pane.setMaximumWidth(STRIP_WIDTH)
            pane.setMinimumWidth(STRIP_WIDTH)
            splitter.setSizes([STRIP_WIDTH, max(0, total - STRIP_WIDTH)])
            return
        pane.setMaximumWidth(16_777_215)
        pane.setMinimumWidth(MIN_WIDTH)
        # Shrink toward MIN_WIDTH before the tab strips shed (#91 D2).
        # The saved width stays as the user left it.
        room = total - splitter.handleWidth() - self._panes_full_width()
        width = min(self._problems_width, max(MIN_WIDTH, min(room, total - 240)))
        splitter.setSizes([width, max(0, total - width)])

    def _panes_full_width(self) -> int:
        """Input / Output width at which neither tab strip sheds, split evenly."""
        widths = [
            pane.full_strip_width() for pane in (self._input_pane, self._output_pane)
        ]
        splitter = self._splitter
        if splitter is None or splitter.orientation() == Qt.Orientation.Vertical:
            return max(widths)
        return 2 * max(widths) + splitter.handleWidth()

    def _on_problems_splitter_moved(self, pos: int, _index: int) -> None:
        sizes = self._problems_splitter.sizes()
        width = sizes[0] if sizes else pos
        self._remember_problems_width(width)

    def _remember_problems_width(self, width: int) -> None:
        if self._applying_problems_layout:
            return
        if self._problems_state != STATE_OPEN or width < MIN_WIDTH:
            return
        self._problems_width = width
        save_problems_preferences(self._problems_state, width)

    def _order_focus(self) -> None:
        """Move from the Problems list to Input, then Output."""
        problems = self._problems_pane.list_widget()
        minimize = self._problems_pane.minimize_button()
        close = self._problems_pane.close_button()
        QWidget.setTabOrder(minimize, close)
        QWidget.setTabOrder(close, problems)
        QWidget.setTabOrder(problems, self._input_pane.editor)
        QWidget.setTabOrder(self._input_pane.editor, self._output_pane.editor)

    def _success_status(self) -> str:
        source = self._format_source.casefold()
        target = self._format_target.casefold()
        if source != target:
            status = "Converted"
        elif target == "yaml":
            status = "Valid YAML"
        else:
            status = "Valid JSON"
        if self._format_sorted:
            return f"{status} {SORTED_KEYS_SUFFIX}"
        return status

    def _focus_input(self) -> None:
        editor = self._input_pane.editor
        self.activateWindow()
        editor.setFocus()
        self._active_editor = editor
        self._update_caption()
        self._update_focus_ring()

    def _return_focus_to_input(self) -> None:
        """Focus Input after the dialog that held it has closed.

        A message box clears focus while it is tearing down. Asking on the
        next turn puts the caret back in Input.
        """
        QTimer.singleShot(0, self, self._focus_input)

    def _on_focus_changed(self, _old: QWidget | None, new: QWidget | None) -> None:
        editor = _editor_from(new)
        if editor is not None:
            self._active_editor = editor
            self._update_caption()
            self._sync_undo_actions()
        self._update_focus_ring()

    def _update_caption(self) -> None:
        if self._active_editor is self._output_pane.editor:
            pane = self._output_pane
        else:
            pane = self._input_pane
        self._editing.setText(_editing_text(pane.title))

    def _update_focus_ring(self) -> None:
        focused = QApplication.focusWidget()
        self._input_pane.set_keyboard_focus(_is_inside(focused, self._input_pane))
        self._output_pane.set_keyboard_focus(_is_inside(focused, self._output_pane))

    def _target_editor(self) -> CodeEditor:
        focused = _editor_from(QApplication.focusWidget())
        if focused is not None:
            return focused
        return self._active_editor

    def _editors(self) -> tuple[CodeEditor, CodeEditor]:
        return self._input_pane.editor, self._output_pane.editor

    def _sync_undo_actions(self, _available: bool = False) -> None:
        """Enable Undo and Redo from the focused editor's own undo stack."""
        document = self._target_editor().document()
        self._actions.undo.setEnabled(document.isUndoAvailable())
        self._actions.redo.setEnabled(document.isRedoAvailable())

    def _undo(self) -> None:
        self._target_editor().undo()

    def _redo(self) -> None:
        self._target_editor().redo()

    def _cut(self) -> None:
        self._target_editor().cut()

    def _copy(self) -> None:
        self._target_editor().copy()

    def _paste(self) -> None:
        self._target_editor().paste()

    def _paste_input(self) -> None:
        self._input_pane.editor.paste()
        self._input_pane.editor.setFocus()

    def _select_all(self) -> None:
        self._target_editor().selectAll()

    def _clear_input(self) -> None:
        """Empty Input and put the status bar back to its empty state (#91)."""
        self._input_pane.editor.clear()
        self._input_pane.editor.setFocus()
        self._clear_problems()
        self._status.set_counts("")
        if not self._formatting:
            self._status.set_state("Ready")

    def can_open(self) -> bool:
        """True when a file may replace the source pane's text now.

        Never while a format job runs, a file is loading, or a sheet is up.
        Menu, shortcut, button, and later drop and recents all ask this.
        """
        return not (
            self._formatting
            or self._loading_path is not None
            or self._replace_box is not None
            or self._size_warning is not None
        )

    def open_path(self, path: Path) -> None:
        """Load ``path`` into the source pane. Opening never formats.

        Every way of opening a file comes through here. Unsaved edits are
        confirmed first.
        """
        if not self.can_open():
            return
        pane = self._source_pane()
        if input_needs_replace(pane.editor.toPlainText(), pane.document):
            self._ask_replace_input(path)
            return
        self._start_loading(path)

    def cancel_open(self) -> None:
        """Drop the file being read. The read itself runs to its end."""
        if self._loading_path is None:
            return
        self._open_job += 1
        self._open_reveal_timer.stop()
        self._loading_path = None
        self._status.set_busy(Busy.IDLE)
        self._sync_format_button()

    def _source_pane(self) -> PaneWidget:
        """The pane Open fills: the one whose spec is a source (#73)."""
        for pane in (self._input_pane, self._output_pane):
            if pane.spec.kind is PaneKind.SOURCE:
                return pane
        raise AssertionError("no source pane")

    def _open_file(self) -> None:
        if not self.can_open():
            return
        chosen = self._choose_open_path()
        if chosen is None:
            self._return_focus_to_input()
            return
        save_last_open_dir(chosen.parent)
        self.open_path(chosen)

    def _choose_open_path(self) -> Path | None:
        filename, _selected = QFileDialog.getOpenFileName(
            self,
            OPEN_DIALOG_CAPTION,
            load_last_open_dir(),
            OPEN_FILE_FILTER,
        )
        if not filename:
            return None
        return Path(filename)

    def _ask_replace_input(self, path: Path) -> None:
        """Ask before replacing unsaved Input. Cancel leaves it unchanged."""
        box = QMessageBox(self)
        box.setObjectName("replaceInput")
        # QMessageBox drops its title on macOS; keep one for screen readers.
        QWidget.setWindowTitle(box, "Galley")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowModality(Qt.WindowModality.WindowModal)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(replace_input_text(path.name))
        replace = box.addButton(REPLACE_BUTTON, QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        replace.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        cancel.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        QWidget.setTabOrder(replace, cancel)
        box.setDefaultButton(replace)
        box.setEscapeButton(cancel)
        self._replace_box = box
        self._sync_open_actions()

        def _choose() -> None:
            accepted = box.clickedButton() is replace
            self._replace_box = None
            box.deleteLater()
            self._sync_open_actions()
            if not accepted or not self.can_open():
                self._return_focus_to_input()
                return
            self._start_loading(path)

        box.finished.connect(_choose)
        box.open()

    def _start_loading(self, path: Path) -> None:
        """Read ``path`` off the UI thread. Open and Format wait for it."""
        self._opened_status_timer.stop()
        self._open_job += 1
        self._loading_path = path
        self._sync_format_button()
        self._open_reveal_timer.start(OPEN_REVEAL_MS)
        self.open_started.emit()
        self._reader.read(self._open_job, path)

    def _show_opening(self) -> None:
        path = self._loading_path
        if path is None:
            return
        self._status.set_busy(Busy.SPINNER, OPENING_ACCESSIBLE_NAME)
        self._status.set_state(opening_text(path.name), str(path))

    def _on_file_read(self, job: int, result: object) -> None:
        path = self._loading_path
        if job != self._open_job or path is None:
            return
        self._open_reveal_timer.stop()
        self._loading_path = None
        self._status.set_busy(Busy.IDLE)
        self._sync_format_button()
        if isinstance(result, LoadError):
            self._status.set_state(open_error_text(path.name, result), str(path))
            self.open_failed.emit(result.kind.value, path.name)
            self._return_focus_to_input()
            return
        if isinstance(result, LoadedDocument):
            self._apply_opened_file(path, result)

    def _apply_opened_file(self, path: Path, loaded: LoadedDocument) -> None:
        """Put the text in the source pane as one undo step.

        The output and the Problems pane are cleared. Format's size answer
        is left alone; Format asks for itself.
        """
        pane = self._source_pane()
        editor = pane.editor
        _replace_as_one_undo(editor, loaded.text)
        cursor = editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        editor.setTextCursor(cursor)
        self._output_pane.editor.clear()
        self._clear_problems()
        if loaded.fmt is not None:
            _set_format(pane, loaded.fmt)
        document = InputDocument(
            path, editor.toPlainText(), loaded.had_bom, loaded.newline
        )
        pane.set_document(document)
        self.setWindowTitle(input_window_title(path.name))
        self.setWindowFilePath(str(path))
        status = opened_text(path.name, si_size(loaded.size))
        self._opened_status = status
        self._status.set_state(status, str(path))
        self._opened_status_timer.start(OPENED_STATUS_MS)
        self.open_finished.emit(document)
        self._return_focus_to_input()

    def _clear_opened_status(self) -> None:
        if self._formatting or self._status.state_text() != self._opened_status:
            return
        self._status.set_state("Ready")

    def _sync_open_actions(self) -> None:
        """Block Open while a file loads or a format runs, and say why."""
        action = self._actions.open_file
        if is_unbuilt(action):
            return
        available = self.can_open()
        action.setEnabled(available)
        action.setToolTip(OPEN_WHILE_FORMATTING_TOOLTIP if self._formatting else "")
        open_button = self._source_pane().open_button
        if open_button is not None:
            open_button.setEnabled(available)
            open_button.setToolTip(action.toolTip())

    def _copy_output(self) -> None:
        QApplication.clipboard().setText(self._output_pane.editor.toPlainText())

    def _set_word_wrap(self, checked: bool) -> None:
        mode = (
            CodeEditor.LineWrapMode.WidgetWidth
            if checked
            else CodeEditor.LineWrapMode.NoWrap
        )
        for editor in self._editors():
            editor.setLineWrapMode(mode)

    def _set_show_whitespace(self, checked: bool) -> None:
        """Record the View choice. Each editor shows dots when its lines allow."""
        for editor in self._editors():
            editor.set_whitespace_wanted(checked)

    def _set_syntax_colors(self, checked: bool) -> None:
        """Record whether colors are wanted; the editors gate very long lines.

        There is no View toggle yet. The long-line notice's "Turn on anyway"
        will call this with True.
        """
        for editor in self._editors():
            editor.set_syntax_colors_wanted(checked)

    def _zoom_in(self) -> None:
        self._zoom(1)

    def _zoom_out(self) -> None:
        self._zoom(-1)

    def _zoom(self, delta: int) -> None:
        for editor in self._editors():
            font = editor.font()
            size = font.pointSize()
            if size < 0:
                size = CODE_POINT_SIZE
            font.setPointSize(max(8, min(48, size + delta)))
            editor.setFont(font)
            editor.refresh_gutter()

    def _actual_size(self) -> None:
        for editor in self._editors():
            font = editor.font()
            font.setPointSize(CODE_POINT_SIZE)
            editor.setFont(font)
            editor.refresh_gutter()

    def _set_full_screen(self, checked: bool) -> None:
        if checked == self.isFullScreen():
            return
        if checked:
            self.showFullScreen()
        else:
            self.showNormal()

    def _on_format_action_enabled(self, _enabled: bool) -> None:
        self._sync_format_button()

    def _sync_format_button(self, text: str | None = None) -> None:
        """Block Format, Convert, Escape, and Unescape for the whole job.

        The button keeps its full accent face while working and reads
        ``Formatting…`` (spec §5.2). The disabled action blocks re-entry.
        ``text`` is the input when the caller already has it; copying a
        long document out of the editor is not free.
        """
        if text is None:
            text = self._input_pane.editor.toPlainText()
        has_input = not _is_blank(text)
        loading = self._loading_path is not None
        available = has_input and not self._formatting and not loading
        action = self._actions.format
        if action.isEnabled() != available:
            action.setEnabled(available)
        button = self._format_button
        button.setEnabled(has_input or self._formatting)
        if self._formatting:
            button.setText(FORMATTING_MESSAGE)
            button.setAccessibleName(FORMATTING_MESSAGE)
            button.setAccessibleDescription(FORMAT_BUSY_DESCRIPTION)
            button.setToolTip(FORMAT_BUSY_TOOLTIP)
            button.setCursor(Qt.CursorShape.BusyCursor)
        else:
            button.setText(self._format_idle_text)
            button.setAccessibleName(FORMAT_ACCESSIBLE_NAME)
            button.setAccessibleDescription("")
            button.setToolTip(self._format_idle_tooltip)
            button.unsetCursor()
        self._actions.convert_python.setEnabled(available)
        self._actions.escape_string.setEnabled(available)
        self._actions.unescape_string.setEnabled(available)
        self._sync_open_actions()
        self._sync_fold_actions()

    def _sync_fold_actions(self) -> None:
        """Fold commands work on Output while it has blocks and no job runs."""
        editor = self._output_pane.editor
        foldable = editor.has_fold_ranges()
        actions = self._actions
        for action in (actions.fold, actions.unfold):
            action.setEnabled(foldable and not self._formatting)
        for action, keys in (
            (actions.expand_all, EXPAND_ALL),
            (actions.collapse_all, COLLAPSE_ALL),
        ):
            action.setEnabled(foldable and not self._formatting)
            if foldable:
                tip = f"{action.text()} blocks in Output ({chord_text(keys)})"
            else:
                tip = f"{action.text()}: {NOTHING_TO_FOLD}"
            if action.toolTip() != tip:
                action.setToolTip(tip)
        description = ""
        if foldable:
            description = (
                f"Fold ({chord_text(FOLD)}) and Unfold ({chord_text(UNFOLD)}) "
                "act on the line with the caret."
            )
        if editor.accessibleDescription() != description:
            editor.setAccessibleDescription(description)

    def _expand_all(self) -> None:
        count = self._output_pane.editor.expand_all()
        self._status.set_state(f"Expanded {_blocks(count)}")

    def _collapse_all(self) -> None:
        count = self._output_pane.editor.collapse_all()
        self._status.set_state(f"Collapsed {_blocks(count)}")

    def _announce_fold(self, item: object, folded: bool) -> None:
        """Say what a fold did in the status bar, which screen readers read."""
        if isinstance(item, FoldRange):
            self._status.set_state(fold_status_text(item, folded))

    def _sync_input_state(self) -> None:
        editor = self._input_pane.editor
        text = editor.toPlainText()
        empty = _is_blank(text)
        if empty:
            self._size_warning_accepted = False
        self._sync_format_button(text)
        clear_button = self._input_pane.clear_button
        if clear_button is not None:
            # Whitespace still needs clearing, so Clear follows the raw text.
            clear_button.setEnabled(text != "")
        self._input_pane.set_empty_visible(empty)
        if empty:
            self._input_pane.count_label.setText("Empty")
            return
        self._input_pane.count_label.setText(
            f"{_line_label(editor.blockCount())} · {len(text)} characters"
        )

    def _sync_output_state(self) -> None:
        editor = self._output_pane.editor
        text = editor.toPlainText()
        empty = text == ""
        copy_button = self._output_pane.copy_button
        if copy_button is not None:
            copy_button.setEnabled(not empty)
        self._output_pane.set_empty_visible(empty)
        if empty:
            self._output_pane.count_label.setText("")
            return
        self._output_pane.count_label.setText(_line_label(editor.blockCount()))

    def _sync_direction(self) -> None:
        left = self._input_pane.format_name()
        right = self._output_pane.format_name()
        self._status.set_info(f"{left} → {right}")


def _is_blank(text: str) -> bool:
    """True for empty or whitespace-only input. Both count as empty (#91)."""
    return text.strip() == ""


def _set_format(pane: PaneWidget, name: str) -> None:
    combo = pane.format_combo
    index = combo.findText(name)
    if index >= 0:
        combo.setCurrentIndex(index)


def _platform_name() -> str:
    return QApplication.platformName()


def _shows_own_full_screen_item(platform: str | None = None) -> bool:
    """Whether View gets Galley's own Enter Full Screen item (#91).

    macOS adds its own item to any menu titled View once the window can go
    full screen, so cocoa would show two. There the system item stands in
    (^⌘F still works). Offscreen and every other platform keep the spec's
    action. Only a real macOS run shows the system item: check it by hand.
    """
    name = _platform_name() if platform is None else platform
    return name != "cocoa"


def _add_menu(bar: QMenuBar, title: str) -> QMenu:
    menu = bar.addMenu(title)
    menu.setObjectName("menu" + title.replace(" ", ""))
    return menu


def _monotonic() -> float:
    return time.monotonic()


def _elapsed_ms(started_s: float) -> float:
    return (_monotonic() - started_s) * 1000


def _reveal_due(delay_ms: int, started_s: float) -> bool:
    if delay_ms <= 0:
        return True
    return _elapsed_ms(started_s) >= delay_ms


def _formatting_status(elapsed_ms: float) -> str:
    if elapsed_ms < ELAPSED_STATUS_MS:
        return FORMATTING_MESSAGE
    total_seconds = int(elapsed_ms // 1000)
    minutes, seconds = divmod(total_seconds, 60)
    return f"{FORMATTING_MESSAGE} {minutes}:{seconds:02d}"


def _utf8_size(text: str) -> int:
    return len(text.encode("utf-8"))


def _busy_progress_bytes(source: str, text: str) -> tuple[int, int]:
    if format_named(source) is YAML:
        if _has_tab(text):
            return YAML_TAB_BUSY_BYTES, YAML_TAB_PROGRESS_BYTES
        return YAML_BUSY_BYTES, YAML_PROGRESS_BYTES
    return JSON_BUSY_BYTES, JSON_PROGRESS_BYTES


def _warning_bytes(source: str) -> int:
    if format_named(source) is YAML:
        return YAML_WARNING_BYTES
    return JSON_WARNING_BYTES


def compare_needs_size_warning(
    left_text: str, left_source: str, right_text: str, right_source: str
) -> bool:
    """True when Compare should ask first, using Format's warning limits.

    The larger side decides when both sides share a format. With mixed
    formats, each side is held to its own format's limit, so a YAML side
    past ``YAML_WARNING_BYTES`` still asks beside a larger JSON side.
    """
    sides = ((left_text, left_source), (right_text, right_source))
    return any(_utf8_size(text) >= _warning_bytes(source) for text, source in sides)


def si_size(num_bytes: int) -> str:
    """Return ``num_bytes`` in SI units, such as ``1.2 MB``."""
    if num_bytes < _SI_KB:
        return f"{num_bytes} B"
    for scale, label in (
        (1_000_000_000_000, "TB"),
        (1_000_000_000, "GB"),
        (_SI_MB, "MB"),
        (_SI_KB, "KB"),
    ):
        if num_bytes < scale:
            continue
        value = num_bytes / scale
        if abs(value - round(value)) < 1e-9:
            return f"{int(round(value))} {label}"
        return f"{value:.1f} {label}"
    return f"{num_bytes} B"


def _replace_as_one_undo(editor: CodeEditor, text: str) -> None:
    """Replace the whole document as a single undo step."""
    cursor = editor.textCursor()
    cursor.beginEditBlock()
    cursor.select(QTextCursor.SelectionType.Document)
    cursor.insertText(text)
    cursor.endEditBlock()
    editor.setTextCursor(cursor)


def _capture_editor(editor: CodeEditor) -> _EditorView:
    cursor = editor.textCursor()
    return _EditorView(
        cursor.position(),
        cursor.anchor(),
        editor.verticalScrollBar().value(),
        editor.horizontalScrollBar().value(),
    )


def _restore_editor(editor: CodeEditor, view: _EditorView) -> None:
    cursor = QTextCursor(editor.document())
    cursor.setPosition(view.anchor)
    cursor.setPosition(view.position, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    editor.verticalScrollBar().setValue(view.vertical)
    editor.horizontalScrollBar().setValue(view.horizontal)


def _editing_text(title: str) -> str:
    return f"Editing <b>{html.escape(title)}</b>"


def _editor_from(widget: QWidget | None) -> CodeEditor | None:
    while widget is not None:
        if isinstance(widget, CodeEditor):
            return widget
        widget = widget.parentWidget()
    return None


def _is_inside(widget: QWidget | None, ancestor: QWidget) -> bool:
    while widget is not None:
        if widget is ancestor:
            return True
        widget = widget.parentWidget()
    return False


def _blocks(count: int) -> str:
    return f"{count} block" if count == 1 else f"{count} blocks"


def _line_label(count: int) -> str:
    word = "line" if count == 1 else "lines"
    return f"{count} {word}"
