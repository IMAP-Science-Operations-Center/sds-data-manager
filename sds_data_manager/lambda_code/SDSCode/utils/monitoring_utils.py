"""Utils for monitoring Spice and science data freshness."""

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import boto3

# AWS Clients
CLOUDWATCH_CLIENT = boto3.client("cloudwatch")

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@dataclass
class AlarmResult:
    """Dataclass to hold the result of a freshness check for a science alarm."""

    data_name: str
    days_old: int
    threshold: int

    def is_stale(self) -> bool:
        """Return True if the data is stale, False otherwise."""
        return self.days_old > self.threshold

    def to_dict(self) -> dict:
        """Return a dict representation of the AlarmResult."""
        return {**asdict(self), "is_stale": self.is_stale()}


@dataclass
class Alarm:
    """An alarm that fires if no new data arrives within threshold_days."""

    instrument: str
    descriptor: str
    data_level: str
    # How many days since the last file before the alarm triggers
    threshold_days: int

    def __post_init__(self):
        """Validate the threshold_days value."""
        if self.threshold_days <= 0:
            raise ValueError("threshold_days must be greater than 0.")

    def name(self):
        """Return the name of the alarm."""
        return f"{self.instrument}-{self.descriptor}-{self.data_level}"

    def alarm_description(self):
        """Return the description of the alarm."""
        return (
            f"{self.instrument.upper()}, {self.data_level}, {self.descriptor} data "
            f"is stale: no new files in the last {self.threshold_days} days."
        )


MONITORED_SCIENCE_PRODUCTS = [Alarm("idex", "raw", "l0", 5)] + [
    Alarm("idex", "sci-10days", level, 8) for level in ["l1a", "l1b", "l2a", "l2b"]
]


def publish_metric(metric_namespace: str, data_name: str, days_old: int):
    """Publish a CloudWatch metric for data freshness.

    Parameters
    ----------
    metric_namespace : str
        Namespace for the CloudWatch metric
    data_name : str
        Name of the data source being monitored
    days_old : int
        Number of days since the most recent file
    """
    try:
        CLOUDWATCH_CLIENT.put_metric_data(
            Namespace=metric_namespace,
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


def log_results_summary(results: list[AlarmResult]):
    """Log a summary of stale or up-to-date metrics."""
    stale_sources = [result.data_name for result in results if result.is_stale()]
    if stale_sources:
        logger.warning(f"Stale data detected in: {', '.join(stale_sources)}")
    else:
        logger.info("All monitored data sources are up to date")
