# Galley

Galley is a desktop app for formatting, validating, and converting JSON and YAML, and for formatting XML and HTML. Paste text or open a file, and Galley gives you clean output, plain-language errors that point at the exact line, and a Convert from Python that never runs your code. It's built with Qt 6 and PySide6, with a print-inspired design.

![Galley converting minified JSON into YAML, in light mode](docs/images/json-to-yaml-light.png)

> **Work in progress.** Galley is still being built and hasn't had a release yet. The features below work today. [In the works](#in-the-works) lists what's next.

## What it does

- **Format and convert.** Pick the Input format and the Output format, then press Format. JSON to JSON, YAML to YAML, JSON to YAML and YAML to JSON all work. Input is never changed, and each result lands in Output as one undo step.
- **XML and HTML.** Format also takes XML and HTML. XML formats to XML and HTML to HTML; neither converts to JSON or YAML. Format refuses XML or HTML over 2 MB. The XML parser reads no external DTD or entity, and nothing is ever fetched.
- **Sort keys.** Turn on Sort keys, and the next Format writes object keys A–Z at every level, with "· keys sorted A–Z" in the status bar. Input is never changed, and Galley remembers the setting between launches.
- **Minify JSON.** Press Minify to write Input's JSON to Output with no spaces or line breaks. Key order is kept, and Input is never changed.
- **Validate.** Press Validate to check JSON, YAML or XML in Input for errors. Problems lists each one, and Output is never changed. Validate isn't available for HTML, and a good HTML Format says "Formatted HTML".
- **Indent size, Indent and Outdent.** Pick 2 spaces, 4 spaces or Tab, and Format writes that indent from the next Format on. YAML always indents with spaces. Indent and Outdent (⌘] and ⌘[) shift the selected lines in either pane, and Tab and Shift+Tab do the same for a selection over several lines. Otherwise Tab types spaces up to the next indent stop, or a tab character when the indent size is Tab (never in YAML). The Options link under Output shows the indent size and Sort keys too, with Reset to defaults.
- **Point at the problem.** Errors open the Problems pane, with one row per error and a marker in the gutter on the line to fix. The messages are plain sentences, such as "Remove the repeated key "status" on line 5." Repeated JSON keys are reported as errors, where most parsers silently keep only the last value.
- **Every JSON error at once.** For JSON, the Problems pane lists every error in the document, not just the first. Next Problem and Previous Problem (F8 and Shift+F8) step through them.
- **Find and replace.** Find… opens a find bar over Input or Output, with a match count, Previous and Next that wrap around, and Match case. On Output, Keys only matches object keys and skips values. Find and Replace… adds a replace row to Input's bar, with Replace and Replace All, and Replace All is one undo step. Regex and Whole word work on both bars, and a regex replacement can put in `$1` to `$9`.
- **Compare two documents.** View › Compare puts two JSON or YAML documents side by side as Left and Right. The Changes pane lists every added, removed, changed and moved value, Next Change and Previous Change step through them, and Link Scrolling keeps matching lines level. Fill Right by pasting or with File › Open Right Document…. Compare doesn't take XML or HTML.
- **Copy As, with masking.** Edit › Copy As, also in Output's right-click menu, copies Output as Original, as Masked with sensitive values shown as `•••`, or as Random fake with made-up values in their place. Sensitive values are found by key name (password, token, email and so on) and by value (emails and phone numbers). Copy As works on JSON and YAML.
- **Fold big documents.** Collapse objects and arrays in Output from the gutter, the View menu, or the keyboard. A folded line shows a summary such as `{ … }, 3 keys`. Collapse all and Expand all work across the whole document, and copying always gives the full text.
- **Convert from Python.** Paste a Python literal (a dict or list with `True`, `None`, tuples and so on) and get JSON or YAML. Galley reads it with Python's `ast` module. It never runs the text. Input over 1 MB when Output is JSON, or over 192 KB when it's YAML, is refused so the window stays responsive.
- **Escape and unescape strings.** Turn a document into one escaped JSON string, or turn an escaped string back into formatted JSON or YAML.
- **Open files three ways.** Use File › Open file…, pick one of the ten most recent files from File › Open Recent, or drop a file on the window. Files up to 100 MB open.
- **Stay responsive on large files.** Formatting runs in a separate process. A spinner and a progress bar appear for long jobs, and Cancel stops the job right away and puts the previous result back. Very long single lines turn off whitespace dots and syntax colors so typing stays fast.
- **Light, Dark, or System.** View › Appearance follows macOS or stays fixed. Every color is tested for contrast in both modes, and color is never the only cue.
- **The small things.** Show Whitespace, Word Wrap, a font of your choice from View › Font, bigger and smaller text, and Stack Panes to put Input above Output.

| | |
|---|---|
| ![The Problems pane listing two repeated keys, with error markers in the gutter](docs/images/problems-light.png) | ![Output in dark mode with three folded blocks and their key and item counts](docs/images/folding-dark.png) |
| Errors go to the Problems pane, with a marker on each line to fix. | Folded Output in dark mode. |

