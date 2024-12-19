"""Functions for supporting the batch starter component of the architecture."""

import json
import logging
from datetime import datetime

import boto3
import imap_data_access
from imap_data_access import ScienceFilePath
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..database import database as db
from ..database import models

# import dependency
from ..pipeline_lambdas import dependency

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Create a batch client
BATCH_CLIENT = boto3.client("batch", region_name="us-west-2")


def get_file(session, instrument, data_level, descriptor, start_date, version):
    """Query to database to get the first ScienceFiles record.

    Parameters
    ----------
    session : orm session
        Database session.
    instrument : str
        Instrument name.
    data_level : str
        Data level.
    descriptor : str
        Data descriptor.
    start_date : str
        Start date of the event data.
    version : str
        Version of the event data.

    Returns
    -------
    record : models.ScienceFiles or None
        The first ScienceFiles record matching the query criteria.
        None is returned if no record matches the criteria.
    """
    # TODO: narrow down the query using end_date.
    # This will give ability to query range of time.
    # Eg. when we are query 3 months of data to create
    # 3 months map, end_date will help narrow search.
    # When we have the end_date, we can query
    # using table.start_date >= start_date and
    # table.end_date <= end_date.
    record = (
        session.query(models.ScienceFiles)
        .filter(
            models.ScienceFiles.instrument == instrument,
            models.ScienceFiles.data_level == data_level,
            models.ScienceFiles.descriptor == descriptor,
            models.ScienceFiles.start_date == datetime.strptime(start_date, "%Y%m%d"),
            models.ScienceFiles.version == version,
        )
        .first()
    )

    return record


