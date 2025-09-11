"""Test the I-Alirt pointing schedule lambda function."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from imap_processing.ialirt.constants import StationProperties

from sds_data_manager.lambda_code.IAlirtCode.ialirt_pointing_schedule import (
    generate_and_upload_schedule,
    lambda_handler,
)


@patch("spiceypy.furnsh")
@patch("imap_data_access.processing_input.ProcessingInputCollection.download_all_files")
@patch("sds_data_manager.lambda_code.IAlirtCode.ialirt_coverage.requests.get")
@patch("imap_processing.ialirt.constants.STATIONS")
@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_pointing_schedule."
    "generate_text_files"
)
@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_pointing_schedule.get_latest_spice_kernels"
)
def test_lambda_handler(
    mock_get_latest_spice_kernels,
    mock_generate_text_files,
    mock_stations,
    mock_requests_get,
    mock_download,
    mock_furnsh,
    s3_client,
):
    """Test the lambda_handler function."""
    mock_get_latest_spice_kernels.return_value = MagicMock()

    bucket = "test-data-bucket"
    region = "us-west-2"

    s3_client.create_bucket(Bucket=bucket)

    mock_response = MagicMock()
    mock_response.json.return_value = [
        "de440.bsp",
        "naif0012.tls",
        "pck00011.tpc",
        "imap_pred_20250826_20251001_v00.bsp",
        "earth_000101_250826_251001.bpc",
    ]
    mock_requests_get.return_value = mock_response

    mock_download.return_value = None
    mock_furnsh.return_value = None

    # Mock the Stations dictionary to have more than one item
    mock_stations.items.return_value = [
        (
            "Kiel",
            StationProperties(
                longitude=10.1808,
                latitude=54.2632,
                altitude=0.1,
                min_elevation_deg=5,
            ),
        ),
        (
            "Korea",
            StationProperties(
                longitude=1,
                latitude=1,
                altitude=1,
                min_elevation_deg=5,
            ),
        ),
    ]

    event = {
        "region": region,
        "detail": {
            "bucket": {"name": bucket},
        },
    }

    first_generate_text_files_response = [
        "Station: Kiel\n",
        "Target: IMAP\n",
        "Creation date (UTC): \n",
    ]

    second_generate_text_files_response = [
        "Station: Korea\n",
        "Target: IMAP\n",
        "Creation date (UTC): \n",
    ]

    mock_generate_text_files.side_effect = [
        first_generate_text_files_response,
        second_generate_text_files_response,
    ]
    lambda_handler(event, {})

    objects = s3_client.list_objects_v2(Bucket=bucket)
    keys = [obj["Key"] for obj in objects.get("Contents", [])]

    # Check the naming pattern
    assert keys[0].startswith("pointing_schedules/Kiel/")
    assert keys[1].startswith("pointing_schedules/Korea/")


@patch(
    "sds_data_manager.lambda_code.IAlirtCode.ialirt_pointing_schedule."
    "generate_text_files"
)
def test_generate_and_upload_schedule(mock_generate_text_files, s3_client):
    """Test the generate_and_upload_30_days function."""
    bucket = "test-data-bucket"
    region = "us-west-2"
    s3_client.create_bucket(Bucket=bucket)

    mock_generate_text_files.return_value = [
        "Station: Kiel\n",
        "Target: IMAP\n",
        "Creation date (UTC): \n",
        "Start time: \n",
        "End time: \n",
        "Cadence (sec): 60\n\n",
        "Date/Time"
        + "Azimuth".rjust(29)
        + "Elevation".rjust(17)
        + "Doppler".rjust(15)
        + "\n",
        "(UTC)" + "(deg.)".rjust(33) + "(deg.)".rjust(16) + "(km/s)".rjust(16) + "\n",
    ]

    day = (datetime.now(timezone.utc) + timedelta(days=10)).strftime("%Y-%m-%d")

    generate_and_upload_schedule(bucket, region, "Kiel", day)

    objects = s3_client.list_objects_v2(Bucket=bucket)
    keys = [obj["Key"] for obj in objects.get("Contents", [])]

    # Check the naming pattern
    assert keys[0].startswith("pointing_schedules/Kiel/")

    # Download and verify one file's content
    response = s3_client.get_object(Bucket=bucket, Key=keys[0])
    content = response["Body"].read().decode("utf-8")

    assert "Station: Kiel" in content
    assert "Target: IMAP" in content
    assert "(km/s)" in content
