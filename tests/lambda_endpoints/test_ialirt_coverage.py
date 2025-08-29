"""Test the I-Alirt coverage lambda function."""

import json
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

from imap_data_access.processing_input import (
    ProcessingInputCollection,
    SPICEInput,
)

from sds_data_manager.lambda_code.IAlirtCode.ialirt_coverage import (
    generate_and_upload_30_days,
    get_dsn,
    get_latest_outage_file,
    get_latest_spice_kernels,
    lambda_handler,
    parse_outage_file,
    setup_spice_file,
)


@patch("spiceypy.furnsh")
@patch("imap_data_access.processing_input.ProcessingInputCollection.download_all_files")
@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_coverage.requests.get")
@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_coverage.get_dsn")
def test_lambda_handler(
    mock_get_dsn,
    mock_requests_get,
    mock_download,
    mock_furnsh,
    s3_client,
):
    """Test the lambda_handler function."""
    bucket = "test-data-bucket"
    region = "us-west-2"

    s3_client.put_object(
        Bucket=bucket,
        Key="imap_ialirt_outages_20260922_v001.json",
        Body=json.dumps(
            {
                "Kiel": [["2026-09-22T13:50:00.00Z", "2026-09-22T14:10:00.00Z"]],
                "DSS-75": [["2026-09-25T08:00:00.00Z", "2026-09-25T09:30:00.00Z"]],
            }
        ),
    )

    mock_response = MagicMock()
    mock_response.json.return_value = ["de440.bsp", "pck00011.tpc"]
    mock_requests_get.return_value = mock_response

    mock_download.return_value = None
    mock_furnsh.return_value = None
    mock_get_dsn.return_value = (
        Path("/imap_ialirt_contact-schedule_20260922_v001.tsv"),
        {},
    )

    event = {
        "region": region,
        "detail": {
            "bucket": {"name": bucket},
        },
    }

    lambda_handler(event, {})


@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_coverage.imap_data_access.download"
)
@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_coverage.imap_data_access.AncillaryFilePath"
)
@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_coverage.imap_data_access.query")
def test_get_latest_outage_file(
    mock_query, mock_ancillaryfilepath, mock_download, tmp_path
):
    """Test the get_latest_outage_file function."""
    mock_path = Path("/imap_ialirt_outages_20260922_v001.json")
    mock_download.return_value = mock_path
    mock_query.return_value = [{"file_path": "/imap_ialirt_outages_20260922_v001.json"}]
    mock_construct_path = MagicMock(return_value=mock_path)
    mock_ancillaryfilepath.return_value.construct_path = mock_construct_path

    with patch.object(Path, "exists", return_value=False):
        path = get_latest_outage_file(tmp_path)

    assert path == mock_path


def test_parse_outage_file(tmp_path: Path):
    """Test the parse_outage_file function with a local file."""
    file_path = tmp_path / "imap_ialirt_outages_20260922_vxxx.json"
    json_data = {
        "Kiel": [
            ["2026-09-22T13:50:00.00Z", "2026-09-22T14:10:00Z"],
        ],
        "DSS-75": [["2026-09-25T08:00:00.00Z", "2026-09-25T09:30:00Z"]],
    }
    file_path.write_text(json.dumps(json_data), encoding="utf-8")

    outages = parse_outage_file(file_path)

    expected_outages = {
        "Kiel": [("2026-09-22T13:50:00.00Z", "2026-09-22T14:10:00Z")],
        "DSS-75": [("2026-09-25T08:00:00.00Z", "2026-09-25T09:30:00Z")],
    }

    assert outages == expected_outages


def test_generate_and_upload_30_days(s3_client):
    """Test the generate_and_upload_30_days function."""
    bucket = "test-data-bucket"
    region = "us-west-2"
    s3_client.create_bucket(Bucket=bucket)

    outages = {"Kiel": [("2026-09-22T13:50:00.00Z", "2026-09-22T14:10:00.00Z")]}
    dsn = {"DSS-55": [("2026-09-22T08:00:00.00Z", "2026-09-22T09:00:00.00Z")]}

    generate_and_upload_30_days(bucket, region, outages, dsn)

    objects = s3_client.list_objects_v2(Bucket=bucket)
    keys = [obj["Key"] for obj in objects.get("Contents", [])]

    # Verify that 30 files were created
    assert len(keys) == 30

    # Check the naming pattern
    assert keys[0].startswith("coverage/imap_ialirt_coverage_")

    # Download and verify one file's content
    response = s3_client.get_object(Bucket=bucket, Key=keys[0])
    content = response["Body"].read().decode("utf-8")

    assert "# I-ALiRT Coverage Summary" in content
    assert "Kiel" in content
    assert "DSS-55" in content


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

    result = get_latest_spice_kernels(["planetary_ephemeris", "planetary_constants"])
    assert result.processing_input[0].filename_list == mock_files


@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_coverage.spiceypy.furnsh")
@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_coverage.ProcessingInputCollection.download_all_files"
)
def test_setup_spice_file(mock_download, mock_furnsh):
    """Test setup_spice_file function."""
    mock_files = [
        "de440.bsp",
        "pck00011.tpc",
    ]
    collection = ProcessingInputCollection()
    collection.add(SPICEInput(*mock_files))

    result = setup_spice_file(collection)

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
def test_get_dsn(mock_query, mock_ancillaryfilepath, mock_download, tmp_path):
    """Test get_dsn function."""
    dsn_file = tmp_path / "imap_ialirt_contact-schedule_20260922_v001.tsv"
    dsn_file.write_text(
        textwrap.dedent(
            """\
            S/C   Year/DOY    AOS       LOS      STA    Orbit  SOE/TR  Local Time
            ---------------------------------------------------------------------
            IMAP  2025/203  21:40:00  01:40:00  DSS-56  -----  ------  Tue Jul 22
            IMAP  2025/204  22:00:00  01:10:00  DSS-55  -----  ------  Wed Jul 23
            """
        )
    )
    mock_download.return_value = dsn_file
    mock_query.return_value = [
        {"file_path": "imap_ialirt_contact-schedule_20260922_v001.tsv"}
    ]
    mock_ancillaryfilepath.return_value.construct_path = MagicMock(
        return_value=dsn_file
    )

    path, dsn_dict = get_dsn(tmp_path)

    assert path == dsn_file
    assert dsn_dict == {
        "DSS-56": [("2025-07-22T21:40:00Z", "2025-07-23T01:40:00Z")],
        "DSS-55": [("2025-07-23T22:00:00Z", "2025-07-24T01:10:00Z")],
    }
