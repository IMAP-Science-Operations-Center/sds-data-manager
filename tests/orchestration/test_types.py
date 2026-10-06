"""Tests for classes in types.py."""

from datetime import datetime, timezone

import pytest

from sds_data_manager.orchestration.maps_utils import (
    _CADENCE_TYPES,
    FIRST_MAP_START_DATE,
)
from sds_data_manager.orchestration.types import MapWindow

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
