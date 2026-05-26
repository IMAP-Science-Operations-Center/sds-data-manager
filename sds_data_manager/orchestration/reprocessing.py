"""Reprocessing logic."""

import datetime
import json
import os

import boto3
from dagster import AssetKey, AssetSelection, SensorEvaluationContext, sensor
from dagster._core.execution.backfill import PartitionBackfill

from sds_data_manager.orchestration.dagster_utilities import get_affected_partitions
from sds_data_manager.orchestration.dependency import (
    DependencyConfigReader,
    get_kickoff_jobs,
)
from sds_data_manager.orchestration.imap_job import partition_map

SQS_CLIENT = boto3.client("sqs", "us-west-2")


def read_sqs_messages(sqs_queue_url=None):
    """Read SQS messages from the reprocessing queue."""
    response = SQS_CLIENT.receive_message(
        QueueUrl=sqs_queue_url,
        MaxNumberOfMessages=10,
        WaitTimeSeconds=1,
    )
    return response.get("Messages", [])


@sensor(
    asset_selection=AssetSelection.all(),
    minimum_interval_seconds=100,  # TODO what do we want here
)
def reprocess_sensor(context: SensorEvaluationContext):
    """Sensor that triggers reprocessing backfills."""
    sqs_queue_url = os.getenv("REPROCESSING_SQS_URL")
    messages = read_sqs_messages(sqs_queue_url)
    reader = DependencyConfigReader()
    if not messages:
        return None

    for message in messages:
        params = json.loads(message["Body"])

        instrument = params.get("instrument")
        data_level = params.get("data_level")
        descriptor = params.get("descriptor")
        start_date = params.get("start_date")
        end_date = params.get("end_date")

        context.log.info(
            f"A reprocessing event was triggered with the parameters: {instrument=}, "
            f"{data_level=}, {descriptor=}, {start_date=}, {end_date=}"
        )
        if not end_date or not start_date:
            raise ValueError(
                "Start date and end date are required for a reprocessing Event."
            )
        if not instrument:
            raise ValueError("Instrument must be provided for a reprocessing event.")

        if bool(data_level) != bool(descriptor):
            raise ValueError(
                "data_level and descriptor must both be provided or both None."
            )

        if not data_level:
            # If data_level is not provided, we need to reprocess all levels.
            # Get the jobs that kick of each pipeline, to trigger processing
            # for all levels.
            root_job = get_kickoff_jobs(instrument)[0]
            # Create the asset name based on the root job information
            asset_name = f"{instrument}_{root_job.data_type}_{root_job.descriptor}"
            partition = root_job.partition
        else:
            # If data_level is provided (and therefore descriptor) construct the
            # asset name using the input parameters
            asset_name = f"{instrument}_{data_level}_{descriptor}"
            # Get the partition type from the dependency config
            partition = reader.config[(instrument, data_level, descriptor)].partition

        # Get the partitions definition based on the DependencyNode
        partition_def = partition_map.get(partition)
        # convert start and end date to datetime
        start_date = datetime.datetime.strptime(start_date, "%Y%m%d").replace(
            tzinfo=datetime.timezone.utc
        )
        end_date = datetime.datetime.strptime(end_date, "%Y%m%d").replace(
            tzinfo=datetime.timezone.utc
        )

        # Determine the affected partitions based on the start_date and end_date
        partition_keys = get_affected_partitions(
            context, partition_def, start_date, end_date
        )
        if not partition_keys:
            return None
        context.log.info(
            f"Reprocessing asset {asset_name} across {partition_keys} partitions"
        )

        backfill = PartitionBackfill.from_asset_partitions(
            backfill_id=f"reprocess-{instrument}-{int(datetime.datetime.now().timestamp())}",
            asset_graph=context.repository_def.asset_graph,
            partition_names=partition_keys,
            asset_selection=[AssetKey(asset_name)],
            backfill_timestamp=datetime.datetime.now(datetime.timezone.utc).timestamp(),
            tags={
                "instrument": instrument,
                "descriptor": descriptor or "",
                "data_level": data_level or "",
            },
            dynamic_partitions_store=context.instance,
            all_partitions=False,
            title=None,
            description=None,
            run_config=None,
        )

        context.instance.add_backfill(backfill)

        SQS_CLIENT.delete_message(
            QueueUrl=sqs_queue_url,
            ReceiptHandle=message["ReceiptHandle"],
        )

    return None
