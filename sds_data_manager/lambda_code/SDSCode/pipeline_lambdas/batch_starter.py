"""Functions for supporting the batch starter component of the architecture."""

import datetime
import hashlib
import json
import logging
import os
from enum import Enum
from pathlib import Path
from typing import Optional

import boto3
import imap_data_access
import requests
from imap_data_access import (
    VALID_INSTRUMENTS,
    AncillaryFilePath,
    DependencyFilePath,
    ScienceFilePath,
    SPICEFilePath,
)
from imap_data_access.processing_input import ProcessingInputType
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from ..api_lambdas import upload_api
from ..database import database as db
from ..database import models
from . import VALID_CADENCE_STRS, dependency
from .dependency import DependencyConfig, get_jobs

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

DEPENDENCY_CONFIG = DependencyConfig()
# Create a batch client
BATCH_CLIENT = boto3.client("batch", region_name="us-west-2")
# Define the retry strategy for batch jobs
BATCH_JOB_RETRY_STRATEGY = {
    "attempts": 2,
    "evaluateOnExit": [
        {
            "onStatusReason": "Your Spot Task was interrupted.",
            "action": "RETRY",
        },
        {"onReason": "*", "action": "EXIT"},
    ],
}
# Create an sqs client
SQS_CLIENT = boto3.client("sqs", region_name="us-west-2")

SPECIAL_CASE_JOBS = [
    {
        "data_source": "hi",
        "data_type": "l3",
        "descriptor": "h90-ena-h-sf-sp-full-hae-4deg-6mo",
    },
    {
        "data_source": "lo",
        "data_type": "l3",
        "descriptor": "ilo-ena-h-sf-sp-full-hae-4deg-6mo",
    },
    {
        "data_source": "ultra",
        "data_type": "l3",
        "descriptor": "u90-ena-h-sf-sp-full-hae-4deg-3mo",
    },
]


def cadence_to_datetime_range(
    cadence: str, start_date: Optional[datetime] = None, as_str: Optional[bool] = False
) -> tuple[datetime, datetime] | tuple[str, str]:
    """Convert the cadence to a datetime range.

    Parameters
    ----------
    cadence : str
        The cadence string (e.g. "1mo", "3mo", "6mo", "1yr").
    start_date : datetime, optional
        The start date for the cadence. This is used to calculate the end date. If
        not provided, the end date will be set to today. Default is None.
    as_str : bool
        If True, return the start and end dates as strings. Default is False.

    Returns
    -------
    tuple(datetime, datetime)
        The start date and end date of the cadence. The end_date is set to today
    """
    # Subtract one day from the number of days in the cadence because the query in
    # dependency.py get_files() is inclusive for both the start and end date. This is
    # done to avoid overlapping data by one day.
    num_days = CadenceDays.str_lookup(cadence).value - 1
    if start_date:
        # Find the end date by adding the number of days in the cadence to the start
        # date.
        end_date = start_date + datetime.timedelta(days=num_days)
    else:
        end_date = datetime.datetime.today()
        # Find the start date by subtracting the number of days in the cadence from the
        # end date.
        start_date = end_date - datetime.timedelta(days=num_days)
    if as_str:
        start_date = start_date.strftime("%Y%m%d")
        end_date = end_date.strftime("%Y%m%d")

    return start_date, end_date


class CadenceDays(float, Enum):
    """Enum for a cadence value and the corresponding days."""

    ONE_YEAR = 365.25
    ONE_MONTH = ONE_YEAR / 12
    THREE_MONTHS = ONE_YEAR / 4
    SIX_MONTHS = ONE_YEAR / 2

    @staticmethod
    def valid_cadence_str():
        """Get a list of valid cadence strings."""
        return VALID_CADENCE_STRS

    @classmethod
    def str_lookup(cls, cadence_str: str):
        """Get a CadenceDays value from a string.

        Parameters
        ----------
        cadence_str : str
            The cadence string (e.g. "1mo", "3mo", "6mo", "1yr").

        Returns
        -------
        CadenceDays
            The corresponding CadenceDays enum value.
        """
        if cadence_str not in cls.valid_cadence_str():
            raise ValueError(
                f"Invalid cadence: {cadence_str}. Valid cadences are:"
                f" {cls.valid_cadence_str}"
            )
        return {
            "1mo": cls.ONE_MONTH,
            "3mo": cls.THREE_MONTHS,
            "6mo": cls.SIX_MONTHS,
            "1yr": cls.ONE_YEAR,
        }[cadence_str]


