"""IALiRT instrument data freshness alarm lambda."""

import json
import logging
import os
from datetime import datetime, timedelta, timezone

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

INSTRUMENTS = ["hit", "mag", "codice_hi", "codice_lo", "swe", "swapi"]


def check_instrument(table, instrument: str, cutoff: str) -> bool:
    """Return True if the instrument has data within the last 8 hours.

    Parameters
    ----------
    table : boto3 DynamoDB Table resource
        Table resource used to query.
    instrument : str
        Instrument partition key value.
    cutoff : str
        ISO 8601 timestamp; items with time_utc >= this value are considered fresh.

    Returns
    -------
    bool
        True if at least one item exists within the cutoff window.

    """
    response = table.query(
        KeyConditionExpression=Key("instrument").eq(instrument)
        & Key("time_utc").gte(cutoff),
        Limit=1,
    )
    return response["Count"] > 0


def notify_missing(sns_client, topic_arn: str, missing: list) -> None:
    """Publish an SNS alert for instruments with no recent data.

    Parameters
    ----------
    sns_client : boto3 SNS client
        Client used to publish the notification.
    topic_arn : str
        ARN of the SNS topic.
    missing : list
        Instrument names that have not reported data.

    """
    instruments_str = ", ".join(missing)
    message = (
        f"The following instruments have not reported data in the last 8 hours: "
        f"{instruments_str}"
    )
    sns_client.publish(
        TopicArn=topic_arn,
        Subject="I-ALiRT Instrument Data Missing",
        Message=message,
    )
    logger.info("Published SNS alert for missing instruments: %s", instruments_str)


def lambda_handler(event, context):
    """Check each instrument for data within the last 8 hours.

    Parameters
    ----------
    event : dict
        The JSON formatted document with the data required for the
        lambda function to process.
    context : LambdaContext
        This object provides methods and properties that provide
        information about the invocation, function,
        and runtime environment.

    """
    logger.info("Received event: %s", json.dumps(event))

    table_name = os.environ["DATA_TABLE"]
    topic_arn = os.environ["SNS_TOPIC_ARN"]

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat()

    table = boto3.resource("dynamodb").Table(table_name)
    sns_client = boto3.client("sns")

    missing = [
        instrument
        for instrument in INSTRUMENTS
        if not check_instrument(table, instrument, cutoff)
    ]

    if missing:
        notify_missing(sns_client, topic_arn, missing)

    return {"missing_instruments": missing}
