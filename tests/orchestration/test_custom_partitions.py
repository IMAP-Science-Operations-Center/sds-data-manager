"""Tests for dynamic partition sensors in custom_partitions."""

import datetime
from unittest import mock

from dagster import build_sensor_context, instance_for_test

from sds_data_manager.orchestration import custom_partitions


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


@mock.patch("sds_data_manager.orchestration.custom_partitions.datetime")
def test_add_idex_30_day_partitions(mock_datetime):
    """Check that add_idex_30_day_partitions adds the correct partitions."""
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
            "idex_30_day_partitions",
            ["idex30_2025-09-27T00:00:00_to_2025-10-07T00:00:00"],
        )
        context = build_sensor_context(instance=instance)
        # Trigger the sensor. This should add more partitions.
        sensor_result = custom_partitions.add_idex_30_day_partitions(context)

    new_partitions = sensor_result.dynamic_partitions_requests[0].partition_keys
    assert new_partitions == [
        "idex30_2025-09-24T00:00:00_to_2025-09-27T00:00:00",
        "idex30_2025-09-27T00:00:00_to_2025-10-27T00:00:00",
    ]