def determine_job_version(
    session: db.Session,
    instrument: str,
    data_level: str,
    descriptor: str,
    start_date: datetime,
    current_dependencies: str,
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
    current_dependencies : str
        Serialized dependencies for the current job.

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
            table.descriptor == descriptor,
            table.start_date == start_date,
        ]
        if table == models.ProcessingJob:
            conditions.append(
                table.status.in_(
                    [models.Status.INPROGRESS.value, models.Status.SUCCEEDED.value]
                )
            )
        return conditions

    # First check to see if there are any jobs in progress and get the max version
    max_version_record = (
        session.query(models.ProcessingJob)
        .filter(*filter_conditions(models.ProcessingJob))
        .order_by(models.ProcessingJob.version.desc())
        .first()
    )
    if max_version_record:
        max_version = max_version_record.version
        # If there is a job already in progress, determine whether the current job
        # is a duplicate of the in-progress job by checking the dependency file hash.
        # If the hashes are different, then we know the dependencies have changed and
        # we should bump the version number and continue with processing.
        if max_version_record.status == models.Status.INPROGRESS:
            command = max_version_record.container_command
            if dependency_hash(current_dependencies) in command:
                # Return the current max version and this job will not proceed if
                # everything else is the same.
                return max_version
            logger.info(
                f"Job with id: {max_version_record.id} is in progress, but the "
                f"dependencies have changed. Bumping version number."
            )
    else:
        max_version = None
    # If the descriptor is "all", we should only check the processing job table. The
    # ScienceFiles table does not have descriptors of "all" since the products
    # produced will have their own specific descriptors.
    if descriptor == "all":
        return f"v{int(max_version[1:]) + 1:03d}" if max_version else "v001"
    # If no jobs are in progress, check the science files table for the max version.
    if not max_version:
        max_version = (
            session.query(func.max(models.ScienceFiles.version)).filter(
                *filter_conditions(models.ScienceFiles)
            )
        ).scalar()
    # Bump the version number. "V001" will be returned if max_version is None.
    return f"v{int(max_version[1:]) + 1:03d}" if max_version else "v001"


def get_special_case_date_range(session, job_node, start_date):
    """Determine the start and end dates for special case jobs.

    This function is used to handle unique processing jobs where the normal method of
    determining the start and end date for the upstream dependencies is not enough.
    For example, l3 survival probability correlated maps for HI, LO, and ULTRA
    depend on their respective l2 maps and multiple GLOWS l3e files (containing the
    survival probabilities) covering the date of the l2 map. We cannot simply use the
    start date of the trigger file in this case. To determine the correct start date to
    use, we need to find the most recent l2 map file and its cadence, e.g 3mo, 6mo, or
    1yr and use that range to query for all the GLOWS l3e and l2 map files.

    Note: This does not handle cadence jobs. Cadence jobs are handled in the
    cadence_processing_event function.

    Parameters
    ----------
    session : orm session
        Database session.
    job_node : dict
        Dictionary containing job details: data source, data type, and descriptor.
    start_date : str
        Start date for querying data in the format 'YYYYMMDD'.

    Returns
    -------
    tuple
        Tuple containing the start and end date for the job.
    """

    def find_most_recent_start_date(dep: dict, date: datetime) -> datetime:
        """Find the most recent start date for a dependency given the filters.

        Parameters
        ----------
        dep : dict
            Dependency details including data source, data type, and descriptor.
        date : datetime
            The date to filter dependencies up to.

        Returns
        -------
        datetime
            The most recent start date for the dependency.
        """
        return (
            session.query(func.max(models.ScienceFiles.start_date))
            .filter(
                models.ScienceFiles.instrument == dep["data_source"],
                models.ScienceFiles.data_level == dep["data_type"],
                models.ScienceFiles.descriptor == dep["descriptor"],
                models.ScienceFiles.start_date <= date,
            )
            .scalar()
        )

    start_date = datetime.datetime.strptime(start_date, "%Y%m%d")

    deps = get_jobs(
        dependency_type="UPSTREAM",
        relationship="HARD",
        data_source=job_node["data_source"],
        data_type=job_node["data_type"],
        descriptor=job_node["descriptor"],
    )
    # Special case for l3 sp-correlated HI, LO, and ULTRA map jobs:
    # These jobs require both l2 map files and corresponding GLOWS l3e files.
    # Find the most recent l2 map file and use its date range to query GLOWS l3e
    # files.
    # Get the l2 upstream dependency (there should only be one).
    l2_dep = next((dep for dep in deps if dep["data_type"] == "l2"), None)
    if l2_dep is None:
        raise ValueError(f"Missing required l2 dependency for job: {job_node}.")
    # Find the most recent l2 map file start_date.
    new_start_date = find_most_recent_start_date(l2_dep, start_date)
    if not new_start_date:
        raise ValueError(
            f"No l2 map files found for {l2_dep['data_source']} "
            f"{l2_dep['data_type']} {l2_dep['descriptor']}."
        )
    new_start_date = new_start_date.strftime("%Y%m%d")
    # Determine the number of days the map was created for based on the cadence.
    cadence_key = job_node["descriptor"].split("-")[-1]
    if cadence_key not in CadenceDays.valid_cadence_str():
        raise ValueError(
            f"Invalid cadence '{cadence_key}' from descriptor"
            f"'{job_node['descriptor']}'. Valid cadences are: "
            f"{CadenceDays.valid_cadence_str()}"
        )
    map_days = CadenceDays.str_lookup(cadence_key).value
    # Use the date range of the l2 map as the query range for the l3 job.
    new_end_date = (start_date + datetime.timedelta(days=map_days)).strftime("%Y%m%d")
    return new_start_date, new_end_date


