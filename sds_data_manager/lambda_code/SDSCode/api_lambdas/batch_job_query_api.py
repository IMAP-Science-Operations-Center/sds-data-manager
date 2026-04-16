"""Lambda function to query batch processing job information from the database."""

import json
import logging
from datetime import datetime

from sqlalchemy import and_, select

from ..database import database as db
from ..database.models import ProcessingJob

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    """Lambda function to query batch processing job information."""
    logger.info("Received event: " + json.dumps(event, indent=2))

    processing_table = ProcessingJob
    filters = []

    # Only allow valid columns to be used for filtering
    valid_parameters = [
        column.key
        for column in processing_table.__table__.columns
        if column.key not in ["id"]
    ]

    for param, value in event.get("queryStringParameters", {}).items():
        if param not in valid_parameters:
            logger.info(f"Invalid parameter: {param}")
            return {
                "statusCode": 400,
                "body": json.dumps({"error": f"Invalid parameter: {param}"}),
            }

        column_attr = getattr(processing_table, param)

        if param == "start_date":
            try:
                date_value = datetime.strptime(value, "%Y%m%d")
                filters.append(column_attr == date_value)
            except ValueError:
                return {
                    "statusCode": 400,
                    "body": json.dumps(
                        {
                            "error": (
                                f"Invalid date format for {param}. Expected YYYYMMDD."
                            ),
                        }
                    ),
                }
        else:
            filters.append(column_attr == value)

    # Construct query using filters list
    with db.Session() as session:
        query = (
            select(processing_table)
            .where(and_(*filters))
            .order_by(ProcessingJob.id.desc())
            .limit(500)
        )
        result = session.scalars(query).all()
        logger.info(f"Query result: {result}")

        result_list = [_format_processing_job(job) for job in result]

    return {
        "statusCode": 200,
        "body": json.dumps(result_list),
    }


def _format_processing_job(processing_job: ProcessingJob) -> dict:
    """Format processing job information for output."""
    # Turn it into a dictionary and add a local command for
    # re-running the job locally
    job_dict = processing_job.to_dict()
    # container_command can be None, so we also need to set that case
    # to the empty string
    container_command = job_dict.pop("container_command", "") or ""
    local_command = (
        "imap_cli " + container_command.replace("--upload-to-sdc", "").strip()
    )
    # Put command at the start of the dict so it displays first
    return {"job_command": local_command, **job_dict}
