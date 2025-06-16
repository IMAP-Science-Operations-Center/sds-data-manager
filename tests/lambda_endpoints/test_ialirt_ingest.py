"""Test the I-Alirt ingest lambda function."""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest
from imap_data_access.processing_input import (
    ProcessingInputCollection,
    SPICEInput,
)
import xarray as xr
from boto3.dynamodb.conditions import Key

from sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest import (
    download_spice_file,
    get_latest_spice_kernels,
    insert_data,
    lambda_handler,
    parse_packets,
    process_algorithms,
    query_filenames,
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


@patch("spiceypy.str2et", return_value=123456789.0)
@patch("spiceypy.furnsh")
@patch("imap_data_access.processing_input.ProcessingInputCollection.download_all_files")
@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.requests.get")
def test_lambda_handler(
    mock_get, mock_download, mock_furnsh, mock_str2et, setup_dynamodb
):
    """Test the lambda_handler function."""
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

    response = algorithm_table.get_item(Key={"apid": 478, "met": 123})
    item = response.get("Item")

    assert item["met"] == 123
    assert item["insert_time"] == "2021-01-01T00:00:00Z"
    assert item["product_name"] == "hit_product_1"
    assert item["data_product_1"] == str(1234.56)


@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.packet_file_to_datasets")
def test_parse_packet_s3(mock_packet_file_to_datasets, s3_test_packet, tmp_path):
    """Test parse_packet function."""
    ds = xr.Dataset({"data": (["epoch"], [1.0])}, coords={"epoch": (["epoch"], [100])})
    mock_packet_file_to_datasets.return_value = {478: ds}

    filename = [Path(s3_test_packet).name]

    result = parse_packets(filename, "test-data-bucket", tmp_path)

    assert result == ds
    mock_packet_file_to_datasets.assert_called_once()

    # Check if file was downloaded
    real_tmp_file = tmp_path / filename[0]
    assert real_tmp_file.exists()


@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.packet_file_to_datasets")
def test_parse_packet_duplicate(mock_packet_file_to_datasets, s3_test_packet, tmp_path):
    """Test parse_packet function that duplicate packets are removed."""
    # Simulate two datasets with the same epoch.
    ds1 = xr.Dataset({"data": (["epoch"], [1.0])}, coords={"epoch": (["epoch"], [100])})
    ds2 = xr.Dataset({"data": (["epoch"], [2.0])}, coords={"epoch": (["epoch"], [100])})

    # Each time the function packet_file_to_datasets() is called
    # return the next item from this list.
    mock_packet_file_to_datasets.side_effect = [
        {478: ds1},
        {478: ds2},
    ]

    filenames = [s3_test_packet, s3_test_packet]

    combined = parse_packets(filenames, "test-data-bucket", tmp_path)

    # One entry remains.
    assert isinstance(combined, xr.Dataset)
    assert len(combined["epoch"]) == 1

    # Mock was called twice.
    assert mock_packet_file_to_datasets.call_count == 2


def test_query_filenames(s3_client):
    """Test the query_filenames function."""
    bucket = "test-data-bucket"
    region = "us-west-2"
    now = datetime(2025, 4, 28, 16, 5, 0, tzinfo=timezone.utc)

    # Files in the desired time range
    inside_range_keys = [
        "packets/iois_1_packets_2025_118_16_01_00",
        "packets/iois_1_packets_2025_118_16_03_00",
        "packets/iois_1_packets_2025_118_16_04_00",
    ]

    outside_range_key = "packets/iois_1_packets_2025_118_15_59_00"

    for key in [*inside_range_keys, outside_range_key]:
        s3_client.put_object(Bucket=bucket, Key=key, Body=b"dummy data")

    result = query_filenames(bucket, region, now)

    assert sorted(result) == sorted(inside_range_keys)


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


def test_insert_data(setup_dynamodb):
    """Test insert_data function."""
    algorithm_table = setup_dynamodb["algorithm_table"]

    # Existing item with 'hit' keys
    algorithm_table.put_item(
        Item={"apid": 478, "met": 123456, "hit_e_a_side_low_en": Decimal("0.0")}
    )

    # Existing item with no 'hit' keys
    algorithm_table.put_item(Item={"apid": 478, "met": 123457, "other_data": 42})

    # Create data for all three cases
    test_data = [
        # Will skip.
        {
            "apid": 478,
            "met": 123456,
            "utc": "2025-05-21T14:00:00",
            "ttj2000ns": 759175836184000000,
            "hit_e_a_side_med_en": Decimal("2.0"),
        },
        # Will update.
        {
            "apid": 478,
            "met": 123457,
            "utc": "2025-05-21T14:00:01",
            "ttj2000ns": 759175836184000001,
            "hit_e_a_side_low_en": Decimal("3.0"),
        },
        # Will insert.
        {
            "apid": 478,
            "met": 123458,
            "utc": "2025-05-21T14:00:02",
            "ttj2000ns": 759175836184000002,
            "hit_e_a_side_low_en": Decimal("5.0"),
        },
    ]

    insert_data(test_data, algorithm_table, "hit")

    item1 = algorithm_table.get_item(Key={"apid": 478, "met": 123456})["Item"]
    item2 = algorithm_table.get_item(Key={"apid": 478, "met": 123457})["Item"]
    item3 = algorithm_table.get_item(Key={"apid": 478, "met": 123458})["Item"]

    # Not updated
    assert item1["hit_e_a_side_low_en"] == Decimal("0.0")

    # Existing item with no 'hit' data should be updated
    assert item2["hit_e_a_side_low_en"] == Decimal("3.0")
    assert item2["other_data"] == 42  # Original data still there

    # New item should be inserted
    assert item3["hit_e_a_side_low_en"] == Decimal("5.0")


@mock.patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.process_hit")
@mock.patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.process_swe")
def test_process_algorithms(mock_swe, mock_hit, setup_dynamodb):
    """Tests process_algorithms function."""
    algorithm_table = setup_dynamodb["algorithm_table"]

    mock_hit.return_value = [
        {
            "apid": 478,
            "met": 111,
            "hit_e_a_side_low_en": Decimal("1.0"),
        }
    ]
    mock_swe.return_value = [
        {
            "apid": 478,
            "met": 222,
            "swe_normalized_counts_quarter_1_esa_0": Decimal("0.123"),
        }
    ]

    process_algorithms(combined=None, algorithm_table=algorithm_table)

    response = algorithm_table.query(KeyConditionExpression=Key("apid").eq(478))[
        "Items"
    ]

    assert any(
        item["met"] == 111 and "hit_e_a_side_low_en" in item for item in response
    )
    assert any(
        item["met"] == 222 and "swe_normalized_counts_quarter_1_esa_0" in item
        for item in response
    )


@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.requests.get")
def test_get_latest_spice_kernels(mock_get):
    """Test get_latest_spice_kernels function."""
    mock_files = [
        "imap_sclk_0000.tsc",
        "naif0012.tls",
        "imap_001.tf",
        "de440.bsp",
        "imap_pred_20260922_20261020_v01.bsp",
        "imap_2026_269_2026_269_01.ah.bc",
        "imap_science_0001.tf",
        "imap_dps_2026_269_2026_269_01.ah.bc",
    ]

    mock_response = MagicMock()
    mock_response.json.return_value = mock_files
    mock_get.return_value = mock_response

    result = get_latest_spice_kernels()
    assert result.processing_input[0].filename_list == mock_files


@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.spiceypy.furnsh")
@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.ProcessingInputCollection.download_all_files"
)
@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.EFS_BASE_PATH",
    Path("/mock/efs"),
)
def test_download_spice_file(mock_download, mock_furnsh):
    """Test download_spice_file function."""
    mock_files = [
        "imap_sclk_0000.tsc",
        "naif0012.tls",
        "imap_pred_20260922_20261020_v01.bsp",
    ]
    collection = ProcessingInputCollection()
    collection.add(SPICEInput(*mock_files))

    result = download_spice_file(collection)

    assert [file.name for file in result] == [
        "imap_sclk_0000.tsc",
        "naif0012.tls",
        "imap_pred_20260922_20261020_v01.bsp",
    ]