def dependency_hash(serialized_dependencies):
    """Generate a hash for the serialized dependencies. Use only the first 8 characters.

    Parameters
    ----------
    serialized_dependencies : str
        The serialized dependencies string.

    Returns
    -------
    str
        The first 8 characters of the SHA-256 hash of the serialized dependencies.
    """
    return hashlib.sha256(serialized_dependencies.encode("utf-8")).hexdigest()[:8]


def try_to_submit_job(
    session: db.Session,
    job_info: dict,
    start_date: str,
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
    start_date : str
        Start date of the data in the format 'YYYYMMDD'.
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
        start_time=start_date,
        version=version,
        extension="json",
        repointing=repoint,  # since we can have different repointings on the same day
    )
    dependency_file_path = dependency_file.construct_path()
    response = upload_dependency_file(dependency_file_path, serialized_dependencies)
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
        start_date,
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
        start_date=datetime.datetime.strptime(start_date, "%Y%m%d"),
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
    BATCH_CLIENT.submit_job(
        jobName=job_name,
        jobQueue=job_queue,
        jobDefinition=job_definition,
        containerOverrides={
            "command": batch_command,
        },
        retryStrategy=BATCH_JOB_RETRY_STRATEGY,
    )
    logger.info(f"Submitted job {job_name} with this command: {batch_command}")


def submit_all_jobs(
    session,
    job_node,
    trigger_start_date,
    trigger_end_date,
    calculate_crids=False,
    filter_dependencies=True,
):
    """Submit all jobs for the given job and upstream dependencies.

    Parameters
    ----------
    session : orm session
        Database session.
    job_node : dict
        job node to get the potential jobs from. This is a dictionary with the
        keys: data_source, data_type, and descriptor. This can ONLY be a science job.
    trigger_start_date : str
        The start date of the file that triggered the job in the format 'YYYYMMDD'. This
        determines the range of potential jobs.
    trigger_end_date : str
        The end date of the file that triggered the job in the format 'YYYYMMDD'.
    calculate_crids : bool
        True if the file that triggered the job is a science file, False if it is SPICE
        or ancillary.
    filter_dependencies : bool
        If True, filter the upstream dependencies to only include the files valid for
        upstream primary science start_date. There are a few special cases where we do
        not want to filter any dependencies out, for example, ULTRA l3
        "u90-ena-h-sf-sp-full-hae-4deg-3mo" needs all the psets in the collection.
        Default is set to True.
    """
    logger.info(f"Finding dependencies for the job node: {job_node}")

    # Make initial query for upstream dependency files.
    # These dependencies will be used to determine the start dates of the jobs to
    # submit.
    upstream_dependencies = dependency.get_jobs(
        data_source=job_node["data_source"],
        data_type=job_node["data_type"],
        descriptor=job_node["descriptor"],
        dependency_type="UPSTREAM",
        relationship="ALL",
        start_date=trigger_start_date,
        end_date=trigger_end_date,
        calculate_crids=calculate_crids,
        get_spice=False,  # Skip spice files. Not needed to determine start dates
    )
    if not upstream_dependencies:
        logger.info(
            f"Skipping job submission for {job_node} because of a missing upstream "
            f"dependency."
        )
        return

    # Handle special case reprocessing jobs.
    logger.info(f"All required dependencies found for the dependency: {job_node}")
    if (
        job_node["data_source"] == "spacecraft"
        and job_node["descriptor"] == "pointing-attitude"
    ):
        serialized_deps = upstream_dependencies.serialize()
        job_version = determine_job_version(
            session=session,
            instrument=job_node["data_source"],
            descriptor=job_node["descriptor"],
            start_date=datetime.datetime.strptime(trigger_start_date, "%Y%m%d"),
            data_level=job_node["data_type"],
            current_dependencies=serialized_deps,
        )
        try_to_submit_job(
            session,
            job_node,
            trigger_start_date,
            job_version,
            serialized_deps,
        )
        return

    # For jobs, we need to use the start date from the primary science file.
    # this is not necessarily the same as the start date of the trigger file.
    # Find the first science processingInput that has the same instrument as the
    # potential job. Use this to determine the start date.
    primary_science_inputs = upstream_dependencies.get_processing_inputs(
        input_type=ProcessingInputType.SCIENCE_FILE, source=job_node["data_source"]
    )
    if not primary_science_inputs:
        logger.info(
            f"Skipping job submission for {job_node} because there are no upstream "
            f"primary science files found."
        )
        return
    primary_science = primary_science_inputs[0]
    num_jobs = len(primary_science.imap_file_paths)
    logger.info(f"Found {num_jobs} jobs to process.")
    for filename in primary_science.filename_list:
        science_file = ScienceFilePath(filename)
        start_date, end_date = determine_date_range(session, science_file)

        # Get the repointing number from the science file object
        job_repointing = science_file.repointing

        # If there is only one file to process, then we can use upstream dependencies
        # that have already been queried.
        if num_jobs > 1 or filter_dependencies:
            # Query for upstream files only needed for this job with using the
            # start date of the primary science file.
            upstream_deps_for_job = dependency.get_jobs(
                data_source=job_node["data_source"],
                data_type=job_node["data_type"],
                descriptor=job_node["descriptor"],
                dependency_type="UPSTREAM",
                relationship="ALL",
                start_date=start_date,
                end_date=end_date,
                calculate_crids=False,
                get_spice=True,
            )
            if not upstream_deps_for_job:
                logger.info(
                    f"Skipping job submission for {job_node} with start_date: "
                    f"{start_date} because of a missing upstream dependency."
                )
                continue
        else:
            upstream_deps_for_job = upstream_dependencies
        serialized_deps = upstream_deps_for_job.serialize()
        job_version = determine_job_version(
            session=session,
            instrument=job_node["data_source"],
            descriptor=job_node["descriptor"],
            start_date=datetime.datetime.strptime(start_date, "%Y%m%d"),
            data_level=job_node["data_type"],
            current_dependencies=serialized_deps,
        )
        try_to_submit_job(
            session,
            job_node,
            start_date,
            job_version,
            serialized_deps,
            repoint=job_repointing,
        )


