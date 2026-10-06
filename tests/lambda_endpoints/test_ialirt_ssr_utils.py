"""Test the I-ALiRT SSR utilities."""

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import xarray as xr
from imap_data_access.processing_input import ProcessingInputCollection, SPICEInput

from sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_utils import (
    download_spice_files,
    get_ancillary,
    get_latest_spice_kernels,
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


@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_utils.requests.get")
def test_get_latest_spice_kernels(mock_get):
    """Test get_latest_spice_kernels function."""
    mock_files = [
        "imap_sclk_0000.tsc",
        "naif0012.tls",
        "imap_001.tf",
        "de440.bsp",
        "imap_pred_20260922_20261020_v01.bsp",
        "imap_science_0001.tf",
    ]

    mock_response = MagicMock()
    mock_response.json.return_value = mock_files
    mock_get.return_value = mock_response

    result = get_latest_spice_kernels(
        "https://api.dev.imap-mission.com", datetime(2026, 10, 2)
    )
    assert result.processing_input[0].filename_list == mock_files


@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_utils.ProcessingInputCollection.download_all_files"
)
@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_utils.EFS_BASE_PATH",
    Path("/mock/efs"),
)
def test_download_spice_files(mock_download):
    """Test download_spice_files function."""
    mock_files = [
        "imap_sclk_0000.tsc",
        "naif0012.tls",
        "imap_pred_20260922_20261020_v01.bsp",
    ]
    collection = ProcessingInputCollection()
    collection.add(SPICEInput(*mock_files))

    result = download_spice_files(collection)

    assert [file.name for file in result] == [
        "imap_sclk_0000.tsc",
        "naif0012.tls",
        "imap_pred_20260922_20261020_v01.bsp",
    ]


@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_utils.imap_data_access.download"
)
@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_ssr_utils.imap_data_access.query"
)
def test_get_ancillary(mock_query, mock_download):
    """Test get_ancillary function."""
    # The query only returns files that start on or before the day.
    mock_query.return_value = [
        {
            "file_path": "imap_swe_l1b-in-flight-cal_20260810_v001.csv",
            "start_date": "20260810",
            "end_date": None,
            "version": "v001",
        },
        {
            "file_path": "imap_swe_l1b-in-flight-cal_20260824_v001.csv",
            "start_date": "20260824",
            "end_date": None,
            "version": "v001",
        },
    ]

    get_ancillary("swe", "l1b-in-flight-cal", datetime(2026, 8, 25))

    assert mock_query.call_args.kwargs["end_date"] == "20260825"
    mock_download.assert_called_once_with(
        "imap_swe_l1b-in-flight-cal_20260824_v001.csv"
    )
