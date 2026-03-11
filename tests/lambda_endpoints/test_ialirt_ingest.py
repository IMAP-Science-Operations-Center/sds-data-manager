"""Test the I-Alirt ingest lambda function."""

import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
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
    insert_formatted_data,
    insert_kernels,
    lambda_handler,
    parse_packets,
    process_algorithms,
    query_filenames,
    reformat_data,
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
def test_lambda_handler(mock_get, mock_download, mock_furnsh, setup_data_table):
    """Test the lambda_handler function."""
    # Mock event data
    data_table = setup_data_table["data_table"]
    os.environ["DATA_TABLE"] = data_table.name

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

    response = data_table.scan()
    items = response.get("Items", [])
    assert len(items) == 0


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
        "packets/iois_1_packets_2025_118_15_58_00",
        "packets/iois_1_packets_2025_118_15_59_00",
        "packets/iois_1_packets_2025_118_16_00_00",
        "packets/iois_1_packets_2025_118_16_01_00",
        "packets/iois_1_packets_2025_118_16_03_00",
        "packets/iois_1_packets_2025_118_16_04_00",
    ]

    outside_range_key = "packets/iois_1_packets_2025_118_15_57_00"

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


def test_reformat_data():
    """Test reformat_data function."""
    test_data = [
        {
            "apid": 478,
            "met": 374,
            "instrument": "mag",
            "met_in_utc": "2021-01-01T00:00:00",
            "ttj2000ns": 759175836184000000,
            "mag_data": Decimal("1.0"),
            "mag_hk_status": {"hk1v5_warn": False, "hk1v5_danger": True},
        },
        {
            "apid": 478,
            "met": 375,
            "instrument": "mag",
            "met_in_utc": "2021-01-01T00:00:01",
            "ttj2000ns": 759175836184000001,
            "mag_data": Decimal("2.0"),
            "mag_hk_status": {"hk1v5_warn": True, "hk1v5_danger": False},
        },
    ]

    science_data, hk_data = reformat_data(test_data)

    assert all("apid" not in d for d in science_data)
    assert all("met" not in d for d in science_data)
    assert all("mag_hk_status" not in d for d in science_data)
    assert science_data[0]["time_utc"] == "2021-01-01T00:00:00"

    assert hk_data[0]["instrument"] == "mag_hk"
    assert hk_data[0]["time_utc"] == "2021-01-01T00:00:00"
    assert hk_data[0]["mag_hk_status"]["hk1v5_danger"] is True


def test_reformat_data_no_hk():
    """Test reformat_data function with no HK data."""
    test_data = [
        {
            "apid": 478,
            "met": 374,
            "instrument": "hit",
            "met_in_utc": "2021-01-01T00:00:00",
            "ttj2000ns": 759175836184000000,
            "hit_data": Decimal("1.0"),
        },
        {
            "apid": 478,
            "met": 375,
            "instrument": "hit",
            "met_in_utc": "2021-01-01T00:00:01",
            "ttj2000ns": 759175836184000001,
            "hit_data": Decimal("2.0"),
        },
    ]

    science_data, hk_data = reformat_data(test_data)

    expected_science_data = [
        {
            "instrument": "hit",
            "time_utc": "2021-01-01T00:00:00",
            "ttj2000ns": 759175836184000000,
            "hit_data": Decimal("1.0"),
        },
        {
            "instrument": "hit",
            "time_utc": "2021-01-01T00:00:01",
            "ttj2000ns": 759175836184000001,
            "hit_data": Decimal("2.0"),
        },
    ]

    assert science_data == expected_science_data
    assert hk_data == []


