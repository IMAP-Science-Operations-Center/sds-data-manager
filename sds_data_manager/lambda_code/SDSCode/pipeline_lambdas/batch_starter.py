"""Functions for supporting the batch starter component of the architecture."""

# ruff: noqa: S310
# potentially unsafe usage of urlopen TODO: are we concerned here?
import contextlib
import json
import logging
import os
import urllib
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

import boto3
import imap_data_access
from imap_data_access import (
    ScienceFilePath,
    SPICEFilePath,
)
from imap_data_access.processing_input import ProcessingInputCollection
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from ..database import database as db
from ..database import models

# import dependency

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Create a batch client
BATCH_CLIENT = boto3.client("batch", region_name="us-west-2")


class IMAPDependencyFinderError(Exception):
    """Base class for exceptions in this module."""

    pass


@contextlib.contextmanager
def _get_url_response(url: str):
    """Get the response from a URL.

    This is a helper function to make it easier to handle
    the different types of errors that can occur when
    opening a URL and write out the response body.

    Parameters
    ----------
    url: str
        The url string to query the api with.

    Yields
    ------
    http.client.HTTPResponse
        The response object received from the API.
    """
    try:
        # Open the URL and yield the response
        with urllib.request.urlopen(url) as response:
            yield response
    except HTTPError as e:
        message = (
            f"HTTP Error: {e.code} - {e.reason}\n"
            f"Server Message: {e.read().decode('utf-8')}"
        )
        raise IMAPDependencyFinderError(message) from e

    except URLError as e:
        message = f"URL Error: {e.reason}"
        raise IMAPDependencyFinderError(message) from e


def _get_dependencies(dependency_events: dict):
    """Return dependencies for the input dependency requirements.

    Parameters
    ----------
    dependency_events : dict
        Dependency information to be used as query parameters in the API request url.

    Returns
    -------
    Union[list, ProcessingInputCollection, None]
        - A list of dependency dictionaries if "start_date" is not in the
            request url .
        - ProcessingInputCollection if 'start_date' is in the request url.
        - None If the API returns a 206 status code indicating missing dependencies.

    """
    base = f"{os.getenv('IMAP_DATA_ACCESS_URL')}/dependency?"
    url = f"{base}{urlencode(dependency_events)}"

    logger.info("Finding dependencies for %s with url %s", dependency_events, url)
    with _get_url_response(url) as response:
        # Retrieve the response as a list of dictionaries containing the dependency
        # information
        dependency_response = response.read().decode("utf-8")
        # Check for a 206 status code
        if response.status == 206:
            logger.info(f"Dependency API response: {dependency_response}")
            return None

        logger.debug(f"Received dependencies: {dependency_response}")
    # The API returns different output formats depending on the query parameters:
    # Without "start_date": Returns a list of dependency dictionaries.
    #      This functionality is used when searching for downstream dependencies
    # With "start_date" (requires "version" and "trigger_type"; "end_date" optional):
    # Returns a serialized ProcessingInputCollection of files from S3
    if "start_date" in url:
        dependencies = ProcessingInputCollection()
        dependencies.deserialize(dependency_response)
    else:
        dependencies = json.loads(dependency_response)

    return dependencies


def determine_job_version(
    session: db.Session,
    instrument: str,
    data_level: str,
    descriptor: str,
    start_date: datetime,
) -> str:
    """Return the maximum existing file version in the pipeline increased by one.

    Parameters
    ----------
    session : orm session
        Database session.
    instrument : str
        Instrument.
    data_level : str
        Data level.
    descriptor : str
        Data descriptor.
    start_date : datetime
        Start date.

    Returns
    -------
     str
        The highest version number.
    """

    def filter_conditions(table):
        # Filter conditions for the query
        conditions = [
            table.instrument == instrument,
            table.data_level == data_level,
            table.start_date == start_date,
        ]
        if table == models.ProcessingJob:
            conditions.append(
                table.status.in_(
                    [models.Status.INPROGRESS.value, models.Status.SUCCEEDED.value]
                )
            )
        if descriptor != "all":
            # If the descriptor is all we want to check for all descriptors
            conditions.append(table.descriptor == descriptor)

        return conditions

    # First check to see if there are any jobs in progress and get the max version
    max_version = (
        session.query(func.max(models.ProcessingJob.version)).filter(
            *filter_conditions(models.ProcessingJob)
        )
    ).scalar()
    # If no jobs are in progress, check the science files table for the max version.
    if not max_version:
        max_version = (
            session.query(func.max(models.ScienceFiles.version)).filter(
                *filter_conditions(models.ScienceFiles)
            )
        ).scalar()
    # Bump the version number. "V001" will be returned if max_version is None.
    return f"v{int(max_version[1:]) + 1:03d}" if max_version else "v001"


