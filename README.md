# galley

Galley is a desktop app for formatting, validating, and converting JSON and YAML. Paste text or open a file to get clean output, exact error locations, path search, and a safe Convert from Python that never runs your code. Built with Qt6 and PySide6, with a print-inspired design.

## Zero to running

1. Install uv from https://docs.astral.sh/uv/getting-started/installation/. If the installer finishes but `uv` is not found, open a new terminal so your PATH reloads, then run `uv sync`.

2. Create the environment and install dependencies:

   ```sh
   uv sync
   ```

   If this fails because Python 3.12 is missing, run `uv python install 3.12`, then run `uv sync` again.

3. Open the app:

   ```sh
   uv run galley
   ```

   If this fails with a missing module or an out-of-date environment, run `uv sync`, then run `uv run galley` again.

4. Run the tests. The suite sets `QT_QPA_PLATFORM=offscreen`, so no display is required:

   ```sh
   uv run pytest
   ```

   If pytest is missing or an import fails, run `uv sync`, then run `uv run pytest` again.

5. Lint:

   ```sh
   uv run ruff check
   ```

   If ruff reports a problem, fix the file it names, then run `uv run ruff check` again. Check formatting with `uv run ruff format --check`. If that fails, run `uv run ruff format`, then run `uv run ruff format --check` again.

An unsigned Apple Silicon disk image is built by the Release macOS workflow. See [docs/releasing.md](docs/releasing.md) for how to run it and how to open the app the first time. The app has no Developer ID signature and is not notarized. On first launch, right-click Galley.app and choose Open, or use System Settings > Privacy & Security > Open Anyway.
