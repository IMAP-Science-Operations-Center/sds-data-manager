"""Functions for supporting the indexer component of the architecture."""

import json
import logging
import os
from datetime import datetime, timezone

import boto3
from imap_data_access import (
    AncillaryFilePath,
    QuicklookFilePath,
    ReleaseFilePath,
    ScienceFilePath,
)
from imap_data_access.file_validation import generate_imap_file_path

from ..database import database as db
from ..database import models
from .dependency import calculate_crid
from .lambda_custom_events import IMAPLambdaPutEvent

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def get_file_ingestion_date(file_path):
    """Get s3 file ingestion date.

    Parameters
    ----------
    file_path: str
        S3 object path. Eg. filepath/filename.ext

    Returns
    -------
    file_ingestion_date: datetime.datetime
        Last modified data of s3 file.

    """
    # Create an S3 client
    s3_client = boto3.client("s3")

    # Retrieve the metadata of the object
    bucket_name = os.getenv("S3_BUCKET")
    logger.info(f"looking up ingestion date for {file_path}")

    response = s3_client.head_object(Bucket=bucket_name, Key=file_path)
    file_ingestion_date = response["LastModified"]

    # LastModified looks like this:
    # 2024-01-25 23:35:26+00:00
    return file_ingestion_date


def http_response(headers=None, status_code=200, body="Success"):
    """Customize HTTP response for the lambda function.

    Parameters
    ----------
    headers : dict, optional
        Content headers for the response, defaults to Content-type: text/html.
    status_code : int, optional
        HTTP status code indicating the result of the operation, defaults to 200.
    body : str, optional
        The content of the response, defaults to 'Success'.

    Returns
    -------
    dict
        A dictionary containing headers, status code, and body, designed to be returned
        by a Lambda function as an API response.

    """
    if headers is None:
        headers = (
            {
                "Content-Type": "text/html",
            },
        )
    return {
        "headers": headers,
        "statusCode": status_code,
        "body": body,
    }


def send_event_from_indexer(file_obj):
    """Send custom PutEvent to EventBridge.

    Example of what PutEvent looks like:
    event = {
        "Source": "imap.lambda",
        "DetailType": "Processed File",
        "Detail": {
            "object": {
                  "key": filename
                  "instrument": instrument_name
            },
        },
    }

    Parameters
    ----------
    file_obj : AncillaryFilePath, ScienceFilePath
        The filename to use in the PutEvent

    Returns
    -------
    dict
        EventBridge response

    """
    logger.info("Sending event function from indexer Lambda")
    event_client = boto3.client("events")

    # Create event["detail"] information

    # Batch starter uses "key" to retrieve the filename. SQS/Eventbridge use the
    # other object items to sort or filter messages.
    detail = {
        "object": {
            "key": str(file_obj.filename),
            "instrument": file_obj.instrument,
            "data_level": "ancillary",
        }
    }

    # used to filter science file events in SQS
    if isinstance(file_obj, ScienceFilePath):
        detail["object"]["data_level"] = file_obj.data_level

    # create PutEvent dictionary
    event = IMAPLambdaPutEvent(detail_type="Processed File", detail=detail)
    event_data = event.to_event()
    logger.info(f"sending this detail to event - {event_data}")

    # Send event to EventBridge
    response = event_client.put_events(Entries=[event_data])
    logger.info(f"response - {response}")
    return response


