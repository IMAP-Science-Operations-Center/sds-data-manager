"""Functions for triggering a processing job on a schedule."""

import datetime as dt
import logging
from typing import Optional

from imap_data_access import DependencyFilePath, ProcessingInputCollection, RepointInput
from sqlalchemy.exc import IntegrityError

from ..database import database as db
from ..database import models
from . import (
    batch_starter,
    dependency,
)
from .batch_starter import dependency_hash
from .scheduled_job_config_reader import read_scheduled_job_config

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def scheduled_processing_event(session, events):
    """Process events triggerd by EventBridge rules.

    Parameters
    ----------
    session : orm session
        Database session.
    events : dict
        Event input from an Event Bridge rule.
    """
    if events["scheduled"] not in read_scheduled_job_config():
        logger.error(
            "There are no jobs found with this schedule: %s", events["scheduled"]
        )

    triggered_jobs = read_scheduled_job_config()[events["scheduled"]]

    processing_inputs = []
    try:
        min_python_date = dt.datetime(1, 1, 1)
        latest_repoint_file_name = dependency.get_latest_repoint_file(min_python_date)
        processing_inputs.append(RepointInput(latest_repoint_file_name))
    except ValueError:
        logger.warning("No repointing files found, proceeding without one.")
        pass

    processing_input_collection = ProcessingInputCollection(*processing_inputs)

    for job in triggered_jobs:
        try_to_submit_job(
            session,
            job,
            dt.datetime.now(),
            "v001",
            processing_input_collection.serialize(),
        )


def try_to_submit_job(
    session: db.Session,
    job_info: dict,
    start_date: dt.datetime,
    version: str,
    serialized_dependencies: str,
    repoint: Optional[int] = None,
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
    serialized_dependencies : str
        The serialized ProcessingInputCollection of the upstream
        dependencies.
    repoint : int, optional
        The repointing number for the job, if applicable. Default is None. Should
        be just an integer, no "repoint" prefix.
    """
    instrument = job_info["data_source"]
    data_level = job_info["data_type"]
    descriptor = job_info["descriptor"]

    formatted_start_date = start_date.strftime("%Y%m%d")

    # Serialize the upstream dependencies and write them to a JSON file. The Imap
    # processing code will read the JSON file and deserialize the dependencies. This is
    # to avoid passing a large string through the batch job command line.
    # release
    # The descriptor should include a hash of the serialized dependencies.
    # This makes it unique for this file and set of dependencies.
    dep_descriptor = f"{descriptor}-{dependency_hash(serialized_dependencies)}"
    dependency_file = DependencyFilePath.generate_from_inputs(
        instrument=instrument,
        data_level=data_level,
        descriptor=dep_descriptor,
        start_time=formatted_start_date,
        version=version,
        extension="json",
        repointing=repoint,  # since we can have different repointings on the same day
    )
    dependency_file_path = dependency_file.construct_path()
    response = batch_starter.upload_dependency_file(
        dependency_file_path, serialized_dependencies
    )
    # If response is None, then the upload failed and we should skip submitting the job.
    if not response:
        return

    batch_command = [
        "--instrument",
        instrument,
        "--data-level",
        data_level,
        "--descriptor",
        descriptor,
        "--start-date",
        formatted_start_date,
        "--version",
        version,
        "--dependency",
        dependency_file_path.name,
        "--upload-to-sdc",
    ]

    if repoint is not None:
        batch_command.extend(["--repointing", f"repoint{repoint:05d}"])

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
        repointing=repoint,
        container_command=" ".join(batch_command),
    )
    try:
        session.add(processing_job)
        session.commit()
    except IntegrityError:
        # Rollback the session to clear the failed transaction
        session.rollback()
        logger.info(f"Job already completed or in progress: {processing_job}")
        return

    logger.info(
        f"Wrote job INPROGRESS to Processing Jobs Table with id: {processing_job.id}"
    )
    # NOTE: The batch job name should contain only alphanumeric characters and hyphens
    # E.g. "codice-l1a-sci-job-1"
    # The `processing_job.id` is used later for updating the job processing table
    job_name = f"{instrument}-{data_level}-{descriptor}-job-{processing_job.id}"
    # Get the necessary AWS information
    # NOTE: These are here for easier mocking in tests rather than at the module level
    step = "-l3" if data_level >= "l3" else ""
    job_definition = f"ProcessingJob-{instrument}{step}"
    job_queue = "ProcessingJobQueue"
    batch_starter.BATCH_CLIENT.submit_job(
        jobName=job_name,
        jobQueue=job_queue,
        jobDefinition=job_definition,
        containerOverrides={
            "command": batch_command,
        },
        retryStrategy=batch_starter.BATCH_JOB_RETRY_STRATEGY,
    )
    logger.info(f"Submitted job {job_name} with this command: {batch_command}")


def lambda_handler(events, context):
    """Lambda handler.

    This lambda is triggered on a cron schedule.
    The event should contain a 'scheduled' field
    which contains the job instrument, data_level,
    and descriptor.

    Parameters
    ----------
    events : dict
        Event input
    context : LambdaContext
        Lambda context object
    """
    logger.info(f"Events: {events}")

    with db.Session() as session:
        if events.get("scheduled"):
            scheduled_processing_event(session, events)
