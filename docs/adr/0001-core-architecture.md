# ADR 0001: Core architecture

## Status

Proposed

## Context

Galley formats, validates, and converts JSON and YAML. The stack is PySide6 / Qt 6. Galley targets macOS and Windows. The UI spec treats Galley as a cross-platform Qt app (`docs/ui-spec.md`).

`galley.core` is pure Python. `format_json` uses the stdlib; `format_yaml` dumps with `ruamel.yaml` in safe mode. `galley.app` is the Qt shell. Convert from Python parses with `ast` against an allowlist. It rejects `datetime(...)` and every other function call, and nothing in the text is executed. Users paste dates as ISO strings. Format still runs on the caller.

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

`set_start_method("spawn")` raises `RuntimeError` when one process calls it twice (the tests, a second window). `get_context("spawn")` leaves that global state alone, so `configure_runtime` does not call it. `configure_runtime` still calls `multiprocessing.freeze_support()` before `QApplication` exists. The parse session creates the worker `Process` and both queues from `multiprocessing.get_context("spawn")`. A test runs that worker in a fresh interpreter and checks that `get_start_method(allow_none=True)` is still `None`.

Start one warm worker after `configure_runtime()` returns, and replace it in the background when it exits or is killed. Format sends a job to that process so the click does not pay interpreter startup. The replacement after Cancel gets new queues.

The worker callable is a top-level function in `galley.core`. Under `spawn`, Python pickles it by import path, so the target has to be a module-level function.

`spawn` is the default start method on macOS since Python 3.8, and the default on Windows. Galley requires Python 3.12, so the macOS build that ships now already starts children with `spawn`. A top-level picklable worker and `multiprocessing.freeze_support()` are requirements for that current macOS build, not only for a later Windows build.

`freeze_support()` stays on the entry path a frozen executable re-enters. `src/galley/__main__.py` calls `main()`, and `main()` calls `configure_runtime()` before `QApplication` exists. That function calls `multiprocessing.freeze_support()`.

CI runs the tests on macOS arm64 and on Windows. A non-importable worker fails those jobs, before packaging.

Jobs and progress travel on queues owned by the current worker. The document is sent once. Progress events carry counts.

#### How Cancel meets the requirements

Cancel kills the worker. The handler updates the window first, then kills the child, and does not join it. Killing a worker while it uses a `multiprocessing.Queue` can corrupt that queue, so after Cancel the replacement worker gets fresh queues. Queues are never reused across a killed worker.

1. **The UI answers Cancel within about 100 ms.** The handler immediately shows `Cancelled. Showing the previous result.` and restores the previous output on the UI thread. It then kills the process. `Process.kill()` returns without waiting for the parser, and the handler does not join the child. The existing poll timer reads `exitcode` (a non-blocking wait). When that code is set, the poll deletes the temp and starts the next warm worker with new queues. It does not join on that tick. A child that is still alive after `REAP_DEADLINE_MS` keeps its temp path for cleanup at shutdown. That poll starts the replacement without joining and leaves `Cancelled. Showing the previous result.` in place. If Format was pressed while every live reap was still inside its deadline, and that deadline then expires, the poll shows `The formatter stopped unexpectedly. Your text and file weren't changed. Press Format to try again.` and then sends that job to the new worker. A Format press made when a reap is already past `REAP_DEADLINE_MS` runs on the replacement and does not show that banner, including when another child was just cancelled. It still does not join. Cancelling that Format again before the deadline drops the unsent job only. The stuck child remains the only reap, its temp stays queued once, no second replacement starts, and the status stays `Cancelled. Showing the previous result.` After the deadline, cancelling the job that is running on the replacement retires that replacement beside the first child. Retired children are a list. Each entry keeps its own process, resource, queues, reap start time, and temp paths. The poll reads `exitcode` and does not join. When one child exits, only that child's temps are deleted. Shutdown deletes whatever is still queued. The status returns to `Cancelled. Showing the previous result.` Format is enabled again, and the previous output is restored.
2. **Cancel during the 6d write leaves the user's text and file byte-for-byte unchanged.** The worker receives a copy of the text. The UI process creates an empty temp file in the destination's directory and passes that path in the job, so Cancel can delete it without reading a queue. The worker writes formatted bytes only to that temp and never opens the destination for writing. Only the UI process commits, and only after success, by calling `os.replace` on that temp. Cancel kills the worker before `os.replace`. The temp is removed when the poll sees that the child which owns it has exited, so Windows can delete a file that child still had open. Another retired child's temp is left in that child's entry. If the exit has not happened by shutdown, that path is removed then. A child still alive after `REAP_DEADLINE_MS` keeps its own path until its own exit or shutdown. Neither step joins the child or runs on the Cancel status path. The original file is untouched. Input stays a copy, and the previously committed output is restored. The 6d pane may show lines as they arrive; Cancel discards that view (UI spec 6d). Streaming those lines into the editor is not part of this worker; the editor is replaced on success and restored on Cancel. The same-directory temp keeps `os.replace` on one volume, where the replace is atomic. The worker closes the temp before reporting success or failure, so Windows can replace the destination.
3. **Queue events drive the 6d progress bar. Screen 6m is spinner-only.** During the read (6m) the worker emits no fraction: a spinner and `Formatting…`, with no bar and no percent. A read event has phase `read`, `written == 0`, and `total == 0`. During the write (6d) it enqueues phase `write`, the amount written, the total, and the temp path. The UI draws the bar and the percentage from those events. `WRITE_CHUNK_SIZE` and `WRITE_PROGRESS_EVERY_CHUNKS` (one event per chunk, plus the final byte) are named placeholders until Isaac's benchmark. The bar exists only in this phase (UI spec §5.7, §7, §13). `SPINNER_REVEAL_MS` is 500 and `PROGRESS_BAR_REVEAL_MS` is 2000. Those two values are final. The status bar shows an indeterminate progress bar, accessible name `Formatting`, and the Cancel button once the spinner delay has elapsed. The determinate write bar and the status `Writing output…` wait for the longer delay. The Format button is disabled while a job runs, and its label stays `Format`.
4. **`spawn` on the current macOS build and on Windows.** The worker function lives at module top level in `galley.core`, so it is picklable, and `multiprocessing.freeze_support()` runs on the `__main__` → `main` → `configure_runtime` path. Both are required for the macOS app that ships now, because `spawn` has been the default on macOS since Python 3.8, and for Windows, where `spawn` is also the default. CI runs tests on macOS arm64 and Windows, so a non-importable worker fails there before packaging.

