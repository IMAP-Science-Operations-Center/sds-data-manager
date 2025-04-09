"""Functions for supporting the batch starter component of the architecture."""

# ruff: noqa: S310
# potentially unsafe usage of urlopen TODO: are we concerned here?
import contextlib
import json
import logging
import os
import urllib
from datetime import datetime
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

import boto3
import imap_data_access
from imap_data_access import (
    SPICEFilePath,
    processing_input,
)
from imap_data_access.processing_input import ProcessingInputCollection
from sqlalchemy import select
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


def is_job_in_processing_table(
    session: db.Session,
    instrument: str,
    data_level: str,
    descriptor: str,
    start_date: datetime,
    version: str,
):
    """Check if the job is already running.

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
    version : str
        Data version.

    Returns
    -------
    bool
        True if a duplicate job is found, False otherwise.
    """
    # check in the processing table if the job is already in progress
    # for this instrument, data level, version, and descriptor
    query = select(models.ProcessingJob.__table__).where(
        models.ProcessingJob.instrument == instrument,
        models.ProcessingJob.descriptor == descriptor,
        models.ProcessingJob.data_level == data_level,
        models.ProcessingJob.start_date == start_date,
        models.ProcessingJob.version == version,
        models.ProcessingJob.status.in_(
            [models.Status.INPROGRESS.value, models.Status.SUCCEEDED.value]
        ),
    )

    results = session.execute(query).all()

    if results:
        return True
    return False


def try_to_submit_job(
    session: db.Session,
    job_info: dict,
    start_date: datetime,
    version: str,
    upstream_dependencies: ProcessingInputCollection,
):
    """Try to submit a batch job with the given job information.

    Go through the job information to retrieve all necessary input files
    (upstream dependencies). If any are missing, return. If we have
    all the necessary input files, submit the job to the batch queue.

    Parameters
    ----------
    session : orm session
        Database session.
    job_info : dict
        Dictionary containing components with dates and versions appended.
    start_date : datetime
        Start date of the data.
    version : str
        Version of the data.
    upstream_dependencies : ProcessingInputCollection
        Input collection of upstream dependencies.

    Returns
    -------
    bool
        Whether or not this job is ready to be processed.
    """
    instrument = job_info["data_source"]
    data_level = job_info["data_type"]
    descriptor = job_info["descriptor"]

    start_date_str = datetime.strftime(start_date, "%Y%m%d")

    logger.info("Checking for job in progress.")

    if is_job_in_processing_table(
        session=session,
        instrument=instrument,
        descriptor=descriptor,
        start_date=start_date,
        version=version,
        data_level=data_level,
    ):
        logger.info(
            f"Job already in progress for {instrument}, {data_level}, "
            f"{descriptor}, {start_date_str}, {version}"
        )
        return

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
        f"Wrote job INPROGRESS to Processing Jobs Table with id: "
        f"{processing_job.id}"
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
    # Eg. "codice-l1a-sci-job-1"
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


def submit_all_jobs(
    session: db.Session,
    job: dict,
    start_date: datetime,
    version: str,
    trigger_type: str,
    end_date: Optional[datetime] = None,
):
    """Submit downstream jobs for each upstream primary science dependency file.

    Parameters
    ----------
    session: orm session
        Database session.
    job : dict
        Job information containing data_source, data_type, and descriptor.
    start_date : datetime
        The trigger file start date.
    version : str
        The trigger file version.
    trigger_type : str
        The data_source of the file that triggered the batch starter.
    end_date : datetime, optional
        The trigger file end date, by default None.

    Returns
    -------
    None
    """
    # Find the files that this job depends on
    dependency_event_msg = {
        "data_source": job["data_source"],
        "data_type": job["data_type"],
        "descriptor": job["descriptor"],
        "dependency_type": "UPSTREAM",
        "relationship": "HARD",
        "start_date": start_date,
        "version": version,
        "trigger_type": trigger_type,
    }
    if end_date:
        dependency_event_msg["end_date"] = end_date

    upstream_dependencies = _get_dependencies(dependency_event_msg)
    if not upstream_dependencies.processing_input:
        logger.info(
            f"Upstream dependency not found, or downstream dependency "
            f"already exists for: {dependency_event_msg}"
        )
        return

    logger.info(f"All dependencies found for the job: {job}")
    # Find science processingInputs that have the same source as the potential job
    for dep in upstream_dependencies.get_science_inputs():
        if job["data_source"] == dep.source and isinstance(
            dep, processing_input.ScienceInput
        ):
            # Try to start a downstream science job with the start_date from the
            # upstream science dependency
            # E.g.:
            # if "job" == {"data_source":"swe","data_type":"l1b","descriptor":"sci"}
            # And there is a processingInput in the upstream_dependencies that is
            # {"type": "science",
            #     "files": [
            #         "imap_swe_l1a_sci_20240312_v001.cdf",
            #         "imap_swe_l1a_sci_20240313_v001.cdf" ]}
            # That means we need to kick off two swe l1b jobs for dates: "20240312" and
            # "20240313"
            for upstream_file in dep.imap_file_paths:
                # TODO add function to processingInput to filter for start_date.
                dep.imap_file_paths = [upstream_file]
                dep.filename_list = [str(upstream_file.filename)]
                job_start_date = datetime.strptime(upstream_file.start_date, "%Y%m%d")
                job_version = upstream_file.version
                # TODO when do we bump version?
                try_to_submit_job(
                    session, job, job_start_date, job_version, upstream_dependencies
                )


def lambda_handler(events: dict, context):
    """Lambda handler.

    This lambda is triggered by different events.
    1. Event of a new science or ancillary file arrival from indexer lambda.
        Example event:
            {
                "DetailType": "Processed File",
                "Source": "imap.lambda",
                "Detail": {
                    "object": {
                        "key": str,
                        "instrument": str,
                    }
                }
            }
    2. Event of a new spice file arrival from spice indexer lambda.
        TODO: This will be implemented in the future.
    3. Event of a new science reprocessing.
        TODO: This will be implemented in the future.
    4. Event of bulk processing of science in normal processing.
        TODO: This will be implemented in the future.

    Parameters
    ----------
    events : dict
        Event input
    context : LambdaContext
        Lambda context object
    """
    logger.info(f"Events: {events}")
    logger.info(f"Context: {context}")

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
        version = file_obj.version

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

        if not potential_jobs:
            logger.info(f"Found no dependencies for {dependency_event_msg}.")
            continue

        with db.Session() as session:
            for job in potential_jobs:
                submit_all_jobs(session, job, start_date, version, data_type, end_date)