def generate_queue_url(event):
    """Generate the SQS queue URL from the input event.

    Each SQS event includes an "eventSourceARN" field which contains all the
    information needed to construct the queue URL.

    Parameters
    ----------
    event : dict
        Input event from events["Records"] which contains information for one event.

    Returns
    -------
    str
        The SQS queue URL constructed from the event's "eventSourceARN". This is either
        the normal file arrived queue or the delay queue.
    """
    source_arn = event[
        "eventSourceARN"
    ]  # e.g., arn:aws:sqs:us-east-1:123456789012:my-queue-name.fifo
    queue_name = source_arn.split(":")[-1]
    region = source_arn.split(":")[3]
    account_id = source_arn.split(":")[4]
    queue_url = f"https://sqs.{region}.amazonaws.com/{account_id}/{queue_name}"
    return queue_url


def calculate_pointing_date_range(session, pointing_id):
    """Calculate date range for the pointing id using pointing data.

    Parameters
    ----------
    session : sqlalchemy.orm.Session
        Database session.
    pointing_id : int
        The ID of the repointing.

    Returns
    -------
    tuple
        A tuple containing the start date and end date in the format YYYYMMDD.
    """
    # Query the pointing table to find the pointing information.
    pointing_record = (
        session.query(models.PointingTable).filter(
            models.PointingTable.pointing_id == pointing_id
        )
    ).first()

    if not pointing_record:
        raise ValueError(f"No PointingTable record found for ID: {pointing_id}")

    start_date = pointing_record.pointing_start_utc.strftime("%Y%m%d")
    end_date = pointing_record.pointing_end_utc.strftime("%Y%m%d")
    logger.debug(f"pointing date range, start_date: {start_date}, end_date: {end_date}")

    return start_date, end_date


