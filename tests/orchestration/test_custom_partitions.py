"""Tests for dynamic partition sensors in custom_partitions."""

import datetime
from unittest import mock
from unittest.mock import MagicMock, patch

from dagster import DagsterInstance, build_sensor_context, instance_for_test

from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.orchestration import custom_partitions
from sds_data_manager.orchestration.custom_partitions import (
    add_pointing_attitude_partitions,
)
from sds_data_manager.orchestration.maps_utils import (
    FIRST_MAP_START_DATE,
    get_map_partition_names,
)


@mock.patch("sds_data_manager.orchestration.custom_partitions.datetime")
def test_add_idex_10_day_partitions(mock_datetime):
    """Check that add_idex_10_day_partitions adds the correct partitions."""
    mock_datetime.datetime.now.return_value = datetime.datetime(
        2025, 9, 29, tzinfo=datetime.timezone.utc
    )
    mock_datetime.datetime.fromisoformat.return_value = datetime.datetime(
        2025, 9, 24, tzinfo=datetime.timezone.utc
    )
    mock_datetime.timezone = datetime.timezone
    mock_datetime.timedelta = datetime.timedelta
    with instance_for_test() as instance:
        # Mock existing partitions
        instance.add_dynamic_partitions(
            "idex_10_day_partitions",
            ["idex10_2025-09-27T00:00:00_to_2025-10-07T00:00:00"],
        )
        context = build_sensor_context(instance=instance)
        # Trigger the sensor. This should add more partitions.
        sensor_result = custom_partitions.add_idex_10_day_partitions(context)

    new_partitions = sensor_result.dynamic_partitions_requests[0].partition_keys
    assert new_partitions == [
        "idex10_2025-10-07T00:00:00_to_2025-10-17T00:00:00",
        "idex10_2025-10-17T00:00:00_to_2025-10-27T00:00:00",
        "idex10_2025-10-27T00:00:00_to_2025-11-06T00:00:00",
    ]


def test_add_cadence_map_partitions_open_window():
    """Check that get_map_partition_names includes the active open window."""
    partition_names = get_map_partition_names(
        "3mo",
        start_time=FIRST_MAP_START_DATE,
        current_time=datetime.datetime(2026, 8, 20, tzinfo=datetime.timezone.utc),
        include_open=True,
    )

    assert partition_names == [
        "cadence-3mo_2026-01-17T00:00:00_to_2026-04-18T00:00:00",
        "cadence-3mo_2026-04-18T00:00:00_to_2026-07-18T00:00:00",
        "cadence-3mo_2026-07-18T00:00:00_to_2026-10-17T00:00:00",
    ]


# ---------------------------------------------------------------------------
# add_pointing_attitude_partitions - helpers
# ---------------------------------------------------------------------------


def _dt(s):
    """Parse %Y-%m-%dT%H:%M:%S to a UTC-aware datetime."""
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(
        tzinfo=datetime.timezone.utc
    )


def make_ah_kernel(min_str, max_str):
    """Minimal mock SPICEFiles attitude_history record."""
    k = MagicMock()
    k.min_date_datetime = _dt(min_str)
    k.max_date_datetime = _dt(max_str)
    return k


def make_pointing(start_str, end_str):
    """Minimal mock PointingTable record."""
    p = MagicMock()
    p.pointing_start_utc = _dt(start_str)
    p.pointing_end_utc = _dt(end_str)
    return p


def _run_pointing_attitude_sensor(instance, ah_kernels, pointing_query_results):
    """Run the sensor with mocked DB data and return the SensorResult.

    ah_kernels may be passed in any order -- the sensor sorts them by coverage
    duration (longest first) and drops any kernel fully contained within
    another before querying pointings. pointing_query_results is therefore a
    flat list in *duration-descending, maximal-kernel* order:
    [first_overlapping_0, last_covered_0, first_overlapping_1, last_covered_1, ...]

    Each surviving (maximal) kernel issues two PointingTable queries
    (first_overlapping then last_covered), so the list length must equal
    2 * (number of maximal kernels), not 2 * len(ah_kernels).
    """
    mock_session = MagicMock()

    spice_query = MagicMock()
    spice_query.filter.return_value.all.return_value = ah_kernels

    pointing_query = MagicMock()
    pointing_query.filter.return_value.order_by.return_value.first.side_effect = (
        pointing_query_results
    )

    def _query_dispatch(table):
        if table is models.SPICEFiles:
            return spice_query
        return pointing_query

    mock_session.query.side_effect = _query_dispatch

    context = build_sensor_context(instance=instance)
    with patch.object(db, "Session") as mock_cls:
        mock_cls.return_value.__enter__.return_value = mock_session
        mock_cls.return_value.__exit__.return_value = False
        return add_pointing_attitude_partitions(context)


