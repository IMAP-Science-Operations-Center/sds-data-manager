"""Functions for supporting the batch starter component of the architecture."""

import json
import logging
from datetime import datetime

import boto3
from imap_data_access import ScienceFilePath
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import dependency_config
from .database import database as db
from .database import models

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Create a batch client
BATCH_CLIENT = boto3.client("batch", region_name="us-west-2")


def get_dependencies(node, direction, relationship):
    """Lookup the dependencies for the given ``node``.

    A ``node`` is an identifier of the data product, which can be an
    (instrument, data_level, descriptor) tuple, SPICE file identifiers,
    or ancillary data file identifiers.

    Parameters
    ----------
    node : tuple
        Quantities that uniquely identify a data product.
    direction : str
        Whether it's UPSTREAM or DOWNSTREAM dependency.
    relationship : str
        Whether it's HARD or SOFT dependency.
        HARD means it's required and SOFT means it's nice to have.

    Returns
    -------
    dependencies : list
        List of dictionary containing the dependency information.
    """
    dependencies = dependency_config.DEPENDENCIES[relationship][direction].get(node, [])
    # Add keys for a dict-like representation
    dependencies = [
        {"instrument": dep[0], "data_level": dep[1], "descriptor": dep[2]}
        for dep in dependencies
    ]

    return dependencies


def get_file(session, instrument, data_level, descriptor, start_date, version):
    """Query to database to get the first FileCatalog record.

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
    record : models.FileCatalog or None
        The first FileCatalog record matching the query criteria.
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
        session.query(models.FileCatalog)
        .filter(
            models.FileCatalog.instrument == instrument,
            models.FileCatalog.data_level == data_level,
            models.FileCatalog.descriptor == descriptor,
            models.FileCatalog.start_date == datetime.strptime(start_date, "%Y%m%d"),
            models.FileCatalog.version == version,
        )
        .first()
    )

    return record


def get_downstream_dependencies(session, filename_components):
    """Get information of downstream dependents.

    Parameters
    ----------
    session : orm session
        Database session.
    filename_components : dict
        Dictionary containing components of the filename.

    Returns
    -------
    downstream_dependents : list of dict
        Dictionary containing components with dates and versions appended.
    """
    # Get downstream dependency data
    downstream_dependents = get_dependencies(
        node=(
            filename_components["instrument"],
            filename_components["data_level"],
            filename_components["descriptor"],
        ),
        direction="DOWNSTREAM",
        relationship="HARD",
    )

    for dependent in downstream_dependents:
        # TODO: query the version table here for appropriate version
        #  of each downstream_dependent.
        dependent["version"] = filename_components["version"]  # placeholder

        # TODO: add repointing table query if dependent is ENA or GLOWS
        # Use start_date to query repointing table.
        # Add pointing number to dependent.
        dependent["start_date"] = filename_components["start_date"]

    return downstream_dependents


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


def try_to_submit_job(session, job_info):
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

    Returns
    -------
    bool
        Whether or not this job is ready to be processed.
    """
    instrument = job_info["instrument"]
    data_level = job_info["data_level"]
    descriptor = job_info["descriptor"]
    start_date = job_info["start_date"]
    version = job_info["version"]

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
    upstream_dependencies = get_dependencies(
        node=(instrument, data_level, descriptor),
        direction="UPSTREAM",
        relationship="HARD",
    )

    for upstream_dependency in upstream_dependencies:
        upstream_instrument = upstream_dependency["instrument"]
        upstream_data_level = upstream_dependency["data_level"]
        upstream_descriptor = upstream_dependency["descriptor"]

        # Check to see if each upstream dependency file is available
        # TODO: Update start_date / version request to be more specific
        #       Currently we are using the same as the job product, but the versions
        #       may not match exactly if one dependency updates before another
        upstream_start_date = start_date
        upstream_version = version
        upstream_dependency.update(
            {"start_date": upstream_start_date, "version": upstream_version}
        )
        record = get_file(
            session,
            upstream_instrument,
            upstream_data_level,
            upstream_descriptor,
            upstream_start_date,
            upstream_version,
        )
        if record is None:
            logger.info(
                f"Dependency not found: {upstream_instrument}, "
                f"{upstream_data_level}, "
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

    # FYI, these are the keys the upstream_dependencies should contain:
    # {
    #     'instrument': 'swe',
    #     'data_level': 'l0',
    #     'descriptor': 'lveng-hk',
    #     'start_date': '20231212',
    #     'version': 'v001',
    # },
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
    """Lambda handler."""
    logger.info(f"Events: {events}")
    logger.info(f"Context: {context}")

    with db.Session() as session:
        # Since the SQS events can be batched together, we need to loop through
        # each event. In this loop, "event" represents one file landing.
        for event in events["Records"]:
            # Event details:
            logger.info(f"Individual event: {event}")
            body = json.loads(event["body"])

            filename = body["detail"]["object"]["key"]
            logger.info(f"Retrieved filename: {filename}")
            components = ScienceFilePath.extract_filename_components(filename)

            # Potential jobs are the instruments that depend on the current file.
            potential_jobs = get_downstream_dependencies(session, components)
            logger.info(
                f"Potential jobs found [{len(potential_jobs)}]: {potential_jobs}"
            )

            for job in potential_jobs:
                try_to_submit_job(session, job)
