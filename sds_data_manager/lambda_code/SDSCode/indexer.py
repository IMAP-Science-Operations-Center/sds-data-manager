"""Functions for supporting the indexer component of the architecture."""

import json
import logging
import os
from datetime import datetime

import boto3
from imap_data_access import ScienceFilePath

from .database import database as db
from .database import models
from .database_handler import update_file_catalog_table
from .lambda_custom_events import IMAPLambdaPutEvent

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def get_file_creation_date(file_path):
    """Get s3 file creation date.

    Parameters
    ----------
    file_path: str
        S3 object path. Eg. filepath/filename.ext

    creation_date: datetime.datetime
        Last modified data of s3 file.

    """
    # Create an S3 client
    s3_client = boto3.client("s3")

    # Retrieve the metadata of the object
    bucket_name = os.getenv("S3_BUCKET")
    logger.info(f"looking up ingestion date for {file_path}")

    response = s3_client.head_object(Bucket=bucket_name, Key=file_path)
    file_creation_date = response["LastModified"]

    # LastModified looks like this:
    # 2024-01-25 23:35:26+00:00
    return file_creation_date


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


def send_event_from_indexer(filename):
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
    filename : str
        The filename to use in the PutEvent

    Returns
    -------
    dict
        EventBridge response

    """
    logger.info("in send event function")
    event_client = boto3.client("events")

    # Create event["detail"] information
    # TODO: This is what batch starter expect
    # as input. Revisit this.

    science_filepath = ScienceFilePath(filename)

    detail = {"object": {"key": filename, "instrument": science_filepath.instrument}}

    # create PutEvent dictionary
    event = IMAPLambdaPutEvent(detail_type="Processed File", detail=detail)
    event_data = event.to_event()
    logger.info(f"sending this detail to event - {event_data}")

    # Send event to EventBridge
    response = event_client.put_events(Entries=[event_data])
    logger.info(f"response - {response}")
    return response


def s3_event_handler(event):
    """S3 events handler.

    S3 event handler takes s3 event and then writes information to
    file catalog table. It also sends event to the batch starter
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
    # TODO: add checks for SPICE or other
    # data types

    # setup a dictionary of metadata parameters to unpack in the
    # file catalog table. Eg.
    # {
    #     "file_path": None,
    #     "instrument": self.instrument,
    #     "data_level": self.data_level,
    #     "descriptor": self.descriptor,
    #     "start_date": datetime.strptime(self.startdate, "%Y%m%d"),
    #     "repointing": self.repointing,
    #     "version": self.version,
    #     "extension": self.extension,
    #     "ingestion_date": date_object,
    # }
    file_params = ScienceFilePath.extract_filename_components(filename)
    # delete mission key from metadata params
    file_params.pop("mission")
    file_params["data_level"] = file_params.pop("data_level")
    file_params["start_date"] = datetime.strptime(
        file_params.pop("start_date"), "%Y%m%d"
    )

    file_params["file_path"] = s3_filepath

    ingestion_date_object = get_file_creation_date(s3_filepath)

    file_params["ingestion_date"] = ingestion_date_object
    with db.Session() as session:
        update_file_catalog_table(session, file_params)
    logger.info("Wrote data to file catalog table")

    # Send event from this lambda for Batch starter
    # lambda
    send_event_from_indexer(filename)
    logger.debug("S3 event handler complete")


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

    with db.Session() as session:
        # Get the batch job by its ID
        job = session.get(models.ProcessingJob, job_id)
        # Make the updates
        job.status = job_status
        job.job_definition = event["detail"]["jobDefinition"]
        job.job_log_stream_id = event["detail"]["container"]["logStreamName"]
        job.container_image = event["detail"]["container"]["image"]
        job.container_command = " ".join(event["detail"]["container"]["command"])
        session.commit()

    return http_response(status_code=200, body="Success")


# Handlers mapping
event_handlers = {
    "aws.s3": s3_event_handler,
    "aws.batch": batch_event_handler,
}


def handle_event(event, handler):
    """Event handling logic."""
    try:
        handler(event)
        return http_response(status_code=200, body="Success")
    except ScienceFilePath.InvalidScienceFileError as e:
        logger.error(str(e))
        return http_response(status_code=400, body=str(e))


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

    handler = event_handlers.get(source)
    if handler:
        return handle_event(event, handler)
    else:
        logger.error("Unknown event source")
        return http_response(status_code=400, body="Unknown event source")