@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.imap_state",
    return_value=np.array([[1, 2, 3, 4, 5, 6]]),
)
@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.str_to_et",
    return_value=12345.0,
)
@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.et_to_ttj2000ns",
    return_value=759175836184000000,
)
def test_insert_formatted_data(
    mock_et_to_ttj2000ns,
    mock_str_to_et,
    mock_imap_state,
    setup_data_table,
):
    """Test insert_formatted_data function."""
    data_table = setup_data_table["data_table"]

    # Existing item.
    data_table.put_item(
        Item={
            "instrument": "mag",
            "time_utc": "2021-01-01T00:00:00",
            "mag_data": Decimal("0.0"),
        }
    )

    # Existing item.
    data_table.put_item(
        Item={
            "instrument": "mag",
            "time_utc": "2021-02-01T00:00:00",
            "mag_data": Decimal("0.0"),
        }
    )

    # Existing item.
    data_table.put_item(
        Item={
            "instrument": "mag_hk",
            "time_utc": "2021-02-01T00:00:00",
            "mag_hk_status": {"hk1v5_warn": False, "hk1v5_danger": True},
        }
    )

    # Create data for all three cases
    test_data = [
        # Will insert.
        {
            "instrument": "mag",
            "met_in_utc": "2021-01-01T00:00:00",
            "ttj2000ns": 759175836184000000,
            "mag_data": Decimal("2.0"),
            "mag_hk_status": {"hk1v5_warn": False, "hk1v5_danger": True},
        },
        # Will insert.
        {
            "instrument": "mag",
            "met_in_utc": "2021-02-01T00:00:00",
            "ttj2000ns": 759175836184000001,
            "mag_data": Decimal("3.0"),
            "mag_hk_status": {"hk1v5_warn": False, "hk1v5_danger": True},
        },
        # Will insert.
        {
            "instrument": "mag",
            "met_in_utc": "2021-03-01T00:00:00",
            "ttj2000ns": 759175836184000002,
            "mag_data": Decimal("5.0"),
            "mag_hk_status": {"hk1v5_warn": False, "hk1v5_danger": True},
        },
    ]

    insert_formatted_data(test_data, data_table, "mag", "test-kernel-set-id")

    item1 = data_table.get_item(
        Key={"instrument": "mag", "time_utc": "2021-01-01T00:00:00"}
    )["Item"]
    item2 = data_table.get_item(
        Key={"instrument": "mag", "time_utc": "2021-02-01T00:00:00"}
    )["Item"]
    item3 = data_table.get_item(
        Key={"instrument": "mag", "time_utc": "2021-03-01T00:00:00"}
    )["Item"]
    item4 = data_table.get_item(
        Key={"instrument": "mag_hk", "time_utc": "2021-02-01T00:00:00"}
    )["Item"]
    item5 = data_table.get_item(
        Key={"instrument": "spacecraft", "time_utc": "2021-01-01T00:00:00"}
    )["Item"]

    # Existing
    assert item1["mag_data"] == Decimal("2.0")
    assert item2["mag_data"] == Decimal("3.0")

    # New item should be inserted
    assert item3["mag_data"] == Decimal("5.0")
    assert item4["mag_hk_status"]["hk1v5_danger"] is True
    assert item5["sc_position_GSM"] == [Decimal("1"), Decimal("2"), Decimal("3")]


