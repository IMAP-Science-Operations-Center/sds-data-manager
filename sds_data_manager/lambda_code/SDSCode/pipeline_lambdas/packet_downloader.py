"""Lambda function to automate packet downloads and L0 file creation."""

import logging
import os
from pathlib import Path

import boto3
import imap_data_access
from imap_data_access import SPICEFilePath, webpoda

S3_CLIENT = boto3.client("s3")
# We need to access the webpoda-api-key
SECRETS_MANAGER = boto3.client("secretsmanager")
# We need to access the IMAP API key from SSM
SSM_CLIENT = boto3.client("ssm")

# Logger setup
# Set default logging level to INFO, to also capture INFO for the underlying downloaders
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_two_most_recent_contact_times(bucket):
    """Retrieve the two most recent file upload times.

    This is an approximation of Earth Received Times (ERT) to indicate when we should
    be querying for data. The repointing files will be delivered to us after a contact.
    So we will be querying for data between when a previous repointing file was uploaded
    and the most recent one. It is a broader window than we need, but is a rough guess
    with the information we have in an attempt to bracket the packet lookups.

    Parameters
    ----------
    bucket : str
        The name of the S3 bucket to search for repointing files.

    Returns
    -------
    list or None
        A list of the two most recent file upload times.
        None if there are less than 2 repointing files available.
    """
    paginator = S3_CLIENT.get_paginator("list_objects_v2")
    # In case we have more than 1000 of these files (~3 years)
    pages = paginator.paginate(
        Bucket=bucket, Prefix=f"{SPICEFilePath._dir_prefix}/repoint/imap_"
    )

    repointing_file_times = []
    for page in pages:
        if "Contents" not in page:
            continue

        # Process objects in the page and keep only the latest two
        for obj in page["Contents"]:
            # We want last-modified time to know when these files were uploaded
            repointing_file_times.append(obj["LastModified"])

    if len(repointing_file_times) == 0:
        # Nothing in the bucket yet, query from the start of the mission to right now.
        logger.warning("No repointing files found")
        return None
    elif len(repointing_file_times) == 1:
        # Only one repointing file, so we can't bracket a contact yet.
        # Use the first launch opportunity as an initial start time,
        # and the time this repointing file landed as the end time to
        # brack the queries with.
        logger.warning("Only one repointing file found, using the start of the mission")
        return ["2025-09-23T00:00:00.000Z", repointing_file_times[0]]

    # We only want the latest two times
    return sorted(repointing_file_times)[-2:]


def lambda_handler(event, context):
    """Lambda handler to download raw packet data and create L0 files.

    The lambda function will be triggered by an S3 event when a new
    repointing file is uploaded to the S3 bucket. This means a contact
    has finished and the other SPICE / packet files are available now.

    Parameters
    ----------
    event : dict
        The JSON formatted document with the source s3 event (repointing file).
    context : obj
        The context object for the lambda function
    """
    logger.info("Received event: %s", event)
    # Extract bucket name and key from event
    record = event["Records"][0]
    bucket_name = record["s3"]["bucket"]["name"]
    repointing_key = record["s3"]["object"]["key"]
    repointing_file = "/tmp/" + repointing_key.split("/")[-1]  # noqa: S108

    times = get_two_most_recent_contact_times(bucket_name)
    if times is None:
        return {
            "statusCode": 500,
            "body": "There were fewer than 2 repointing files available.",
        }
    start_time, end_time = times

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

    # Download the repointing file from S3 for use in the repointing downloads
    S3_CLIENT.download_file(
        Bucket=bucket_name,
        Key=repointing_key,
        Filename=repointing_file,
    )

    # ENA imagers group by repointing
    repointing_instruments = {"glows", "hi", "lo", "ultra"}

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