### 3. Convert from Python uses `ast` only

Conversion parses with `ast.parse(..., mode="eval")` and walks an allowlist. Dicts, lists, tuples, strings, numbers, booleans, `None`, and unary `+` / `-` on a numeric constant become JSON or YAML. Nothing in the text is executed. The call does not use `eval`, `exec`, or `ast.literal_eval`.

`datetime(...)` and every other function call are rejected, with a line and column. A date has no JSON equivalent, so users paste it as an ISO string such as `"2026-10-18"`.

`tests/test_no_exec.py` already rejects `eval` and `exec` under `src/` and `tests/`, and checks that Ruff selects `S102` and `S307`.

### 4. YAML formatting keeps the located-error loader

`format_json` and `format_yaml` are the formatters that raise `InputError` (`kind`, `lineno`, `colno`, `details`) for invalid UTF-8, nesting past `MAX_DEPTH`, and JSON lone surrogates. YAML uses `YAML(typ="safe")`. Documents deeper than ruamel can recurse are built with an explicit stack. The worker calls those functions through `format_document` and does not replace them with round-trip mode. Round-trip would keep comments, and it would also drop this depth guard and these located errors. Comment preservation stays open until it can sit on top of this loader.

An `InputError` pickles as those four fields. `Exception` would otherwise rebuild it as `InputError(kind)` and raise `TypeError` in the parent. The worker copies the exception onto the failure event. Banner sentences are built in `galley.app`.

### 5. Cross-platform paths, shortcuts, and processes

The starter depends on PySide6 and ruamel.yaml, both cross-platform, and it has no macOS path literals. Keep new dependencies the same way: AppKit, pyobjc, and other macOS frameworks stay out. Traffic-light colors in the UI spec are platform chrome.

- Config and cache directories follow the platformdirs layout (`platformdirs`, or the same per-OS names). A temp file for `os.replace` is the exception and sits beside the destination. `QSettings` may store window geometry; Qt picks its per-OS location (UI spec §8).
- Shortcuts use `QKeySequence.StandardKey` where one exists. Other bindings come from the macOS and Windows columns in UI spec §6, via the platform layer in §12 (`⌘↵` on macOS, `Ctrl+Return` on Windows, the main Return key).
- The worker uses `multiprocessing.get_context("spawn")` (decision 2). That context does not set the global start method.

### 6. Large-file size bands

macOS measurements are in `docs/benchmarks/macos-size-bench.md` (commit `46d7ddc`). Pure-Python YAML is the worst case. The C-library row applies only once the clib fallback PR merges and clib ships. Windows numbers are not measured yet.

`SPINNER_REVEAL_MS` is 500 and `PROGRESS_BAR_REVEAL_MS` is 2000. Those values are final.

UI spec §10 sets no file-size cap. Thresholds for word wrap, syntax colors, indent guides, and warning sizes stay open for a later UI PR. This record sets no file-size numbers.

## Options considered

