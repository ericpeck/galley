# Galley

Project rules for coding agents. This repository is public.

## Commands

Python 3.12 (`requires-python` in `pyproject.toml`). Use uv.

- `uv sync`
- `uv run ruff check`
- `uv run ruff format --check`
- `uv run pytest` — `tests/conftest.py` sets `QT_QPA_PLATFORM=offscreen`
- `uv run galley`
- `uv run python -m galley.app.actions` — release check

The release check exits 0 when no action still shows Coming soon. While any remain, it exits 1 and prints their names. A `v*` tag runs the same module as `uv run --frozen --no-dev --no-editable python -m galley.app.actions` in `.github/workflows/release-macos.yml` before the disk image is built.

## Layout

Import Qt only from `galley.app`. `galley.core` stays free of Qt. Ruff `TID251` bans PySide6, PyQt, shiboken, and qtpy outside `src/galley/app/` and `tests/`.

## Merge

Squash-merge a pull request only when all of the following are true for the exact head commit:

- The staff engineer reviewer has approved that commit.
- UI or wording changes also have the UI/UX reviewer's approval.
- All four CI checks are green: Lint, Test (macOS arm64), Test (Windows), and Secret scan (`.github/workflows/ci.yml`).
- The branch is 0 commits behind `main`.
- The pull request is not a draft.

Bring `main` into a branch with a merge commit. Never force-push or rebase pushed commits.

## Ownership

One owner per job. Claim the issue before starting.

## Secrets

Never commit secrets, tokens, credentials, personal file paths, or personal notes or vault files.

## Safety

Ruff `S102` and `S307` ban `exec` and `eval`. Convert from Python parses with `ast` and does not run user code. macOS builds are unsigned (no Developer ID signature and no notarization).

## Releases

Push a `v*` release tag only after the project owner has tested that build and approved it. Do not release while any menu or toolbar action still shows Coming soon or Not yet. The release check fails a tag build while Coming soon actions remain.

## Color

Colors come from `QPalette` roles, not hard-coded values. Syntax colors are the exception.
