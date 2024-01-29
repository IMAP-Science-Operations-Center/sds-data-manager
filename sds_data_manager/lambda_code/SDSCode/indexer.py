import datetime
import json
import logging
import os
import sys

import boto3
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import database as db
from .database import models
from .database_handler import update_file_catalog_table, update_status_table
from .lambda_custom_events import IMAPLambdaPutEvent
from .path_helper import InvalidScienceFileError, ScienceFilepathManager

# Logger setup
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)

s3 = boto3.client("s3")


def get_file_creation_date(s3_uri):
    """Get s3 file creation date.

    Parameters
    ----------
    s3_uri: str
        S3 URI. Eg. s3://bucket/filepath/filename.ext

    creation_date: datetime.datetime
        Last modified data of s3 file.
    """
    # Create an S3 client
    s3_client = boto3.client("s3")

    # Retrieve the metadata of the object
    bucket_name, key = s3_uri.replace("s3://", "").split("/", 1)
    logger.info(f"bucket_name: {bucket_name}")
    logger.info(f"key: {key}")

    response = s3_client.head_object(Bucket=bucket_name, Key=key)
    file_creation_date = response["LastModified"]

    # time looks like this:
    # 2024-01-25 23:35:26+00:00
    # Formats the datetime object to a string with the format "%Y%m%d".
    # ingestion_data_str = file_creation_date.strftime("%Y%m%d")
    return file_creation_date


def get_dependency(instrument, data_level, descriptor, direction, relationship):
    """Make query to dependency table to get dependency.

    TODO: Move this function to batch starter after February demo or keep it here
    based on discussion during that time. This is just to setup and test
    cababilities to make queries to pre-processing dependency table.

    Parameters
    ----------
    instrument : str
        Primary instrument that we are looking for its dependency.
    data_level : str
        Primary data level.
    descriptor : str
        Primary data descriptor.
    direction: str
        Whether it's UPSTREAM or DOWNSTREAM dependency.
    relationship: str
        Whether it's HARD or SOFT dependency.
        HARD means it's required and SOFT means it's nice to have.
    Returns
    -------
    dependency : list
        List of dictionary containing the dependency information.
    """
    dependency = []
    # Send EventBridge event for downstream dependency
    with Session(db.get_engine()) as session:
        query = select(models.PreProcessingDependency.__table__).where(
            models.PreProcessingDependency.primary_instrument == instrument,
            models.PreProcessingDependency.primary_data_level == data_level,
            models.PreProcessingDependency.primary_descriptor == descriptor,
            models.PreProcessingDependency.direction == direction,
            models.PreProcessingDependency.relationship == relationship,
        )
        results = session.execute(query).all()
        for result in results:
            dependency.append(result)
    return dependency


def http_response(headers=None, status_code=200, body="Success"):
    """Customizes HTTP response for the lambda function.

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
    """Sends custom PutEvent to EventBridge.


    Example of what PutEvent looks like:
    event = {
        "Source": "imap.lambda",
        "DetailType": "Processed File",
        "Detail": {
            "object": {
                  "key": filename
            },
        },
    }

    Parameters
    ----------
    filename : str

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
    detail = {"object": {"key": filename}}

    # create PutEvent dictionary
    event = IMAPLambdaPutEvent(detail_type="Processed File", detail=detail)
    event_data = event.to_event()
    logger.info(f"sending this detail to event - {event_data}")

    # Send event to EventBridge
    response = event_client.put_events(Entries=[event_data])
    logger.info(f"response - {response}")
    return response


