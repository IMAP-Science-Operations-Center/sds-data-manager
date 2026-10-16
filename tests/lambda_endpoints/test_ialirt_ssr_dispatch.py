"""Test the I-ALiRT SSR dispatch lambda."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_dispatch import (
    get_days,
    lambda_handler,
)


def test_get_days_daily():
    """Test that the daily trigger returns the day 7 days ago."""
    expected = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y%m%d")

    assert get_days({}) == [expected]


def test_get_days_manual():
    """Test that a manual range includes the start and end dates."""
    detail = {"start_date": "20260930", "end_date": "20261002"}

    assert get_days(detail) == ["20260930", "20261001", "20261002"]


@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_dispatch.boto3.client")
def test_lambda_handler(mock_client):
    """Test that each instrument is invoked for each day."""
    event = {
        "detail": {
            "start_date": "20260930",
            "end_date": "20261001",
            "instruments": ["mag"],
        }
    }

    lambda_handler(event, {})

    mock_invoke = mock_client.return_value.invoke
    assert mock_invoke.call_count == 2
    mock_invoke.assert_called_with(
        FunctionName="ialirt-ssr-mag",
        InvocationType="Event",
        Payload='{"day": "20261001", "version": "v001"}',
    )


def test_lambda_handler_bad_version():
    """Test that an invalid version raises."""
    with pytest.raises(ValueError, match="version"):
        lambda_handler({"detail": {"version": "2"}}, {})
