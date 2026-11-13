"""Tests for the science data monitoring lambda."""

import json
from datetime import datetime, timedelta, timezone

from sds_data_manager.lambda_code.SDSCode.database.models import ScienceFiles
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas import (
    science_data_monitoring,
)
from sds_data_manager.lambda_code.SDSCode.utils.science_monitoring_config import (
    MONITORED_SCIENCE_PRODUCTS,
    Alarm,
)
from sds_data_manager.orchestration.dependency import DependencyConfigReader
from sds_data_manager.orchestration.types import DependencyNode


def get_put_metric_call_data(mocked_cloudwatch_client):
    """Extract metric call data from mocked cloudwatch client."""
    call_arg_list = mocked_cloudwatch_client.put_metric_data.call_args_list
    return [
        {
            "value": c.kwargs["MetricData"][0]["Value"],
            "data_name": c.kwargs["MetricData"][0]["Dimensions"][0]["Value"],
        }
        for c in call_arg_list
    ]


def test_monitored_science_products():
    """Test validate each alarm in MONITORED_SCIENCE_PRODUCTS."""
    reader = DependencyConfigReader()
    for alarm in MONITORED_SCIENCE_PRODUCTS:
        # Validate that there is either and input or an output node for every
        # alarm
        node = DependencyNode(alarm.instrument, alarm.data_level, alarm.descriptor)
        # This will raise an error if there is no
        # ProcessingJob that produces this output.
        try:
            reader.get_node_for_output(node)
        except ValueError as e:
            nodes = reader.get_nodes_for_input(node)
            if not nodes:
                raise ValueError(
                    f"No input or output node found for {alarm.instrument},"
                    f"{alarm.data_level}, {alarm.descriptor}"
                ) from e


def test_alarm():
    """Test the Alarm class."""
    alarm = Alarm("idex", "l0", "raw", 5)
    assert alarm.instrument == "idex"
    assert alarm.data_level == "l0"
    assert alarm.descriptor == "raw"
    assert alarm.threshold_days == 5
    assert (
        alarm.alarm_description()
        == "IDEX, l0, raw data is stale: no data with a new start date in "
        "the last 5 days."
    )
    assert alarm.name() == "idex_l0_raw"


