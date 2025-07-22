"""Test the I-Alirt coverage lambda function."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from imap_data_access.processing_input import (
    ProcessingInputCollection,
    SPICEInput,
)

from sds_data_manager.lambda_code.IAlirtCode.ialirt_coverage import (
    download_spice_file,
    get_dsn,
    get_latest_outage_file,
    get_latest_spice_kernels,
    parse_outage_file,
)


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
    """Test the get_latest_outage_file function."""
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

    assert result == "outages/outages_2026_09_22.txt"


def test_parse_outage_file(s3_client):
    """Test the parse_outage_file function."""
    bucket = "test-data-bucket"
    region = "us-west-2"
    key = "outages_2026_09_22.txt"
    file_content = (
        "Kiel,2026-09-22T13:50:00.00Z,2026-09-22T14:10:00Z\n"
        "DSS-75,2026-09-25T08:00:00.00Z,2026-09-25T09:30:00Z\n"
    )

    s3_client.put_object(Bucket=bucket, Key=key, Body=file_content)

    outages = parse_outage_file(bucket, region, key)

    expected_outages = {
        "Kiel": [("2026-09-22T13:50:00.00Z", "2026-09-22T14:10:00Z")],
        "DSS-75": [("2026-09-25T08:00:00.00Z", "2026-09-25T09:30:00Z")],
    }

    assert outages == expected_outages


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
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_coverage.imap_data_access.download"
)
@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_coverage.imap_data_access.AncillaryFilePath"
)
@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_coverage.imap_data_access.query")
def test_get_dsn(mock_query, mock_ancillaryfilepath, mock_download):
    """Test get_dsn function."""
    mock_path = Path("/ialirt/contact-schedule/dsn_file.txt")
    mock_download.return_value = mock_path
    mock_query.return_value = [{"file_path": "ialirt/contact-schedule/dsn_file.txt"}]
    mock_construct_path = MagicMock(return_value=mock_path)
    mock_ancillaryfilepath.return_value.construct_path = mock_construct_path

    with patch.object(Path, "exists", return_value=False):
        path, dict = get_dsn(Path("/tmp"))

    assert path == mock_path
    assert dict == {}
