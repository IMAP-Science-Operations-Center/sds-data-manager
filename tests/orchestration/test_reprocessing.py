"""Test the dagster reprocessing functionality."""

import json
import os
from unittest.mock import Mock, patch

import pytest
from dagster import AssetKey, DagsterInstance, Definitions, asset, build_sensor_context
from dagster._core.definitions.partitions.subset import DefaultPartitionsSubset

from sds_data_manager.orchestration import reprocessing
from sds_data_manager.orchestration.custom_partitions import (
    daily_partitions,
    repoint_partitions,
)


@pytest.fixture
def mock_env_vars():
    """Mock environment variables."""
    with patch.dict(
        os.environ,
        {
            "REPROCESSING_SQS_URL": "https://sqs.us-west-2.amazonaws.com/test/reprocessing_queue"
        },
    ):
        yield


def test_reprocess_one_repoint_partition(mock_env_vars) -> None:
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
        reprocessing.reprocess_sensor(context)

    # Check that there was a backfill submitted
    backfills = instance.get_backfills()
    assert len(backfills) == 1
    backfill_subset = backfills[
        0
    ].asset_backfill_data.target_subset.partitions_subsets_by_asset_key
    # There should be 2 assets reprocessed (1 job)
    assert len(backfill_subset) == 2
    assert backfill_subset[AssetKey("glows_l1a_de")] == DefaultPartitionsSubset(
        subset={"repoint123_2026-01-01T00:00:00_to_2026-01-02T00:00:00"}
    )


def test_reprocess_all_swe(mock_env_vars) -> None:
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
        reprocessing.reprocess_sensor(context)

    # Check that there was a backfill submitted
    backfills = instance.get_backfills()
    assert len(backfills) == 1
    backfill_subset = backfills[
        0
    ].asset_backfill_data.target_subset.partitions_subsets_by_asset_key
    # There should be 3 keys
    assert len(backfill_subset) == 3
    assert backfill_subset[AssetKey("swe_l1a_sci")] == DefaultPartitionsSubset(
        subset={"daily_2026-01-01T00:00:00_to_2026-01-02T00:00:00"}
    )


def test_reprocess_all_output_node(mock_env_vars) -> None:
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
        reprocessing.reprocess_sensor(context)

    # Check that there was a backfill submitted
    backfills = instance.get_backfills()
    assert len(backfills) == 1
    backfill_subset = backfills[
        0
    ].asset_backfill_data.target_subset.partitions_subsets_by_asset_key
    # There should be 3 keys
    assert len(backfill_subset) == 3
    assert backfill_subset[AssetKey("swe_l1a_sci")] == DefaultPartitionsSubset(
        subset={"daily_2026-01-01T00:00:00_to_2026-01-02T00:00:00"}
    )