def write_file_metadata_to_table(
    filename: str, s3_filepath: str
) -> tuple[object, dict] | None:
    """Write file metadata to database.

    Uses generate_imap_file_path to determine the file type, then extracts
    metadata and writes it to the appropriate database table. Supports
    ScienceFilePath, QuicklookFilePath, ReleaseFilePath, and AncillaryFilePath.

    Parameters
    ----------
    filename : str
        The filename to extract metadata from and write to database.
    s3_filepath : str
        S3 object path. Eg. filepath/filename.pkts

    Returns
    -------
    file_obj : ImapFilePath | None
        The file object created from the filename.
    params : dict
        The metadata parameters extracted from the filename and written
        to the database.

    """
    file_obj = generate_imap_file_path(filename)

    # Extract filename components and prepare common parameters for
    # database entry
    params = file_obj.extract_filename_components(filename)
    params.pop("mission")
    params["start_date"] = datetime.strptime(params.pop("start_date"), "%Y%m%d")
    params["file_path"] = s3_filepath
    params["ingestion_date"] = get_file_ingestion_date(s3_filepath)

    # Check quicklook first since it inherits from science file.
    if isinstance(file_obj, QuicklookFilePath):
        with db.Session() as session, session.begin():
            session.add(models.QuicklookFiles(**params))
        logger.info(
            "Skipped sending event to batch starter for quicklook. "
            "The file doesn't kick off any processing jobs."
        )
        return http_response(status_code=200, body="Success")

    elif isinstance(file_obj, ScienceFilePath):
        with db.Session() as session, session.begin():
            science_file = models.ScienceFiles(**params)
            session.add(science_file)
            crid = calculate_crid(session, science_file)
            science_file.crid = crid
        logger.info("Wrote data to the ScienceFiles table")

    # Check ReleaseFilePath before AncillaryFilePath since it inherits from it.
    elif isinstance(file_obj, ReleaseFilePath):
        if params.get("end_date"):
            params["end_date"] = datetime.strptime(params.pop("end_date"), "%Y%m%d")
        with db.Session() as session, session.begin():
            session.add(models.ReleaseFiles(**params))
        logger.info("Wrote data to the ReleaseFiles table")
        logger.info(
            "Skipped sending event to batch starter for release files. "
            "The file doesn't kick off any processing jobs."
        )
        return http_response(status_code=200, body="Success")

    elif isinstance(file_obj, AncillaryFilePath):
        if params.get("end_date"):
            params["end_date"] = datetime.strptime(params.pop("end_date"), "%Y%m%d")
        with db.Session() as session, session.begin():
            session.add(models.AncillaryFiles(**params))
        logger.info("Wrote data to the AncillaryFiles table")

    return file_obj, params


def s3_event_handler(event):
    """S3 events handler.

    S3 event handler takes s3 event and then writes information to
    the proper file table. It also sends event to the batch starter
    lambda once it finishes writing information to database.

    Parameters
    ----------
    event : dict
        The JSON formatted document with the data required for the
        lambda function to process

    Returns
    -------
    dict
        HTTP response

    """
    # Retrieve the Object name
    s3_filepath = event["detail"]["object"]["key"]
    filename = os.path.basename(s3_filepath)

    try:
        file_obj = generate_imap_file_path(filename)
    except ValueError:
        logger.error(f"Filename {filename} is not a valid filetype.")
        return http_response(
            status_code=400,
            body=f"Filename {filename} is not a valid SCIENCE, "
            + "ANCILLARY or QUICKLOOK file, or RELEASE file.",
        )

    # Skip IDEX L0 files — they are indexed by a separate lambda.
    if type(file_obj) is ScienceFilePath:
        if file_obj.instrument == "idex" and file_obj.data_level == "l0":
            message = (
                f"Received an IDEX L0 file {filename}. This file will be indexed "
                f"in a separate lambda. See idex-l0-file-indexer lambda for details."
            )
            logger.info(message)
            return http_response(status_code=200, body=message)

    file_obj, _ = write_file_metadata_to_table(filename, s3_filepath)

    # Quicklook and release files don't kick off any further processing.
    if isinstance(file_obj, (QuicklookFilePath, ReleaseFilePath)):
        logger.info(
            "Skipped sending event to batch starter for quicklook/release. "
            "The file doesn't kick off any processing jobs."
        )
        return http_response(status_code=200, body="Success")

    # Send event from this lambda for Batch starter lambda
    send_event_from_indexer(file_obj)
    logger.debug("S3 event handler complete")
    return http_response(status_code=200, body="Success")


