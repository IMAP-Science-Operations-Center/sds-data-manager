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
    AlarmResult,
    log_results_summary,
    publish_metric,
)
from ..utils.science_monitoring_config import (
    MONITORED_SCIENCE_PRODUCTS,
    Alarm,
)

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Configuration from environment variables
METRIC_NAMESPACE = os.environ.get("METRIC_NAMESPACE", "IMAP/ScienceDataFreshness")


def get_days_since_new_data(alarm: Alarm) -> int | None:
    """Get how many days it has been since new data arrived for a product.

    The calculation changes based on what alarm.multi_day_files is. If
    true, multi-day files like IDEX sci-10days are checked by the most
    recent ingestion date of the newest start_date. If false, daily products
     are checked by the newest start_date.

    Parameters
    ----------
    alarm : Alarm
        The product to check.

    Returns
    -------
    int | None
        Number of days since new data arrived, or None if no files exist.
    """
    product_filters = (
        models.ScienceFiles.instrument == alarm.instrument,
        models.ScienceFiles.data_level == alarm.data_level,
        models.ScienceFiles.descriptor == alarm.descriptor,
    )
    try:
        with db.Session() as session:
            newest_start_date_query = session.query(
                func.max(models.ScienceFiles.start_date)
            ).filter(*product_filters)

            # For 10-day files like IDEX, the newest start_date doest change until the
            # next 10-day start date. The current 10-day window file keeps updating with
            # new data when l0 packets are delivered. To properly check data freshness,
            # we should instead find the most recent 10-day window start date, and
            # query for the latest ingestion date.
            if alarm.multi_day_files:
                most_recent_update_date = (
                    session.query(func.max(models.ScienceFiles.ingestion_date))
                    .filter(
                        *product_filters,
                        models.ScienceFiles.start_date
                        == newest_start_date_query.scalar_subquery(),
                    )
                    .scalar()
                )
            else:
                most_recent_update_date = newest_start_date_query.scalar()

        if most_recent_update_date is None:
            logger.warning(f"No files found in ScienceFiles table for {alarm.name()}")
            return None

        # start_date is stored without a timezone
        if most_recent_update_date.tzinfo is None:
            most_recent_update_date = most_recent_update_date.replace(
                tzinfo=timezone.utc
            )

        age = (datetime.now(timezone.utc) - most_recent_update_date).days
        if alarm.multi_day_files:
            date_msg = "newest file last ingested"
        else:
            date_msg = "newest start_date"
        logger.info(
            f"{alarm.name()}: {date_msg} is {age} days old ({most_recent_update_date})"
        )
        return age

    except Exception as e:
        logger.error(f"Error checking ScienceFiles table for {alarm.name()}: {e!s}")
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
        Response with status code and summary of results
    """
    logger.info(f"Received event: {event}")
    logger.info("Checking science data freshness from database")

    results = []

    for alarm in MONITORED_SCIENCE_PRODUCTS:
        logger.info(
            f"Checking ({alarm.instrument}, {alarm.data_level}, {alarm.descriptor})"
            f" with threshold {alarm.threshold_days} days"
        )

        days_old = get_days_since_new_data(alarm)
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
