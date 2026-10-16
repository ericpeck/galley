# ADR 0001: Core architecture

## Status

Proposed

## Context

Galley formats, validates, and converts JSON and YAML. The stack is PySide6 / Qt 6. Galley targets macOS and Windows. The UI spec treats Galley as a cross-platform Qt app (`docs/ui-spec.md`).

`galley.core` is pure Python. `format_json` uses the stdlib; `format_yaml` dumps with `ruamel.yaml` in safe mode. `galley.app` is the Qt shell. Convert from Python is specified in UI spec §9 and is not built yet. Format still runs on the caller.

`galley.app.runtime.configure_runtime` runs before the event loop. It calls `multiprocessing.freeze_support()` and `multiprocessing.set_start_method("spawn")`. `src/galley/__main__.py` reaches that by calling `main()`.

Screens 6m and 6d require a responsive window, Cancel throughout, a spinner with no percentage while input is read, and a progress bar only while output is written. Cancel during that write restores the previous output and must not leave a partial file.

## Decision

### 1. Core has no Qt imports

`galley.app` imports `galley.core`. Core does not import `galley.app` or any Qt binding. The worker runs core code, so the child never loads a GUI library.

Enforcement is already in the tree:

- Ruff `TID251` bans `PySide6`, `PyQt5`, `PyQt6`, `shiboken2`, `shiboken6`, and `qtpy` outside `src/galley/app/` and `tests/` (`pyproject.toml`).
- `tests/core/test_no_qt_imports.py` parses `src/galley/core` and fails on those modules, on `Qt`, and on a non-literal `__import__` or `import_module`.

### 2. Parsing and formatting run in a worker process

A child process parses and formats. The UI process owns the windows, the editor buffers, and the file commit. The worker uses `multiprocessing.get_context("spawn")` for its `Process` and its queues, and does not set the global start method. A `QProcess` child gives the same isolation.

`set_start_method("spawn")` raises `RuntimeError` when one process calls it twice (the tests, a second window). `get_context("spawn")` leaves that global state alone. `configure_runtime` keeps `multiprocessing.freeze_support()`. Any global `set_start_method` call that remains must be guarded with `force=True` or limited to a single run. Follow-up for the worker implementation: create the process and its queues with `get_context("spawn")`. Code stays as it is until then. `configure_runtime` still calls `set_start_method("spawn")`, and `tests/app/test_main_window.py` asserts that call and that a fresh process reports `spawn`.

Start one warm worker after `configure_runtime()` returns, and replace it in the background when it exits or is killed. Format sends a job to that process so the click does not pay interpreter startup. The replacement after Cancel gets new queues.

The worker callable is a top-level function in `galley.core`. Under `spawn`, Python pickles it by import path, so the target has to be a module-level function.

`spawn` is the default start method on macOS since Python 3.8, and the default on Windows. Galley requires Python 3.12, so the macOS build that ships now already starts children with `spawn`. A top-level picklable worker and `multiprocessing.freeze_support()` are requirements for that current macOS build, not only for a later Windows build.

`freeze_support()` stays on the entry path a frozen executable re-enters. `src/galley/__main__.py` calls `main()`, and `main()` calls `configure_runtime()` before `QApplication` exists. That function calls `multiprocessing.freeze_support()`.

CI runs the tests on macOS arm64 and on Windows. A non-importable worker fails those jobs, before packaging.

Jobs and progress travel on queues owned by the current worker. The document is sent once. Progress events carry counts.

#### How Cancel meets the requirements

Cancel kills the worker. The handler updates the window first, then kills the child, and does not join it. Killing a worker while it uses a `multiprocessing.Queue` can corrupt that queue, so after Cancel the replacement worker gets fresh queues. Queues are never reused across a killed worker.

1. **The UI answers Cancel within about 100 ms.** The handler immediately shows `Cancelled. Showing the previous result.` and restores the previous output on the UI thread. It then kills the process in the background. `Process.kill()` returns without waiting for the parser. Reaping the child and starting the next warm worker happen after that update.
2. **Cancel during the 6d write leaves the user's text and file byte-for-byte unchanged.** The worker receives a copy of the text. It writes formatted bytes to a temporary file in the destination's directory and never opens the destination for writing. Only the UI process commits, and only after success, by calling `os.replace` on that temp. Cancel kills the worker before `os.replace` and removes the temp, so the original file is untouched. Input stays a copy, and the previously committed output is restored. The 6d pane may show lines as they arrive; Cancel discards that view (UI spec 6d). The same-directory temp keeps `os.replace` on one volume, where the replace is atomic. The worker closes the temp before reporting success, so Windows can replace the destination.
3. **Queue events drive the 6d progress bar. Screen 6m is spinner-only.** During the read (6m) the worker emits no fraction: a spinner and `Formatting…`, with no bar and no percent. During the write (6d) it enqueues the amount written and the total, and the UI draws the bar and the percentage from those events. The bar exists only in this phase (UI spec §5.7, §7, §13).
4. **`spawn` on the current macOS build and on Windows.** The worker function lives at module top level in `galley.core`, so it is picklable, and `multiprocessing.freeze_support()` runs on the `__main__` → `main` → `configure_runtime` path. Both are required for the macOS app that ships now, because `spawn` has been the default on macOS since Python 3.8, and for Windows, where `spawn` is also the default. CI runs tests on macOS arm64 and Windows, so a non-importable worker fails there before packaging.

### 3. Convert from Python uses `ast` only

