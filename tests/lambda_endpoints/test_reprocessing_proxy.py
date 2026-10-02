"""Tests for the reprocessing proxy lambda."""

import json
from unittest.mock import Mock


def test_reprocessing_proxy_returns_reprocess_id(monkeypatch):
    """Test that the SQS MessageId is returned as the reprocess_id."""
    monkeypatch.setenv("QUEUE_URL", "test-queue")
    from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas import (
        reprocessing_proxy,
    )

    mock_sqs_client = Mock()
    mock_sqs_client.send_message.return_value = {"MessageId": "test-id"}
    monkeypatch.setattr(reprocessing_proxy, "sqs_client", mock_sqs_client)

    response = reprocessing_proxy.lambda_handler(
        {"queryStringParameters": {"start_date": "20260101"}}, None
    )

    assert response["statusCode"] == 200
    assert json.loads(response["body"])["reprocess_id"] == "test-id"