def determine_date_range(session, file_obj):
    """Determine the start and end dates based on the file type.

    This date range is used to query upstream dependencies for the file.

    Parameters
    ----------
    session : sqlalchemy.orm.Session
        Database session.
    file_obj : SPICEFilePath, ScienceFilePath, or AncillaryFilePath
        The file object for which to determine the date range.

    Returns
    -------
    tuple
        A tuple containing the start date and end date in the format YYYYMMDD.
    """
    if isinstance(file_obj, SPICEFilePath):
        file_type = file_obj.spice_metadata["type"]
        if file_type == "repoint":
            # NOTE:
            # Repoint file is used to kicks off pointing_attitude job only.
            # This date range is used to query attitude kernel file(s). If
            # Other is dependent on the repoint file, please revisit this logic.
            start_date = (
                file_obj.spice_metadata["end_date"] - datetime.timedelta(days=1)
            ).strftime("%Y%m%d")
            end_date = file_obj.spice_metadata["end_date"].strftime("%Y%m%d")
        else:
            # Convert datetime object to string of format YYYYMMDD
            start_date = file_obj.spice_metadata["start_date"].strftime("%Y%m%d")
            end_date = file_obj.spice_metadata["end_date"].strftime("%Y%m%d")
    elif isinstance(file_obj, ScienceFilePath):
        # TODO: GLOWS may need other handling using carrington rotation.
        if file_obj.repointing is not None and file_obj.instrument in [
            "glows",
            "hi",
            "lo",
            "ultra",
        ]:
            logger.debug(
                "Using repointing file to calculate date range for"
                f" {file_obj.instrument}."
            )
            start_date, end_date = calculate_pointing_date_range(
                session, file_obj.repointing
            )
        else:
            start_date = end_date = file_obj.start_date
    elif isinstance(file_obj, AncillaryFilePath):
        start_date = file_obj.start_date
        # Ancillary files can have an end date.
        # If there is no end date for the ancillary file, then it is implicitly
        # valid through today.
        end_date = getattr(
            file_obj, "end_date", None
        ) or datetime.datetime.now().strftime("%Y%m%d")
    else:
        raise ValueError("Unsupported file type")
    return start_date, end_date


def s3_processing_event(session, events):
    """Process SQS events that were triggered by S3 file arrivals.

    Parameters
    ----------
    session : sqlalchemy.orm.Session
        Database session.
    events : dict
        SQS event input.
    """
    # Since the SQS events can be batched together, we need to loop through
    # each event. In this loop, "event" represents one file landing.

    # Check for GLOWS l3e files. They might come in large groupings from the sqs because
    # GLOWS l3 processing might produce ~30 files at once. We only want one to trigger
    # one downstream l3 survival probability map job in this case.
    triggered_from_glows_l3e = False

    for event in events["Records"]:
        sqs_queue_url = generate_queue_url(event)

        # Event details:
        logger.info("Individual event: " + json.dumps(event, indent=2))
        body = json.loads(event["body"])
        filename = body["detail"]["object"]["key"]

        file_obj = imap_data_access.file_validation.generate_imap_file_path(filename)
        input_obj = imap_data_access.processing_input.generate_imap_input(filename)

        trigger_start_time, trigger_end_time = determine_date_range(session, file_obj)

        if input_obj.source == "glows" and input_obj.data_type == "l3e":
            if triggered_from_glows_l3e:
                logger.info(
                    f"Already tried to submit job from a GLOWS l3e file."
                    f"Skipping trigger from filename {filename}"
                )
                continue
            else:
                triggered_from_glows_l3e = True

        potential_jobs = dependency.get_jobs(
            data_source=input_obj.source,
            descriptor=input_obj.descriptor,
            data_type=input_obj.data_type,
            dependency_type="DOWNSTREAM",
            relationship="HARD",
        )

        # SOFT_TRIGGER dependencies will try to set off processing
        potential_soft_jobs = dependency.get_jobs(
            data_source=input_obj.source,
            descriptor=input_obj.descriptor,
            data_type=input_obj.data_type,
            dependency_type="DOWNSTREAM",
            relationship="SOFT_TRIGGER",
        )
        logger.info(
            f"Potential jobs: {potential_jobs} and potential soft jobs: "
            f"{potential_soft_jobs}"
        )
        if not potential_jobs and not potential_soft_jobs:
            logger.info(f"No downstream dependencies found for the file: {filename}")
            continue

        # Boolean to determine if the file that triggered the job is a science file.
        # If True, we will check if the expected CRIDs exist for the upstream
        # dependencies. If so, processing will continue. If not, it will return None.
        # This check should only be done for jobs that were triggered by a science file
        # because this indicates that there may be a reprocessing of an upstream file,
        # and we want to avoid multiple reprocessing of the same file.
        calculate_crids = isinstance(file_obj, ScienceFilePath)
        for job in potential_jobs + potential_soft_jobs:
            if job["data_source"] not in VALID_INSTRUMENTS:
                raise ValueError(
                    f"Unable to submit job for invalid instrument {job['data_source']}."
                    f" Downstream dependencies must be science files."
                )

            job.pop("relationship")

            if job in SPECIAL_CASE_JOBS:
                trigger_start_time, trigger_end_time = get_special_case_date_range(
                    session, job, trigger_start_time
                )
                logger.info(
                    f"Using special case date range: "
                    f"{trigger_start_time} to {trigger_end_time}"
                )
                filter_dependencies = False
            else:
                filter_dependencies = True

            submit_all_jobs(
                session,
                job,
                trigger_start_time,
                trigger_end_time,
                calculate_crids,
                filter_dependencies,
            )

        if sqs_queue_url:
            # When the record from the sqs event has been processed, it can safely be
            # deleted from the queue.
            SQS_CLIENT.delete_message(
                QueueUrl=sqs_queue_url,
                ReceiptHandle=event["receiptHandle"],
            )
            logger.info(
                f"SQS record with receipt handle: {event['receiptHandle']} "
                f"processed and deleted from the SQS."
            )