@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.imap_state",
    return_value=np.array([[1, 2, 3, 4, 5, 6]]),
)
@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.et_to_ttj2000ns",
    return_value=12345.0,
)
@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.str_to_et",
    return_value=12345.0,
)
@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.load_cdf")
@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.pd.read_csv")
@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.get_ancillary")
@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.process_hit")
@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.process_packet")
@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.process_swe")
@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.process_codice")
@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.process_swapi_ialirt")
def test_process_algorithms(
    mock_swapi,
    mock_codice,
    mock_swe,
    mock_packet,
    mock_hit,
    mock_get_ancillary,
    mock_read_csv,
    mock_load_cdf,
    mock_str_to_et,
    mock_et_to_ttj2000ns,
    mock_imap_state,
    setup_data_table,
):
    """Test process_algorithms function."""
    data_table = setup_data_table["data_table"]

    mock_load_cdf.return_value = {"mock": "calibration data"}
    mock_read_csv.return_value = pd.DataFrame({"mock": [1.23]})
    mock_get_ancillary.return_value = Path(
        "/mock/imap_mag_l1b-calibration_20250101_v002.cdf"
    )

    mock_hit.return_value = [
        {
            "instrument": "hit",
            "met_in_utc": "2025-05-21T00:00:01",
            "apid": 478,
            "met": 111,
            "hit_e_a_side_low_en": Decimal("1.0"),
        }
    ]
    mock_swe.return_value = [
        {
            "instrument": "swe",
            "met_in_utc": "2025-05-21T00:00:02",
            "apid": 478,
            "met": 222,
            "swe_normalized_counts_quarter_1_esa_0": Decimal("0.123"),
        }
    ]
    mock_packet.return_value = [
        {
            "instrument": "mag",
            "met_in_utc": "2025-05-21T00:00:03",
            "apid": 478,
            "met": 333,
            "mag_phi_4s_b_gsm": Decimal("0.456"),
        }
    ]
    mock_codice.return_value = (
        [],
        [
            {
                "instrument": "codice_hi",
                "met_in_utc": "2025-05-21T00:00:04",
                "apid": 478,
                "met": 444,
                "codice": Decimal("0.111"),
            }
        ],
    )
    mock_swapi.return_value = [
        {
            "instrument": "swapi",
            "met_in_utc": "2025-05-21T00:00:05",
            "apid": 478,
            "met": 555,
            "swapi": Decimal("0.123"),
        }
    ]

    process_algorithms(
        combined=None,
        data_table=data_table,
        kernel_set_key="2025-05-21T00:00:00",
        kernel_set_key_ttj2000ns=12345,
    )

    # Query by instrument since that's the partition key
    for instrument, _met, _field in [
        ("hit", 111, "hit_e_a_side_low_en"),
        ("swe", 222, "swe_normalized_counts_quarter_1_esa_0"),
        ("mag", 333, "mag_phi_4s_b_gsm"),
        ("codice_hi", 444, "codice"),
        ("swapi", 555, "swapi"),
    ]:
        response = data_table.query(
            KeyConditionExpression=Key("instrument").eq(instrument)
        )
        items = response["Items"]
        assert items[0]["instrument"] == instrument


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

    result = get_latest_spice_kernels("https://api.dev.imap-mission.com")
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
def test_get_ancillary(mock_query, mock_ancillaryfilepath, mock_download):
    """Test get_ancillary function."""
    mock_path = Path("/mock/efs/swe/l1b-in-flight-cal/calibration.cdf")
    mock_download.return_value = mock_path
    mock_query.return_value = [
        {
            "file_path": "swe/l1b-in-flight-cal/calibration_2.cdf",
            "version": 2,
            "start_date": "2025-01-02",
        }
    ]
    mock_construct_path = MagicMock(return_value=mock_path)
    mock_ancillaryfilepath.return_value.construct_path = mock_construct_path

    with patch.object(Path, "exists", return_value=False):
        path = get_ancillary("swe", "l1b-in-flight-cal")

    assert path == mock_path


@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.met_to_ttj2000ns",
    return_value=813665124895612928,
)
@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.met_to_utc",
    return_value="2025-10-13T22:04:15.000",
)
@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.et_to_met",
    return_value=498089058,
)
@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_ingest.str_to_et",
    return_value=123456.0,
)
def test_insert_kernels(
    mock_str_to_et,
    mock_et_to_met,
    mock_met_to_utc,
    mock_met_to_ttj2000ns,
    setup_data_table,
):
    "Test insert_kernels function."
    data_table = setup_data_table["data_table"]

    spice_input = MagicMock()
    spice_input.source = ["leapseconds", "planetary_constants", "imap_frames"]
    spice_input.filename_list = ["naif0012.tls", "pck00011.tpc", "imap_100.tf"]

    dependency_inputs = MagicMock()
    dependency_inputs.processing_input = [spice_input]

    insert_kernels(dependency_inputs, data_table)

    response = data_table.query(
        KeyConditionExpression=Key("instrument").eq("spice"),
    )
    items = response["Items"]

    assert items[0]["instrument"] == "spice"
    assert items[0]["time_utc"] == "2025-10-13T22:04:15"
    assert int(items[0]["ttj2000ns"]) == 813665124895612928
    assert items[0]["spice_kernels"] == {
        "leapseconds": "naif0012.tls",
        "planetary_constants": "pck00011.tpc",
        "imap_frames": "imap_100.tf",
    }
