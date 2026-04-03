"""IALiRT coverage plots lambda."""

import json
import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import imap_data_access
import requests
import spiceypy
from imap_data_access.processing_input import (
    ProcessingInputCollection,
    SPICEInput,
    SPICESource,
)
from imap_processing.ialirt.generate_coverage import (
    format_coverage_summary,
    generate_coverage,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


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

    Notes
    -----
    Example of DSN structure:
    S/C   Year/DOY    AOS       LOS      STA    Orbit  SOE/TR  Local Time (UTC -0600)
    ---------------------------------------------------------------------------------
    IMAP  2025/203  21:40:00  01:40:00  DSS-56  -----  ------  Tue Jul 22 03:40PM
    IMAP  2025/204  22:00:00  01:10:00  DSS-55  -----  ------  Wed Jul 23 04:00PM
    """
    imap_data_access.config["DATA_DIR"] = download_dir
    dsn_files = imap_data_access.query(
        table="ancillary",
        instrument="ialirt",
        descriptor="contact-schedule",
        version="latest",
    )

    if not dsn_files:
        logger.info("No DSN files found for IALiRT. Returning empty dict.")
        return None, {}

    dsn_path = sorted(
        dsn_files, key=lambda x: (x["start_date"], x["version"]), reverse=True
    )[0]
    download_path = imap_data_access.download(dsn_path["file_path"])
    logger.info(f"Downloading to {download_path}.")

    dsn_dict: dict[str, list[tuple[str, str]]] = {}

    with open(download_path, encoding="utf-8") as f:
        lines = f.readlines()

    # Skip header lines
    for line in lines:
        if line.startswith("IMAP"):
            parts = line.split()

            year_doy = parts[1]
            aos = parts[2]
            los = parts[3]
            station = parts[4]

            # Parse AOS time
            year, doy = map(int, year_doy.split("/"))
            aos_dt = datetime.strptime(f"{year} {doy} {aos}", "%Y %j %H:%M:%S")
            los_dt = datetime.strptime(f"{year} {doy} {los}", "%Y %j %H:%M:%S")

            # If LOS time is earlier than AOS, it must be the next day
            if los_dt < aos_dt:
                los_dt += timedelta(days=1)

            start = aos_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            end = los_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            dsn_dict.setdefault(station, []).append((start, end))

    return download_path, dsn_dict


def parse_uksa_schedule_xml(xml_content: str) -> list[tuple[str, str]]:
    """Parse UKSA contact schedule XML and return track timestamps.

    Parameters
    ----------
    xml_content : str
        Raw XML string from the UKSA schedule file.

    Returns
    -------
    list[tuple[str, str]]
        List of (start, end) tuples derived from each activity's
        ``beginningOfActivity`` (+30 min) and ``endOfActivity`` (-15 min),
        converted from DOY format (YYYY-DOYThh:mm:ss.sssZ) to ISO 8601 calendar format.

    Notes
    -----
    Input timestamp format: 2025-177T12:40:00.000Z (year + day-of-year)
    Output timestamp format: 2025-06-26T12:40:00Z
    """
    root = ET.fromstring(xml_content)  # noqa: S314
    contacts = []
    for activity in root.iter("scheduledActivity"):
        start = activity.get("beginningOfActivity")
        end = activity.get("endOfActivity")
        if start and end:
            start_dt = datetime.strptime(start, "%Y-%jT%H:%M:%S.%fZ") + timedelta(
                minutes=30
            )
            end_dt = datetime.strptime(end, "%Y-%jT%H:%M:%S.%fZ") - timedelta(
                minutes=15
            )
            contacts.append(
                (
                    start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                )
            )
    return contacts


def get_uksa(bucket: str, region: str) -> list[tuple[str, str]]:
    """Read and parse the latest UKSA contact schedule XML from S3.

    Parameters
    ----------
    bucket : str
        The name of the S3 bucket.
    region : str
        The AWS region.

    Returns
    -------
    list[tuple[str, str]]
        List of (start, end) tuples from the UKSA schedule, with offsets applied
        per ``parse_uksa_schedule_xml``.
    """
    s3_client = boto3.client("s3", region_name=region)
    prefix = "ground_station_schedules/uksa/"

    paginator = s3_client.get_paginator("list_objects_v2")
    latest_obj = None
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if not obj["Key"].endswith(".xml"):
                continue
            if latest_obj is None or obj["LastModified"] > latest_obj["LastModified"]:
                latest_obj = obj

    if latest_obj is None:
        logger.info("No UKSA schedule files found in S3. Returning empty list.")
        return []

    latest_key = latest_obj["Key"]
    logger.info(f"Reading UKSA schedule from s3://{bucket}/{latest_key}")

    obj = s3_client.get_object(Bucket=bucket, Key=latest_key)
    xml_content = obj["Body"].read().decode("utf-8")

    return parse_uksa_schedule_xml(xml_content)


def get_latest_spice_kernels(kernels: list[str], url: str) -> ProcessingInputCollection:
    """Query the SPICE metakernel API for latest SPICE kernel filenames.

    Parameters
    ----------
    kernels : list[str]
        List of SPICE kernel categories to collect.
    url: str
        URL to download the kernels from.

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

    file_types = ",".join(kernels)
    metakernel_url = url + "/metakernel"

    params = {
        "start_time": str(int(et_start_time)),
        "end_time": str(int(et_end_time)),
        "list_files": "True",
        "file_types": file_types,
    }

    logger.info(f"Sending request to {metakernel_url} with params: {params}")
    response = requests.get(metakernel_url, params=params, timeout=10)
    metakernel_files = response.json()

    logger.info(f"Found metakernel files: {metakernel_files}. Adding to collection.")
    dependency_inputs.add(SPICEInput(*metakernel_files))

    return dependency_inputs


def setup_spice_file(dependencies) -> list[Path]:
    """Download and furnish SPICE kernel files.

    Parameters
    ----------
    dependencies: ProcessingInputCollection
        A collection containing a SPICEInput object with the list of kernel filenames
        returned from the metakernel API.

    Returns
    -------
    spice_files: list[Path]
        A list of Path objects representing the downloaded SPICE files.

    Notes
    -----
    List is priority ordered so furnishing in order results in correct SPICE priority.
    """
    dependencies.download_all_files()

    spice_files = dependencies.get_file_paths(data_type=SPICESource.SPICE.value)
    spiceypy.furnsh([str(file.resolve()) for file in spice_files])

    return spice_files


def get_latest_outage_file(download_dir: Path) -> Path | None:
    """Get the most recent outage file key from S3.

    Parameters
    ----------
    download_dir : Path
        The directory where the file will be downloaded.

    Returns
    -------
    download_path : Path
        File path.
    """
    imap_data_access.config["DATA_DIR"] = download_dir
    outages = imap_data_access.query(
        table="ancillary",
        instrument="ialirt",
        descriptor="outages",
        version="latest",
    )
    if not outages:
        return None

    latest_outage_file = sorted(
        outages, key=lambda x: (x["version"], x["start_date"]), reverse=True
    )[0]

    download_path = imap_data_access.download(latest_outage_file["file_path"])
    logger.info(f"Downloading to {download_path}.")

    return download_path


def parse_outage_file(file_path: Path) -> dict[str, list[tuple[str, str]]]:
    """Parse outage file into outages dict.

    Parameters
    ----------
    file_path : Path
        File path.

    Returns
    -------
    outages : dict
        Dictionary containing the data.

    Notes
    -----
    Input json file format:
    {
        "Kiel": [
            ["2026-09-22T13:50:00.00Z", "2026-09-22T14:10:00Z"],
            ["2026-09-25T08:00:00.00Z", "2026-09-25T09:30:00Z"]
        ]
    }

    Output dictionary structure:
        outages = {
        "Kiel": [
            ("2026-09-22T13:50:00.00Z", "2026-09-22T14:10:00Z"),
            ("2026-09-25T08:00:00.00Z", "2026-09-25T09:30:00Z"),
        ],
    }
    """
    with file_path.open("r", encoding="utf-8") as f:
        raw_outages: dict[str, list[list[str]]] = json.load(f)

    # Convert inner lists to tuples
    outages = {
        station: [tuple(period) for period in periods]
        for station, periods in raw_outages.items()
    }

    return outages


def generate_and_upload_30_days(
    bucket: str,
    region: str,
    outages: dict,
    dsn: dict,
    uksa: list | None = None,
):
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
    uksa : list, optional
        List of UKSA contact windows as (start, end) tuples.

    Notes
    -----
    Example dictionary structure for outages and dsn:
    outages = {"Kiel": [("2026-09-22T13:50:00.00Z", "2026-09-22T14:10:00.00Z")]}
    dsn = {"DSS-55": [("2026-09-22T08:00:00.00Z", "2026-09-22T09:00:00.00Z")]}
    """
    today = datetime.now(timezone.utc)

    for i in range(30):
        day = today + timedelta(days=i)
        start_time = day.strftime("%Y-%m-%dT00:00:00Z")

        coverage_dict, outage_dict = generate_coverage(start_time, outages, dsn, uksa)
        table_output = format_coverage_summary(coverage_dict, outage_dict, start_time)

        output_key = f"coverage/imap_ialirt_coverage_{day.strftime('%Y%m%d')}.json"

        s3_client = boto3.client("s3", region_name=region)
        s3_client.put_object(
            Bucket=bucket,
            Key=output_key,
            Body=json.dumps(table_output, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
        logger.info(f"Uploaded coverage table to s3://{bucket}/{output_key}")


def lambda_handler(event, context):
    """Create coverage json files."""
    logger.info("Received event: %s", json.dumps(event))

    bucket = os.environ.get("S3_BUCKET")
    region = os.environ.get("AWS_REGION")
    url = os.environ.get("IMAP_DATA_ACCESS_URL")

    # Get dsn_schedule
    _, dsn = get_dsn(Path("/tmp"))  # noqa: S108

    # Get UKSA schedule
    uksa = get_uksa(bucket, region)

    # Download latest SPICE kernels
    dependency_inputs = get_latest_spice_kernels(
        [
            "planetary_ephemeris",  # e.g., de440s.bsp
            "planetary_constants",  # e.g. pck00011.tpc
            "leapseconds",
            "ephemeris_predicted",
            "ephemeris_90days",
        ],
        url,
    )
    logger.info("dependency_inputs: %s", dependency_inputs)
    setup_spice_file(dependency_inputs)

    # Get latest outage file
    outage_file_path = get_latest_outage_file(Path("/tmp"))  # noqa: S108

    if outage_file_path:
        outages = parse_outage_file(outage_file_path)
        logger.info("Parsed outages: %s", outages)
    else:
        outages = {}
        logger.info(
            "No outage files found in bucket %s. Using empty outages dict.", bucket
        )

    generate_and_upload_30_days(bucket, region, outages, dsn, uksa)
