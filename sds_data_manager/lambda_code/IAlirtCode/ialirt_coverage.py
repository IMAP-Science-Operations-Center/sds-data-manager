"""IALiRT coverage plots lambda."""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import botocore
import imap_data_access
import requests
import spiceypy
from imap_data_access.processing_input import (
    ProcessingInputCollection,
    SPICEInput,
    SPICESource,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


KERNELS = {
    "planetary_ephemeris",  # e.g., de440s.bsp
    "planetary_constants",  # e.g. pck00011.tpc
}


def get_dsn(download_dir: Path):
    """Query and download DSN data.

    Parameters
    ----------
    download_dir : Path
        The directory where the file will be downloaded.

    Returns
    -------
    dsn_path : Path
        Path to the downloaded DSN file.
    dsn_dict : dict
        Contents of latest contact schedule.
    """
    imap_data_access.config["DATA_DIR"] = download_dir
    dsn_files = imap_data_access.query(
        table="ancillary",
        instrument="ialirt",
        descriptor="contact-schedule",
        version="latest",
    )

    if not dsn_files:
        dsn_dict = {}
        logger.info("No DSN files found for IALiRT. Returning empty dict.")

    dsn_path = dsn_files[0]

    download_path = imap_data_access.download(dsn_path["file_path"])
    logger.info(f"Adding to {download_path} to calibration files.")

    # Placeholder
    # TODO: parse the file and return a populated dict once we know the file structure.
    dsn_dict = {}

    return download_path, dsn_dict


def get_latest_spice_kernels() -> ProcessingInputCollection:
    """Query the SPICE metakernel API for latest SPICE kernel filenames.

    Returns
    -------
    dependency_inputs: ProcessingInputCollection
        A collection containing a SPICEInput object with the list of kernel filenames
        returned from the metakernel API.
    """
    dependency_inputs = ProcessingInputCollection()

    now = datetime.now(timezone.utc)
    one_week_ago = now - timedelta(weeks=1)
    # Define J2000 epoch: 2000-01-01T12:00:00 UTC
    # TODO: remove this once Bryan changes takes in 'yyyymmdd' format
    j2000 = datetime(2000, 1, 1, 11, 58, 56, tzinfo=timezone.utc)
    et_end_time = (now - j2000).total_seconds()
    et_start_time = (one_week_ago - j2000).total_seconds()

    file_types = ",".join(KERNELS)
    # TODO: replace this url with the endpoint from imap-data-access.
    url = "https://ylxiee1ond.execute-api.us-west-2.amazonaws.com/metakernel"

    params = {
        "start_time": str(int(et_start_time)),
        "end_time": str(int(et_end_time)),
        "list_files": "True",
        "file_types": file_types,
    }

    logger.info(f"Sending request to {url} with params: {params}")
    response = requests.get(url, params=params, timeout=10)
    metakernel_files = response.json()

    logger.info(f"Found metakernel files: {metakernel_files}. Adding to collection.")
    dependency_inputs.add(SPICEInput(*metakernel_files))

    return dependency_inputs


def download_spice_file(dependencies) -> list[Path]:
    """Download SPICE kernel files.

    Parameters
    ----------
    dependencies: ProcessingInputCollection
        A collection containing a SPICEInput object with the list of kernel filenames
        returned from the metakernel API.

    Returns
    -------
    spice_files: list[Path]
        A list of Path objects representing the SPICE files stored in EFS.

    Notes
    -----
    List is priority ordered so furnishing in order results in correct SPICE priority.
    """
    dependencies.download_all_files()

    spice_files = dependencies.get_file_paths(data_type=SPICESource.SPICE.value)
    spiceypy.furnsh([str(file.resolve()) for file in spice_files])

    return spice_files


def get_latest_outage_file(bucket: str, region: str) -> str | None:
    """Get the most recent outage file key from S3.

    Parameters
    ----------
    bucket : str
        The name of the S3 bucket.
    region : str
        The region in which the s3 bucket resides.

    Returns
    -------
    latest_outage_file : str
        File path.
    """
    s3_client = boto3.client(
        "s3",
        region_name=region,
        config=botocore.client.Config(signature_version="s3v4"),
    )

    response = s3_client.list_objects_v2(Bucket=bucket, Prefix="outages")
    objects = response.get("Contents", [])
    if not objects:
        return None

    # Assumes filenames sort by date, e.g., outages_20260922.txt
    latest_outage_file = max(objects, key=lambda obj: obj["Key"])["Key"]
    return latest_outage_file


def parse_outage_file(
    bucket: str, region: str, key: str
) -> dict[str, list[tuple[str, str]]]:
    """Download outage file and parse into outages dict.

    Parameters
    ----------
    bucket : str
        The name of the S3 bucket.
    region : str
        The region in which the s3 bucket resides.
    key : str
        The key of the latest S3 object containing the outage data.

    Returns
    -------
    outages : dict
        Dictionary containing the data.

    Notes
    -----
    Input text file format:
    Kiel,2026-09-22T13:50:00.00Z,2026-09-22T14:10:00Z
    Kiel,2026-09-25T08:00:00.00Z,2026-09-25T09:30:00Z

    Output dictionary structure:
        outages = {
        "Kiel": [
            ("2026-09-22T13:50:00.00Z", "2026-09-22T14:10:00Z"),
            ("2026-09-25T08:00:00.00Z", "2026-09-25T09:30:00Z"),
        ],
    }
    """
    s3_client = boto3.client(
        "s3",
        region_name=region,
        config=botocore.client.Config(signature_version="s3v4"),
    )

    response = s3_client.get_object(Bucket=bucket, Key=key)
    content = response["Body"].read().decode("utf-8").strip().splitlines()

    outages: dict[str, list[tuple[str, str]]] = {}
    for line in content:
        if not line.strip():
            continue
        station, start, end = [x.strip() for x in line.split(",")]
        outages.setdefault(station, []).append((start, end))

    return outages


def generate_and_upload_30_days(bucket: str, region: str, outages: dict, dsn: dict):
    """Upload new coverage json files to S3.

    Parameters
    ----------
    bucket : str
        The name of the S3 bucket.
    region : str
        The region in which the s3 bucket resides.
    outages : dict
        Dictionary containing outages data.
    dsn : dict
        Dictionary containing DSN data.
    """
    today = datetime.now(timezone.utc)

    for i in range(30):
        day = today + timedelta(days=i)
        # start_time = day.strftime("%Y-%m-%dT00:00:00Z")

        # Placeholder for after we import from imap_processing.
        # coverage_dict = generate_coverage(start_time=start_time,
        # outages=outages, dsn=dsn)
        # table_output = format_coverage_summary(coverage_dict, start_time)
        table_output = (
            "# I-ALiRT Coverage Summary\n"
            "# Generated: 2026-09-22T00:00:00Z\n"
            "# Stations: Kiel, DSS-55\n"
            "# Time format: UTC (ISOC)\n"
            "Time (UTC)                Kiel     DSS-55\n"
            "-----------------------------------------\n"
            "2026-09-22T07:00:00.000   1        0\n"
            "2026-09-22T08:00:00.000   1        0\n"
            "-----------------------------------------\n"
            "Total Coverage Percent: 37.5%"
        )

        output_key = f"coverage/coverage_{day.strftime('%Y%m%d')}.json"

        s3_client = boto3.client("s3", region_name=region)
        s3_client.put_object(
            Bucket=bucket,
            Key=output_key,
            Body=table_output.encode("utf-8"),
            ContentType="text/plain",
        )
        logger.info(f"Uploaded coverage table to s3://{bucket}/{output_key}")


def lambda_handler(event, context):
    """Create coverage json files."""
    logger.info("Received event: %s", json.dumps(event))

    bucket = event["detail"]["bucket"]["name"]
    region = event["region"]

    # Get dsn_schedule
    _, dsn = get_dsn(Path("/tmp"))  # noqa: S108

    # Download latest SPICE kernels
    dependency_inputs = get_latest_spice_kernels()
    logger.info("dependency_inputs: %s", dependency_inputs)
    download_spice_file(dependency_inputs)

    # Get latest outage file
    latest_key = get_latest_outage_file(bucket, region)

    if latest_key:
        outages = parse_outage_file(bucket, region, latest_key)
        logger.info("Parsed outages: %s", outages)
    else:
        outages = {}
        logger.info(
            "No outage files found in bucket %s. Using empty outages dict.", bucket
        )

    generate_and_upload_30_days(bucket, region, outages, dsn)
