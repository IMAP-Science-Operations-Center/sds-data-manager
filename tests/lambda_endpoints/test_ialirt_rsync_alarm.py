"""Test the I-ALiRT rsync failure checker lambda."""

from unittest.mock import patch

from sds_data_manager.lambda_code.IAlirtCode.ialirt_rsync_alarm import (
    check_for_rsync_failure,
    lambda_handler,
    publish_failure_metric,
)


@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_rsync_alarm.publish_failure_metric"
)
def test_lambda_handler_detects_rsync_failure(mock_metric, s3_client):
    """Test lambda_handler returns True when rsync failure is found."""
    bucket = "test-data-bucket"
    key = "logs/flight_iois_1.log.2025-253T19_26_00"

    log_content = (
        "2025/253-19:26:00.926 Spawning: rsync\n"
        "2025/253-19:26:01.926 command failed: rsync\n"
        "2025/253-19:26:01.926   rsync: connection unexpectedly closed "
        "(0 bytes received so far) [sender]\n"
        "2025/253-19:26:01.926   rsync error: unexplained error (code 255) "
        "at io.c(232) [sender=3.2.7]\n"
    )

    s3_client.put_object(Bucket=bucket, Key=key, Body=log_content.encode("utf-8"))

    event = {
        "region": "us-west-2",
        "detail": {
            "bucket": {"name": bucket},
            "object": {"key": key},
        },
        "now": "2025-09-10T19:30:00Z",
    }

    response = lambda_handler(event, context={})

    assert response == {"found_rsync_failure": True}
    mock_metric.assert_called_once()


@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_rsync_alarm.publish_failure_metric"
)
def test_lambda_handler_returns_false_when_no_failure(mock_metric, s3_client):
    """Test lambda_handler returns False when no rsync failure is found."""
    bucket = "test-data-bucket"
    key = "logs/flight_iois_1.log.2025-253T19_26_00"

    log_content = (
        "2025/253-19:26:00.926 Spawning: rsync\n"
        "2025/253-19:26:00.999 connection established successfully\n"
    )

    s3_client.put_object(Bucket=bucket, Key=key, Body=log_content.encode("utf-8"))

    event = {
        "region": "us-west-2",
        "detail": {
            "bucket": {"name": bucket},
            "object": {"key": key},
        },
        "now": "2025-09-10T19:30:00Z",
    }

    response = lambda_handler(event, context={})

    assert response == {"found_rsync_failure": False}


def test_check_for_rsync_failure(s3_client):
    """Test direct call to check_for_rsync_failure."""
    bucket = "test-data-bucket"
    key = "logs/flight_iois_1.log.2025-253T19_26_00"
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=b"2025/253-19:26:01.926 command failed: rsync\n",
    )

    result = check_for_rsync_failure(
        s3_client, "logs/flight_iois_1.log.2025-253T19_26_00", bucket
    )
    assert result is True


def test_check_for_rsync_failure_returns_false(s3_client):
    """Test that check_for_rsync_failure returns False when string not found."""
    bucket = "test-data-bucket"
    key = "logs/flight_iois_1.log.2025-253T19_26_00"
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=b"2025/253-19:26:01.926 normal log message\n",
    )

    result = check_for_rsync_failure(
        s3_client, "logs/flight_iois_1.log.2025-253T19_26_00", bucket
    )
    assert result is False


@patch("boto3.client")
def test_publish_failure_metric(mock_boto3_client):
    """Test that publish_failure_metric sends the correct metric to CloudWatch."""
    mock_cloudwatch = mock_boto3_client.return_value

    found = True
    publish_failure_metric(found)

    mock_cloudwatch.put_metric_data.assert_called_once_with(
        Namespace="IMAP/Ialirt",
        MetricData=[
            {
                "MetricName": "IalirtRsyncFailures",
                "Dimensions": [{"Name": "Function", "Value": "ialirt-rsync-alarm"}],
                "Unit": "Count",
                "Value": 1,
            }
        ],
    )
