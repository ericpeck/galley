"""The app reads the format table, with JSON and YAML unchanged (#63)."""

import pytest

from galley.app.limits import (
    JSON_BUSY_BYTES,
    JSON_PROGRESS_BYTES,
    JSON_WARNING_BYTES,
    YAML_BUSY_BYTES,
    YAML_PROGRESS_BYTES,
    YAML_TAB_BUSY_BYTES,
    YAML_TAB_PROGRESS_BYTES,
    YAML_WARNING_BYTES,
)
from galley.app.main_window import _busy_progress_bytes, _warning_bytes
from galley.app.pane import PaneRole, PaneSpec, PaneWidget
from galley.app.status_bar import INFO_RESERVE


@pytest.mark.parametrize(
    ("role", "labels"),
    [
        (PaneRole.INPUT, ["JSON", "YAML", "XML"]),
        # Output offers what JSON Input writes. XML follows XML Input (#63).
        (PaneRole.OUTPUT, ["JSON", "YAML"]),
    ],
)
def test_the_pickers_list_the_table_in_order(
    qtbot, role: PaneSpec, labels: list[str]
) -> None:
    pane = PaneWidget(role)
    qtbot.addWidget(pane)
    combo = pane.format_combo
    assert [combo.itemText(i) for i in range(combo.count())] == labels
    assert pane.format_name() == "JSON"


def test_the_status_reserve_covers_every_format() -> None:
    names = ("JSON", "YAML", "XML", "Python")
    assert INFO_RESERVE == (
        *(f"{left} → {right}" for left in names for right in names),
        "Ln 99999, Col 999",
    )


@pytest.mark.parametrize("source", ["JSON", "json", " Json "])
def test_json_size_bands(source: str) -> None:
    assert _busy_progress_bytes(source, "\t") == (JSON_BUSY_BYTES, JSON_PROGRESS_BYTES)
    assert _warning_bytes(source) == JSON_WARNING_BYTES


@pytest.mark.parametrize("source", ["YAML", "yaml"])
def test_yaml_size_bands(source: str) -> None:
    assert _busy_progress_bytes(source, "a: 1\n") == (
        YAML_BUSY_BYTES,
        YAML_PROGRESS_BYTES,
    )
    assert _busy_progress_bytes(source, 'a: "\t"\n') == (
        YAML_TAB_BUSY_BYTES,
        YAML_TAB_PROGRESS_BYTES,
    )
    assert _warning_bytes(source) == YAML_WARNING_BYTES
