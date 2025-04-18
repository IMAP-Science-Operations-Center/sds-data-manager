"""Tests for the DPS Maker Lambda."""

from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas import dps_maker


def test_lambda_handler():
    """Test that lambda properly handles event."""
    event = {
        "detail": {
            "path": "/mnt/spice/repoint/imap_2026_267_01.repoint.csv",
            "prefix": "repoint",
        }
    }
    # TODO: add more tests.
    dps_maker.lambda_handler(event=event, context=None)
