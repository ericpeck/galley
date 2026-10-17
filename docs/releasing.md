# Releasing Galley for macOS

The **Release macOS** workflow builds an unsigned Galley disk image for Apple Silicon. It runs on `macos-14`. It does not run on pull requests.

The app has no Developer ID signature and is not notarized. No signing secrets are used. PyInstaller ad-hoc signs the bundle so Apple Silicon can start the binary. Gatekeeper still treats a copy downloaded from the internet as untrusted.

`contents: write` is granted only to the job that attaches a GitHub Release, and that job runs only for a `v*` tag. A manual run uploads workflow artifacts and does not publish a release.

## Run the build

1. On GitHub, open **Actions** and choose **Release macOS**.
2. Use **Run workflow** (`workflow_dispatch`). The version in the file name is the package version (`galley.__version__`). The run uploads two artifacts:
   - `Galley-<version>-arm64.dmg`
   - `Galley-<version>-arm64.dmg.sha256`
3. To publish those files on a GitHub Release, push a tag whose name starts with `v`, such as `v0.1.0`. The file name uses the tag without the leading `v`. The release job creates the release when one does not already exist, or uploads onto the existing release.

Check the checksum from the directory that contains the disk image:

```sh
shasum -a 256 -c Galley-<version>-arm64.dmg.sha256
```

The image is a compressed `hdiutil` disk image (`UDZO`) whose volume contains `Galley.app`. The bundle identifier is `com.galley.app`. The build spec is `packaging/galley.spec`.

## Open the app the first time

After you download the disk image, macOS may refuse to open Galley because it cannot verify the developer.

- Right-click `Galley.app` and choose **Open**, then confirm **Open**.
- Or open **System Settings > Privacy & Security** and choose **Open Anyway** next to the message about Galley.

Later launches can use a normal double-click, once that first confirmation has been given for this copy of the app.

## ruamel.yaml.clib

The workflow looks for the `ruamel.yaml.clib` extension (`_ruamel_yaml`) inside `Galley.app`. Galley does not depend on that extension yet, so a missing file is reported as a note and the job continues. When the core change that adds it as a runtime dependency has merged, set `REQUIRE_RUAMEL_CLIB` to `true` in `.github/workflows/release-macos.yml`. The same step then fails the build if the extension was not packaged.
