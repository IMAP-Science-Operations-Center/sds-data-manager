"""Test the I-Alirt ingest lambda function."""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest
import xarray as xr
from boto3.dynamodb.conditions import Key
from imap_data_access.processing_input import (
    ProcessingInputCollection,
    SPICEInput,
)

from sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest import (
    download_spice_file,
    get_ancillary,
    get_latest_spice_kernels,
    insert_data,
    lambda_handler,
    parse_packets,
    process_algorithms,
    query_filenames,
)
from sds_data_manager.lambda_code.IAlirtCode.ialirt_coverage import get_latest_outage_file


@pytest.fixture
def s3_test_packet(s3_client):
    """Add a fake binary packet file to the mock S3 bucket."""
    test_file = "iois_1_packets_YYYY_DOY_HH_MM_SS.ccsds"

    s3_client.put_object(
        Bucket="test-data-bucket",
        Key=test_file,
        Body=b"dummy test data",
    )

    return test_file


@patch("spiceypy.furnsh")
@patch("imap_data_access.processing_input.ProcessingInputCollection.download_all_files")
@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.requests.get")
def test_lambda_handler(mock_get, mock_download, mock_furnsh, setup_dynamodb):
    """Test the lambda_handler function."""
    # Mock event data
    algorithm_table = setup_dynamodb["algorithm_table"]

    mock_response = MagicMock()
    mock_response.json.return_value = [
        "imap_sclk_0000.tsc",
        "naif0012.tls",
        "imap_001.tf",
    ]
    mock_get.return_value = mock_response
    mock_download.return_value = None
    mock_furnsh.return_value = None

    event = {
        "region": "us-west-2",
        "detail": {
            "object": {"key": "packets/file.txt"},
            "bucket": {"name": "test-data-bucket"},
        },
    }

    lambda_handler(event, {})

    response = algorithm_table.get_item(
        Key={
            "apid": 478,
            "met": 123,
        }
    )
    item = response.get("Item")

    assert item is None


def test_get_latest_outage_file(s3_client):
    """Test the query_filenames function."""
    bucket = "test-data-bucket"
    region = "us-west-2"

    # Files in the desired time range
    keys = [
        "outages/outages_2026_09_21.txt",
        "outages/outages_2026_09_22.txt",
    ]

    for key in keys:
        s3_client.put_object(Bucket=bucket, Key=key, Body=b"dummy data")

    result = get_latest_outage_file(bucket, region)

    assert result == 'outages/outages_2026_09_22.txt'


def test_query_filenames_crossing_hour_boundary(s3_client):
    """Test query_filenames when crossing hour boundary."""
    bucket = "test-data-bucket"
    region = "us-west-2"

    now = datetime(2025, 4, 28, 1, 2, 0, tzinfo=timezone.utc)

    first_prefix_key = "packets/iois_1_packets_2025_118_00_58_00"
    second_prefix_key = "packets/iois_1_packets_2025_118_01_00_00"
    outside_range_key = "packets/iois_1_packets_2025_118_00_50_00"

    for key in [first_prefix_key, second_prefix_key, outside_range_key]:
        s3_client.put_object(Bucket=bucket, Key=key, Body=b"dummy data")

    result = query_filenames(bucket, region, now)

    assert sorted(result) == sorted([first_prefix_key, second_prefix_key])


@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_coverage.requests.get")
def test_get_latest_spice_kernels(mock_get):
    """Test get_latest_spice_kernels function."""
    mock_files = [
        "de440.bsp",
        "pck00011.tpc",
    ]

    mock_response = MagicMock()
    mock_response.json.return_value = mock_files
    mock_get.return_value = mock_response

    result = get_latest_spice_kernels()
    assert result.processing_input[0].filename_list == mock_files


@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_coverage.spiceypy.furnsh")
@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_coverage.ProcessingInputCollection.download_all_files"
)
def test_download_spice_file(mock_download, mock_furnsh):
    """Test download_spice_file function."""
    mock_files = [
        "de440.bsp",
        "pck00011.tpc",
    ]
    collection = ProcessingInputCollection()
    collection.add(SPICEInput(*mock_files))

    result = download_spice_file(collection)

    assert [file.name for file in result] == [
        "de440.bsp",
        "pck00011.tpc",
    ]


@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.imap_data_access.download"
)
@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.imap_data_access.AncillaryFilePath"
)
@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.imap_data_access.query")
@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.EFS_BASE_PATH",
    Path("/mock/efs"),
)
def test_get_dsn(mock_query, mock_ancillaryfilepath, mock_download):
    """Test get_ancillary function."""
    mock_path = Path("/mock/efs/swe/l1b-in-flight-cal/calibration.cdf")
    mock_download.return_value = mock_path
    mock_query.return_value = [{"file_path": "swe/l1b-in-flight-cal/calibration.cdf"}]
    mock_construct_path = MagicMock(return_value=mock_path)
    mock_ancillaryfilepath.return_value.construct_path = mock_construct_path

    with patch.object(Path, "exists", return_value=False):
        path = get_ancillary("swe", "l1b-in-flight-cal")

    assert path == mock_path
