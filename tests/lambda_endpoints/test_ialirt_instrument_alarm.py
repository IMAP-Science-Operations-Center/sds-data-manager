"""Test the I-ALiRT instrument data freshness alarm lambda."""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sds_data_manager.lambda_code.IAlirtCode.ialirt_instrument_alarm import (
    INSTRUMENTS,
    check_instrument,
    lambda_handler,
    notify_missing,
)

TOPIC_ARN = "arn:aws:sns:us-east-1:123456789012:TestTopic"


def test_check_instrument_returns_true_when_recent_data(setup_data_table):
    """check_instrument returns True when a recent item exists."""
    table = setup_data_table["data_table"]
    table.put_item(
        Item={
            "instrument": "hit",
            "time_utc": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        }
    )
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat()
    assert check_instrument(table, "hit", cutoff) is True


@patch("boto3.client")
def test_notify_missing_publishes_correct_message(mock_boto3_client):
    """notify_missing publishes the expected SNS message."""
    mock_sns = mock_boto3_client.return_value
    notify_missing(mock_sns, TOPIC_ARN, ["hit", "mag"])
    mock_sns.publish.assert_called_once_with(
        TopicArn=TOPIC_ARN,
        Subject="I-ALiRT Instrument Data Missing",
        Message=(
            "The following instruments have not reported data in the last 8 hours: "
            "hit, mag"
        ),
    )


@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_instrument_alarm.notify_missing")
def test_lambda_handler_returns_missing_instruments(mock_notify, setup_data_table):
    """lambda_handler returns all instruments as missing when table is empty."""
    os.environ["SNS_TOPIC_ARN"] = TOPIC_ARN

    result = lambda_handler({}, None)

    assert result["missing_instruments"] == INSTRUMENTS
    mock_notify.assert_called_once()
