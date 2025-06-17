"""Tests for the batch logs API Lambda function."""

import os
from unittest.mock import patch

from sds_data_manager.lambda_code.SDSCode.api_lambdas import batch_logs_api


def test_lambda_handler_missing_query_params():
    """Test lambda handler with missing query parameters."""
    event = {}
    result = batch_logs_api.lambda_handler(event, None)
    assert result["statusCode"] == 400
    assert "Required Batch job log stream ID." in result["body"]


def test_lambda_handler_missing_job_log_stream_id():
    """Test lambda handler with missing job_log_stream_id."""
    event = {"queryStringParameters": {}}
    result = batch_logs_api.lambda_handler(event, None)
    assert result["statusCode"] == 400
    assert "Required Batch job log stream ID." in result["body"]

    event = {"queryStringParameters": {"job_log_stream_id": ""}}
    result = batch_logs_api.lambda_handler(event, None)
    assert result["statusCode"] == 400
    assert "job_log_stream_id is required." in result["body"]


def test_lambda_handler_success():
    """Test lambda handler with valid job_log_stream_id."""

    class MockLogsClient:
        def get_log_events(self, **kwargs):
            return {
                "events": [
                    {"message": "log line 1"},
                    {"message": "log line 2"},
                ]
            }

    with patch.object(batch_logs_api, "LOGS_CLIENT", MockLogsClient()):
        event = {
            "queryStringParameters": {
                "job_log_stream_id": "ProcessingJob-test/default/abc123"
            }
        }
        result = batch_logs_api.lambda_handler(event, None)
        assert result["statusCode"] == 200
        assert "log line 1" in result["body"]
        assert "log line 2" in result["body"]


def test_lambda_handler_logs_client_error():
    """Test lambda handler when logs client finds an error."""
    os.environ["AWS_DEFAULT_REGION"] = "us-west-2"

    class MockLogsClient:
        def get_log_events(self, **kwargs):
            raise Exception("CloudWatch error")

    with patch.object(batch_logs_api, "LOGS_CLIENT", MockLogsClient()):
        event = {
            "queryStringParameters": {
                "job_log_stream_id": "ProcessingJob-test/default/abc123"
            }
        }
        result = batch_logs_api.lambda_handler(event, None)
        assert result["statusCode"] == 500
        assert "Could not fetch logs" in result["body"]
