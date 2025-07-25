"""Functions for supporting the batch starter component of the architecture."""

import datetime
import json
import logging
import os
from enum import Enum
from pathlib import Path

import boto3
import imap_data_access
import requests
from imap_data_access import (
    AncillaryFilePath,
    ScienceFilePath,
    SPICEFilePath,
)
from imap_data_access.file_validation import CadenceFilePath
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from ..api_lambdas import download_api, upload_api
from ..database import database as db
from ..database import models
from . import dependency
from .dependency import DependencyConfig, get_jobs

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

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
    cadence: str, as_str: bool = False
) -> tuple[datetime, datetime] | tuple[str, str]:
    """Convert the cadence to a datetime range.

    Parameters
    ----------
    cadence : str
        The cadence string (e.g. "1mo", "3mo", "6mo", "1yr").
    as_str : bool
        If True, return the start and end dates as strings. Default is False.

    Returns
    -------
    tuple(datetime, datetime)
        The start date and end date of the cadence. The end_date is set to today
    """
    end_date = datetime.datetime.today()
    start_date = end_date - datetime.timedelta(
        days=CadenceDays.str_lookup(cadence).value
    )
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
        return ["1mo", "3mo", "6mo", "1yr"]

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
    max_version = (
        session.query(func.max(models.ProcessingJob.version)).filter(
            *filter_conditions(models.ProcessingJob)
        )
    ).scalar()
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


def duplicate_job(
    instrument,
    data_level,
    descriptor,
    start_date,
    previous_version,
    serialized_dependencies,
) -> bool:
    """Determine if the current job is a duplicate of the most recent job.

    Parameters
    ----------
    instrument : str
        Instrument.
    data_level : str
        Data level.
    descriptor : str
        Data descriptor.
    start_date : str
        Start date.
    previous_version : str
        The previous version of the job
    serialized_dependencies : str
        The serialized upstream dependencies of the job.

    Returns
    -------
    bool
        True if the job is a duplicate, False otherwise.
    """
    # TODO switch to DependencyFilePath
    previous_dependency_file = CadenceFilePath.generate_from_inputs(
        instrument=instrument,
        data_level=data_level,
        descriptor=descriptor,
        start_time=start_date,
        version=previous_version,
        extension="json",
    )
    previous_dependency_path = previous_dependency_file.construct_path()
    # Get the dependency s3 path
    relative_path = previous_dependency_path.relative_to(
        imap_data_access.config["DATA_DIR"]
    )
    previous_dependency_str = get_previous_dependency_str(relative_path)
    if previous_dependency_str is None:
        logger.error(
            f"Failed to download previous dependency file: {previous_dependency_path}. "
            f"Skipping duplicate job check."
        )
        return False
    # If the previous dependency string is the same as the current, this is a duplicate
    # job.
    if previous_dependency_str == serialized_dependencies:
        return True

    return False


