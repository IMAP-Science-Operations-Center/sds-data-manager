import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

import boto3
from sqlalchemy.orm import Session

from .database import database as db
from .database import models
from .lambda_custom_events import IMAPLambdaPutEvent

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def query_instrument(session, upstream_dependency, start_date, end_date):
    """
    Appends start_time, end_time and version information to downstream dependents.

    Parameters
    ----------
    session : orm session
        Database session.
    upstream_dependency : dict
        Dictionary of upstream dependency.
    start_date : str
        Start date of the event data.
    end_date : str
        End date of the event data.

    Returns
    -------
    record : models.FileCatalog or None
        The first FileCatalog record matching the query criteria.
        None is returned if no record matches the criteria.

    """
    instrument = upstream_dependency["instrument"]
    level = upstream_dependency["level"]
    version = upstream_dependency["version"]

    record = (
        session.query(models.FileCatalog)
        .filter(
            models.FileCatalog.instrument == instrument,
            models.FileCatalog.data_level == level,
            models.FileCatalog.version == version,
            models.FileCatalog.start_date >= datetime.strptime(start_date, "%Y%m%d"),
            models.FileCatalog.end_date <= datetime.strptime(end_date, "%Y%m%d"),
        )
        .first()
    )

    return record


def append_attributes(session, downstream_dependents, start_date, end_date, version):
    """
    Appends start_time, end_time and version information to downstream dependents.

    Parameters
    ----------
    session : orm session
        Database session.
    downstream_dependents : list of dict
        A list of dictionaries where each dictionary corresponds to a record
    start_date : str
        Start date of the event data.
    end_date : str
        End date of the event data.
    version : str
        Version of the event data.

    Returns
    -------
    downstream_dependents : list of dict
        Dictionary containing components with dates and versions appended.

    """

    for dependent in downstream_dependents:
        # TODO: query the version table here for appropriate version
        #  of each downstream_dependent.

        # TODO: add repointing table query if dependent is ENA or GLOWS
        #  Use start_date and end_date to query repointing table.
        # Use pointing start_time and end_time in place of start_date and end_date.
        # Add pointing number to dependent.

        dependent["version"] = version  # placeholder
        dependent["start_date"] = start_date
        dependent["end_date"] = end_date

    return downstream_dependents


def find_upstream_dependencies(
    downstream_dependent_instrument,
    downstream_dependent_inst_level,
    downstream_dependent_version,
    data,
):
    """
    Finds dependency information for each instrument.

    Parameters
    ----------
    downstream_dependent_instrument : str
        Downstream dependent instrument.
    downstream_dependent_inst_level : str
        Downstream dependent data level.
    downstream_dependent_version : str
        Downstream dependent version.
    data : dict
        Dictionary containing dependency data.

    Returns
    -------
    upstream_dependencies : list of dict
        A list of dictionaries containing dependency instrument,
        data level, and version.

    Example:
    upstream_dependencies = find_upstream_dependencies("codice", "l3b", "v00-01", data)

    expected_result = [
        {"instrument": "codice", "level": "l2", "version": "v00-01"},
        {"instrument": "codice", "level": "l3a", "version": "v00-01"},
        {"instrument": "mag", "level": "l2", "version": "v00-01"},
    ]

    """
    upstream_dependencies = []

    for instr, levels in data.items():
        for level, deps in levels.items():
            if any(
                dep["instrument"] == downstream_dependent_instrument
                and dep["level"] == downstream_dependent_inst_level
                for dep in deps
            ):
                upstream_dependencies.append({"instrument": instr, "level": level})

    for dependency in upstream_dependencies:
        # TODO: query the version table here for appropriate version
        #  of each dependency. Use downstream_dependent_version to query version table.
        dependency["version"] = downstream_dependent_version  # placeholder

    return upstream_dependencies


