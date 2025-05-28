"""Functions for supporting the batch starter component of the architecture."""

import datetime
import json
import logging
import os
from dataclasses import dataclass

import boto3
import imap_data_access
from imap_data_access import (
    AncillaryFilePath,
    ScienceFilePath,
    SPICEFilePath,
)
from imap_data_access.processing_input import (
    ProcessingInputCollection,
)
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from ..database import database as db
from ..database import models
from . import dependency
from .dependency import DependencyConfig, get_jobs

# import dependency

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Create a batch client
BATCH_CLIENT = boto3.client("batch", region_name="us-west-2")
# Create an sqs client
SQS_CLIENT = boto3.client("sqs", region_name="us-west-2")

SPECIAL_CASE_JOBS = [
    {
        "data_source": "hi",
        "data_type": "l3",
        "descriptor": "h90-ena-h-sf-sp-full-hae-4deg-6mo",
        "relationship": "HARD",
    },
    {
        "data_source": "lo",
        "data_type": "l3",
        "descriptor": "ilo-ena-h-sf-sp-full-hae-4deg-6mo",
        "relationship": "HARD",
    },
    {
        "data_source": "ultra",
        "data_type": "l3",
        "descriptor": "u90-spx-hsf-sp-full-hae-nside8-3mo",
        "relationship": "HARD",
    },
    {
        "data_source": "idex",
        "data_type": "l2b",
        "descriptor": "sci-1week",
        "relationship": "HARD",
    },
]


@dataclass
class Cadence:
    """Valid cadences for processing jobs triggered by cron jobs.

    Valid cadences can be in either months or years
    """

    months3: str = "3mo"
    months6: str = "6mo"
    years1: str = "1yr"

    @property
    def valid_cadence(self) -> list[str]:
        """Get all Cadences.

        Returns
        -------
        list[str]
            list of valid cadences.
        """
        return [self.years1, self.months3, self.months6]

    @property
    def days(self) -> dict:
        """Cadence to days.

        Returns
        -------
        dict
            Cadence values in days.
        """
        return {self.years1: 365, self.months3: 90, self.months6: 180}


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