def try_to_submit_job(
    session: db.Session,
    job_info: dict,
    start_date: datetime,
    version: str,
    upstream_dependencies: ProcessingInputCollection,
):
    """Try to submit a batch job with the given job information.

    Parameters
    ----------
    session : orm session
        Database session.
    job_info : dict
        Dictionary containing components with dates and versions appended.
    start_date : datetime
        Start date of the data.
    version : str
        Version of the job.
    upstream_dependencies : ProcessingInputCollection
        Input collection of upstream dependencies.
    """
    instrument = job_info["data_source"]
    data_level = job_info["data_type"]
    descriptor = job_info["descriptor"]
    start_date_str = datetime.strftime(start_date, "%Y%m%d")

    # All of our upstream requirements have been met.
    # Try to insert a record into the Processing Jobs table
    # If this job already exists, then we will get an integrity error
    # and know that some other process has already taken care of it
    processing_job = models.ProcessingJob(
        status=models.Status.INPROGRESS,
        instrument=instrument,
        data_level=data_level,
        descriptor=descriptor,
        start_date=start_date,
        version=version,
    )
    try:
        session.add(processing_job)
        session.commit()
    except IntegrityError:
        logger.info(f"Job already completed or in progress: {processing_job}")
        return

    logger.info(
        f"Wrote job INPROGRESS to Processing Jobs Table with id: {processing_job.id}"
    )

    # Reformat the upstream dependencies from dependency call to match
    # what batch job expects.

    batch_command = [
        "--instrument",
        instrument,
        "--data-level",
        data_level,
        "--descriptor",
        descriptor,
        "--start-date",
        start_date_str,
        "--version",
        version,
        "--dependency",
        f"{upstream_dependencies.serialize()}",
        "--upload-to-sdc",
    ]

    # NOTE: The batch job name should contain only alphanumeric characters and hyphens
    # E.g. "codice-l1a-sci-job-1"
    # The `processing_job.id` is used later for updating the job processing table
    job_name = f"{instrument}-{data_level}-{descriptor}-job-{processing_job.id}"
    # Get the necessary AWS information
    # NOTE: These are here for easier mocking in tests rather than at the module level
    step = "-l3" if data_level >= "l3" else ""
    job_definition = f"ProcessingJob-{instrument}{step}"
    job_queue = "ProcessingJobQueue"
    BATCH_CLIENT.submit_job(
        jobName=job_name,
        jobQueue=job_queue,
        jobDefinition=job_definition,
        containerOverrides={
            "command": batch_command,
        },
    )
    logger.info(f"Submitted job {job_name} with this command: {batch_command}")