def query_upstream_dependencies(
    session, downstream_dependents, data, s3_bucket, descriptor
):
    """
    Finds dependency information for each instrument. This function looks for
    upstream dependency of current downstream dependent.

    Parameters
    ----------
    session : orm session
        Database session.
    downstream_dependents : list of dict
        Dictionary containing components with dates and versions appended.
    data : dict
        Dictionary containing dependency data.
    s3_bucket : str
        S3 bucket name.

    Returns
    -------
    instruments_to_process : list of dict
        A list of dictionaries containing the filename and prepared data.

    """

    instruments_to_process = []

    # Iterate over each downstream dependent
    for dependent in downstream_dependents:
        instrument = dependent["instrument"]
        level = dependent["level"]
        version = dependent["version"]
        start_date = dependent["start_date"]
        end_date = dependent["end_date"]

        # For each downstream dependent, find its upstream dependencies
        upstream_dependencies = find_upstream_dependencies(
            instrument, level, version, data
        )

        all_dependencies_available = True  # Initialize the flag
        for upstream_dependency in upstream_dependencies:
            # Check to see if each upstream dependency is available
            record = query_instrument(
                session, upstream_dependency, start_date, end_date
            )
            if record is None:
                all_dependencies_available = (
                    False  # Set flag to false if any dependency is missing
                )
                logger.info(
                    f"Missing dependency: {upstream_dependency['instrument']}, "
                    f"{upstream_dependency['level']}, "
                    f"{upstream_dependency['version']}"
                )
                break  # Exit the loop early as we already found a missing dependency
            else:
                logger.info(
                    f"Dependency found: {upstream_dependency['instrument']}, "
                    f"{upstream_dependency['level']}, "
                    f"{upstream_dependency['version']}"
                )

        # If all dependencies are available, prepare the data for batch job
        if all_dependencies_available:
            # TODO: add descriptor logic. Using <sci> as placeholder.
            filename = (
                f"imap_{instrument}_{level}_{descriptor}"
                f"_{start_date}_{end_date}_{version}.cdf"
            )

            prepared_data = prepare_data(filename, upstream_dependencies)
            instruments_to_process.append(
                {"filename": filename, "prepared_data": prepared_data}
            )
            logger.info(f"All dependencies for {instrument} present.")
        else:
            logger.info(f"Some dependencies for {instrument} are missing.")

    return instruments_to_process


def load_data(filepath: Path):
    """
    Loads dependency data.

    Parameters
    ----------
    filepath : Path
        Path of dependency data.

    Returns
    -------
    data : dict
        Dictionary containing dependency data.

    """
    with filepath.open() as file:
        data = json.load(file)

    return data


def prepare_data(filename, upstream_dependencies):
    """
    Prepares data for batch job.

    Parameters
    ----------
    instrument : str
        Instrument.
    level : str
        Data level.
    start_date : str
        Data start date.
    filename : str
        filename.
    upstream_dependencies : list of dict
        A list of dictionaries containing dependency instrument,
        data level, and version.

    Returns
    -------
    prepared_data : str
        Data to submit to batch job.

    """
    components = extract_components(filename)

    instrument = components["instrument"]
    level = components["datalevel"]
    start_date = components["startdate"]

    format_start_date = datetime.strptime(start_date, "%Y%m%d")

    # Format year and month from the datetime object
    year = format_start_date.strftime("%Y")
    month = format_start_date.strftime("%m")

    # Base S3 path
    s3_base_path = f"imap/{instrument}/{level}/{year}/{month}/"

    # Prepare the final command
    # Pre-construct parts of the string
    # NOTE: Batch job expects command like this:
    # "Command": [
    #     "--instrument",
    #     "swe",
    #     "--level",
    #     "l1b",
    #     "--file_path",
    #     "imap/swe/l1b/2023/09/imap_swe_l1b_lveng-hk_20230927_20230927_v01-00.cdf",
    #     "--dependency",
    #     "[{'instrument': 'swe', 'level': 'l0', 'version': 'v00-01'}]"
    #   ]
    prepared_data = [
        "--instrument",
        instrument,
        "--level",
        level,
        "--file_path",
        s3_base_path + filename,
        "--dependency",
        f"{upstream_dependencies}",
    ]

    return prepared_data


def extract_components(filename: str):
    """
    Extracts components from filename.

    Parameters
    ----------
    filename : str
        Path of dependency data.

    Returns
    -------
    components : dict
        Dictionary containing components.

    """
    pattern = (
        r"^imap_"
        r"(?P<instrument>[^_]*)_"
        r"(?P<datalevel>[^_]*)_"
        r"(?P<descriptor>[^_]*)_"
        r"(?P<startdate>\d{8})_"
        r"(?P<enddate>\d{8})_"
        r"(?P<version>v\d{2}-\d{2})"
        r"\.(cdf|pkts)$"
    )
    match = re.match(pattern, filename)
    if match is None:
        logger.info(f"doesn't match pattern - {filename}")
        return
    components = match.groupdict()
    return components


