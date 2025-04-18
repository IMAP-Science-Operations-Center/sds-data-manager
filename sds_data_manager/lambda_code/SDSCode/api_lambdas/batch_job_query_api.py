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

    # If query parameters are not provided, return last n records
    if not event.get("queryStringParameters"):
        with db.Session() as session:
            query = select(ProcessingJob).order_by(ProcessingJob.id.desc()).limit(100)
            result = session.execute(query).scalars().all()
            logger.info(
                f"No input parameters provided. Returning latest 100 records: {result}"
            )

            result_list = [job.to_dict() for job in result] if result else []

        return {
            "statusCode": 200,
            "body": json.dumps(result_list),
        }

    processing_table = ProcessingJob
    filters = []

    # Only allow valid columns to be used for filtering
    valid_parameters = [
        column.key
        for column in processing_table.__table__.columns
        if column.key not in ["id"]
    ]

    for param, value in event["queryStringParameters"].items():
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
        query = select(processing_table).where(and_(*filters))
        result = session.execute(query).scalars().all()
        logger.info(f"Query result: {result}")

        result_list = [job.to_dict() for job in result] if result else []

    return {
        "statusCode": 200,
        "body": json.dumps(result_list),
    }