**Worker process (chosen) vs `QThread`.** A `QThread` shares the GIL and the address space with the UI. CPython cannot force-stop a thread: injecting an exception into code blocked inside `json` or ruamel is unsafe and can deadlock, and a native crash in the parser exits the UI. Cancel then cannot return in ~100 ms during a stuck parse. A child process can be killed, and its crash stays in the child. The cost is `spawn` startup and pickling the document onto the queue (UI spec 6d's 4.8 MB one-line file is the case that matters). A warm worker removes startup from the click. The per-job pickle remains. If that copy dominates the benchmark, the body can move to a temp file or shared memory later, with the parser still off the UI thread.

**`ast` allowlist (chosen) vs `eval` or `ast.literal_eval`.** `eval` runs the user's code. `literal_eval` still evaluates an expression. `ast.parse` yields a tree and does not run it. The walk allows literals only. `datetime(...)` and every other function call are rejected. Users paste dates as ISO strings instead.

**Safe dump (chosen) vs ruamel round-trip (deferred).** `format_yaml` uses `YAML(typ="safe")`. Safe mode drops comments. Round-trip (`typ="rt"`) would keep comments and order, which the YAML options and the "Comments may move" note assume. Round-trip is not what `format_yaml` uses. The located-error formatter uses safe mode, the depth limit, and an explicit stack. This worker calls that formatter. Switching it to round-trip would drop those checks.

**`get_context("spawn")` (chosen) vs a global `set_start_method`.** `spawn` is the default on macOS since Python 3.8 and the default on Windows, so both targets already need a picklable worker and `freeze_support()`. A global `set_start_method("spawn")` raises if it is called again, including from tests and from a second window. `get_context("spawn")` starts the child from that context and does not touch global state. A global call that remains has to pass `force=True` or run once. Leaving the method unset uses `fork` wherever that is the default, and `fork` after threads have started deadlocks.

## Consequences

- The worker imports `galley.core` only. The Qt ban keeps the child from initializing a GUI.
- Cancel updates the window before the child has exited. The update does not wait on the parser.
- The destination file changes only inside `os.replace` on the UI process, after success.
- The session keeps a second interpreter for the warm worker. The Cancel click does not reap or respawn. The poll starts the replacement after `exitcode` is set, with new queues, and does not join on that tick. If that code is still unset at `REAP_DEADLINE_MS`, the poll starts the replacement without joining and leaves the Cancelled status in place. The temp stays queued on that child until that child exits or the UI process shuts down. A Format press that had to wait out a deadline gets the crash banner, then runs on the new worker. A second Cancel inside the deadline drops only that unsent job. A second Cancel after the deadline appends the replacement to the retired list and does not drop the first child. Each retired child keeps its own queues, reap clock, and temps. Exiting one deletes only that child's temps.
- A worker that exits during a job leaves the text and the file unchanged. The status banner is `The formatter stopped unexpectedly. Your text and file weren't changed. Press Format to try again.` It has no line or column. That same poll starts a fresh worker, so the next Format is a new job. The deadline path uses that banner only when the user waited on a worker that then passed `REAP_DEADLINE_MS`, still with no line or column. A Format press after a deadline has already passed runs on the replacement and stays quiet.
- Convert from Python rejects `datetime(...)` and every other function call. It parses with `ast` against an allowlist and does not execute the text. Users paste dates as ISO strings.
- YAML formatting stays on the safe loader and `InputError`. The worker does not rebuild banner sentences. The parent unpickles `kind`, `lineno`, `colno`, and `details`, and the app chooses the words.

## Risks

- Pickling a multi-megabyte document copies it. A warm worker does not remove that copy. Revisit if Isaac's benchmark shows the copy rivaling the parse.
- Cancel can leave a temp file until the child that owns it exits. If that child is still alive at `REAP_DEADLINE_MS`, its path waits until that exit or shutdown. A later cancel does not delete it. A crash of the UI process can orphan one; the orphan is not the user's file.
- Killing a worker while it uses a `multiprocessing.Queue` can corrupt that queue. After Cancel the replacement worker gets fresh queues. Queues are never reused across a killed worker. Each retired child keeps its queues until that child is reaped or the UI process shuts down.
- `os.replace` is atomic on one volume, so the temp stays beside the destination. On Windows the worker closes the temp before the replace.
- Safe mode drops comments. Restoring them with round-trip has to keep `InputError`, `MAX_DEPTH`, and the explicit-stack builder.
- Moving `freeze_support()` off the entry path lets a frozen spawn child relaunch the GUI. The current test guards `configure_runtime`.
- The UI reads the progress queue with a `QTimer`, or with a thread that only blocks on `Queue.get` and posts back to the UI thread. Cancel does not block on the queue.

## Open questions

- Size-band thresholds. macOS measurements are in `docs/benchmarks/macos-size-bench.md` (UI spec §10, open question 13). Pure-Python YAML is the worst case. The C-library row applies only once the clib fallback PR merges and clib ships. Windows numbers are not measured yet. No code path reads a file-size cutoff, so this record still sets none. Word wrap, syntax colors, indent guides, and warning sizes stay open for a later UI PR. `SPINNER_REVEAL_MS` is 500 and `PROGRESS_BAR_REVEAL_MS` is 2000. Those two values are final.
- Resolved. How long Format runs before the spinner or the progress bar appears: `SPINNER_REVEAL_MS` is 500 and `PROGRESS_BAR_REVEAL_MS` is 2000. Those values are final.
- Whether large documents should skip pickle and use a temp file or shared memory.
- How often to send a write-progress event after Isaac's benchmark. The fields are phase, written, total, and the temp path. The placeholder `WRITE_PROGRESS_EVERY_CHUNKS` is `1`.
- One warm worker per process, or one per window, once panes pop out (UI spec §8). This implementation keeps one session on the main window.
- How to remove temp files left when the UI process crashes.
