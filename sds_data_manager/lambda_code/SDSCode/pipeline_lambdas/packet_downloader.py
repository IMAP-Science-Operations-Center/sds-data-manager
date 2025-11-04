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

# ENA imagers group by repointing
REPOINTING_INSTRUMENTS = {"glows", "hi", "lo", "ultra"}


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


def get_latest_repoint_file_and_query_times():
    """Retrieve the latest repointing file from S3.

    Additionally, determine the time range to query for packets based upon
    the latest repointing file and the previous repointing file that is
    outside of 1-hour before the latest repointing file.

    Notes
    -----
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
            # list of (s3 key, datetime)
            # Note: obj["LastModified"] is already a datetime object from boto3
            all_files.extend(
                [
                    (
                        obj["Key"],
                        obj["LastModified"].replace(minute=0, second=0, microsecond=0),
                    )
                    for obj in page["Contents"]
                ]
            )

    if len(all_files) < 2:
        raise ValueError("Not enough repointing files to determine download interval.")

    # Sort files by key (filename); adjust if you want to sort by LastModified instead
    all_files = sorted(all_files, reverse=True)
    # Keep track of the times we need to query between for packets.
    # We will query between the previous time we queried and the time
    # of the last repoint file
    latest_file, current_time = all_files[0]
    logger.info(f"Latest repointing file [{latest_file}] at time [{current_time}]")

    for _, previous_file_datetime in all_files[1:]:
        if previous_file_datetime < current_time - timedelta(hours=1):
            # This is a repointing file that is earlier than 1-hour before
            # the latest repointing file, so we can use this as our start time
            # NOTE: This accounts for if we get several repoint files delivered
            # within a few seconds of each other.
            return latest_file, previous_file_datetime, current_time
    raise ValueError(
        "No repointing files found outside of 1-hour before this repoint."
        f" Latest file time: {current_time}"
    )


def download_ena_data(event_repoint_key):
    """Download data for ENA instruments based upon repointing files."""
    repointing_key, start_time, end_time = get_latest_repoint_file_and_query_times()
    if repointing_key != event_repoint_key:
        # We likely got triggered with a few events at once, but we only
        # want to process the latest repointing file
        logger.info(
            "Repointing file in event does not match latest repointing file. "
            "Skipping downloads."
        )
        return

    repointing_file = imap_data_access.config["DATA_DIR"] / Path(repointing_key).name
    # Download the repointing file from S3 for use in the repointing downloads
    S3_CLIENT.download_file(
        Bucket=S3_BUCKET,
        Key=repointing_key,
        Filename=repointing_file,
    )

    logger.info(f"Downloading data from {start_time} to {end_time}")
    for instrument in REPOINTING_INSTRUMENTS:
        logger.info("Downloading data for instrument: %s", instrument)
        # Download based on repointing
        webpoda.download_repointing_data(
            instrument=instrument,
            start_time=start_time,
            end_time=end_time,
            repointing_file=repointing_file,
            upload_to_server=True,
        )


def download_insitu_data():
    """Download data for in-situ instruments based upon time intervals."""
    # TODO: Update start_time and end_time based upon the actual downlink times
    #       once those come in. Currently it is every 6-hours on a cron schedule.
    now = datetime.now(timezone.utc)
    # Floor to the previous 6-hour mark
    end_time = now.replace(minute=0, second=0, microsecond=0)
    end_time = end_time - timedelta(hours=end_time.hour % 6)
    start_time = end_time - timedelta(hours=6)
    logger.info(f"Downloading data from {start_time} to {end_time}")

    # Loop over all non-repointing instruments and download data
    for instrument in set(webpoda.INSTRUMENT_APIDS) - REPOINTING_INSTRUMENTS:
        logger.info("Downloading data for instrument: %s", instrument)
        webpoda.download_daily_data(
            instrument=instrument,
            start_time=start_time,
            end_time=end_time,
            upload_to_server=True,
        )


def lambda_handler(event, context):
    """Lambda handler to download raw packet data and create L0 files.

    The lambda function is triggered based upon a cron job indicating new
    data is available and should be fetched, or repointing files arriving.
    Currently, this is downloading 6-hours of data on a cron-based schedule.
    In the future, this could be based on notifications
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

    # If we were triggered by an s3 repointing event, download ENA data
    if "Records" in event:
        event_repoint_key = event["Records"][0]["s3"]["object"]["key"]
        download_ena_data(event_repoint_key)
    else:
        download_insitu_data()

    return {"statusCode": 200, "body": "Packet downloader finished."}
