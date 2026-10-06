"""Test the I-ALiRT SSR utilities."""

from datetime import datetime
from unittest.mock import patch

import pytest
import xarray as xr

from sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_utils import (
    parse_packets,
    query_filenames,
)


@patch("imap_data_access.query")
def test_query_filenames(mock_query):
    """Test that query_filenames returns the files for both days."""
    mock_query.return_value = [
        {"file_path": "file_1.pkts", "start_date": "20261002"},
        {"file_path": "file_2.pkts", "start_date": "20261003"},
    ]

    assert query_filenames(datetime(2026, 10, 2)) == ["file_1.pkts", "file_2.pkts"]


@patch("imap_data_access.query")
def test_query_filenames_missing_file(mock_query):
    """Test that query_filenames fails when a day is missing."""
    mock_query.return_value = [{"file_path": "file_1.pkts", "start_date": "20261002"}]

    with pytest.raises(FileNotFoundError):
        query_filenames(datetime(2026, 10, 2))


@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_utils.packet_file_to_datasets"
)
@patch("imap_data_access.download")
def test_parse_packets(mock_download, mock_packet_file_to_datasets, tmp_path):
    """Test that parse_packets combines files and removes duplicate packets."""
    # The packet at epoch 200 is in both files.
    ds1 = xr.Dataset({"src_seq_ctr": ("epoch", [1, 2])}, coords={"epoch": [100, 200]})
    ds2 = xr.Dataset({"src_seq_ctr": ("epoch", [2, 3])}, coords={"epoch": [200, 300]})
    mock_packet_file_to_datasets.side_effect = [{478: ds1}, {478: ds2}]

    combined = parse_packets(["file_1.pkts", "file_2.pkts"], tmp_path)

    assert list(combined["epoch"].values) == [100, 200, 300]