def handle_special_case_reprocessing_jobs(session, job_node, start_date, end_date):
    """Handle special case reprocessing jobs.

    This function is used to handle unique jobs when reprocessing is triggered.

    Parameters
    ----------
    session : orm session
        Database session.
    job_node : dict
        job node to get the potential jobs from.
    start_date : str
        Start date to query the data.
    end_date : str
        End date to query the data.
    """
    # get the upstream dependencies for the reprocessing date range
    upstream_dependencies = dependency.get_jobs(
        data_source=job_node["data_source"],
        data_type=job_node["data_type"],
        descriptor=job_node["descriptor"],
        dependency_type="UPSTREAM",
        relationship="ALL",
        start_date=start_date,
        end_date=end_date,
    )
    if not upstream_dependencies:
        return
    # find the primary science processingInput that has the same source as the job.
    primary_science = upstream_dependencies.get_science_inputs(job_node["data_source"])[
        0
    ]
    logger.info(
        f"Handling special case reprocessing. Found "
        f"{len(primary_science.imap_file_paths)} files to reprocess."
    )
    for filepath in primary_science.imap_file_paths:
        # For each file to reprocess we need to determine the correct
        # start and end date to use for the job.
        start_date, end_date = get_special_case_date_range(
            session, job_node, filepath.start_date
        )
        submit_all_jobs(
            session, job_node, start_date, end_date, filter_dependencies=False
        )


def bulk_reprocessing_event(session, events):
    """Process bulk reprocessing event.

    Parameters
    ----------
    session : orm session
        Database session.
    events : dict
        Event input.
    """
    instrument = events.get("instrument")
    data_level = events.get("data_level")
    descriptor = events.get("descriptor")
    start_date = events.get("start_date")
    end_date = events.get("end_date")
    logger.info(
        f"A reprocessing event was triggered with the parameters: {instrument=}, "
        f"{data_level=}, {descriptor=}, {start_date=}, {end_date=}"
    )

    if not end_date or not start_date:
        raise ValueError(
            "Start date and end date are required for a reprocessing Event."
        )
    if data_level:
        # If data_level is provided, instrument and descriptor are required.
        if not instrument or not descriptor:
            raise ValueError(
                "If data_level is provided, instrument and descriptor are required."
            )
        # we need to find the upstream dependencies for this instrument, data level,
        # and descriptor
        potential_jobs = [
            {
                "data_source": instrument,
                "data_type": data_level,
                "descriptor": descriptor,
            }
        ]
    else:
        # If no instrument is provided, there should be no descriptor or data level.
        if not instrument and descriptor:
            raise ValueError(
                "If descriptor is provided, instrument must also be provided."
            )
        # If data_level is not provided, we need to reprocess all levels.
        # Get the jobs that kick of each pipeline, to trigger processing
        # for all levels.
        potential_jobs = DependencyConfig().kickoff_pipeline_jobs()
        # filter the jobs by instrument and descriptor if provided
        potential_jobs = [
            job
            for job in potential_jobs
            if (
                (job["data_source"] == instrument or not instrument)
                and (job["descriptor"] == descriptor or not descriptor)
            )
        ]
    for job in potential_jobs:
        if job in SPECIAL_CASE_JOBS:
            handle_special_case_reprocessing_jobs(session, job, start_date, end_date)
        elif (
            job in DEPENDENCY_CONFIG.get_cadence_jobs()
            or job["descriptor"] in CadenceDays.valid_cadence_str()
        ):
            cadence_reprocessing_event(session, job, start_date, end_date)
        else:
            submit_all_jobs(session, job, start_date, end_date)