def s3_processing_event(session, events):
    """Process SQS events that were triggered by S3 file arrivals.

    Parameters
    ----------
    session : orm session
        Database session.
    events : dict
        SQS event input.
    """
    # Since the SQS events can be batched together, we need to loop through
    # each event. In this loop, "event" represents one file landing.
    for event in events["Records"]:
        # Event details:
        logger.info(f"Individual event: {event}")
        body = json.loads(event["body"])

        filename = body["detail"]["object"]["key"]
        logger.info(f"Retrieved filename: {filename}")

        file_obj = imap_data_access.file_validation.generate_imap_file_path(filename)

        if isinstance(file_obj, SPICEFilePath):
            raise ValueError(
                f"Batch starter handling for spice file: {filename} is not "
                f"implemented yet"
            )

        # TODO: How to handle repointing
        start_date = file_obj.start_date
        end_date = file_obj.end_date if hasattr(file_obj, "end_date") else None

        if not end_date:
            if isinstance(file_obj, ScienceFilePath):
                # Set end_date to start_date for science files
                end_date = file_obj.start_date
            else:
                # Set end_date to today's date for ancillary or SPICE files
                end_date = datetime.today().strftime("%Y%m%d")

        # TODO: handle spice once implemented
        data_type = (
            file_obj.data_level if hasattr(file_obj, "data_level") else "ancillary"
        )

        dependency_event_msg = {
            "data_source": file_obj.instrument,
            "descriptor": file_obj.descriptor,
            "data_type": data_type,
            "dependency_type": "DOWNSTREAM",
            "relationship": "HARD",
        }
        # Potential jobs are the instruments that depend on the current file,
        # which are the downstream dependencies.
        potential_jobs = _get_dependencies(dependency_event_msg)

        # SOFT_TRIGGER dependencies will try to set off processing
        dependency_event_msg["relationship"] = "SOFT_TRIGGER"
        potential_soft_jobs = _get_dependencies(dependency_event_msg)

        for job in potential_jobs + potential_soft_jobs:
            # Submit downstream jobs for each upstream primary science dependency file.
            event_msg = {
                "data_source": job["data_source"],
                "data_type": job["data_type"],
                "descriptor": job["descriptor"],
                "dependency_type": "UPSTREAM",
                "relationship": "ALL",
                "start_date": start_date,
                "end_date": end_date,
            }

            # Find the files that this job depends on
            upstream_dependencies = _get_dependencies(event_msg)
            if not upstream_dependencies:
                return

            logger.info(f"All required dependencies found for the job: {job}")
            # Find the first science processingInput that has the same source as the
            # potential job. Use this to determine the start date.
            primary_science = upstream_dependencies.get_science_inputs(
                job["data_source"]
            )[0]
            for filepath in primary_science.imap_file_paths:
                job_start_date = datetime.strptime(filepath.start_date, "%Y%m%d")
                job_version = determine_job_version(
                    session=session,
                    instrument=job["data_source"],
                    descriptor=job["descriptor"],
                    start_date=job_start_date,
                    data_level=job["data_type"],
                )
                # Filter dependencies to get only files needed for this job
                try_to_submit_job(
                    session,
                    job,
                    job_start_date,
                    job_version,
                    upstream_dependencies.get_valid_inputs_for_start_date(
                        job_start_date
                    ),
                )


def lambda_handler(events: dict, context):
    """Lambda handler.

    This lambda is triggered by different events.
    1. Event of a new science or ancillary file arrival from indexer lambda.
        Example event:
            {
                "Records": [
                    {
                        "body": '{"detail": '
                        '{"object": {"key": '
                        '"imap_swe_l1b-in-flight-cal_20240101_v001.cdf"}}'
                        "}"
                    }
                ]
            }
    2. Event of a new science reprocessing.
        Example event: see example above.
    3. Event of a new spice file arrival from spice indexer lambda.
        TODO: This will be implemented in the future.
    4. Event of bulk reprocessing of science.
        TODO: This will be implemented in the future.
        Example event:
            {
                "reprocessing": True,
                "start_date": <>,
                "end_date": <>,
                "instrument": None, optional,
                "data_level": None, optional,
                "data_descriptor": None, optional,
            }
    5. Event of a cron job cadence trigger.
        TODO: This will be implemented in the future.
        Example event:
            {
                "cadence": 3months or 7days,
                "instrument": <>,
                "data_level": <>,
                "descriptor": <>
            }

    Parameters
    ----------
    events : dict
        Event input
    context : LambdaContext
        Lambda context object
    """
    logger.info(f"Events: {events}")
    logger.info(f"Context: {context}")

    with db.Session() as session:
        # handle s3 event from the SQS queue
        s3_processing_event(session, events)