def get_special_case_date_range(session, job_node, start_date, end_date):
    """Determine the start and end dates for special case jobs.

    This function is used to handle unique processing jobs where the normal method of
    determining the start and end date for the upstream dependencies is insufficient.
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
    end_date : str
        End date for querying data in the format 'YYYYMMDD'.

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
    if job_node["data_source"] == "idex":
        # Special case for IDEX l2b jobs:
        # IDEX l2b requires all l1b event datasets since the last l2b job.
        # Since a l2a file can only trigger IDEX l2b, (l1b files are HARD_NO_TRIGGER)
        # the start_date is from the current l2a file. This means we need to subtract
        # one day from the query to 'find_most_recent_start_date' to get the last l2b
        # job before the current l2a file.
        one_day = datetime.timedelta(days=1)
        new_start_date = find_most_recent_start_date(job_node, start_date - one_day)
        if not new_start_date:
            # If there are no l2b jobs, subtract 7 days from the start date.
            new_start_date = start_date - datetime.timedelta(days=7)
        # Add one day from the most recent l2b job start date since we need IDEX l1b evt
        # files AFTER the last l2b job.
        new_start_date = (new_start_date + one_day).strftime("%Y%m%d")
        # The end date stays the same since we want the cutoff of the query to be the
        # end_date (for in-situ science files besides GLOWS, the start_date is the same
        # as the end_date for normal processing) of the current l2a file.
        new_end_date = end_date
    else:
        # Special case for l3 sp-correlated HI, LO, and ULTRA map jobs:
        # These jobs require both l2 map files and corresponding GLOWS l3e files.
        # Find the most recent l2 map file and use its date range to query GLOWS l3e
        # files.
        deps = get_jobs(
            dependency_type="UPSTREAM",
            relationship="HARD",
            data_source=job_node["data_source"],
            data_type=job_node["data_type"],
            descriptor=job_node["descriptor"],
        )
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
        if cadence_key not in Cadence().valid_cadence:
            raise ValueError(
                f"Invalid cadence '{cadence_key}' from descriptor"
                f"'{job_node['descriptor']}'."
            )
        map_days = Cadence().days[cadence_key]
        # Use the date range of the l2 map as the query range for the l3 job.
        new_end_date = (start_date + datetime.timedelta(days=map_days)).strftime(
            "%Y%m%d"
        )
    return new_start_date, new_end_date


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
    start_date_str = datetime.datetime.strftime(start_date, "%Y%m%d")

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
    # TODO: do we need a reprocessing column to indicate if this is a reprocessing job?
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


def submit_all_jobs(session, job_node, start_date, end_date, filter_dependencies=True):
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
    filter_dependencies : bool
        If True, filter the upstream dependencies to only include the files valid for
        upstream primary science start_date. There are a few special cases where we do
        not want to filter any dependencies out, for example, IDEX l2b needs all of the
        l1b housekeeping datasets in the collection. Default is set to True.

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
    )
    if not upstream_dependencies:
        logger.info(
            f"Skiping job submission for {job_node} because no upstream dependencies."
        )
        return

    # Handle special case reprocessing jobs.
    logger.info(f"All required dependencies found for the dependency: {job_node}")
    # Find the first science processingInput that has the same source as the
    # potential job. Use this to determine the start date.
    primary_science = upstream_dependencies.get_science_inputs(job_node["data_source"])[
        0
    ]
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
            )
        else:
            upstream_deps_for_job = upstream_dependencies
        try_to_submit_job(
            session, job_node, job_start_date, job_version, upstream_deps_for_job
        )


def s3_processing_event(session, events):
    """Process SQS events that were triggered by S3 file arrivals.

    Parameters
    ----------
    session : orm session
        Database session.
    events : dict
        SQS event input.
    """
    # ruff: noqa: PLR0912
    # Since the SQS events can be batched together, we need to loop through
    # each event. In this loop, "event" represents one file landing.

    # Check for GLOWS l3e files. They might come in large groupings from the sqs because
    # GLOWS l3 processing might produce ~30 files at once. We only want one to trigger
    # one downstream l3 survival probability map job in this case.
    triggered_from_glows_l3e = False
    sqs_queue_url = os.getenv("SQS_URL")
    if not sqs_queue_url:
        logger.warning(
            "SQS_URL environment variable is not set. Messages will not"
            " be deleted from the SQS."
        )
    for event in events["Records"]:
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

        if isinstance(file_obj, SPICEFilePath):
            # Set the start and end dates for the upstream event message.
            # TODO: fix date range if/when repoint file ingestion event is
            # passed to batch starter to kickoff HARD or SOFT_TRIGGER downstream jobs.
            # Convert datetime object to string of format YYYYMMDD
            start_date = file_obj.spice_metadata["start_date"].strftime("%Y%m%d")
            end_date = file_obj.spice_metadata["end_date"].strftime("%Y%m%d")
        elif isinstance(file_obj, ScienceFilePath):
            # Set the start and end dates for the upstream event message
            # TODO: if ENA or glows instrument, then get repoint number from filename
            # and set start date and end date differently.
            start_date = end_date = file_obj.start_date
        elif isinstance(file_obj, AncillaryFilePath):
            # Set the start and end dates for the upstream event message
            start_date = file_obj.start_date
            # Ancillary files can have an end date.
            end_date = getattr(file_obj, "end_date", None)
            # If there is no end date for the ancillary file, then it is implicitly
            # valid through today.
            if not end_date:
                end_date = datetime.today().strftime("%Y%m%d")
        # Potential jobs are the instruments that depend on the current file,
        # which are the downstream dependencies.
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

        for job in potential_jobs + potential_soft_jobs:
            if job in SPECIAL_CASE_JOBS:
                start_date, end_date = get_special_case_date_range(
                    session, job, start_date, end_date
                )
                logger.info(
                    f"Found a special case job. Using date range: "
                    f"{start_date} - {end_date}"
                )
                filter_dependencies = False
            else:
                filter_dependencies = True

            submit_all_jobs(session, job, start_date, end_date, filter_dependencies)

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
            session, job_node, filepath.start_date, filepath.start_date
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

    with db.Session() as session:
        api_event = events.get("queryStringParameters")
        if api_event and api_event.get("reprocessing"):
            # handle reprocessing event
            bulk_reprocessing_event(session, api_event)
        else:
            # handle s3 event from the SQS queue
            s3_processing_event(session, events)
