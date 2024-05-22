"""Creates RDS PostgreSQL database schema."""

import json
import logging

import requests
from SDSCode.database import database as db
from SDSCode.database.models import Base
from SDSCode.dependency_config import downstream_dependents, upstream_dependents
from SDSCode.dependency_config_ultra import (
    downstream_dependents as downstream_dependents_ultra,
)
from SDSCode.dependency_config_ultra import (
    upstream_dependents as upstream_dependents_ultra,
)
from sqlalchemy.orm import Session

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def send_response(event, context, response_status):
    """Send the response.

    Constructs a response to indicate the status of the custom resource lambda
    "SUCCESS" or "FAILED"

    Parameters
    ----------
    event: dict
        JSON-formatted document that contains data for a Lambda function to process.
    context: Context
        provides methods and properties that provide information about the invocation,
        function, and execution environment.
    response_status: str
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
    }

    json_response_body = json.dumps(response_body)

    headers = {"content-type": "", "content-length": str(len(json_response_body))}

    response = requests.put(
        response_url, data=json_response_body, headers=headers, timeout=60
    )
    response.raise_for_status()


def lambda_handler(event, context):
    """Entry point to the create schema lambda."""
    logger.info("Creating RDS tables")
    logger.info(event)
    try:
        # Create tables
        engine = db.get_engine()
        Base.metadata.create_all(engine)
        # Write dependencies to pre-processing dependency table
        downstream_dependents.extend(downstream_dependents_ultra)
        downstream_dependents.extend(upstream_dependents)
        downstream_dependents.extend(upstream_dependents_ultra)
        with Session(engine) as session:
            session.add_all(downstream_dependents)
            session.commit()
        send_response(event, context, "SUCCESS")
    except Exception as e:
        logger.error(e)
        send_response(event, context, "FAILED")
