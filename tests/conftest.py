"""Pytest defaults for Galley. Tests always run without a display."""

import os

import pytest
from tests.perf import Budget, budget_for, current_mode

os.environ["QT_QPA_PLATFORM"] = "offscreen"


@pytest.fixture
def perf_budget(request: pytest.FixtureRequest) -> Budget:
    """Time and check a wall-clock budget. Only for tests marked ``perf``."""
    return budget_for(request.node)


def pytest_configure(config: pytest.Config) -> None:
    """Stop at once on a GALLEY_PERF_MODE that is neither gate nor strict."""
    del config
    current_mode()