def try_to_submit_job(
    session: db.Session,
    job_info: dict,
    start_date: datetime,
    version: str,
    serialized_dependencies: str,
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
    """
    instrument = job_info["data_source"]
    data_level = job_info["data_type"]
    descriptor = job_info["descriptor"]
    start_date_str = datetime.datetime.strftime(start_date, "%Y%m%d")

    # Search for any duplicate jobs that have the same exact dependencies for this
    # instrument, data level, descriptor, and start date by checking the CRID.
    # Only check for duplicates if this is a reprocessing job.
    # We know this is a reprocessing job if the version is not "v001".
    if version != "v001":
        previous_version = f"v{int(version[1:]) - 1:03d}"
        if duplicate_job(
            instrument,
            data_level,
            descriptor,
            start_date_str,
            previous_version,
            serialized_dependencies,
        ):
            filepath = ScienceFilePath.generate_from_inputs(
                instrument=instrument,
                data_level=data_level,
                descriptor=descriptor,
                start_time=start_date_str,
                version=previous_version,
            )
            logger.info(
                f"This job is a duplicate of the previous job. See file: "
                f"{filepath.filename!s}. Skipping submission."
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
        f"Wrote job INPROGRESS to Processing Jobs Table with id: {processing_job.id}"
    )
    # TODO: do we need a reprocessing column to indicate if this is a reprocessing job?

    # Serialize the upstream dependencies and write them to a JSON file. The Imap
    # processing code will read the JSON file and deserialize the dependencies. This is
    # to avoid passing a large string through the batch job command line.
    # release
    # TODO switch to DependencyFilePath
    dependency_file = CadenceFilePath.generate_from_inputs(
        instrument=instrument,
        data_level=data_level,
        descriptor=descriptor,
        start_time=start_date.strftime("%Y%m%d"),
        version=version,
        extension="json",
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
        start_date_str,
        "--version",
        version,
        "--dependency",
        dependency_file_path.name,
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
        retryStrategy=BATCH_JOB_RETRY_STRATEGY,
    )
    logger.info(f"Submitted job {job_name} with this command: {batch_command}")


def submit_all_jobs(
    session,
    job_node,
    start_date,
    end_date,
    calculate_crids=False,
    filter_dependencies=True,
):
    """Submit all jobs for the given job and upstream dependencies.

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
    # Submit downstream jobs for each upstream primary science dependency file.
    # Find the files that this job depends on
    upstream_dependencies = dependency.get_jobs(
        data_source=job_node["data_source"],
        data_type=job_node["data_type"],
        descriptor=job_node["descriptor"],
        dependency_type="UPSTREAM",
        relationship="ALL",
        start_date=start_date,
        end_date=end_date,
        calculate_crids=calculate_crids,
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
        job_version = determine_job_version(
            session=session,
            instrument=job_node["data_source"],
            descriptor=job_node["descriptor"],
            start_date=start_date,
            data_level=job_node["data_type"],
        )
        try_to_submit_job(
            session,
            job_node,
            datetime.datetime.strptime(start_date, "%Y%m%d"),
            job_version,
            upstream_dependencies.serialize(),
        )
        return

    # Find the first science processingInput that has the same source as the
    # potential job. Use this to determine the start date.
    primary_science_inputs = upstream_dependencies.get_science_inputs(
        job_node["data_source"]
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
    for filepath in primary_science.imap_file_paths:
        job_start_date = datetime.datetime.strptime(filepath.start_date, "%Y%m%d")
        job_version = determine_job_version(
            session=session,
            instrument=job_node["data_source"],
            descriptor=job_node["descriptor"],
            start_date=job_start_date,
            data_level=job_node["data_type"],
        )
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
                start_date=filepath.start_date,
                end_date=filepath.start_date,
                calculate_crids=False,
            )
            if not upstream_deps_for_job:
                logger.info(
                    f"Skipping job submission for {job_node} with start_date: "
                    f"{start_date} because of a missing upstream dependency."
                )
                continue
        else:
            upstream_deps_for_job = upstream_dependencies
        try_to_submit_job(
            session,
            job_node,
            job_start_date,
            job_version,
            upstream_deps_for_job.serialize(),
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
    """Calculate date range for the pointing data range using repoint files.

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
    logger.info(f"pointing date range, start_date: {start_date}, end_date: {end_date}")

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
            # This date range is used to query attitude kernel file(s).
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
            logger.info(
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

        if input_obj.source == "glows" and input_obj.data_type == "l3e":
            if triggered_from_glows_l3e:
                logger.info(
                    f"Already tried to submit job from a GLOWS l3e file."
                    f"Skipping trigger from filename {filename}"
                )
                continue
            else:
                triggered_from_glows_l3e = True

        # Determine the start and end dates for the upstream query.
        start_date, end_date = determine_date_range(session, file_obj)

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
            job.pop("relationship")
            if job in SPECIAL_CASE_JOBS:
                start_date, end_date = get_special_case_date_range(
                    session, job, start_date
                )
                logger.info(
                    f"Found a special case job: {job}. Using start_date: {start_date}"
                )
                filter_dependencies = False
            else:
                filter_dependencies = True

            submit_all_jobs(
                session,
                job,
                start_date,
                end_date,
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
    # TODO: We need s3 tag or column in db to track bulk reprocessing
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
                "descriptor": descriptor,
                "data_type": data_level,
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
        else:
            submit_all_jobs(session, job, start_date, end_date)


def get_previous_dependency_str(dependency_file_path: Path):
    """Download a JSON file containing a dependencies from S3 and return its contents.

    Parameters
    ----------
    dependency_file_path : Path
        The dependency JSON file to download.

    Returns
    -------
    str or None
        The contents of the dependency file if successful, otherwise None.
    """
    response = download_api.lambda_handler(
        {"pathParameters": {"proxy": dependency_file_path.as_posix()}}, None
    )
    if response["statusCode"] != 302:
        logger.error(
            f"Failed to get S3 pre-signed URL for file: {dependency_file_path}. "
            f"Error message: {response['body']}, "
            f"with status code: {response['statusCode']}."
        )
        return None
    try:
        download_url = json.loads(response["body"])["download_url"]
        response = requests.get(download_url, timeout=60.0)
        logger.info(
            f"Dependency file downloaded successfully from s3 with status code: "
            f"{response.status_code}"
        )
        return response.text
    except Exception as e:
        logger.error(
            f"Unexpected error during dependency file download: {e}. "
            f"Dependency file download failed."
        )
        return None


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
    if signed_url["statusCode"] != 200:
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


def cadence_processing_event(session, events):
    """Process events triggerd by EventBridge rules.

    Parameters
    ----------
    session : orm session
        Database session.
    events : dict
        Event input from an Event Bridge rule.
    """
    cadence = events.get("cadence")
    # TODO: remove start_date and end_date handling after SIT-4.
    start_date = events.get("start_date")
    end_date = events.get("end_date")
    logger.info(f"A cadence event was triggered with the parameters: {cadence=}")
    if not cadence:
        raise ValueError("Cadence event must include 'cadence' key.")
    dep_config = DependencyConfig()
    # Get jobs for specified cadence. Sort them for testing purposes.
    potential_jobs = sorted(dep_config.get_cadence_jobs(cadence), key=lambda x: x[2])
    logger.info(f"Found {len(potential_jobs)} potential cadence jobs: {potential_jobs}")
    # Get the start and end dates for this job
    if not start_date and not end_date:
        start_date, end_date = cadence_to_datetime_range(cadence, as_str=True)
    elif not start_date or not end_date:
        raise ValueError(
            "Cadence event must include both 'start_date' and 'end_date' if either is"
            " provided."
        )
    logger.info(f"Using {start_date=} and {end_date=} for cadence jobs.")

    for job_node in potential_jobs:
        if job_node[0] == "idex" and job_node[1] == "l2b":
            # IDEX l2b jobs are dependent on idex l1b evt housekeeping files. The job
            # should be offset by 1 month to allow for all the event message
            # packets to be processed for the corresponding l2a files. L2b jobs also
            # depend on l1b evt housekeeping files that might be before the cadence job
            # start date. To account for this, we will query for all the l1b evt files
            # including those two weeks before the cadence job start date. This should
            # ensure that all the l1b evt files are available for the l2b job.
            offset_1month = datetime.timedelta(days=CadenceDays.ONE_MONTH)
            start_date = (
                datetime.datetime.strptime(start_date, "%Y%m%d") - offset_1month
            )
            end_date = (
                datetime.datetime.strptime(end_date, "%Y%m%d") - offset_1month
            ).strftime("%Y%m%d")
            # Subtract two weeks from the start date to get all the necessary hk files.
            l1b_evt_start_date = (start_date - datetime.timedelta(weeks=2)).strftime(
                "%Y%m%d"
            )
            start_date = start_date.strftime("%Y%m%d")
            upstream_extended_idex_deps = dependency.get_jobs(
                data_source=job_node[0],
                data_type=job_node[1],
                descriptor=job_node[2],
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
            data_source=job_node[0],
            data_type=job_node[1],
            descriptor=job_node[2],
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
        job_version = determine_job_version(
            session=session,
            instrument=job_node[0],
            data_level=job_node[1],
            descriptor=job_node[2],
            start_date=start_date,
        )
        # Submit the map job with all of the upstream dependencies in the date range
        node = {
            "data_source": job_node[0],
            "data_type": job_node[1],
            "descriptor": job_node[2],
        }
        try_to_submit_job(
            session,
            node,
            datetime.datetime.strptime(start_date, "%Y%m%d"),
            job_version,
            upstream_dependencies.serialize(),
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
