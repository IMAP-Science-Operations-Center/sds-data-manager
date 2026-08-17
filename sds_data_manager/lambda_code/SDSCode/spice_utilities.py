"""Shared functions for SPICE-related lambdas."""

import datetime
import json
import logging
import os
import tempfile
from pathlib import Path

import boto3
import imap_data_access
import spiceypy
from imap_data_access import SPICEFilePath

from sds_data_manager.lambda_code.SDSCode.api_lambdas import spice_metakernel_api

MAXIMUM_MISSION_J2000_TIME = 4575787269.183866

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def download_from_s3(s3_key: str, bucket_name: str | None = None) -> Path:
    """Download a file from S3 to a local temporary path.

    Parameters
    ----------
    s3_key : str
        The S3 key (path) of the file to download.
    bucket_name : Optional[str], optional
        The S3 bucket name. If not provided, will use the S3_BUCKET
        environment variable.

    Returns
    -------
    Path
        The local path where the file was downloaded.

    Raises
    ------
    ValueError
        If bucket_name is not provided and S3_BUCKET environment variable is
        not set.
    """
    if bucket_name is None:
        bucket_name = os.environ.get("S3_BUCKET")
        if bucket_name is None:
            raise ValueError(
                "bucket_name must be provided or S3_BUCKET environment "
                "variable must be set"
            )

    # Create a temporary file path
    filename = os.path.basename(s3_key)
    temp_dir = tempfile.gettempdir()
    local_path = Path(temp_dir) / filename

    # Download from S3
    s3_client = boto3.client("s3")
    try:
        s3_client.download_file(bucket_name, s3_key, str(local_path))
        logger.info(f"Downloaded {s3_key} from bucket {bucket_name} to {local_path}")
        return local_path
    except Exception as e:
        logger.error(e)
        raise FileNotFoundError(
            f"Failed to download {s3_key} from bucket {bucket_name}: {e}"
        ) from e


def convert_input_times_to_j2000(start_date_str, end_date_str):
    """Convert input to seconds since J2000."""
    try:
        # Convert to datetime objects
        start_date_datetime = datetime.datetime.strptime(start_date_str, "%Y%m%d")
        end_date_datetime = datetime.datetime.strptime(end_date_str, "%Y%m%d")

        # Use SPICE to convert to J2000

        # First, check if LSK is loaded in yet
        count = spiceypy.ktotal("TEXT")
        lsk_loaded = False
        for i in range(count):
            filename, _, _, _ = spiceypy.kdata(i, "TEXT", 100, 100, 100)

            if ".tls" in filename:
                logger.info("Leapsecond kernel is furnished.")
                lsk_loaded = True
                break

        # If it is not loaded, attempt to load it
        if not lsk_loaded:
            logger.info(
                "Attempting to load leapseconds kernel needed for time conversion."
            )
            furnish_best_spice_file("leapseconds")

        # Convert datetime to J2000 using spiceypy
        start_date = spiceypy.datetime2et(start_date_datetime)
        end_date = spiceypy.datetime2et(end_date_datetime)
    except (TypeError, ValueError):
        start_date = float(start_date_str)
        end_date = float(end_date_str)
    return start_date, end_date


def furnish_best_spice_file(kernel_type: str):
    """Furnish the best kernel for given type.

    Parameters
    ----------
    kernel_type: str
        Kernel type to furnish, e.g. 'leapseconds' or 'spacecraft_clock'.

    Returns
    -------
    highest_version_spice_file: Path
        The path to the SPICE file that was furnished

    Raises
    ------
    FileNotFoundError
        If S3_BUCKET or DATA_DIR are not set, no files are found in the database,
        or the file is not in the S3 bucket, FileNotFoundError will raise.
    """
    # Check if S3_BUCKET and DATA_DIR are set
    if "S3_BUCKET" not in os.environ or "DATA_DIR" not in imap_data_access.config:
        raise FileNotFoundError(
            f"Unable to find the latest {kernel_type} kernel. "
            "Please ensure S3_BUCKET and DATA_DIR are set in the environment variables."
        )

    metakernel_response = spice_metakernel_api.lambda_handler(
        {
            "queryStringParameters": {
                "start_time": 0,
                "end_time": MAXIMUM_MISSION_J2000_TIME,
                "list_files": "True",
                "file_types": kernel_type,
            }
        },
        None,
    )
    if metakernel_response["statusCode"] != 200:
        raise FileNotFoundError(
            f"Unable to find the latest {kernel_type} kernel. "
            "Please ensure that the kernel is available in the database."
        )
    kernel_filename = json.loads(metakernel_response["body"])[0]
    logger.info(f"Furnishing the latest {kernel_type} kernel: {kernel_filename}")
    # Download the latest kernel file
    # Convert this into an s3 key
    # Relative to our base directory to trim off the initial path
    s3_key = str(
        SPICEFilePath(kernel_filename)
        .construct_path()
        .relative_to(imap_data_access.config["DATA_DIR"])
    )
    highest_version_spice_file = download_from_s3(s3_key)
    logger.info(f"Downloaded SPICE file: {highest_version_spice_file}")
    # Furnish the SPICE file
    spiceypy.furnsh(str(highest_version_spice_file))
    return highest_version_spice_file
