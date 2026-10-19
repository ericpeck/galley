# Releasing Galley for macOS

The **Release macOS** workflow builds an unsigned Galley disk image for Apple Silicon. It runs on `macos-14`.

The app has no Developer ID signature and is not notarized. No signing secrets are used. PyInstaller ad-hoc signs the bundle so Apple Silicon can start the binary. Gatekeeper still treats a copy downloaded from the internet as untrusted.

The build job has `contents: read`. `contents: write` is granted only to the job that attaches a GitHub Release, and that job runs only for a `v*` tag. A pull request run and a manual run upload workflow artifacts and do not publish a release or a tag.

## Run the build

A pull request runs this workflow when it changes `packaging/**`, `LICENSES/**`, or `.github/workflows/release-macos.yml`. The spec under `packaging/` is the build script, so that path filter covers it. The run builds the disk image, runs the smoke checks, and uploads the disk image and its SHA-256 checksum as workflow artifacts. It does not create a release or a tag.

You can also run it by hand, or by pushing a tag:

1. On GitHub, open **Actions** and choose **Release macOS**.
2. Use **Run workflow** (`workflow_dispatch`). The version in the file name is the package version (`galley.__version__`). The run uploads two artifacts:
   - `Galley-<version>-arm64.dmg`
   - `Galley-<version>-arm64.dmg.sha256`
3. To publish those files on a GitHub Release, push a tag whose name starts with `v`, such as `v0.1.0`. The file name uses the tag without the leading `v`. The release job creates the release when one does not already exist, or uploads onto the existing release.

Each ref has its own concurrency group. A new run on that ref cancels the run already in progress.

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

`LSMinimumSystemVersion` in the app is `14.4`. Qt 6.12 documents macOS 14.4 as its minimum, and the Qt libraries in the app record that floor. The bundled CPython 3.12.10 arm64 build and the arm64 executable record macOS 11.0. The highest of those floors is 14.4.

PySide6 6.12.0 and Shiboken6 6.12.0 extension modules record a Mach-O minimum of 26.0. The disk-image job launches the built app on macOS 14, so the declared minimum follows Qt's 14.4 floor.

The workflow leaves `MACOSX_DEPLOYMENT_TARGET` unset. The job ships prebuilt Python, Qt, the PyInstaller bootloader, and ruamel.yaml.clib, and PyInstaller 6.22.3 does not read that variable.

The disk image copies `LICENSES/` to `Galley.app/Contents/Resources/LICENSES`. `LICENSES/README.md` maps each bundled component and version to its license file. The **Verify bundle metadata** step checks that `LSMinimumSystemVersion` is the spec's `MINIMUM_MACOS` value, that the license directory is present and not empty, and that `CFBundleIconFile` names the `.icns` file in `Contents/Resources`.

## ruamel.yaml.clib

The workflow installs `ruamel.yaml.clib==0.2.15`, the same pin as the project metadata, and packages that extension into `Galley.app`. `REQUIRE_RUAMEL_CLIB` is `true`, so the job fails when `_ruamel_yaml` is missing from the bundle.

After that file check, the job runs the bundled executable with `--self-check`. The flag does not open a window. It imports `ruamel.yaml.clib` and parses a tiny YAML document with the C parser, prints one line, and exits 0 on success or 1 on failure. A failed step names the check, what broke, and this command:

```sh
dist/Galley.app/Contents/MacOS/Galley --self-check
```

The same flag works in a development checkout:

```sh
uv run galley --self-check
```
