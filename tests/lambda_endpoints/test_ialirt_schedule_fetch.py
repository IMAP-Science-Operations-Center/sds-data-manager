"""Test the I-ALiRT schedule fetch lambda function."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from sds_data_manager.lambda_code.IAlirtCode.ialirt_schedule_fetch import (
    fetch_schedule_xml,
    lambda_handler,
)

SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Schedule>
    <Activity>
        <BeginningOfActivity>2026-04-01T10:00:00Z</BeginningOfActivity>
        <EndOfActivity>2026-04-01T12:00:00Z</EndOfActivity>
    </Activity>
</Schedule>"""


@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_schedule_fetch.requests.get")
def test_fetch_schedule_xml(mock_get):
    """Test that fetch_schedule_xml."""
    mock_response = MagicMock()
    mock_response.text = SAMPLE_XML
    mock_get.return_value = mock_response

    result = fetch_schedule_xml(
        url="https://example.com/schedule",
        cert_path=Path("/tmp/client.crt"),
        key_path=Path("/tmp/client.key"),
    )

    assert result == SAMPLE_XML


@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_schedule_fetch.requests.get")
def test_lambda_handler(mock_get):
    """Test lambda_handler function."""
    mock_response = MagicMock()
    mock_response.text = SAMPLE_XML
    mock_response.status_code = 200
    mock_get.return_value = mock_response

    os.environ["SCHEDULE_ENDPOINT_URL"] = "https://example.com/schedule"
    os.environ["CERT_CONTENT"] = "mock-cert-content"
    os.environ["KEY_CONTENT"] = "mock-key-content"

    lambda_handler({}, {})

    mock_get.assert_called_once()