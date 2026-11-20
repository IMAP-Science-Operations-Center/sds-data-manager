"""Tests for classes in types.py."""

import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sds_data_manager.lambda_code.SDSCode.spice_utilities import KernelCollection
from sds_data_manager.orchestration.maps_utils import (
    _CADENCE_TYPES,
    FIRST_MAP_START_DATE,
)
from sds_data_manager.orchestration.types import SPICE_SOURCES, MapWindow

REPO_ROOT = Path(__file__).parents[2]

# Imports a module with Dagster and the Lambda code blocked, in a fresh process so
# that modules this test session has already imported don't hide the problem.
_BLOCKED_IMPORT_CHECK = textwrap.dedent(
    """
    import importlib
    import importlib.abc
    import sys

    BLOCKED = ("dagster", "sds_data_manager.lambda_code")


    class Blocker(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name.startswith(BLOCKED):
                raise ImportError(f"blocked import: {name}")
            return None


    sys.meta_path.insert(0, Blocker())
    importlib.import_module(sys.argv[1])
    """
)


def _import_without_dagster(module: str) -> subprocess.CompletedProcess:
    """Import ``module`` in a new interpreter where Dagster can't be imported."""
    # The command is this interpreter plus constants, so S603 doesn't apply.
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", _BLOCKED_IMPORT_CHECK, module],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )


@pytest.mark.parametrize(
    "module",
    [
        "sds_data_manager.orchestration.types",
        "sds_data_manager.orchestration.dependency",
    ],
)
def test_dependency_config_is_dagster_free(module):
    """types.py and dependency.py must import without Dagster or Lambda code.

    They are moving to imap-data-access, which has neither.
    """
    result = _import_without_dagster(module)
    assert result.returncode == 0, result.stderr


def test_blocked_import_check_catches_dagster():
    """The import check fails for a module that does import Dagster."""
    result = _import_without_dagster("sds_data_manager.orchestration.dagster_utilities")
    assert result.returncode != 0
    assert "blocked import: dagster" in result.stderr


def test_spice_sources_match_kernel_collection():
    """types.SPICE_SOURCES must list the same types, in order, as KernelCollection."""
    assert list(SPICE_SOURCES) == KernelCollection().file_types


#### Test for BaseENAMapPartition class #####


@pytest.mark.parametrize("cadence_str", ["3mo", "6mo", "1yr"])
def test_get_current_windows(cadence_str):
    """Test that the get_current_windows method returns the correct window."""
    cadence_type = _CADENCE_TYPES.get(cadence_str)
    current_time = datetime(2026, 1, 10, tzinfo=timezone.utc)
    window = cadence_type(current_time).get_current_window()
    assert isinstance(window, MapWindow)
    assert window.cadence == cadence_str
    # The current_time is between a year rollover
    # Ensure that the start year is 2025 and the end year is 2026
    assert window.start.year == 2025
    assert window.end.year == 2026
    assert window.start < current_time <= window.end


@pytest.mark.parametrize("cadence_str", ["3mo", "6mo", "1yr"])
def test_get_windows_since(cadence_str):
    """Test that the get_windows_since method returns the correct windows."""
    cadence_type = _CADENCE_TYPES.get(cadence_str)
    current_time = datetime(2026, 1, 10, tzinfo=timezone.utc)
    # get all the windows since the first map through the current time
    windows = cadence_type(current_time).get_windows_since(FIRST_MAP_START_DATE)
    # The first window should always start on dec 25th 2024
    assert windows[0].start == datetime(2024, 12, 25, tzinfo=timezone.utc)

    # The last window should be through the next year
    assert windows[-1].end.year == 2027

    for window in windows:
        assert window.cadence == cadence_str
        # The start and end should always fall on the 25th of whatever month
        assert window.start.day == 25
        assert window.end.day == 25
