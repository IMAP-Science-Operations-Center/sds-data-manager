"""Tests for dynamic partition sensors in custom_partitions."""

import datetime
from unittest.mock import patch

from dagster import build_sensor_context, instance_for_test

from sds_data_manager.orchestration import custom_partitions


# wrap the real datetime *module*; override only datetime.datetime.now
class _FrozenDatetime(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime.datetime(2025, 9, 29, tzinfo=datetime.timezone.utc)


@patch.object(custom_partitions.datetime, "datetime", _FrozenDatetime)
def test_add_idex_10_day_partitions():
    """Check that add_idex_10_day_partitions adds the correct partitions."""
    with instance_for_test() as instance:
        instance.add_dynamic_partitions(
            "idex_10_day_partitions",
            ["idex10_2025-09-27T00:00:00_to_2025-10-07T00:00:00"],
        )
        context = build_sensor_context(instance=instance)
        sensor_result = custom_partitions.add_idex_10_day_partitions(context)

    new_partitions = sensor_result.dynamic_partitions_requests[0].partition_keys
    assert new_partitions == [
        "idex10_2025-10-07T00:00:00_to_2025-10-17T00:00:00",
        "idex10_2025-10-17T00:00:00_to_2025-10-27T00:00:00",
        "idex10_2025-10-27T00:00:00_to_2025-11-06T00:00:00",
    ]


@patch.object(custom_partitions.datetime, "datetime", _FrozenDatetime)
def test_add_idex_30_day_partitions():
    """Check that add_idex_30_day_partitions adds the correct partitions."""
    with instance_for_test() as instance:
        instance.add_dynamic_partitions(
            "idex_30_day_partitions",
            ["idex30_2025-09-27T00:00:00_to_2025-10-07T00:00:00"],
        )
        context = build_sensor_context(instance=instance)
        sensor_result = custom_partitions.add_idex_30_day_partitions(context)

    new_partitions = sensor_result.dynamic_partitions_requests[0].partition_keys
    assert new_partitions == [
        "idex30_2025-09-24T00:00:00_to_2025-09-27T00:00:00",
        "idex30_2025-09-27T00:00:00_to_2025-10-27T00:00:00",
    ]
