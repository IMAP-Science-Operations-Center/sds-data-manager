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
from sds_data_manager.orchestration.types import Node

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
    minimum_interval_seconds=100,
)
def reprocess_sensor(context: SensorEvaluationContext):
    """Sensor that triggers reprocessing backfills."""
    sqs_queue_url = os.getenv("REPROCESSING_SQS_URL")
    messages = read_sqs_messages(sqs_queue_url)

    context.log.info(f"Found {len(messages)} reprocessing events")

    if not messages:
        return None

    reader = DependencyConfigReader()

    for message in messages:
        params = json.loads(message["Body"])

        instrument = params.get("instrument")
        data_level = params.get("data_level")
        descriptor = params.get("descriptor")
        start_date = params.get("start_date")
        end_date = params.get("end_date")

        context.log.info(
            f"Reprocessing event received: {instrument=}, "
            f"{data_level=}, {descriptor=}, {start_date=}, {end_date=}"
        )

        # Check inputs. If they are not valid, log a warning and delete the message to
        # avoid retrying.
        if not validate_reprocess_params(
            context, instrument, data_level, descriptor, start_date, end_date
        ):
            delete_sqs_message(sqs_queue_url, message)
            continue

        # Get the assets for this reprocessing
        result = get_job_assets(context, reader, instrument, data_level, descriptor)
        if result is None:
            # If there is no root node continue.
            delete_sqs_message(sqs_queue_url, message)
            continue

        output_asset_keys, partition = result
        partition_def = partition_map.get(partition)

        start_dt = datetime.datetime.strptime(start_date, "%Y%m%d").replace(
            tzinfo=datetime.timezone.utc
        )
        end_dt = datetime.datetime.strptime(end_date, "%Y%m%d").replace(
            tzinfo=datetime.timezone.utc
        )

        partition_keys = get_affected_partitions(
            context, partition_def, start_dt, end_dt
        )
        if not partition_keys:
            context.log.warning(
                f"No partitions found for {output_asset_keys} between {start_date} and "
                f"{end_date}."
            )
            delete_sqs_message(sqs_queue_url, message)
            continue

        context.log.info(
            f"Reprocessing {output_asset_keys} across partitions: {partition_keys}"
        )

        backfill = PartitionBackfill.from_asset_partitions(
            backfill_id=f"reprocess-{instrument}-{int(datetime.datetime.now().timestamp())}",
            asset_graph=context.repository_def.asset_graph,
            partition_names=partition_keys,
            asset_selection=output_asset_keys,
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

        # After a submitting the backfill, remove the sqs message
        delete_sqs_message(sqs_queue_url, message)

    return None


def validate_reprocess_params(
    context: SensorEvaluationContext,
    instrument: str | None,
    data_level: str | None,
    descriptor: str | None,
    start_date: str | None,
    end_date: str | None,
) -> bool:
    """Validate reprocessing parameters. Logs a warning and returns False if invalid."""
    if not instrument:
        context.log.warning("Reprocessing message missing 'instrument'. Skipping.")
        return False
    if not start_date or not end_date:
        context.log.warning(
            f"Reprocessing message for {instrument} missing start and end date. "
            f"Skipping."
        )
        return False
    if bool(data_level) != bool(descriptor):
        context.log.warning(
            f"Reprocessing message for {instrument}. Both data_level and descriptor "
            f" must be provided or both omitted. Skipping."
        )
        return False
    return True


def get_job_assets(
    context: SensorEvaluationContext,
    reader: DependencyConfigReader,
    instrument: str,
    data_level: str | None,
    descriptor: str | None,
) -> tuple[list[AssetKey], str] | None:
    """Resolve the job node to reprocess. Returns None if not found."""
    if not data_level:
        kickoff_jobs = get_kickoff_jobs(instrument)
        if not kickoff_jobs:
            context.log.warning(f"No kickoff jobs found for {instrument}. Skipping.")
            return None
        job_node = kickoff_jobs[0]
    else:
        node_key = (instrument, data_level, descriptor)
        if node_key in reader.config:
            job_node = reader.config[node_key]
        else:
            try:
                job_node = reader.get_node_for_output(
                    Node(instrument, data_level, descriptor)
                )
            except ValueError:
                context.log.warning(
                    f"No job found for ({instrument}, {data_level}, {descriptor}). "
                    "Check that the instrument, data_level, and descriptor combination "
                    "is valid. Skipping."
                )
                return None
    # get the output keys for the job node. If there are no outputs, log a warning and
    # skip.
    output_keys = [
        AssetKey(output.to_dagster_asset().to_user_string())
        for output in job_node.outputs
    ]
    if not output_keys:
        context.log.warning(
            f"Job node for ({instrument}, {data_level}, {descriptor}) has no outputs."
            f" Skipping."
        )
        return None

    return output_keys, job_node.partition


def delete_sqs_message(queue_url: str, message: dict) -> None:
    """Delete a message from the SQS queue."""
    SQS_CLIENT.delete_message(
        QueueUrl=queue_url,
        ReceiptHandle=message["ReceiptHandle"],
    )


sensors = [reprocess_sensor]