def upload_dependency_file(dependency_file_path: Path, serialized_dependencies: str):
    """Upload a JSON file containing a job's dependencies to S3.

    Parameters
    ----------
    dependency_file_path : Path
        The dependency JSON file to upload.
    serialized_dependencies : str
        The serialized upstream dependencies to upload.
    """
    # Check if the file already exists
    if os.path.isfile(dependency_file_path):
        raise KeyError(
            f"{dependency_file_path} already exists, cannot create JSON file."
        )
    # call the upload API handler directly
    signed_url = upload_api.lambda_handler(
        {"pathParameters": {"proxy": dependency_file_path.as_posix()}}, None
    )
    if signed_url["statusCode"] == 409:
        logger.info(
            f"Dependency file already exists in S3: {dependency_file_path}. Reusing"
            f"file."
        )
        return {"statusCode": 200, "body": signed_url["body"]}
    elif signed_url["statusCode"] != 200:
        logger.error(
            f"Failed to get S3 pre-signed URL for file: {dependency_file_path}. "
            f"As a result, failed to kick off job. "
            f"Error message: {signed_url['body']}, "
            f"with status code: {signed_url['statusCode']}."
        )
        return None
    try:
        response = requests.put(
            signed_url["body"].strip('"'),
            data=serialized_dependencies,
            headers={"Content-Type": "application/json"},
            timeout=60.0,
        )
        logger.info(
            f"Dependency file uploaded successfully to s3 with status code: "
            f"{response.status_code}"
        )
        return response
    except Exception as e:
        logger.error(
            f"Unexpected error during cadence file upload: {e}. "
            f"Dependency file upload failed and the job did not get kicked off."
        )
        return None


def cadence_reprocessing_event(session, job, start_date, end_date):
    """Handle reprocessing of cadence jobs.

    Parameters
    ----------
    session : orm session
        Database session.
    job : dict
        Job node containing data source, data type, and descriptor.
    start_date : str
        Start date for the reprocessing job in the format YYYYMMDD.
    end_date : str
        End date for the reprocessing job in the format YYYYMMDD.
    """
    if job["descriptor"] in CadenceDays.valid_cadence_str():
        cadence_str = job["descriptor"]
        potential_jobs = [
            node
            for node in DEPENDENCY_CONFIG.get_cadence_jobs(cadence_str)
            if node["data_source"] == job["data_source"]
            and node["data_type"] == job["data_type"]
        ]
    else:
        cadence_str = job["descriptor"].split("-")[-1]
        potential_jobs = [job]
    logger.info(f"Reprocessing cadence jobs: {potential_jobs}")
    for job_node in potential_jobs:
        # get the upstream dependencies for the reprocessing date range

        # Get all the start dates for the existing processing jobs that match the job
        # node.
        table = models.ProcessingJob
        processed_start_dates = [
            row[0]
            for row in (
                session.query(models.ProcessingJob.start_date).filter(
                    table.instrument == job_node["data_source"],
                    table.data_level == job_node["data_type"],
                    table.descriptor == job_node["descriptor"],
                    table.start_date
                    >= datetime.datetime.strptime(start_date, "%Y%m%d"),
                    table.start_date <= datetime.datetime.strptime(end_date, "%Y%m%d"),
                )
            ).all()
        ]
        # Processed start dates are the dates of jobs that have already been processed
        # in the given date range. If there are no processed start dates for this job,
        # skip reprocessing for this specific job node, but continue checking other
        # jobs. For example, if there are no jobs for ultra,l2,"...4deg-3mo",
        # we still want to attempt to reprocess ultra,l2,"...6deg-3mo" maps.
        if not processed_start_dates:
            logger.info(
                f"No previously processed jobs found for: {job_node}, skipping."
            )
            continue
        logger.info(
            f"Handling cadence reprocessing. Found {len(processed_start_dates)} files "
            f"to reprocess for job: {job_node}."
        )
        for date in list(set(processed_start_dates)):
            # For each file to reprocess, we need to determine the correct
            # start and end date to use for the map job.
            start_date, end_date = cadence_to_datetime_range(
                cadence_str, date, as_str=True
            )
            cadence_processing_event(
                session,
                events=None,
                job=job_node,
                start_date=start_date,
                end_date=end_date,
            )


