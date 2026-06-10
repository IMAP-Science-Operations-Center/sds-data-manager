"""Forward reprocessing events to Dagster."""

import json
import logging
import os

import boto3

sqs_client = boto3.client("sqs")
QUEUE_URL = os.environ["QUEUE_URL"]
# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    """Forward events to a reprocessing queue."""
    params = event.get("queryStringParameters", {})

    logger.info(
        f"Received event: {json.dumps(event, indent=2)}. Sending to reprocessing queue"
    )

    sqs_client.send_message(
        QueueUrl=QUEUE_URL,
        MessageBody=json.dumps(params),
        MessageGroupId="reprocess",
    )

    return {"statusCode": 200, "body": json.dumps({"message": "Reprocess job queued"})}
