"""Creates RDS PostgreSQL database schema"""

import json
import logging
import sys

import requests

# Logger setup
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)


def send_response(event, context, response_data, response_status):
    """Construct a response to indicate the status of the custom
    resource lambda "SUCCESS" or "FAILED"

    Parameters
    -------
    event: dict
        JSON-formatted document that contains data for a Lambda function to process.
    context: Context
        provides methods and properties that provide information about the invocation,
        function, and execution environment.
    response_data: str
        either the query that was sent or an error message if the query failed.
    reponse_status: str
        "SUCCESS" or "FAILED" status depending on query status.
    """
    response_url = event["ResponseURL"]
    response_body = {
        "Status": response_status,
        "Reason": "See the details in CloudWatch Log Stream: "
        + context.log_stream_name,
        "PhysicalResourceId": context.log_stream_name,
        "StackId": event["StackId"],
        "RequestId": event["RequestId"],
        "LogicalResourceId": event["LogicalResourceId"],
        "Data": response_data,
    }

    json_response_body = json.dumps(response_body)

    headers = {"content-type": "", "content-length": str(len(json_response_body))}

    response = requests.put(response_url, data=json_response_body, headers=headers)
    response.raise_for_status()


def lambda_handler(event, context):
    logger.info("Sending custom resource response.")
    send_response(event, context, "", "SUCCESS")
