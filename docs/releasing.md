# Releasing Galley for macOS

The **Release macOS** workflow builds an unsigned Galley disk image for Apple Silicon. It runs on `macos-15`.

The app has no Developer ID signature and is not notarized. No signing secrets are used. PyInstaller ad-hoc signs the bundle so Apple Silicon can start the binary. Gatekeeper still treats a copy downloaded from the internet as untrusted.

The build job has `contents: read`. `contents: write` is granted only to the job that attaches a GitHub Release, and that job runs only for a `v*` tag. A pull request run and a manual run upload workflow artifacts and do not publish a release or a tag.

## Run the build

A pull request runs this workflow when it changes `packaging/**`, `LICENSES/**`, `.github/workflows/release-macos.yml`, `pyproject.toml`, or `uv.lock`. The spec under `packaging/` is the build script, so that path filter covers it. The run builds the disk image, runs the smoke checks, and uploads the disk image and its SHA-256 checksum as workflow artifacts. It does not create a release or a tag.

You can also run it by hand, or by pushing a tag:

1. On GitHub, open **Actions** and choose **Release macOS**.
2. Use **Run workflow** (`workflow_dispatch`). The version in the file name is the package version (`galley.__version__`). The run uploads one artifact, `galley-macos-arm64`, holding two files:
   - `Galley-<version>-arm64.dmg`
   - `Galley-<version>-arm64.dmg.sha256`
3. To publish those files on a GitHub Release, push a tag whose name starts with `v`, such as `v1.0.0`. The file name uses the tag without the leading `v`. The release job creates the release when one does not already exist, or uploads onto the existing release.

Before tagging, `galley.__version__` and the version in `pyproject.toml` must both equal the tag without the `v`, and CI enforces it.
A tag build also runs three steps that a manual or pull request run skips: Check tag matches version, Check copyright notice and Check unbuilt actions. Run the last two locally before tagging with `python packaging/check_copyright_notice.py` and `python -m galley.app.actions`.

Each ref has its own concurrency group. A new run on that ref cancels the run already in progress.

Workflow artifacts expire after the repository's retention period. Record the run ID, head SHA and SHA-256 somewhere that lasts longer than the file.

Check the checksum from the directory that contains the disk image:

```sh
shasum -a 256 -c Galley-<version>-arm64.dmg.sha256
```

The image is a compressed `hdiutil` disk image (`UDZO`). Its volume contains `Galley.app` next to an Applications shortcut. The bundle identifier is the `BUNDLE_IDENTIFIER` constant in `packaging/galley.spec` (`com.ericmercurypeck.galley`).

The icon master is `packaging/icon/galley.svg`. When the art changes, regenerate the committed `packaging/icon/galley.icns` with `python packaging/icon/regenerate.py`; the release workflow uses that file and does not render the SVG.

## Open the app the first time

After you download the disk image, macOS may refuse to open Galley because it cannot verify the developer.

1. Drag Galley to Applications.
2. Open it once and dismiss the warning.
3. Go to System Settings > Privacy & Security, scroll down to Security, and click Open Anyway next to the message about Galley (asks for your password).
4. Note: on macOS 14 and earlier, right-click > Open still works.

After that, Galley opens with a normal double-click.

## Minimum macOS and licenses

`LSMinimumSystemVersion` in the app is `14.0`. The floor is the highest minos any bundled binary records, raised to the oldest macOS the app was last tested on in CI. With PySide6 6.9.3 that highest recorded minos is 12.0, and CI last tested on macOS 14 (`macos-14`, until GitHub retired that runner on 2026-11-02), so the declared floor is 14.0. CI now runs on macOS 15, so 14.0 is supported but no longer tested in CI. PySide6 6.10 and newer bindings record the build SDK as minos, so do not bump PySide6 without re-checking those binaries.

The **Verify bundle metadata** step fails when any Mach-O file records a minimum macOS higher than that floor. It runs `vtool -show-build` per architecture, uses `LC_VERSION_MIN_MACOSX` when a file has no `LC_BUILD_VERSION`, and on failure prints the count, each path, architecture, minos, and floor, then `pin dependencies lower or raise MINIMUM_MACOS in galley.spec`.

