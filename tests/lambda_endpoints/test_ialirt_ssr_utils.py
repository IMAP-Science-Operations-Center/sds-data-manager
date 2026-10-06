"""Test the I-ALiRT SSR utilities."""

from datetime import datetime
from unittest.mock import patch

from sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_utils import query_filenames


@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_utils.imap_data_access.query"
)
def test_query_filenames(mock_query):
    """Test that query_filenames queries the archive and returns the file path."""
    mock_query.return_value = [
        {
            "file_path": (
                "imap/ialirt/l0/2026/10/imap_ialirt_l0_raw_20261002_v001.0006.pkts"
            ),
            "version": "v001.0006",
        },
    ]

    filename = query_filenames(datetime(2026, 10, 2, 15, 30))

    mock_query.assert_called_once_with(
        table="science",
        instrument="ialirt",
        data_level="l0",
        descriptor="raw",
        start_date="20261002",
        end_date="20261002",
        version="latest",
    )
    assert (
        filename == "imap/ialirt/l0/2026/10/imap_ialirt_l0_raw_20261002_v001.0006.pkts"
    )


@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_utils.imap_data_access.query"
)
def test_query_filenames_no_results(mock_query):
    """Test that query_filenames returns None when no file is found."""
    mock_query.return_value = []

    assert query_filenames(datetime(2026, 10, 2)) is None
