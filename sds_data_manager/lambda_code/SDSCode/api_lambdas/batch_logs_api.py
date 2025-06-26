"""Lambda function to handle batch logs API requests."""

import json
import logging
from urllib.parse import unquote

import boto3

LOGS_CLIENT = boto3.client("logs", region_name="us-west-2")

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    """Lambda function to handle batch logs API requests.

    Parameters
    ----------
    event : dict
        The JSON formatted document with the data required for the
        lambda function to process.
    context : LambdaContext
        The context object for the lambda function.

    Returns
    -------
    dict
        The response object containing the status code and message or logs.
    """
    logger.info("Received event: " + json.dumps(event, indent=2))

    # NOTE: We are using pathParams with the {id+} field in the API Gateway
    # We are guaranteed to have the id in the pathParameters and if it is not
    # a valid id, we will return an error message below.
    # e.g. ProcessingJob-codice-l3/default/bcc41f2cc3f146eb818a12eec1d7177c
    job_log_stream_id = unquote(event["pathParameters"]["id"])

    # Get logs from CloudWatch
    try:
        batch_log_group_name = "/aws/batch/job"
        # NOTE: Not setting 'limit' here to fetch all logs which can result in
        # max size of 1 MB and AWS API gateway has a limit of 6 MB for the
        # response body which covers 1 MB of logs.
        response = LOGS_CLIENT.get_log_events(
            logGroupName=batch_log_group_name,
            logStreamName=job_log_stream_id,
        )
        logs = [event["message"] for event in response.get("events", [])]
        logger.info(f"Fetched logs for {job_log_stream_id}.")
    except Exception as e:
        logger.error(f"Error fetching logs: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps(
                {"error": f"Could not fetch logs for id [{job_log_stream_id}]: {e!s}"}
            ),
        }

    return {
        "statusCode": 200,
        "body": "\n".join(logs),
    }