# ---------------------------------------------------------------------------
# add_pointing_attitude_partitions - tests
# ---------------------------------------------------------------------------


def test_no_ah_kernels_returns_no_requests():
    """Sensor is a no-op when there are no attitude_history kernels."""
    instance = DagsterInstance.ephemeral()
    result = _run_pointing_attitude_sensor(
        instance, ah_kernels=[], pointing_query_results=[]
    )
    assert result.dynamic_partitions_requests == []


def test_no_overlapping_pointings_creates_no_partition():
    """No partition is created when no pointings overlap with the ah kernel."""
    instance = DagsterInstance.ephemeral()
    kernel = make_ah_kernel("2025-01-01T00:00:00", "2025-04-01T00:00:00")
    result = _run_pointing_attitude_sensor(instance, [kernel], [None, None])
    assert result.dynamic_partitions_requests == []


def test_partial_coverage_only_creates_no_partition():
    """No partition is created when overlap exists but no pointing is fully covered."""
    instance = DagsterInstance.ephemeral()
    kernel = make_ah_kernel("2025-01-01T00:00:00", "2025-04-01T00:00:00")
    first = make_pointing("2025-01-01T00:00:00", "2025-01-15T00:00:00")
    result = _run_pointing_attitude_sensor(instance, [kernel], [first, None])
    assert result.dynamic_partitions_requests == []


def test_new_partition_created():
    """A new partition is added when none exists for the kernel's coverage."""
    instance = DagsterInstance.ephemeral()
    kernel = make_ah_kernel("2025-01-01T00:00:00", "2025-04-01T00:00:00")
    first = make_pointing("2025-01-01T00:00:00", "2025-01-15T00:00:00")
    last = make_pointing("2025-03-15T00:00:00", "2025-04-01T00:00:00")

    result = _run_pointing_attitude_sensor(instance, [kernel], [first, last])

    assert len(result.dynamic_partitions_requests) == 1
    assert result.dynamic_partitions_requests[0].partition_keys == [
        "pointingattitude_2025-01-01T00:00:00_to_2025-04-01T00:00:00"
    ]


def test_already_up_to_date_creates_no_requests():
    """No requests are made when the exact partition already exists."""
    instance = DagsterInstance.ephemeral()
    existing = "pointingattitude_2025-01-01T00:00:00_to_2025-04-01T00:00:00"
    instance.add_dynamic_partitions("pointing_attitude_partitions", [existing])

    kernel = make_ah_kernel("2025-01-01T00:00:00", "2025-04-01T00:00:00")
    first = make_pointing("2025-01-01T00:00:00", "2025-01-15T00:00:00")
    last = make_pointing("2025-03-15T00:00:00", "2025-04-01T00:00:00")

    result = _run_pointing_attitude_sensor(instance, [kernel], [first, last])

    assert result.dynamic_partitions_requests == []


def test_growing_append_replaces_existing_partition():
    """When the ah kernel extends its end date, the stale partition is replaced.

    This is the normal appending case: a kernel with the same start date grows
    its end date with each new delivery.
    """
    instance = DagsterInstance.ephemeral()
    old_key = "pointingattitude_2025-01-01T00:00:00_to_2025-02-15T00:00:00"
    instance.add_dynamic_partitions("pointing_attitude_partitions", [old_key])

    kernel = make_ah_kernel("2025-01-01T00:00:00", "2025-04-01T00:00:00")
    first = make_pointing("2025-01-01T00:00:00", "2025-01-15T00:00:00")
    last = make_pointing("2025-03-15T00:00:00", "2025-04-01T00:00:00")

    result = _run_pointing_attitude_sensor(instance, [kernel], [first, last])

    # Sensor always emits delete before add
    assert len(result.dynamic_partitions_requests) == 2
    delete_req = result.dynamic_partitions_requests[0]
    add_req = result.dynamic_partitions_requests[1]
    assert delete_req.partition_keys == [old_key]
    assert add_req.partition_keys == [
        "pointingattitude_2025-01-01T00:00:00_to_2025-04-01T00:00:00"
    ]


