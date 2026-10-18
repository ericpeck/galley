# Third-party licenses

These files are the upstream license texts for code that ships inside `Galley.app`. Versions match the build that the disk-image job packages.

| Component | Version | License | File |
| --- | --- | --- | --- |
| CPython | 3.12.10 | PSF License Version 2 | `Python-3.12.10-LICENSE.txt` |
| OpenSSL | 3.0.16 | Apache-2.0 | `OpenSSL-3.0.16-LICENSE.txt` |
| Expat | 2.7.1 | MIT | `expat-2.7.1-COPYING` |
| XZ Utils (liblzma) | 5.2.3 | Public domain (see its `COPYING`) | `xz-5.2.3-COPYING` |
| libmpdec | 2.5.1 | BSD-2-Clause | `libmpdec-2.5.1-LICENSE.txt` |
| Qt, PySide6, and Shiboken6 | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | `Qt-6.12.0-LGPL-3.0-only.txt`, `Qt-6.12.0-GPL-3.0-only.txt`, `Qt-6.12.0-GPL-2.0-only.txt` |
| ruamel.yaml | 0.19.1 | MIT | `ruamel.yaml-0.19.1-LICENSE` |
| ruamel.yaml.clib | 0.2.15 | MIT | `ruamel.yaml.clib-0.2.15-LICENSE` |
| PyInstaller bootloader and runtime hooks | 6.22.3 | GPL-2.0-or-later with the bootloader exception; Apache-2.0 for runtime hooks | `PyInstaller-6.22.3-COPYING.txt` |

CPython 3.12.10 is the python.org macOS build the disk-image job bundles. `libssl.3.dylib` and `libcrypto.3.dylib` from that build identify themselves as OpenSSL 3.0.16. Expat 2.7.1 is compiled into `pyexpat`. liblzma from XZ Utils 5.2.3 is compiled into `_lzma`. libmpdec 2.5.1 is compiled into `_decimal`.

This software includes code from XZ Utils <http://tukaani.org/xz/>.

PySide6 6.12.0, Shiboken6 6.12.0, and Qt 6.12.0 (including Qt Pdf from the PySide6-Pdf 6.12.0.140 wheel) use the SPDX expression `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`. LGPL-3.0 incorporates GPL-3.0, so `Qt-6.12.0-GPL-3.0-only.txt` is included with the LGPL text. The GPL-2.0 text is included because that grant is part of the same choice.

The app ships these Qt frameworks: QtCore, QtDBus, QtGui, QtNetwork, QtOpenGL, QtPdf, QtQml, QtQmlMeta, QtQmlModels, QtQmlWorkerScript, QtQuick, QtSvg, QtVirtualKeyboard, QtVirtualKeyboardQml, and QtWidgets. It also ships `libpyside6`, `libshiboken6`, the PySide6 and Shiboken extension modules, and the Qt plugins the build collects.

Those libraries are separate frameworks and dylibs under `Contents/Frameworks`, so the link is dynamic. To use a modified Qt, PySide6, or Shiboken, replace those files and sign the app again. An ad-hoc signature is enough for Apple Silicon to start the binary.

Qt 6.12.0 source:

<https://download.qt.io/archive/qt/6.12/6.12.0/single/qt-everywhere-src-6.12.0.tar.xz>

PySide6 and Shiboken 6.12.0 source:

<https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.12.0-src/pyside-setup-everywhere-src-6.12.0.tar.xz>

`PyInstaller-6.22.3-COPYING.txt` is the upstream file for the `Galley` executable (the 6.22.3 bootloader) and the runtime hooks embedded in it. It contains the GPL-2.0-or-later terms, the bootloader exception, and the Apache-2.0 terms.