def s3_event_handler(event):
    """Handler function for S3 events.

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

    try:
        science_file = ScienceFilepathManager(filename)
    except InvalidScienceFileError as e:
        logger.error(str(e))
        return http_response(status_code=400, body=str(e))

    # setup a dictionary of metadata parameters to unpack in the
    # file catalog table
    metadata_params = science_file.get_file_metadata_params()
    metadata_params["file_path"] = s3_filepath

    if metadata_params["data_level"] != "l0":
        logger.error("Invalid data level. Expected l0")
        return http_response(status_code=400, body="Invalid data level")

    # Add data to the file catalog and status tables
    # create status params
    # Event time looks like this:
    # "time": "2024-01-16T17:35:08Z"
    # Parses the time string from the event to a datetime object.
    ingestion_date = datetime.datetime.strptime(event["time"], "%Y-%m-%dT%H:%M:%SZ")
    # Formats the datetime object to a string with the format "%Y%m%d".
    ingestion_data_str = ingestion_date.strftime("%Y%m%d")
    # Parses the newly formatted string back into a datetime
    # object with the desired structure.
    ingestion_date_object = datetime.datetime.strptime(ingestion_data_str, "%Y%m%d")
    status_params = {
        "file_to_create_path": s3_filepath,
        "status": models.Status.SUCCEEDED,
        "job_definition": None,
        "ingestion_date": ingestion_date_object,
    }
    try:
        logger.info(f"Inserting {filename} into database")
        update_status_table(status_params)
        logger.info("Wrote data to status table")
    except Exception as e:
        logger.error(str(e))
        return http_response(status_code=400, body=str(e))

    # Query to get foreign key id for catalog table
    status_tracking = None
    with Session(db.get_engine()) as session:
        # Query to get foreign key id for catalog table
        query = select(models.StatusTracking.__table__).where(
            models.StatusTracking.file_to_create_path == s3_filepath
        )

        status_tracking = session.execute(query).first()

    if status_tracking is None:
        logger.error("No status tracking record found")
        return http_response(status_code=400, body="No status tracking record found")
    logger.info(f"Found record in status table associated with - {filename}")
    try:
        metadata_params["status_tracking_id"] = status_tracking.id
        update_file_catalog_table(metadata_params)
        logger.info("Wrote data to file catalog table")
    except Exception as e:
        logger.error(str(e))
        return http_response(status_code=400, body=str(e))

    # Send L0 event from this lambda for Batch starter
    # lambda
    send_event_from_indexer(filename)
    return http_response(status_code=200, body="Success")


def batch_event_handler(event):
    """Handler for Batch event

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
            "jobName": "test-batch-tenzin",
            "status": "FAILED",
            "statusReason": "some error message",
            "container": {
                "image": (
                    "123456789012.dkr.ecr.us-west-2.amazonaws.com/" "codice-repo:latest"
                ),
                "command": [
                    (
                        "--instrument hit --level l1a "
                        "--s3_uri 's3://bucket_name/imap/hit/l1a/2024/01/"
                        "imap_hit_l1a_sci_20240101_20240102_v00-01.cdf' "
                        "--dependency [{}]"
                    ),
                ],
            },
        },
    }

    Returns
    -------
    dict
        HTTP response
    """
    # splited command looks like this:
    # [
    #   '',
    #   'instrument codice ',
    #   'level l1a ',
    #   "s3_uri 's3://data-bucket/path/filename'",
    #   "dependency [{'instrument': 'hit', 'level': 'l0', 'version': 'v00-01'}]"
    #  ]
    command = event["detail"]["container"]["command"][0].split("--")

    # Get event inputs ready
    s3_uri = command[3].replace("s3_uri '", "").replace("'", "")
    filename = os.path.basename(s3_uri)

    # TODO: post demo, revisit this and improve it
    if event["detail"]["status"] == "SUCCEEDED":
        # Frist write to status table and then
        # write to file catalog with foreign key
        # information
        try:
            # query and update status table record with new
            # information from batch
            with Session(db.get_engine()) as session:
                # Had to query this way because the select statement
                # returns a RowProxy object when it executes it,
                # not the actual StatusTracking model instance,
                # which is why it can't update table row directly.
                result = (
                    session.query(models.StatusTracking)
                    .filter(models.StatusTracking.file_to_create_path == s3_uri)
                    .first()
                )

                # update three fields with updated information
                result.status = models.Status.SUCCEEDED
                result.job_definition = event["detail"]["jobDefinition"]
                result.ingestion_date = get_file_creation_date(s3_uri)
                session.commit()

                # Then write to file catalog table
                sci_file = ScienceFilepathManager(filename)
                metadata_params = sci_file.get_file_metadata_params()
                metadata_params["file_path"] = s3_uri
                metadata_params["status_tracking_id"] = result.id
                update_file_catalog_table(metadata_params)
                # Send event from this lambda for Batch starter
                # lambda
                response = send_event_from_indexer(filename)
                logger.info(f"Sent event to EventBridge - {response}")

        except Exception as e:
            logger.error(str(e))
            return http_response(status_code=400, body=str(e))

    elif event["detail"]["status"] == "FAILED":
        try:
            # Update only status table with Failed status
            with Session(db.get_engine()) as session:
                # Had to query this way because the select statement
                # returns a RowProxy object when it executes it,
                # not the actual StatusTracking model instance,
                # which is why it can't update table row directly.
                result = (
                    session.query(models.StatusTracking)
                    .filter(models.StatusTracking.file_to_create_path == s3_uri)
                    .first()
                )

                # update two fields with updated information
                result.status = models.Status.FAILED
                result.job_definition = event["detail"]["jobDefinition"]
                session.commit()
        except Exception as e:
            logger.error(str(e))
            return http_response(status_code=400, body=str(e))

    else:
        # Technically, we shouldn't get other job status since event
        # bridge filters out only succeeded or failed status.
        logger.error("Unknown batch job status")
        return http_response(status_code=400, body="Unknown batch job status")

    return http_response(status_code=200, body="Success")


def custom_event_handler(event):
    """_summary_

    Parameters
    ----------
    event : dict
        The JSON formatted document with the data required for the
        lambda function to process

    PutEvent Example:
        {
        "DetailType": "Batch Job Started",
        "Source": "imap.lambda",
        "Detail": {
          "file_to_create": "str",
          "status": "INPRGRESS",
          "dependency": json.dumps({
              "codice": "s3-filepath",
              "mag": "s3-filepath"}
          )
        }}

    Returns
    -------
    dict
        HTTP response
    """
    file_to_create = event["detail"]["file_to_create"]
    filename = os.path.basename(file_to_create)
    logger.info(f"Attempting to insert {filename} into database")

    try:
        _ = ScienceFilepathManager(filename)
    except InvalidScienceFileError as e:
        logger.error(str(e))
        return http_response(status_code=400, body=str(e))

    # Write event information to status tracking table.
    logger.info(f"Inserting {filename} into database")
    status_params = {
        "file_to_create_path": file_to_create,
        "status": models.Status.INPROGRESS,
        "job_definition": None,
        "ingestion_date": None,
    }
    try:
        update_status_table(status_params)
    except Exception as e:
        return http_response(status_code=400, body=str(e))

    return http_response(status_code=200, body="Success")


def lambda_handler(event, context):
    """Handler function for creating metadata, adding it to the database.

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

    # L0 data or other data type such as SPICE data ingestion
    if event["source"] == "aws.s3":
        return s3_event_handler(event)
    # Data processing status
    elif event["source"] == "aws.batch":
        return batch_event_handler(event)
    # Data processing initiation
    elif event["source"] == "imap.lambda":
        return custom_event_handler(event)
    else:
        logger.error("Unknown event source")
        return http_response(status_code=400, body="Unknown event source")
