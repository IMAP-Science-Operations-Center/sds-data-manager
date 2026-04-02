"""Tests for the ialirt_schedule_fetch module."""

from unittest.mock import MagicMock, patch

from sds_data_manager.lambda_code.IAlirtCode.ialirt_schedule_fetch import (
    fetch_schedule_xml,
)

SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Schedule>
    <Activity>
        <BeginningOfActivity>2026-04-01T10:00:00Z</BeginningOfActivity>
        <EndOfActivity>2026-04-01T12:00:00Z</EndOfActivity>
    </Activity>
</Schedule>"""


@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_schedule_fetch.requests.get")
def test_fetch_schedule_xml(mock_get, tmp_path):
    """Test that fetch_schedule_xml function."""
    mock_get.return_value = MagicMock(text=SAMPLE_XML)

    result = fetch_schedule_xml(
        url="https://example.com/schedule",
        cert_path=tmp_path / "client.crt",
        key_path=tmp_path / "client.key",
    )

    assert result == SAMPLE_XML
