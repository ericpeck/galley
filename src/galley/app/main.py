"""Galley application shell."""

import importlib
import pyexpat
import sys

from PySide6.QtWidgets import QApplication, QMainWindow
from ruamel.yaml import YAML
from ruamel.yaml.main import CParser

from galley.app.about import licenses_directory, licenses_self_check
from galley.app.appearance import install_appearance
from galley.app.fonts import ui_font
from galley.app.main_window import MainWindow
from galley.app.runtime import configure_runtime
from galley.core.xml_format import expat_problem

# Hidden from the window. The disk-image job runs the built executable with it.
_SELF_CHECK_FLAG = "--self-check"
_SELF_CHECK_OK = "ruamel.yaml.clib self-check: ok"
_SELF_CHECK_YAML = "a: 1\n"


def create_main_window() -> QMainWindow:
    """Return the Galley window in its empty state.

    The saved appearance is applied first, so the window never shows in
    the wrong mode.
    """
    application = QApplication.instance()
    if isinstance(application, QApplication):
        install_appearance(application)
    return MainWindow()


def clib_self_check() -> str | None:
    """Return None when ``ruamel.yaml.clib`` parses a tiny document.

    The C extension is optional, so the import happens here and the app
    still starts when the wheel is absent. A returned string is why it failed.
    """
    try:
        extension = importlib.import_module("_ruamel_yaml")
    except ImportError as exc:
        return f"ruamel.yaml.clib did not import ({exc})"
    extension_parser = getattr(extension, "CParser", None)
    if extension_parser is None or CParser is not extension_parser:
        return "ruamel.yaml did not load the C parser from ruamel.yaml.clib"
    yaml = YAML(typ="safe")
    if yaml.Parser is not extension_parser:
        return "safe YAML did not select the ruamel.yaml.clib C parser"
    try:
        loaded = yaml.load(_SELF_CHECK_YAML)
    except Exception as exc:
        return f"ruamel.yaml.clib C parser failed ({type(exc).__name__}: {exc})"
    if loaded != {"a": 1}:
        return f"ruamel.yaml.clib C parser returned {loaded!r}"
    return None


def run_self_check() -> int:
    """Print the self-check lines. Return 0 only when every check passes.

    A development checkout prints the ``ruamel.yaml.clib`` line. A frozen
    app also prints whether ``licenses_directory`` found license files
    inside the bundle. The last line checks that expat is new enough for
    XML.
    """
    lines: list[str] = []
    failed = False
    problem = clib_self_check()
    if problem is None:
        lines.append(_SELF_CHECK_OK)
    else:
        failed = True
        lines.append(f"ruamel.yaml.clib self-check: failed: {_one_line(problem)}")
    if getattr(sys, "frozen", False):
        license_problem = licenses_self_check()
        if license_problem is None:
            found = licenses_directory()
            lines.append(f"LICENSES self-check: ok {found}")
        else:
            failed = True
            lines.append(f"LICENSES self-check: failed: {_one_line(license_problem)}")
    expat_failure = expat_problem()
    if expat_failure is None:
        lines.append(f"expat self-check: ok {pyexpat.EXPAT_VERSION}")
    else:
        failed = True
        lines.append(f"expat self-check: failed: {expat_failure}")
    print("\n".join(lines), flush=True)
    return 1 if failed else 0


def _one_line(problem: str) -> str:
    detail = " ".join(problem.split())
    if detail:
        return detail
    return "the check did not report a reason"


def main(argv: list[str] | None = None) -> int:
    """Open one Galley window and run its event loop.

    ``--self-check`` checks ``ruamel.yaml.clib``, when frozen the bundled
    LICENSES folder, and the expat version. It exits without a window.
    """
    configure_runtime()
    args = sys.argv if argv is None else argv
    if _SELF_CHECK_FLAG in args:
        return run_self_check()
    application = QApplication(sys.argv)
    application.setApplicationName("Galley")
    application.setApplicationDisplayName("Galley")
    application.setFont(ui_font())
    window = create_main_window()
    window.start_parse_worker()
    window.show()
    try:
        return application.exec()
    finally:
        window.shutdown_parse_worker()
