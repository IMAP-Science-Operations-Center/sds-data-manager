"""Lambda function to monitor science data freshness.

This Lambda checks for science data gaps by querying the database
and publishing CloudWatch metrics when data is stale.
"""

import json
import logging
import os
from datetime import datetime, timezone

from sqlalchemy import func

from ..database import database as db
from ..database import models
from ..utils.monitoring_utils import (
    MONITORED_SCIENCE_PRODUCTS,
    AlarmResult,
    log_results_summary,
    publish_metric,
)

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Configuration from environment variables
METRIC_NAMESPACE = os.environ.get("METRIC_NAMESPACE", "IMAP/ScienceDataFreshness")


def get_most_recent_science_ingestion_date(
    instrument: str, data_level: str, descriptor: str
) -> int | None:
    """Get the age in days of the most recently ingested file from database.

    Parameters
    ----------
    instrument : str
        The instrument for which to check data freshness.
    data_level : str
        The data level for which to check data freshness.
    descriptor : str
        The descriptor for which to check data freshness.

    Returns
    -------
    int | None
        Number of days since the most recent start-date
        or None if no files exist
    """
    try:
        with db.Session() as session:
            # Query ScienceFiles table for the most recent start_date
            start_date = (
                session.query(func.max(models.ScienceFiles.start_date))
                .filter(
                    models.ScienceFiles.instrument == instrument,
                    models.ScienceFiles.data_level == data_level,
                    models.ScienceFiles.descriptor == descriptor,
                )
                .scalar()
            )

            if start_date is None:
                logger.warning(
                    f"No files found in ScienceFiles table for {instrument},"
                    f" {data_level}, {descriptor}"
                )
                return None

            # Calculate age in days
            age = (datetime.now(timezone.utc) - start_date).days
            logger.info(
                f"Most recent start_date is {age} days old (start_date: {start_date})"
            )

            return age

    except Exception as e:
        logger.error(f"Error checking ScienceFiles table: {e!s}")
        return None


def lambda_handler(event, context):
    """Lambda handler to check Science data freshness.

    This function runs on a schedule (daily) and queries the database
    for the most recently ingested files for each InstrumentMonitor.
    It publishes CloudWatch metrics that can be used to trigger alarms.

    Parameters
    ----------
    event : dict
        The JSON formatted document with the data required for the
        lambda function to process. Source is EventBridge scheduled rule.
    context : obj
        The context object for the lambda function

    Returns
    -------
    dict
        Response with status codfe and summary of results
    """
    logger.info(f"Received event: {event}")
    logger.info("Checking science data freshness from database")

    results = []

    for alarm in MONITORED_SCIENCE_PRODUCTS:
        logger.info(
            f"Checking ({alarm.instrument}, {alarm.data_level}, {alarm.descriptor})"
            f" with threshold {alarm.threshold_days} days"
        )

        days_old = get_most_recent_science_ingestion_date(
            alarm.instrument, alarm.data_level, alarm.descriptor
        )
        if days_old is None:
            # No files found - use a sentinel value
            days_old = 999
            logger.warning(f"{alarm.name()}: No files found in database ")
        alarm_name = alarm.name()
        # Publish the metric regardless of threshold
        publish_metric(METRIC_NAMESPACE, alarm_name, days_old)

        # Store result
        results.append(AlarmResult(alarm_name, days_old, alarm.threshold_days))

    # Log the results
    log_results_summary(results)

    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "message": "Science data check complete",
                "results": [r.to_dict() for r in results],
            }
        ),
    }
