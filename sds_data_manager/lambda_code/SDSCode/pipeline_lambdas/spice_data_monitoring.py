"""Lambda function to monitor SPICE data freshness.

This Lambda checks for missing SPICE data by querying the database
and publishing CloudWatch metrics when data is stale.
"""

import json
import logging
import os
from datetime import datetime, timezone

import boto3
from sqlalchemy import func

from ..database import database as db
from ..database import models

# AWS Clients
CLOUDWATCH_CLIENT = boto3.client("cloudwatch")

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Configuration from environment variables
METRIC_NAMESPACE = os.environ.get("METRIC_NAMESPACE", "IMAP/SpiceDataFreshness")

# Monitored data sources configuration
# Format: {category: {name: display_name, threshold_days: N, query_info}}
MONITORED_DATA = {
    "ck_kernels": {
        "name": "CK_Kernels",
        "threshold_days": int(os.environ.get("CK_THRESHOLD_DAYS", "4")),
        "description": "Attitude history and pointing attitude kernels",
        "table": "spice",
        "kernel_types": ["attitude_history", "pointing_attitude"],
    },
    "spin_files": {
        "name": "Spin_Files",
        "threshold_days": int(os.environ.get("SPIN_THRESHOLD_DAYS", "4")),
        "description": "Spacecraft spin files",
        "table": "spin",
        "kernel_types": None,
    },
    "sclk_kernels": {
        "name": "SCLK_Kernels",
        "threshold_days": int(os.environ.get("SCLK_THRESHOLD_DAYS", "4")),
        "description": "Spacecraft clock kernels",
        "table": "spice",
        "kernel_types": ["spacecraft_clock"],
    },
    "repoint_files": {
        "name": "Repoint_Files",
        "threshold_days": int(os.environ.get("REPOINT_THRESHOLD_DAYS", "4")),
        "description": "Spacecraft repoint files",
        "table": "repoint",
        "kernel_types": None,
    },
    "predicted_ephemeris": {
        "name": "Predicted_Ephemeris",
        "threshold_days": int(
            os.environ.get("PREDICTED_EPHEMERIS_THRESHOLD_DAYS", "4")
        ),
        "description": "Predicted ephemeris kernels",
        "table": "spice",
        "kernel_types": ["ephemeris_predicted"],
    },
}


def get_most_recent_ingestion_age(
    table: str, kernel_types: list[str] | None = None
) -> int | None:
    """Get the age in days of the most recently ingested file from database.

    Parameters
    ----------
    table : str
        Which table to query: 'spice', 'spin', or 'repoint'
    kernel_types : list[str] | None
        For SPICEFiles table, filter by kernel types.
        For SpinFiles and RepointFiles tables, this is ignored.

    Returns
    -------
    int | None
        Number of days since the most recent file was ingested,
        or None if no files exist
    """
    try:
        with db.Session() as session:
            if table == "spice":
                # Query SPICEFiles table
                query = session.query(func.max(models.SPICEFiles.ingestion_date))
                if kernel_types:
                    query = query.filter(
                        models.SPICEFiles.kernel_type.in_(kernel_types)
                    )
                most_recent = query.scalar()

            elif table == "spin":
                # Query SpinFiles table
                most_recent = session.query(
                    func.max(models.SpinFiles.ingestion_date)
                ).scalar()

            elif table == "repoint":
                # Query RepointFiles table
                most_recent = session.query(
                    func.max(models.RepointFiles.ingestion_date)
                ).scalar()

            else:
                logger.error(f"Unknown table type: {table}")
                return None

            if most_recent is None:
                logger.warning(
                    f"No files found in {table} table (kernel_types: {kernel_types})"
                )
                return None

            # Calculate age in days
            age = (datetime.now(timezone.utc) - most_recent).days
            logger.info(
                f"Table {table} (kernel_types: {kernel_types}): "
                f"Most recent file is {age} days old "
                f"(ingested: {most_recent})"
            )

            return age

    except Exception as e:
        logger.error(
            f"Error checking {table} table (kernel_types: {kernel_types}): {e!s}"
        )
        return None


def publish_metric(data_name: str, days_old: int):
    """Publish a CloudWatch metric for SPICE data freshness.

    Parameters
    ----------
    data_name : str
        Name of the data source being monitored
    days_old : int
        Number of days since the most recent file
    """
    try:
        CLOUDWATCH_CLIENT.put_metric_data(
            Namespace=METRIC_NAMESPACE,
            MetricData=[
                {
                    "MetricName": "DaysSinceLastFile",
                    "Value": days_old,
                    "Unit": "None",
                    "Dimensions": [{"Name": "Prefix", "Value": data_name}],
                    "Timestamp": datetime.now(timezone.utc),
                }
            ],
        )
        logger.info(f"Published metric for {data_name}: {days_old} days old")
    except Exception as e:
        logger.error(f"Error publishing metric for {data_name}: {e!s}")


def lambda_handler(event, context):
    """Lambda handler to check SPICE data freshness.

    This function runs on a schedule (daily) and queries the database
    for the most recently ingested files in each monitored category.
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
    logger.info("Checking SPICE data freshness from database")

    results = {}

    for _category, config in MONITORED_DATA.items():
        data_name = config["name"]
        threshold = config["threshold_days"]
        description = config["description"]
        table = config["table"]
        kernel_types = config["kernel_types"]

        logger.info(
            f"Checking {data_name} ({description}) with threshold {threshold} days"
        )

        days_old = get_most_recent_ingestion_age(table, kernel_types)

        if days_old is None:
            # No files found - use a sentinel value
            days_old = 999
            logger.warning(
                f"{data_name}: No files found in database "
                f"(table: {table}, kernel_types: {kernel_types})"
            )

        # Publish the metric regardless of threshold
        publish_metric(data_name, days_old)

        # Store result
        results[data_name] = {
            "days_old": days_old,
            "threshold": threshold,
            "stale": days_old > threshold,
        }

    # Log summary
    stale_sources = [name for name, result in results.items() if result["stale"]]
    if stale_sources:
        logger.warning(f"Stale data detected in: {', '.join(stale_sources)}")
    else:
        logger.info("All monitored data sources are up to date")

    return {
        "statusCode": 200,
        "body": json.dumps(
            {"message": "SPICE data check complete", "results": results}
        ),
    }
