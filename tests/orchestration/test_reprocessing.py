"""Test the dagster reprocessing functionality."""

import json
from unittest.mock import Mock, patch

from dagster import AssetKey, DagsterInstance, Definitions, asset, build_sensor_context

from sds_data_manager.orchestration import reprocessing
from sds_data_manager.orchestration.custom_partitions import (
    daily_partitions,
    repoint_partitions,
)


def test_reprocess_one_repoint_partition() -> None:
    """Test the reprocessing functionality."""
    instance = DagsterInstance.ephemeral()
    instance.add_dynamic_partitions(
        "repoint_partitions", ["repoint123_2026-01-01T00:00:00_to_2026-01-02T00:00:00"]
    )

    # Set up upstream assets for glows pipeline
    @asset(partitions_def=repoint_partitions)
    def glows_l1a_de():
        pass

    @asset(partitions_def=repoint_partitions)
    def glows_l1a_hist():
        pass

    # Create a definition object with all the related assets
    defs = Definitions(assets=[glows_l1a_de, glows_l1a_hist])

    context = build_sensor_context(
        instance=instance,
        repository_def=defs.get_repository_def(),
    )

    mock_sqs_client = Mock()

    mock_sqs_client.receive_message.return_value = {
        "Messages": [
            {
                "MessageId": "test-id",
                "ReceiptHandle": "test-handle",
                "Body": json.dumps(
                    {
                        "data_level": "l1a",
                        "descriptor": "all",
                        "end_date": "20260101",
                        "instrument": "glows",
                        "reprocessing": "True",
                        "start_date": "20260101",
                    }
                ),
            }
        ]
    }
    with (
        patch.object(reprocessing, "SQS_CLIENT", mock_sqs_client),
    ):
        run_requests = list(reprocessing.reprocess_sensor(context))

    # Check that a run was requested for the one matching partition
    assert len(run_requests) == 1
    run_request = run_requests[0]
    assert (
        run_request.partition_key
        == "repoint123_2026-01-01T00:00:00_to_2026-01-02T00:00:00"
    )
    # There should be 2 assets reprocessed (1 job)
    assert set(run_request.asset_selection) == {
        AssetKey("glows_l1a_de"),
        AssetKey("glows_l1a_hist"),
    }


def test_reprocess_all_swe() -> None:
    """Test the reprocessing functionality for all of swe."""
    instance = DagsterInstance.ephemeral()
    instance.add_dynamic_partitions(
        "daily_partitions", ["daily_2026-01-01T00:00:00_to_2026-01-02T00:00:00"]
    )

    # Set up upstream assets for the codice pipeline
    @asset(partitions_def=daily_partitions)
    def swe_l1a_sci():
        pass

    @asset(partitions_def=daily_partitions)
    def swe_l1a_hk():
        pass

    @asset(partitions_def=daily_partitions)
    def swe_l1a_cemraw():
        pass

    # Create a definition object with all the related assets
    defs = Definitions(assets=[swe_l1a_sci, swe_l1a_hk, swe_l1a_cemraw])

    context = build_sensor_context(
        instance=instance,
        repository_def=defs.get_repository_def(),
    )

    mock_sqs_client = Mock()

    mock_sqs_client.receive_message.return_value = {
        "Messages": [
            {
                "MessageId": "test-id",
                "ReceiptHandle": "test-handle",
                "Body": json.dumps(
                    {
                        "end_date": "20260101",
                        "instrument": "swe",
                        "reprocessing": "True",
                        "start_date": "20260101",
                    }
                ),
            }
        ]
    }
    with (
        patch.object(reprocessing, "SQS_CLIENT", mock_sqs_client),
    ):
        run_requests = list(reprocessing.reprocess_sensor(context))

    # Check that a run was requested for the one matching partition
    assert len(run_requests) == 1
    run_request = run_requests[0]
    assert (
        run_request.partition_key == "daily_2026-01-01T00:00:00_to_2026-01-02T00:00:00"
    )
    # There should be 3 assets reprocessed (1 job)
    assert set(run_request.asset_selection) == {
        AssetKey("swe_l1a_sci"),
        AssetKey("swe_l1a_hk"),
        AssetKey("swe_l1a_cemraw"),
    }


def test_reprocess_all_output_node() -> None:
    """Test the reprocessing functionality for an output node."""
    instance = DagsterInstance.ephemeral()
    instance.add_dynamic_partitions(
        "daily_partitions", ["daily_2026-01-01T00:00:00_to_2026-01-02T00:00:00"]
    )

    # Set up upstream assets for the swe pipeline
    @asset(partitions_def=daily_partitions)
    def swe_l1a_sci():
        pass

    @asset(partitions_def=daily_partitions)
    def swe_l1a_hk():
        pass

    @asset(partitions_def=daily_partitions)
    def swe_l1a_cemraw():
        pass

    # Create a definition object with all the related assets
    defs = Definitions(assets=[swe_l1a_cemraw, swe_l1a_hk, swe_l1a_sci])

    context = build_sensor_context(
        instance=instance,
        repository_def=defs.get_repository_def(),
    )

    mock_sqs_client = Mock()
    # This reprocessing command specifies an output node
    # Test the reprocessing functionality that it can find the root node and reprocess.
    mock_sqs_client.receive_message.return_value = {
        "Messages": [
            {
                "MessageId": "test-id",
                "ReceiptHandle": "test-handle",
                "Body": json.dumps(
                    {
                        "end_date": "20260101",
                        "instrument": "swe",
                        "reprocessing": "True",
                        "data_level": "l1a",
                        "descriptor": "sci",
                        "start_date": "20260101",
                    }
                ),
            }
        ]
    }
    with (
        patch.object(reprocessing, "SQS_CLIENT", mock_sqs_client),
    ):
        run_requests = list(reprocessing.reprocess_sensor(context))

    # Check that a run was requested for the one matching partition
    assert len(run_requests) == 1
    run_request = run_requests[0]
    assert (
        run_request.partition_key == "daily_2026-01-01T00:00:00_to_2026-01-02T00:00:00"
    )
    # There should be 3 assets reprocessed (1 job)
    assert set(run_request.asset_selection) == {
        AssetKey("swe_l1a_sci"),
        AssetKey("swe_l1a_hk"),
        AssetKey("swe_l1a_cemraw"),
    }