![Convert from Python turning a Python dict with True, None and a tuple into JSON](docs/images/convert-python-light.png)

*Convert from Python. The status bar notes that the tuple became a list.*

## Keyboard shortcuts

| Command | macOS | Windows and Linux |
|---|---|---|
| Format | ⌘↵ | Ctrl+Enter |
| Open file | ⌘O | Ctrl+O |
| Find | ⌘F | Ctrl+F |
| Find Next / Find Previous | ⌘G / ⇧⌘G | F3 / Shift+F3 |
| Find and Replace | ⌥⌘F | Ctrl+H |
| Regex / Whole word (in the find bar) | ⌥⌘R / ⌥⌘W | Alt+R / Alt+W |
| Show Problems | ⇧⌘M | Ctrl+Shift+M |
| Next Problem / Previous Problem | ⌘' / ⇧⌘' (also F8 / ⇧F8) | F8 / Shift+F8 |
| Compare | ⇧⌘D | Ctrl+Shift+D |
| Next Change / Previous Change | ⌥⌘↓ / ⌥⌘↑ (also F7 / ⇧F7) | F7 / Shift+F7 |
| Indent / Outdent | ⌘] / ⌘[ | Ctrl+] / Ctrl+[ |
| Fold / Unfold | ⌥⌘[ / ⌥⌘] | Ctrl+Shift+[ / Ctrl+Shift+] |
| Collapse all / Expand all | ⌘K ⌘0 / ⌘K ⌘J | Ctrl+K, Ctrl+0 / Ctrl+K, Ctrl+J |
| Escape as String | ⇧⌘E | Ctrl+Shift+E |
| Unescape String | ⌘K ⌘E | Ctrl+K, Ctrl+E |
| Word Wrap | ⌥Z | Alt+Z |
| Show Whitespace | ⇧⌘8 | Ctrl+Shift+8 |
| Stack Panes | ⌘\\ or ⇧⌘L | Ctrl+\\ or Ctrl+Shift+L |
| Bigger / Smaller / Actual Size | ⌘+ / ⌘− / ⌘0 | Ctrl++ / Ctrl+− / Ctrl+0 |

No shortcut uses Ctrl+Alt on Windows, because AltGr acts as Ctrl+Alt there and would trigger commands while you type characters such as `[` or `€`.

## In the works

The rest of the UI audit is approved and not in the app yet ([#91](https://github.com/ericpeck/galley/issues/91)):

- Output is marked Stale after Input changes, until the next Format or Convert.
- The direction label, such as JSON → YAML, follows the result on screen, not the pickers.
- A Convert from Python error gets a Problems row and a gutter marker, like other errors.

## Planned

Menu items and toolbar buttons that aren't built yet are disabled and say "Coming soon", so nothing pretends to work. Galley won't be released while any remain, and none do today. Not started yet:

- Windows builds

Specs for each feature live in their [GitHub issues](https://github.com/ericpeck/galley/issues).

## Install the macOS app

Galley runs on macOS 14 (Sonoma) or later, on Apple Silicon. The Release macOS workflow builds an unsigned disk image; see [docs/releasing.md](docs/releasing.md). The app has no Developer ID signature and isn't notarized, so macOS asks once before it opens:

1. Drag Galley to Applications.
2. Open it once and dismiss the warning.
3. Go to System Settings > Privacy & Security, scroll down to Security, and click Open Anyway next to the message about Galley (asks for your password).
4. Note: on macOS 14 and earlier, right-click > Open still works.

After that, Galley opens with a normal double-click.

## Run from source

You need [uv](https://docs.astral.sh/uv/getting-started/installation/). Galley uses Python 3.12.

```sh
uv sync
```

```sh
uv run galley
```

- If `uv` isn't found after installing it, open a new terminal so your PATH reloads.
- If `uv sync` says Python 3.12 is missing, run `uv python install 3.12`, then `uv sync` again.
- If `uv run galley` reports a missing module, run `uv sync` again.

## Development

Run the tests. They set `QT_QPA_PLATFORM=offscreen`, so no display is needed:

```sh
uv run pytest
```

Lint and check formatting:

```sh
uv run ruff check
```

```sh
uv run ruff format --check
```

The release check lists every action that still says "Coming soon" or "Not yet", and exits with an error until both lists are empty:

```sh
uv run python -m galley.app.actions
```

Read [AGENTS.md](AGENTS.md) before contributing. It covers the layout (`galley.core` never imports Qt), the merge rules, colors and contrast, cross-platform shortcuts, and what must never be committed to this public repository. Design references:

- [docs/ui-spec.md](docs/ui-spec.md): screens, states, wording, and keyboard paths
- [docs/adr/0001-core-architecture.md](docs/adr/0001-core-architecture.md): the worker process, Cancel, and size bands
- [docs/releasing.md](docs/releasing.md): building the disk image

## Privacy

Galley works offline. It has no network code, and it never runs the text you give it. Settings stay on your computer.

## Third-party licenses

Licenses for Qt, PySide6, Python, ruamel.yaml and the other bundled components are in [LICENSES/](LICENSES/).
