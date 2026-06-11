"""Maps partition utility functions."""

from datetime import datetime, timezone

from sds_data_manager.orchestration.types import (
    BaseMapPartition,
    Map1YrPartition,
    Map3MoPartition,
    Map6MoPartition,
)

_CADENCE_PRIORITY: tuple[str, ...] = ("3mo", "6mo", "1yr")

_CADENCE_TYPES: dict[str, type[BaseMapPartition]] = {
    "3mo": Map3MoPartition,
    "6mo": Map6MoPartition,
    "1yr": Map1YrPartition,
}

DEFAULT_MAPS_TIME = datetime.now(tz=timezone.utc)


def get_map_partition_to_create(
    cadence_str: str,
    current_time: datetime = DEFAULT_MAPS_TIME,
) -> list[str]:
    """Return the most recently closed partition for a cadence.

    Use recently closed partition to create Dagster
    partition if it does not already exist. Creating Dagster
    partition kicks off the map job using that partition's date range.
    """
    cadence_type = _CADENCE_TYPES.get(cadence_str)
    if cadence_type is None:
        raise ValueError(
            f"Invalid cadence: {cadence_str}. "
            f"Valid cadences are: {list(_CADENCE_TYPES.keys())}"
        )

    # Get all the windows
    windows = cadence_type(current_time).get_windows()

    # Only look for past windows.
    closed_windows = [window for window in windows if window.end <= current_time]

    if not closed_windows:
        return []

    # Get the most recently closed window, which is the one
    # we want to create a partition for.
    latest_closed = max(
        closed_windows,
        key=lambda window: window.end,
    )

    # Return the partition name
    return [latest_closed.to_partition_name()]


def get_progressive_map_partition_names(
    current_time: datetime = DEFAULT_MAPS_TIME,
) -> list[str]:
    """Return progressive map partition names, deduping identical date ranges."""
    progressive_partitions: list[str] = []
    seen_ranges: set[tuple[datetime, datetime]] = set()

    for cadence_str in _CADENCE_PRIORITY:
        # Looks for active window for the cadence. For example,
        # in 3mo cadence, there are 4 potential windows: 0-3mo, 3-6mo,
        # 6-9mo, 9-12mo for any given time. If the current time falls
        # within any of those windows, that window is returned.
        cadence_obj = _CADENCE_TYPES[cadence_str](current_time)
        active_window = cadence_obj.get_current_window()

        # Now track the active window start time to current time.
        # It's used to avoid duplicate map jobs that covers same
        # date range.
        partition_range = (
            active_window.start,
            current_time,
        )

        if partition_range in seen_ranges:
            continue

        seen_ranges.add(partition_range)

        progressive_partitions.append(
            cadence_obj.get_current_window().to_partition_name()
        )

    return progressive_partitions


# TODO: remove these print statements after testing in Dagster.
six_month_start = datetime(2026, 7, 18, tzinfo=timezone.utc)
print(get_map_partition_to_create("6mo", current_time=six_month_start))
print(get_map_partition_to_create("3mo"))
print(get_progressive_map_partition_names())

# Output of above print statements:
# ['cadence_6mo_2026-01-17T00:00:00_to_2026-07-18T00:00:00']
# ['cadence_3mo_2026-01-17T00:00:00_to_2026-04-18T00:00:00']
# [
#   'cadence_3mo_2026-04-18T00:00:00_to_2026-07-18T00:00:00',
#   'cadence_6mo_2026-01-17T00:00:00_to_2026-07-18T00:00:00'
# ]