def test_get_days_since_new_data(session):
    """Test the get_days_since_new_data function."""
    records = [
        ScienceFiles(
            file_path="path/to/imap_idex_l0_raw_20260101_v001.0001.cdf",
            instrument="idex",
            data_level="l0",
            descriptor="raw",
            start_date=datetime(2026, 1, 1),
            major_version=1,
            minor_version=1,
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2026-01-1 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
    ]
    session.add_all(records)
    session.commit()
    # Create a mock idex l0 alarm
    alarm = Alarm("idex", "l0", "raw", 2)
    # Mock the current time to be a day after the file's ingestion date.
    current_time = datetime(2026, 1, 2, tzinfo=timezone.utc)
    days_old = science_data_monitoring.get_days_since_new_data(alarm, current_time)
    assert days_old == 1


def test_get_days_since_new_data_mutli_file(session):
    """Test the get_days_since_new_data function."""
    records = [
        ScienceFiles(
            file_path="path/to/imap_idex_l0_raw_20260101_v001.0001.cdf",
            instrument="idex",
            data_level="l0",
            descriptor="raw",
            start_date=datetime(2026, 1, 1),
            major_version=1,
            minor_version=1,
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2026-01-1 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        # Add a version 2 of this file with a later ingestion date.
        ScienceFiles(
            file_path="path/to/imap_idex_l0_raw_20260101_v001.0002.cdf",
            instrument="idex",
            data_level="l0",
            descriptor="raw",
            start_date=datetime(2026, 1, 1),
            major_version=2,
            minor_version=1,
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2026-01-02 00:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
        # Finally, add a file that has an older start date but a later ingestion date.
        ScienceFiles(
            file_path="path/to/imap_idex_l0_raw_20250101_v001.0002.cdf",
            instrument="idex",
            data_level="l0",
            descriptor="raw",
            start_date=datetime(2025, 1, 1),
            major_version=1,
            minor_version=2,
            extension="cdf",
            ingestion_date=datetime.strptime(
                "2026-01-3 23:35:26+00:00", "%Y-%m-%d %H:%M:%S%z"
            ),
        ),
    ]
    session.add_all(records)
    session.commit()
    # Create a mock idex l0 alarm
    alarm = Alarm("idex", "l0", "raw", 2)
    # Mock the current time
    current_time = datetime(2026, 1, 5, tzinfo=timezone.utc)
    days_old = science_data_monitoring.get_days_since_new_data(alarm, current_time)
    # The expected age should be 4 If alarm.multi_day_files is false.
    # It should check the newest
    # start date, which is 2026-01-01. The most recent ingestion for that start
    # date is 2026-01-01, and the current time is 2026-01-05, so the age is 4 days.
    assert days_old == 4
    alarm = Alarm("idex", "l0", "raw", 2, multi_day_files=True)
    days_old = science_data_monitoring.get_days_since_new_data(alarm, current_time)
    # The expected age should be 3 if alarm.multi_day_files is true.
    # The most recent ingestion for the most recent start date
    # (the second file above) is 2026-01-2, and the current time
    # is 2026-01-5, so the age is 3 days.
    assert days_old == 3


def test_monitors_no_data_in_db(cloudwatch_client, session):
    """Test the lambda_handler function when no data in the database."""
    result = science_data_monitoring.lambda_handler({}, None)
    result_dict = json.loads(result["body"])
    metric_data = get_put_metric_call_data(cloudwatch_client)
    for record in result_dict["results"]:
        # Every record should have the default "days_old" value since
        # There is no data in the db.
        assert record["days_old"] == 999
        # is_stale should be true
        assert record["is_stale"]
        # Check that the metrics were published.
        assert {"value": 999, "data_name": record["data_name"]} in metric_data


def test_monitors_multiple_alarms(cloudwatch_client, session):
    """Test the lambda_handler function when some monitored products are stale."""
    # Add some idex data to the db.
    current_time = datetime.now(timezone.utc)
    l0_date = current_time - timedelta(days=4)
    l1a_start_date = current_time - timedelta(days=10)
    l1a_ingestion_date = current_time - timedelta(days=11)
    l1b_start_date = current_time - timedelta(days=10)
    l1b_ingestion_date = current_time - timedelta(days=6)
    records = [
        ScienceFiles(
            file_path=f"path/to/imap_idex_l0_raw_{l0_date.strftime('%Y%m%d')}_v001.0001.cdf",
            instrument="idex",
            data_level="l0",
            descriptor="raw",
            start_date=l0_date,
            major_version=1,
            minor_version=1,
            extension="cdf",
            ingestion_date=l0_date,
        ),
        # Add stale l1a data
        ScienceFiles(
            file_path=f"path/to/imap_idex_l1a_sci-10days_{l1a_start_date.strftime('%Y%m%d')}_v001.0001.cdf",
            instrument="idex",
            data_level="l1a",
            descriptor="sci-10days",
            start_date=l1a_start_date,
            major_version=1,
            minor_version=1,
            extension="cdf",
            ingestion_date=l1a_ingestion_date,
        ),
        # Add a "fresh" l1b file
        ScienceFiles(
            file_path=f"path/to/imap_idex_l1b_sci-10days_{l1b_start_date.strftime('%Y%m%d')}_v001.0001.cdf",
            instrument="idex",
            data_level="l1b",
            descriptor="sci-10days",
            start_date=l1b_start_date,
            major_version=1,
            minor_version=1,
            extension="cdf",
            ingestion_date=l1b_ingestion_date,
        ),
    ]
    session.add_all(records)
    session.commit()

    result = science_data_monitoring.lambda_handler({}, None)
    result_dict = json.loads(result["body"])
    metric_data = get_put_metric_call_data(cloudwatch_client)
    # List of expected days_old and is_stale values for each alarm in
    # MONITORED_SCIENCE_PRODUCTS
    # IDEX l2a and l2b files don't exist in the database and will be the default value
    # (999)
    expected_days_old = [4, 11, 6, 999, 999]
    expected_is_stale = [False, True, False, True, True]
    for record, days_old, is_stale in zip(
        result_dict["results"], expected_days_old, expected_is_stale, strict=True
    ):
        # Every record should have the default "days_old" value since
        # There is no data in the db.
        assert record["days_old"] == days_old
        # is_stale should be true
        assert record["is_stale"] == is_stale
        # Check that the metrics were published.
        assert {"value": days_old, "data_name": record["data_name"]} in metric_data
