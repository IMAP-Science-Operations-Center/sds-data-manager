"""Lambda function to handle batch logs API requests."""

import json
import logging

import boto3

LOGS_CLIENT = boto3.client("logs")

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

    if not event.get("queryStringParameters"):
        return {
            "statusCode": 400,
            "body": (
                "Required Batch job log stream ID. Please provide 'job_log_stream_id'. "
                "Eg. job_log_stream_id=ProcessingJob-codice-l3/default/bcc41f2cc3f146eb"
                "818a12eec1d7177c"
            ),
        }

    job_log_stream_id = event["queryStringParameters"].get("job_log_stream_id")

    if not job_log_stream_id:
        logger.info("No job_log_stream_id provided in query parameters.")
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "job_log_stream_id is required."}),
        }
    logger.info(f"Fetching logs for job_log_stream_id: {job_log_stream_id}")

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
            "body": json.dumps({"error": f"Could not fetch logs: {e!s}"}),
        }

    return {
        "statusCode": 200,
        "body": "\n".join(logs),
    }