def is_job_in_processing_table(
    session: db.Session,
    instrument: str,
    data_level: str,
    descriptor: str,
    start_date: str,
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
    start_date : str
        Start date.
    version : str
        Data version.

    Returns
    -------
    bool
        True if duplicate job is found, False otherwise.
    """
    # check in the processing table if the job is already in progress
    # for this instrument, data level, version, and descriptor
    query = select(models.ProcessingJob.__table__).where(
        models.ProcessingJob.instrument == instrument,
        models.ProcessingJob.data_level == data_level,
        models.ProcessingJob.descriptor == descriptor,
        models.ProcessingJob.start_date == datetime.strptime(start_date, "%Y%m%d"),
        models.ProcessingJob.version == version,
        models.ProcessingJob.status.in_(
            [models.Status.INPROGRESS.value, models.Status.SUCCEEDED.value]
        ),
    )

    results = session.execute(query).all()
    if results:
        return True
    return False


def try_to_submit_job(session, job_info, start_date, version):
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
    start_date : str
        Start date of the data.
    version : str
        Version of the data.

    Returns
    -------
    bool
        Whether or not this job is ready to be processed.
    """
    instrument = job_info["data_source"]
    data_level = job_info["data_type"]
    descriptor = job_info["descriptor"]

    logger.info("Checking for job in progress before looking for dependencies.")

    if is_job_in_processing_table(
        session=session,
        instrument=instrument,
        data_level=data_level,
        descriptor=descriptor,
        start_date=start_date,
        version=version,
    ):
        logger.info(
            f"Job already in progress for {instrument}, {data_level}, "
            f"{descriptor}, {start_date}, {version}"
        )
        return

    # Find the files that this job depends on
    dependency_event_msg = {
        "data_source": instrument,
        "data_type": data_level,
        "descriptor": descriptor,
        "dependency_type": "UPSTREAM",
        "relationship": "HARD",
    }

    # TODO: call dependency lambda when it's implemented
    dependency_response = dependency.lambda_handler(dependency_event_msg, None)

    upstream_dependencies = json.loads(dependency_response["body"])

    if dependency_response["statusCode"] != 200:
        logger.error(f"Dependency query failed with {upstream_dependencies}")
        return {"statusCode": 500, "body": "Dependency query failed"}

    for upstream_dependency in upstream_dependencies:
        upstream_source = upstream_dependency["data_source"]
        upstream_data_type = upstream_dependency["data_type"]
        upstream_descriptor = upstream_dependency["descriptor"]

        upstream_start_date = start_date
        upstream_version = version
        upstream_dependency.update(
            {"start_date": upstream_start_date, "version": upstream_version}
        )
        record = get_file(
            session,
            upstream_source,
            upstream_data_type,
            upstream_descriptor,
            upstream_start_date,
            upstream_version,
        )
        if record is None:
            logger.info(
                f"Dependency not found: {upstream_source}, "
                f"{upstream_data_type}, "
                f"{upstream_descriptor}, "
                f"{upstream_start_date}, "
                f"{upstream_version}"
            )
            return  # Exit the loop early as we already found a missing dependency
    logger.info(f"All dependencies found for the job: {job_info}")

    # All of our upstream requirements have been met.
    # Try to insert a record into the Processing Jobs table
    # If this job already exists, then we will get an integrity error
    # and know that some other process has already taken care of it
    processing_job = models.ProcessingJob(
        status=models.Status.INPROGRESS,
        instrument=instrument,
        data_level=data_level,
        descriptor=descriptor,
        start_date=datetime.strptime(start_date, "%Y%m%d"),
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

    # TODO: change the upstream dependency keys as needed in the future based
    # on needs. Right now, we are keeping same as before to reduce complexity.
    # FYI, upstream_dependencies in the command below should contain these keys:
    #   'instrument',
    #   'data_level',
    #   'descriptor',
    #   'start_date',
    #   'version'
    # Example list of upstream_dependencies in the command below:
    # [
    #   {
    #     'instrument': 'swe',
    #     'data_level': 'l1b',
    #     'descriptor': 'sci',
    #     'start_date': '20231212',
    #     'version': 'v001',
    #   },
    #   {
    #     'instrument': 'sc_attitude',
    #     'data_level': 'spice',
    #     'descriptor': 'historical',
    #     'start_date': '20231212',
    #     'version': '01',
    #   },
    # ]

    # Reformat the upstream dependencies from dependency call to match
    # what batch job expects. Change 'data_source' to 'instrument' and
    # 'data_type' to 'data_level'.
    upstream_dependencies = [
        {
            "instrument": dep["data_source"],
            "data_level": dep["data_type"],
            "descriptor": dep["descriptor"],
            "start_date": dep["start_date"],
            "version": dep["version"],
        }
        for dep in upstream_dependencies
    ]

    batch_command = [
        "--instrument",
        instrument,
        "--data-level",
        data_level,
        "--descriptor",
        descriptor,
        "--start-date",
        start_date,
        "--version",
        version,
        "--dependency",
        f"{upstream_dependencies}",
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
        TODO: We will need to add checks for ancillary files.
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

        dependency_event_msg = {
            "dependency_type": "DOWNSTREAM",
            "relationship": "HARD",
        }

        # TODO: decide how we want to set start date and version
        # for SPICE or ancillary files or sciece files
        # during reprocessing or bulk processing. Should we bring back
        # end_date?
        start_date = ""
        version = ""

        # TODO: How to handle repointing

        # Try to create a science file first
        file_obj = None

        try:
            file_obj = imap_data_access.ScienceFilePath(filename)
            components = ScienceFilePath.extract_filename_components(filename)
            start_date = components["start_date"]
            version = components["version"]

            dependency_event_msg.update(
                {
                    "data_source": components["instrument"],
                    "data_type": components["data_level"],
                    "descriptor": components["descriptor"],
                }
            )
        except imap_data_access.ScienceFilePath.InvalidScienceFileError as e:
            # No science file type matched, return an error with the
            # exception message indicating how to fix it to the user
            logger.error(str(e))
            pass

        # TODO: add ancillary file handling here
        if file_obj is None:
            raise ValueError(f"File handling {filename} is not implemented yet")

        logger.info(f"Sending this event to dependency query: {dependency_event_msg}")
        # Potential jobs are the instruments that depend on the current file,
        # which are the downstream dependencies.
        # TODO: call dependency lambda when it's implemented
        dependency_response = dependency.lambda_handler(dependency_event_msg, None)

        logger.info(f"Dependency query response: {dependency_response}")
        potential_jobs = json.loads(dependency_response["body"])

        if dependency_response["statusCode"] != 200:
            logger.error(f"Dependency query failed with {potential_jobs}")
            raise ValueError("Dependency query failed")

        logger.info(f"Potential jobs found [{len(potential_jobs)}]: {potential_jobs}")

        with db.Session() as session:
            for job in potential_jobs:
                try_to_submit_job(session, job, start_date, version)
