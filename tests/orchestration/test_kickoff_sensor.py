"""Regression tests for kickoff sensor partition matching."""

import json
from unittest.mock import MagicMock, patch

from dagster import (
    AssetKey,
    AssetMaterialization,
    build_sensor_context,
    instance_for_test,
)

from sds_data_manager.orchestration.dagster_utilities import (
    parse_dates_from_partition_key,
)
from sds_data_manager.orchestration.imap_dagster import defs, job_handlers

REPOINT_416 = "repoint416_2026-10-29T10:03:26_to_2026-10-30T10:03:28"
REPOINT_417 = "repoint417_2026-10-30T10:03:28_to_2026-10-31T10:03:22"


def _lo_l1a_job():
    return next(
        job
        for job in job_handlers
        if job.dagster_job_name == "lo_l1a_all_processing_job"
    )


def test_adjacent_repoint_partitions_do_not_overlap():
    """A shared endpoint must not map repoint 417's data to repoint 416."""
    with instance_for_test() as instance:
        instance.add_dynamic_partitions("repoint_partitions", [REPOINT_416])
        up_start, up_end = parse_dates_from_partition_key(REPOINT_417)

        target_partitions = _lo_l1a_job()._get_overlapping_target_partitions(
            REPOINT_417, up_start, up_end, instance
        )

    assert target_partitions == []


def test_kickoff_sensor_retries_until_matching_repoint_is_registered():
    """Keep the input event available until its exact target partition exists."""
    with instance_for_test() as instance:
        instance.add_dynamic_partitions("repoint_partitions", [REPOINT_416])
        instance.report_runless_asset_event(
            asset_event=AssetMaterialization(
                asset_key=AssetKey("lo_l0_raw"),
                partition=REPOINT_417,
                metadata={
                    "file_names": [
                        "imap_lo_l0_raw_20261030-repoint00417_v001.pkts"
                    ],
                    "input_type": "science",
                },
            )
        )

        kickoff_sensor = defs.get_sensor_def("lo_l1a_all_kickoff_sensor")
        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.all.return_value = []
        with patch(
            "sds_data_manager.orchestration.imap_job.db.Session"
        ) as session_factory:
            session_factory.return_value.__enter__.return_value = mock_session
            session_factory.return_value.__exit__.return_value = False

            first_context = build_sensor_context(instance=instance)
            first_requests = list(kickoff_sensor(first_context))

            assert first_requests == []
            assert json.loads(first_context.cursor)["lo_l0_raw"] == 0

            instance.add_dynamic_partitions("repoint_partitions", [REPOINT_417])
            second_context = build_sensor_context(
                instance=instance, cursor=first_context.cursor
            )
            second_requests = list(kickoff_sensor(second_context))

    assert [request.partition_key for request in second_requests] == [REPOINT_417]