Conversion parses with `ast.parse` and edits the user's text in place from node spans (`lineno`, `col_offset`, `end_lineno`, `end_col_offset`). Quotes, `True` / `False` / `None`, tuples, and trailing commas are span replacements. An unsupported expression becomes a JSON string of its exact source slice. Unchanged characters keep their whitespace (UI spec §9, screen 6k). The call does not use `eval`, `exec`, or `ast.literal_eval`. Applied edits are one undo step.

`tests/test_no_exec.py` already rejects `eval` and `exec` under `src/` and `tests/`, and checks that Ruff selects `S102` and `S307`.

### 4. YAML uses ruamel.yaml round-trip

Load and dump YAML with `ruamel.yaml` in round-trip mode (`YAML(typ="rt")`), which keeps comments and key order. The starter's `format_yaml` uses `typ="safe"`, which drops comments; formatting code uses round-trip. Sort-keys may move comments (UI spec §5.12). JSON formatting stays on the stdlib.

### 5. Cross-platform paths, shortcuts, and processes

The starter depends on PySide6 and ruamel.yaml, both cross-platform, and it has no macOS path literals. Keep new dependencies the same way: AppKit, pyobjc, and other macOS frameworks stay out. Traffic-light colors in the UI spec are platform chrome.

- Config and cache directories follow the platformdirs layout (`platformdirs`, or the same per-OS names). A temp file for `os.replace` is the exception and sits beside the destination. `QSettings` may store window geometry; Qt picks its per-OS location (UI spec §8).
- Shortcuts use `QKeySequence.StandardKey` where one exists. Other bindings come from the macOS and Windows columns in UI spec §6, via the platform layer in §12 (`⌘↵` on macOS, `Ctrl+Enter` on Windows).
- The worker uses `multiprocessing.get_context("spawn")` (decision 2). That context does not set the global start method.

### 6. Large-file size bands

Placeholder. Undecided.

UI spec §10 sets no file-size cap. Thresholds for disabling word wrap, syntax colors, and indent guides, and for when screen 6m appears, wait on Isaac's benchmark. This record sets no numbers.

## Options considered

**Worker process (chosen) vs `QThread`.** A `QThread` shares the GIL and the address space with the UI. CPython cannot force-stop a thread: injecting an exception into code blocked inside `json` or ruamel is unsafe and can deadlock, and a native crash in the parser exits the UI. Cancel then cannot return in ~100 ms during a stuck parse. A child process can be killed, and its crash stays in the child. The cost is `spawn` startup and pickling the document onto the queue (UI spec 6d's 4.8 MB one-line file is the case that matters). A warm worker removes startup from the click. The per-job pickle remains. If that copy dominates the benchmark, the body can move to a temp file or shared memory later, with the parser still off the UI thread.

**`ast` span edits (chosen) vs `eval` or `ast.literal_eval`.** `eval` runs the user's code. `literal_eval` still evaluates an expression and cannot apply the in-place span edits in UI spec §9. `ast.parse` yields positions and does not run the tree.

**ruamel round-trip (chosen) vs a safe dump.** `typ="safe"` drops comments. Round-trip keeps comments and order, which the YAML options and the "Comments may move" note assume. It is stricter and slower than a safe dump.

**`get_context("spawn")` (chosen) vs a global `set_start_method`.** `spawn` is the default on macOS since Python 3.8 and the default on Windows, so both targets already need a picklable worker and `freeze_support()`. A global `set_start_method("spawn")` raises if it is called again, including from tests and from a second window. `get_context("spawn")` starts the child from that context and does not touch global state. A global call that remains has to pass `force=True` or run once. Leaving the method unset uses `fork` wherever that is the default, and `fork` after threads have started deadlocks.

## Consequences

- The worker imports `galley.core` only. The Qt ban keeps the child from initializing a GUI.
- Cancel updates the window before the child has exited. The update does not wait on the parser.
- The destination file changes only inside `os.replace` on the UI process, after success.
- The session keeps a second interpreter for the warm worker. Respawn stays off the Cancel path, and the replacement gets new queues.
- Convert from Python rewrites the current buffer from spans. It does not re-serialize the document.
- Round-trip YAML keeps comments the safe-mode stub drops. Data-equality checks still apply; comment and order behavior becomes part of the formatter.

## Risks

- Pickling a multi-megabyte document copies it. A warm worker does not remove that copy. Revisit if Isaac's benchmark shows the copy rivaling the parse.
- Cancel can leave a temp file. The UI deletes it on Cancel and on failure. A crash of the UI process can orphan one; the orphan is not the user's file.
- Killing a worker while it uses a `multiprocessing.Queue` can corrupt that queue. After Cancel the replacement worker gets fresh queues. Queues are never reused across a killed worker.
- `os.replace` is atomic on one volume, so the temp stays beside the destination. On Windows the worker closes the temp before the replace.
- Round-trip mode rejects some inputs a safe loader accepts, and it uses more CPU.
- Moving `freeze_support()` off the entry path lets a frozen spawn child relaunch the GUI. The current test guards `configure_runtime`.
- The UI reads the progress queue with a `QTimer`, or with a thread that only blocks on `Queue.get` and posts back to the UI thread. Cancel does not block on the queue.

## Open questions

- Size-band numbers, and how long Format runs before 6m appears, after Isaac's benchmark (UI spec §10, open question 13).
- Whether large documents should skip pickle and use a temp file or shared memory.
- Progress-event fields, and how often the worker sends one.
- One warm worker per process, or one per window, once panes pop out (UI spec §8).
- How to remove temp files left when the UI process crashes.
