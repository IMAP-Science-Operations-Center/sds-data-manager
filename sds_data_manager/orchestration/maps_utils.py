"""Maps partition utility functions."""

from datetime import datetime, timezone

from sds_data_manager.orchestration.types import (
    BaseENAMapPartition,
    Map1YrPartition,
    Map3MoPartition,
    Map6MoPartition,
)

_CADENCE_TYPES: dict[str, type[BaseENAMapPartition]] = {
    "3mo": Map3MoPartition,
    "6mo": Map6MoPartition,
    "1yr": Map1YrPartition,
}

FIRST_MAP_START_DATE = datetime(2026, 1, 17, tzinfo=timezone.utc)


def get_map_partition_names(
    cadence_str: str,
    start_time: datetime = FIRST_MAP_START_DATE,
    current_time: datetime | None = None,
    include_open: bool = False,
) -> list[str]:
    """Return current and past partition names.

    Find all closed partition since the first map start date to create Dagster
    partition if it does not already exist. If include_open is True, then also return
    partition names for the current open window for the cadence.

    It returns all partition names for the given cadence and time range,
    including the current open window if include_open is True.

    Parameters
    ----------
    cadence_str : str
        The cadence string, e.g., "3mo", "6mo", or "1yr".
    start_time : datetime
        The start time to begin searching for partitions. Defaults to
        FIRST_MAP_START_DATE.
    current_time : datetime, optional
        The current time to use for determining the active window.
        If None, uses the current UTC time.
    include_open : bool, optional
        Whether to include the current open window partition name. Defaults to False.

    Returns
    -------
    list[str]
        A list of partition names for the given cadence and time range.
        Eg.

        If include_open is True, a cadence of "3mo" and a current time
        of 20260806, the returned partition names are
            [
                'cadence-3mo_2026-01-17T00:00:00_to_2026-04-18T00:00:00',
                 'cadence-3mo_2026-04-18T00:00:00_to_2026-07-18T00:00:00',
                'cadence-3mo_2026-07-18T00:00:00_to_2026-10-17T00:00:00'
            ]

        If include_open is False, a cadence of "3mo" and a current time of
        20260806, the returned partition names are
            [
                'cadence-3mo_2026-01-17T00:00:00_to_2026-04-18T00:00:00',
                'cadence-3mo_2026-04-18T00:00:00_to_2026-07-18T00:00:00'
            ]
    """
    if current_time is None:
        current_time = datetime.now(tz=timezone.utc)

    cadence_type = _CADENCE_TYPES.get(cadence_str)
    if cadence_type is None:
        raise ValueError(
            f"Invalid cadence: {cadence_str}. "
            f"Valid cadences are: {list(_CADENCE_TYPES.keys())}"
        )

    # Get all the windows since the first map start date for the cadence.
    windows = cadence_type(current_time).get_windows_since(start_time)
    if include_open:
        # Look for past and present windows
        selected_windows = [
            window for window in windows if window.start <= current_time
        ]
    else:
        # Only look for past windows.
        selected_windows = [window for window in windows if window.end <= current_time]

    if not selected_windows:
        return []

    # Return all selected window partition names.
    return [window.to_partition_name() for window in selected_windows]