def batch_event_handler(event):
    r"""Batch event handler.

    Parameters
    ----------
    event : dict
        The JSON formatted document with the data required for the
        lambda function to process

    Example event input:
    Kept only parameter of interest
    event = {
        "detail-type": "Batch Job State Change",
        "source": "aws.batch",
        "time": "2025-04-11T18:48:16Z",
        "detail": {
            "jobArn": (
                "arn:aws:batch:us-west-2:012345678910:"
                "job/26242c7e-3d49-4e41-9387-74fcaf9630bb"
            ),
            "jobName": "swe-l0-job",
            "jobId": "26242c7e-3d49-4e41-9387-74fcaf9630bb",
            "jobQueue": (
                "arn:aws:batch:us-west-2:012345678910:"
                "job-queue/swe-fargate-batch-job-queue"
            ),
            "status": "FAILED",
            "statusReason": "some error message",
            "createdAt": 1744396985534,
            "startedAt": 1744397031734,
            "stoppedAt": 1744397296519,
            "jobDefinition": (
                "arn:aws:batch:us-west-2:012345678910:"
                "job-definition/fargate-batch-job-definitionswe:1"
            ),
            "container": {
                "image": (
                    "123456789012.dkr.ecr.us-west-2.amazonaws.com/" "swapi-repo:latest"
                ),
                "command": [
                    "--instrument", "swapi",
                    "--data-level", "l1",
                    "--descriptor", "sci",
                    "--start-date", "20230724",
                    "--version", "v001",
                    "--dependency", \"""[
                        {
                            'instrument': 'swapi',
                            'level': 'l0',
                            'start_date': 20230724,
                            'version': 'v001'
                        }
                    ]\""",
                    "--upload-to-sdc",
                ],
                "logStreamName": (
                    "fargate-batch-job-definitionswe/default/"
                    "8a2b784c7bd342f69ea5dac3adaed26f"
                ),
            },
        }
    }

    Returns
    -------
    dict
        HTTP response

    """
    # Get job status
    job_status = (
        models.Status.SUCCEEDED
        if event["detail"]["status"] == "SUCCEEDED"
        else models.Status.FAILED
    )

    # We injected our table ID into the job name
    job_id = event["detail"]["jobName"].split("-")[-1]

    # Convert startedAt and stoppedAt to datetime with timezone
    # These fields may not always be present, so use .get() and handle None
    # Default to startedAt and fallback to createdAt if the job never started
    started_at_timestamp = event["detail"].get(
        "startedAt", event["detail"].get("createdAt")
    )
    stopped_at_timestamp = event["detail"].get("stoppedAt")
    started_at = (
        datetime.fromtimestamp(started_at_timestamp / 1000, tz=timezone.utc)
        if started_at_timestamp is not None
        else None
    )
    stopped_at = (
        datetime.fromtimestamp(stopped_at_timestamp / 1000, tz=timezone.utc)
        if stopped_at_timestamp is not None
        else None
    )

    with db.Session() as session:
        # Get the batch job by its ID
        job = session.get(models.ProcessingJob, job_id)
        # Make the updates
        job.status = job_status
        job.job_definition = event["detail"]["jobDefinition"]
        job.job_log_stream_id = event["detail"]["container"]["logStreamName"]
        job.container_image = event["detail"]["container"]["image"]
        job.started_at = started_at
        job.stopped_at = stopped_at
        session.commit()

    return http_response(status_code=200, body="Success")


def lambda_handler(event, context):
    """Create metadata and add it to the database.

    This function is an event handler for multiple event sources.
    List of event sources are aws.s3, aws.batch and imap.lambda.
    imap.lambda is custom PutEvent from AWS lambda.

    Parameters
    ----------
    event : dict
        The JSON formatted document with the data required for the
        lambda function to process
    context : LambdaContext
        This object provides methods and properties that provide
        information about the invocation, function,
        and runtime environment.

    """
    logger.info("Received event: " + json.dumps(event, indent=2))
    source = event.get("source")

    if source == "aws.s3":
        return s3_event_handler(event)
    elif source == "aws.batch":
        return batch_event_handler(event)
    else:
        logger.error("Unknown event source")
        return http_response(status_code=400, body="Unknown event source")