def send_lambda_put_event(command_parameters):
    """Sends custom PutEvent to EventBridge

    Example of what PutEvent looks like:
    event = {
        "Source": "imap.lambda",
        "DetailType": "Job Started",
        "Detail": {
            "file_path_to_create": "<file_path>",
            "status": "INPROGRESS",
            "dependency": "[{
                "codice": "s3-test",
                "mag": "s3-filepath"
            }]")
        },
    }

    Parameters
    ----------
    command_parameters : str
        IMAP cli command input parameters.
        Example of input:
            [
            "--instrument",
            "hit",
            "--level",
            "l1a",
            "--file_path",
            ("imap/hit/l1a/2024/01/"
            "imap_hit_l1a_sci_20240101_20240102_v00-01.cdf"),
            "--dependency",
            "[{'instrument': 'hit', 'level': 'l0', 'version': 'v00-01'}]",
        ]
    Returns
    -------
    dict
        EventBridge response
    """
    event_client = boto3.client("events")

    # Get event inputs ready
    file_path = command_parameters[5]
    dependency = command_parameters[7]

    # Create event["detail"] information
    detail = {
        "file_path_to_create": file_path,
        "status": models.Status.INPROGRESS.value,
        "dependency": dependency,
    }

    # create PutEvent dictionary
    event = IMAPLambdaPutEvent(detail_type="Job Started", detail=detail)
    event_data = event.to_event()

    # Send event to EventBridge
    response = event_client.put_events(Entries=[event_data])
    return response


def lambda_handler(event: dict, context):
    """Handler function"""
    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    # Event details:
    filename = event["detail"]["object"]["key"]
    components = extract_components(filename)
    logger.info(f"Parsed filename - {components}")
    instrument = components["instrument"]
    level = components["datalevel"]
    descriptor = components["descriptor"]
    version = components["version"]
    start_date = components["startdate"]
    end_date = components["enddate"]

    # S3 Bucket name.
    s3_bucket = os.environ.get("S3_BUCKET")

    # Retrieve dependency data.
    dependency_path = Path(__file__).resolve().parent / "downstream_dependents.json"
    data = load_data(dependency_path)
    logger.info(f"loaded dependent data - {data}")
    # Downstream dependents that are candidates for the batch job.
    downstream_dependents = data[instrument][level]

    # Get information for the batch job.
    region = os.environ.get("REGION")
    account = os.environ.get("ACCOUNT")
    # Create a batch client
    batch_client = boto3.client("batch")

    job_definition = (
        f"arn:aws:batch:{region}:{account}:job-definition/"
        f"fargate-batch-job-definition{instrument}"
    )
    job_queue = (
        f"arn:aws:batch:{region}:{account}:job-queue/"
        f"{instrument}-fargate-batch-job-queue"
    )

    # Get database engine.
    engine = db.get_engine()

    with Session(engine) as session:
        complete_dependents = append_attributes(
            session, downstream_dependents, start_date, end_date, version
        )

        # decide if we have sufficient upstream dependencies
        downstream_instruments_to_process = query_upstream_dependencies(
            session, complete_dependents, data, s3_bucket, descriptor
        )

        # No instruments to process
        if not downstream_instruments_to_process:
            logger.info("No instruments_to_process. Skipping further processing.")
            return

        # Start Batch Job execution for each instrument
        for downstream_data in downstream_instruments_to_process:
            filename = downstream_data["filename"]

            command = downstream_data["prepared_data"]
            logger.info(f"Submitting job with this command - {command}")
            # NOTE: The batch job name should contain only
            # alphanumeric characters and hyphens.
            job_name = f"{instrument}-{level}-job"
            logger.info("Job name: %s", job_name)
            response = batch_client.submit_job(
                jobName=job_name,
                jobQueue=job_queue,
                jobDefinition=job_definition,
                containerOverrides={
                    "command": downstream_data["prepared_data"],
                },
            )
            logger.info(f"Submitted job - {response}")
            # Send EventBridge event to indexer lambda
            logger.info("Sending EventBridge event to indexer lambda.")
            send_lambda_put_event(command)
            logger.info("EventBridge event sent.")
