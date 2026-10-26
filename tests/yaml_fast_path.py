"""Check that a YAML perf fixture stays on the C emitter (#115).

With ``ruamel.yaml.clib``, Galley writes YAML with the C emitter, and a value
it can't match goes to the slower pure-Python emitter. A YAML budget is only
meaningful for a fixture the C emitter writes itself.
"""

import pytest

import galley.core.format as format_module


def assert_c_emitter_writes(value: object) -> None:
    """Fail when the C emitter would hand ``value`` to the pure emitter.

    Without ``ruamel.yaml.clib`` every value takes the pure emitter, so there
    is nothing to check.
    """
    if not format_module._clib_available():
        return
    try:
        format_module._c_emitter_dump(value)
    except format_module._NeedsPureEmitter:
        pytest.fail("this YAML fixture takes the pure-Python emitter")