The workflow leaves `MACOSX_DEPLOYMENT_TARGET` unset. The job ships prebuilt Python, Qt, the PyInstaller bootloader, and ruamel.yaml.clib, and PyInstaller 6.22.3 does not read that variable.

The disk image copies `LICENSES/` to `Galley.app/Contents/Resources/LICENSES`. `LICENSES/README.md` maps each bundled component and version to its license file. The **Verify bundle metadata** step checks that `LSMinimumSystemVersion` is the spec's `MINIMUM_MACOS` value, that the license directory is present and not empty, and that `CFBundleIconFile` names the `.icns` file in `Contents/Resources`.

## ruamel.yaml.clib

The workflow installs `ruamel.yaml.clib==0.2.15`, the same pin as the project metadata, and packages that extension into `Galley.app`. `REQUIRE_RUAMEL_CLIB` is `true`, so the job fails when `_ruamel_yaml` is missing from the bundle.

After that file check, the job runs the bundled executable with `--self-check`. The flag does not open a window. It imports `ruamel.yaml.clib` and parses a tiny YAML document with the C parser. On the frozen app it also checks that `licenses_directory()` found a folder of license files inside the bundle. On either build it also checks that expat is 2.6.0 or newer, which XML Format and Validate need. Success on the frozen app prints three lines: `ruamel.yaml.clib self-check: ok`, `LICENSES self-check: ok` plus that folder, and `expat self-check: ok` plus the expat version. A development checkout prints the first and last of those lines. The process exits 0 on success or 1 on failure. A failed step names the check, what broke, and this command:

```sh
dist/Galley.app/Contents/MacOS/Galley --self-check
```

The same flag works in a development checkout:

```sh
uv run galley --self-check
```

## V1 freeze

The V1 freeze started at `f155b32` and lasts until the `v*` tag. During the freeze, `main` takes only bug fixes, test fixes, CI fixes and docs. No features and no new UI. Dependabot pull requests wait unless they fix a security issue, and PySide6 does not move.

Every freeze pull request lands the same way:

- All six checks are green at the head.
- The branch is 0 behind `main` at the head.
- Chief posts the pre-merge check.
- Eric gives the go.
- It is squash-merged with `Closes #N.` and no trailers.
- Only one freeze pull request is in flight at a time, so each nightly run maps to one change.

To bring `main` into a branch, merge it with a merge commit. Nobody rebases, amends or force-pushes a branch under review, so every approval keeps pointing at a real commit.

If a named reviewer wrote the pull request, they do not approve it. Chief names a substitute reviewer in the Engineering Team room.

### Which gate a pull request gets

The changed files decide the gate, not the title or the description. The reviewer runs this and posts the output in the review:

```sh
git diff --name-only origin/main...<head>
```

The pull request gets the full gate if any changed path is one of these:

- anything under `src/`
- anything under `packaging/`
- `pyproject.toml` or `uv.lock`
- anything under `.github/workflows/`
- a golden file under `tests/**/data/`

Otherwise a pull request that changes only docs, or only tests, takes the lighter gate.

### Full gate

Bug fixes and anything on the list above take the full gate. The first commit adds tests only, and those tests fail against the base, in light and dark for UI. Erik, Bill and Devon each approve the exact head, and Isaac makes the timing call.

### Lighter gate

- **Docs only.** Devon approves the exact head. Bill also approves when the change touches the UI spec. There is no fail-first check, because nothing can fail.
- **Tests only.** Erik and Devon approve the exact head. The new or changed tests pass on `main` as it is, which shows they lock in current behavior. Isaac makes the timing call when the change touches CI, the performance tests, or the size of the suite.

### Nothing may loosen

The lighter gate never loosens a check. A pull request takes the full gate if it does any of these:

- removes an assertion
- adds a skip or an xfail
- changes a timing budget or a golden file (Isaac rules on budgets)
- changes a pinned user-facing string, an accessible name, or a light or dark UI assertion (Bill reviews these)
