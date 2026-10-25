# Galley

Project rules for coding agents. This repository is public.

## Commands

Python 3.12 (`requires-python` in `pyproject.toml`). Use uv.

- `uv sync`
- `uv run ruff check`
- `uv run ruff format --check`
- `uv run pytest` — `tests/conftest.py` sets `QT_QPA_PLATFORM=offscreen`. Deselects `perf` tests.
- `uv run pytest -m perf` — wall-clock budgets only, in gate mode (fails past 3× a budget). `GALLEY_PERF_MODE=strict uv run pytest -m perf` fails past the budget itself. `uv run pytest -m perf -k <test>` reruns one.
- `uv run galley`
- `uv run python -m galley.app.actions` — release check

A wall-clock budget goes only in a `@pytest.mark.perf` test that times through the `perf_budget` fixture (`tests/perf.py`): one warmup run, then the median of 5. A limit relative to a baseline uses `perf_budget.ratio_within`, which alternates the runs and is not multiplied in gate mode. Hang guards such as `waitUntil` timeouts stay in ordinary tests. `.github/workflows/perf-nightly.yml` runs strict mode on `main` each night on macOS and Windows and opens an issue on a miss.

The release check exits 0 only when `UNBUILT_ACTIONS` and `ShellActions._not_yet_actions` are both empty. `UNBUILT_ACTIONS` is the Coming soon list. `_not_yet_actions` is the Not yet list. While either list is non-empty, the check exits 1 and prints `Release blocked: these actions still show "Coming soon"` or `Release blocked: these actions still show "Not yet"`. A `v*` tag runs the same module as `uv run --frozen --no-dev --no-editable python -m galley.app.actions` in `.github/workflows/release-macos.yml` before the disk image is built.

## Layout

Import Qt only from `galley.app`. `galley.core` stays free of Qt. Ruff `TID251` bans PySide6, PyQt, shiboken, and qtpy outside `src/galley/app/` and `tests/`.

## Merge

Squash-merge only when the exact head commit has the approvals below, every check that ran on that commit passed, the branch is 0 commits behind `main`, and the pull request is not a draft. Approvals cover one commit: any new push needs a fresh approval.

- Staff engineer reviewer.
- UI or wording changes: also the UI/UX reviewer.
- `AGENTS.md` and other project-rule changes: also the project owner.

Lint, Secret scan, Test (macOS arm64), Test (Windows), perf, and perf (Windows) run on every pull request. YAML parser comparison and Disk image (macOS arm64) run only when a pull request changes files they cover. GitHub Release is skipped on pull requests; a skipped check is not a failure.

Bring `main` into a branch with a merge commit. Never rebase or force-push a branch under review; that discards the approved commit.

## Ownership

One owner per job. Claim the issue before starting.

## Public repository

Never commit secrets, tokens, credentials, or personal notes. Never commit the gitignored `Project Vault/` folder (it holds UI files) or anything from it. Never commit personal paths of any kind (home directories, usernames in paths).

## Safety

Ruff `S102` and `S307` ban `exec` and `eval`. Convert from Python parses with `ast` and does not run user code. macOS builds are unsigned (no Developer ID signature and no notarization).

## Releases

Push a `v*` release tag only after the project owner has tested that build and approved it. Pushing a `v*` tag does not change the version in `pyproject.toml`; the tag only marks a build the project owner has already tested and approved. Do not release while any menu or toolbar action still shows Coming soon or Not yet. The release check fails a tag build while `UNBUILT_ACTIONS` or `ShellActions._not_yet_actions` is non-empty.

## Cross-platform

Use `Qt.ControlModifier` and `QKeySequence` standard keys for shortcuts. Never hard-code ⌘. Avoid Ctrl+Alt shortcuts on Windows. They collide with AltGr on some keyboard layouts. Avoid macOS-only APIs. Windows support is planned.

## UI

Use the native style and `QPalette` roles for UI colors. Hard-coded colors are allowed only for syntax highlighting and the brand accent `#0080A6` (the Format button fill and the Find highlight), and both live in `theme.py`.

Text contrast is at least 4.5:1. Control edges are at least 3:1. Test both light and dark mode, and have the test set the color scheme itself. CI offscreen ignores OS dark mode.

Color is never the only cue. Pair it with an icon or label.

Use US spelling. Use specific labels and buttons.

UI specs live in their GitHub issues. Build to the spec. Ask before adding UI the spec does not cover.
