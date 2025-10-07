"""Lambda function to automate packet downloads and L0 file creation."""

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import imap_data_access
from imap_data_access import SPICEFilePath, webpoda

S3_BUCKET = os.environ["S3_BUCKET"]
S3_CLIENT = boto3.client("s3")
# We need to access the webpoda-api-key
SECRETS_MANAGER = boto3.client("secretsmanager")
# We need to access the IMAP API key from SSM
SSM_CLIENT = boto3.client("ssm")

# Logger setup
if len(logging.getLogger().handlers) > 0:
    logging.getLogger().setLevel(logging.INFO)
else:
    logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def setup_environment():
    """Get secrets and set necessary config values."""
    response = SECRETS_MANAGER.get_secret_value(SecretId="webpoda-token")
    if "SecretString" not in response:
        return {
            "statusCode": 500,
            "body": "Secret webpoda-token not found in Secrets Manager.",
        }
    # Update the token to be used for use in subsequent requests
    imap_data_access.config["WEBPODA_TOKEN"] = response["SecretString"]

    # Get the IMAP API key from SSM Parameter Store
    ssm_parameter_name = os.environ.get(
        "SSM_API_KEY_PARAMETER", "/imap-sdc/batch-jobs/api-key"
    )
    try:
        ssm_response = SSM_CLIENT.get_parameter(
            Name=ssm_parameter_name, WithDecryption=True
        )
        imap_data_access.config["API_KEY"] = ssm_response["Parameter"]["Value"]
    except Exception as e:
        logger.warning(f"Could not retrieve API key from SSM: {e}")
        # Continue without API key - some operations might still work

    imap_data_access.config["DATA_DIR"] = Path("/tmp")  # noqa: S108


def get_latest_repoint_file():
    """Retrieve the latest repointing file from S3.

    If we want to connect to the database later, we could use the RepointingFile
    database table to get this value instead.

    Returns
    -------
    str or None
        The string of the s3 key of the latest repointing file.
        None if there are no repointing files.
    """
    paginator = S3_CLIENT.get_paginator("list_objects_v2")
    # In case we have more than 1000 of these files (~3 years)
    # Paginate through all objects in the repoint directory
    pages = paginator.paginate(
        Bucket=S3_BUCKET, Prefix=f"{SPICEFilePath._dir_prefix}/repoint/imap_"
    )

    # Collect all file keys
    all_files = []
    for page in pages:
        if "Contents" in page:
            all_files.extend([obj["Key"] for obj in page["Contents"]])

    if not all_files:
        logger.warning("No files found in the repoint directory.")
        return None

    # Sort files by key (filename); adjust if you want to sort by LastModified instead
    last_file = sorted(all_files)[-1]
    logger.info(f"Last file in directory: {last_file}")
    return last_file


def lambda_handler(event, context):
    """Lambda handler to download raw packet data and create L0 files.

    The lambda function is triggered based upon a cron job indicating new
    data is available and should be fetched. Currently, this is downloading
    6-hours of data on a cron-based schedule. In the future, this should be
    updated to be triggered based upon the arrival of files or notifications
    about the end of contacts and a database tracking what data has been
    downloaded.

    Parameters
    ----------
    event : dict
        The JSON formatted document with the source s3 event.
    context : obj
        The context object for the lambda function
    """
    setup_environment()

    repointing_key = get_latest_repoint_file()
    imap_data_access.config["DATA_DIR"]
    repointing_file = imap_data_access.config["DATA_DIR"] / Path(repointing_key).name
    # Download the repointing file from S3 for use in the repointing downloads
    S3_CLIENT.download_file(
        Bucket=S3_BUCKET,
        Key=repointing_key,
        Filename=repointing_file,
    )

    # ENA imagers group by repointing
    repointing_instruments = {"glows", "hi", "lo", "ultra"}

    # TODO: Update start_time and end_time based upon the actual downlink times
    #       once those come in. Currently it is every 6-hours on a cron schedule.
    now = datetime.now(timezone.utc)
    # Floor to the previous 6-hour mark
    end_time = now.replace(minute=0, second=0, microsecond=0)
    end_time = end_time - timedelta(hours=end_time.hour % 6)
    start_time = end_time - timedelta(hours=6)
    logger.info(f"Downloading data from {start_time} to {end_time}")

    for instrument in webpoda.INSTRUMENT_APIDS:
        logger.info("Downloading data for instrument: %s", instrument)
        if instrument in repointing_instruments:
            # Download based on repointing
            webpoda.download_repointing_data(
                instrument=instrument,
                start_time=start_time,
                end_time=end_time,
                repointing_file=repointing_file,
                upload_to_server=True,
            )
        else:
            # Download based on time
            webpoda.download_daily_data(
                instrument=instrument,
                start_time=start_time,
                end_time=end_time,
                upload_to_server=True,
            )

    return {"statusCode": 200, "body": "Packets downloaded and L0 files created."}
