"""Shared functions for SPICE-related lambdas."""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

import boto3
import imap_data_access
import spiceypy
from imap_data_access import SPICEFilePath

from .api_lambdas import spice_metakernel_api

MAXIMUM_MISSION_J2000_TIME = 4575787269.183866

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def download_from_s3(s3_key: str, bucket_name: Optional[str] = None) -> Path:
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
        logger.error()
        raise FileNotFoundError(
            f"Failed to download {s3_key} from bucket {bucket_name}: {e}"
        ) from e


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

    # Query for latest kernel
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