def test_retroactive_combined_file_subsumes_daily_partitions():
    """A combined ah file replaces many small early-mission daily partitions.

    Early in the mission, ah files covered ~1 day each. When conops changed to
    the appending scheme, the team retroactively produced a single file covering
    the first ~3 months, making all the earlier daily partitions stale.
    """
    instance = DagsterInstance.ephemeral()
    old_keys = [
        "pointingattitude_2025-01-01T00:00:00_to_2025-01-15T00:00:00",
        "pointingattitude_2025-01-15T00:00:00_to_2025-01-30T00:00:00",
        "pointingattitude_2025-01-30T00:00:00_to_2025-02-14T00:00:00",
    ]
    instance.add_dynamic_partitions("pointing_attitude_partitions", old_keys)

    # Single combined kernel covering all three old periods
    kernel = make_ah_kernel("2025-01-01T00:00:00", "2025-04-01T00:00:00")
    first = make_pointing("2025-01-01T00:00:00", "2025-01-15T00:00:00")
    last = make_pointing("2025-03-15T00:00:00", "2025-04-01T00:00:00")

    result = _run_pointing_attitude_sensor(instance, [kernel], [first, last])

    assert len(result.dynamic_partitions_requests) == 2
    delete_req = result.dynamic_partitions_requests[0]
    add_req = result.dynamic_partitions_requests[1]
    assert set(delete_req.partition_keys) == set(old_keys)
    assert add_req.partition_keys == [
        "pointingattitude_2025-01-01T00:00:00_to_2025-04-01T00:00:00"
    ]


def test_subsumed_kernel_is_ignored():
    """A kernel fully contained within another kernel's coverage is dropped.

    Old daily ah kernels are never deleted from the DB, so once a combined
    kernel supersedes them they must be filtered out entirely -- otherwise
    the sensor would keep regenerating (and re-adding) their partitions even
    after those partitions were deleted as subsumed. If the daily kernel were
    incorrectly processed here, the mocked pointing-query side_effect list
    (sized for only one kernel) would be exhausted and raise.
    """
    instance = DagsterInstance.ephemeral()
    daily = make_ah_kernel("2025-01-05T00:00:00", "2025-01-06T00:00:00")
    combined = make_ah_kernel("2025-01-01T00:00:00", "2025-04-01T00:00:00")

    first = make_pointing("2025-01-01T00:00:00", "2025-01-15T00:00:00")
    last = make_pointing("2025-03-15T00:00:00", "2025-04-01T00:00:00")

    # Pass the smaller kernel first to show input order doesn't matter.
    result = _run_pointing_attitude_sensor(instance, [daily, combined], [first, last])

    assert len(result.dynamic_partitions_requests) == 1
    assert result.dynamic_partitions_requests[0].partition_keys == [
        "pointingattitude_2025-01-01T00:00:00_to_2025-04-01T00:00:00"
    ]


def test_non_nested_kernels_both_processed_longest_first():
    """Overlapping but non-nested kernels are both kept, longest duration first.

    Neither kernel's coverage fully contains the other's, so both are
    maximal and both produce a partition. They must be processed in
    duration-descending order regardless of input order, so that larger
    partitions land first in the add request.
    """
    instance = DagsterInstance.ephemeral()
    # 59-day coverage
    long_kernel = make_ah_kernel("2025-01-01T00:00:00", "2025-03-01T00:00:00")
    # 45-day coverage, overlapping but extending past long_kernel's end
    other_kernel = make_ah_kernel("2025-02-15T00:00:00", "2025-04-01T00:00:00")

    first_long = make_pointing("2025-01-01T00:00:00", "2025-01-10T00:00:00")
    last_long = make_pointing("2025-02-20T00:00:00", "2025-03-01T00:00:00")
    first_other = make_pointing("2025-02-15T00:00:00", "2025-02-20T00:00:00")
    last_other = make_pointing("2025-03-25T00:00:00", "2025-04-01T00:00:00")

    # Pass the shorter kernel first to show the sensor still sorts internally.
    result = _run_pointing_attitude_sensor(
        instance,
        [other_kernel, long_kernel],
        [first_long, last_long, first_other, last_other],
    )

    assert len(result.dynamic_partitions_requests) == 1
    assert result.dynamic_partitions_requests[0].partition_keys == [
        "pointingattitude_2025-01-01T00:00:00_to_2025-03-01T00:00:00",
        "pointingattitude_2025-02-15T00:00:00_to_2025-04-01T00:00:00",
    ]
