"""Lambda to generate and upload antenna pointing schedule files to s3."""

import json
import logging
import os
from datetime import datetime, timedelta, timezone

import boto3
import imap_processing.ialirt.constants
from imap_processing.ialirt.process_ephemeris import generate_text_files

from sds_data_manager.lambda_code.IAlirtCode.ialirt_coverage import (
    get_latest_spice_kernels,
    setup_spice_file,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def generate_and_upload_schedule(bucket: str, region: str, station: str, day: str):
    """Generate and upload pointing schedule files to S3.

    Parameters
    ----------
    bucket : str
        The name of the S3 bucket.
    region : str
        The region in which the s3 bucket resides.
    station : str
        The ground station for which the pointing schedule file should be generated.
    day : str
        The day for which to generate a pointing schedule, in ISO format.
        Ex: "2025-08-11".
    """
    file_name = f"{day}_{station}.txt"
    s3_path = f"pointing_schedules/{station}/{day}/{file_name}"

    output = generate_text_files(station, day)

    try:
        s3_client = boto3.client("s3", region_name=region)

        # Upload the file content to S3
        s3_client.put_object(
            Bucket=bucket,
            Key=s3_path,
            Body="".join(output),
        )
        logger.info(
            f"Pointing schedule '{day}_{station}.txt' uploaded to "
            f"s3://{bucket}/{s3_path}"
        )
    except Exception as e:
        logger.error(f"Error uploading file: {e}")


def lambda_handler(event, context):
    """Create pointing schedule files."""
    logger.info("Received event: %s", json.dumps(event))

    bucket = os.environ.get("S3_BUCKET")
    region = os.environ.get("AWS_REGION")

    # Download latest SPICE kernels
    dependency_inputs = get_latest_spice_kernels(
        [
            "planetary_ephemeris",  # e.g., de440s.bsp
            "planetary_constants",  # e.g. pck00011.tpc
            "leapseconds",  # e.g., naif0012.tls
            "ephemeris_predicted",  # e.g., imap_spk_demo.bsp
            "ephemeris_90days",  # e.g., imap_spk_demo.bsp
            "earth_attitude",  # e.g., earth_latest_high_prec.bpc
        ]
    )
    logger.info("dependency_inputs: %s", dependency_inputs)
    setup_spice_file(dependency_inputs)

    # Generate schedule for the day that is 30 days from now
    day = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")

    # Stations to generate schedules for
    stations = imap_processing.ialirt.constants.STATIONS
    for station, _ in stations.items():
        generate_and_upload_schedule(bucket, region, station, day)