def cadence_processing_event(
    session,
    events: Optional[dict] = None,
    job: Optional[dict] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    """Process events triggerd by EventBridge rules.

    Parameters
    ----------
    session : orm session
        Database session.
    events : dict
        Event input from an Event Bridge rule. This is used when the event is triggered
        by an event bridge rule. If not supplied, then job_node, start_date, and
        end_date must be provided. Default is None.
    job : dict
        Job node including data source, data type, and descriptor. Default is None.
    start_date : str
        Start date for the job in the format YYYYMMDD. Default is None.
    end_date : str
        End date for the job in the format YYYYMMDD. Default is None.
    """
    if events:
        cadence = events.get("cadence")
        # TODO: remove start_date and end_date handling after SIT-4.
        start_date = events.get("start_date")
        end_date = events.get("end_date")
        logger.info(f"A cadence event was triggered with the parameters: {cadence=}")
        if not cadence:
            raise ValueError("Cadence event must include 'cadence' key.")
        # Get jobs for specified cadence. Sort them for testing purposes.
        potential_jobs = sorted(
            DEPENDENCY_CONFIG.get_cadence_jobs(cadence), key=lambda x: x["descriptor"]
        )
        logger.info(
            f"Found {len(potential_jobs)} potential cadence jobs: {potential_jobs}"
        )
        # Get the start and end dates for this job
        if not start_date and not end_date:
            start_date, end_date = cadence_to_datetime_range(cadence, as_str=True)
        elif not start_date or not end_date:
            raise ValueError(
                "Cadence event must include both 'start_date' and 'end_date' if either "
                "is provided."
            )
        reprocessing = False
    else:
        potential_jobs = [job]
        reprocessing = True

    logger.info(f"Using {start_date=} and {end_date=} for cadence jobs.")

    for job_node in potential_jobs:
        instrument = job_node["data_source"]
        data_level = job_node["data_type"]
        descriptor = job_node["descriptor"]
        if instrument == "idex" and data_level == "l2b":
            # IDEX l2b jobs are dependent on idex l1b evt housekeeping files. The job
            # should be offset by 1 month to allow for all the event message
            # packets to be processed for the corresponding l2a files (This should only
            # be done for first version products). L2b jobs also
            # depend on l1b evt housekeeping files that might be before the cadence job
            # start date. To account for this, we will query for all the l1b evt files
            # including those two weeks before the cadence job start date. This should
            # ensure that all the l1b evt files are available for the l2b job.
            if not reprocessing:
                offset_1month = datetime.timedelta(days=CadenceDays.ONE_MONTH)
                start_date = (
                    datetime.datetime.strptime(start_date, "%Y%m%d") - offset_1month
                )
                end_date = (
                    datetime.datetime.strptime(end_date, "%Y%m%d") - offset_1month
                ).strftime("%Y%m%d")
            start_date = (
                datetime.datetime.strptime(start_date, "%Y%m%d")
                if isinstance(start_date, str)
                else start_date
            )
            # Subtract two weeks from the start date to get all the necessary hk files.
            l1b_evt_start_date = (start_date - datetime.timedelta(weeks=2)).strftime(
                "%Y%m%d"
            )
            start_date = start_date.strftime("%Y%m%d")
            upstream_extended_idex_deps = dependency.get_jobs(
                data_source=instrument,
                data_type=data_level,
                descriptor=descriptor,
                dependency_type="UPSTREAM",
                relationship="ALL",
                start_date=l1b_evt_start_date,
                end_date=end_date,
            )
            if not upstream_extended_idex_deps:
                continue
            # Extract only the processing input for the idex l2b evt files
            additional_input = upstream_extended_idex_deps.get_processing_inputs(
                source="idex", data_type="l1b", descriptor="evt"
            )
        else:
            additional_input = None

        upstream_dependencies = dependency.get_jobs(
            data_source=instrument,
            data_type=data_level,
            descriptor=descriptor,
            dependency_type="UPSTREAM",
            relationship="ALL",
            start_date=start_date,
            end_date=end_date,
        )
        if not upstream_dependencies:
            continue
        if additional_input:
            # If there are additional inputs, add them to the upstream dependencies.
            upstream_dependencies.add(additional_input)

        logger.info(f"All required dependencies found for the dependency: {job_node}")
        serialized_deps = upstream_dependencies.serialize()
        job_version = determine_job_version(
            session=session,
            instrument=instrument,
            data_level=data_level,
            descriptor=descriptor,
            start_date=datetime.datetime.strptime(start_date, "%Y%m%d"),
            current_dependencies=serialized_deps,
        )
        # Submit the map job with all of the upstream dependencies in the date range
        try_to_submit_job(
            session,
            job_node,
            start_date,
            job_version,
            serialized_deps,
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
        Example event:
            {
                "queryStringParameters": {
                    "reprocessing": True,
                    "start_date": <>,
                    "end_date": <>,
                    "instrument": None, optional,
                    "data_level": None, optional,
                    "data_descriptor": None, optional,
                }
            }
    5. Event of a cron job cadence trigger.
        Example event:
            {
                "cadence": 1mo, 3mo, 1yr, or 6mo
            }

    Parameters
    ----------
    events : dict
        Event input
    context : LambdaContext
        Lambda context object
    """
    logger.info(f"Events: {events}")

    with db.Session() as session:
        api_event = events.get("queryStringParameters")
        if api_event and api_event.get("reprocessing"):
            # handle reprocessing event
            bulk_reprocessing_event(session, api_event)
        elif events.get("cadence"):
            # Handle a cadence event
            cadence_processing_event(session, events)
        else:
            # handle s3 event from the SQS queue
            s3_processing_event(session, events)
