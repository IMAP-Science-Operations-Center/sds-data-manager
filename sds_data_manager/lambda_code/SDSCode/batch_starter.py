"""Functions for supporting the batch starter component of the architecture."""

import logging
import os
from datetime import datetime

import boto3
from imap_data_access import ScienceFilePath
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import database as db
from .database import models
from .lambda_custom_events import IMAPLambdaPutEvent

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def get_dependency(instrument, data_level, descriptor, direction, relationship):
    """Make query to dependency table to get dependency.

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
            dependency.append(
                {
                    "instrument": result.dependent_instrument,
                    "data_level": result.dependent_data_level,
                    "descriptor": result.dependent_descriptor,
                }
            )
    return dependency


def query_instrument(session, upstream_dependency, start_date, version):
    """Query to database to get the first FileCatalog record.

    Parameters
    ----------
    session : orm session
        Database session.
    upstream_dependency : dict
        Dictionary of upstream dependency.
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
    instrument = upstream_dependency["instrument"]
    data_level = upstream_dependency["data_level"]
    descriptor = upstream_dependency["descriptor"]

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
            models.FileCatalog.version == version,
            models.FileCatalog.descriptor == descriptor,
            models.FileCatalog.start_date == datetime.strptime(start_date, "%Y%m%d"),
        )
        .first()
    )

    return record


def query_downstream_dependencies(session, filename_components):
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
    downstream_dependents = get_dependency(
        instrument=filename_components["instrument"],
        data_level=filename_components["data_level"],
        descriptor=filename_components["descriptor"],
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


def query_upstream_dependencies(session, downstream_dependents):
    """Find dependency information for each instrument.

    This function looks for upstream dependency of current downstream
    dependents.

    Parameters
    ----------
    session : orm session
        Database session.
    downstream_dependents : list of dict
        Dictionary containing components with dates and versions appended.

    Returns
    -------
    instruments_to_process : list of dict
        A list of dictionaries containing the information needed for batch
        job command.
    """
    instruments_to_process = []

    # Iterate over each downstream dependent
    for dependent in downstream_dependents:
        instrument = dependent["instrument"]
        data_level = dependent["data_level"]
        descriptor = dependent["descriptor"]
        start_date = dependent["start_date"]
        version = dependent["version"]

        job_already_exist = is_job_in_status_table(
            instrument=instrument,
            data_level=data_level,
            descriptor=descriptor,
            start_date=start_date,
            version=version,
        )

        if job_already_exist:
            logger.info(
                f"Job already in progress for {instrument}, {data_level}, "
                f"{descriptor}, {start_date}, {version}"
            )
            continue

        # For each downstream dependent, find its upstream dependencies
        upstream_dependencies = get_dependency(
            instrument=instrument,
            data_level=data_level,
            descriptor=descriptor,
            direction="UPSTREAM",
            relationship="HARD",
        )

        all_dependencies_available = True  # Initialize the flag
        for upstream_dependency in upstream_dependencies:
            # Check to see if each upstream dependency is available
            record = query_instrument(session, upstream_dependency, start_date, version)
            if record is None:
                all_dependencies_available = (
                    False  # Set flag to false if any dependency is missing
                )
                logger.info(
                    f"Missing dependency: {upstream_dependency['instrument']}, "
                    f"{upstream_dependency['data_level']}, "
                    f"{upstream_dependency['descriptor']}, "
                    f"{version}"
                )
                logger.info(
                    f"Failed to kickoff this downstream dependent: {dependent}, "
                    "because it requires data for all these upstream "
                    f" dependencies: {upstream_dependencies}"
                )
                break  # Exit the loop early as we already found a missing dependency
            else:
                logger.info(
                    f"Dependency found: {upstream_dependency['instrument']}, "
                    f"{upstream_dependency['data_level']}, "
                    f"{upstream_dependency['descriptor']}, "
                    f"{version}"
                )
                # Add additional information to the upstream dependency
                additional_info = {
                    "start_date": start_date,
                    "version": version,
                }
                upstream_dependency.update(additional_info)

        # If all dependencies are available, prepare the data for batch job
        if all_dependencies_available:
            # Add it's information to the instrument to process list
            instruments_to_process.append(
                {
                    "instrument": instrument,
                    "data_level": data_level,
                    "descriptor": descriptor,
                    "start_date": start_date,
                    "version": version,
                    "upstream_dependencies": upstream_dependencies,
                }
            )

            logger.info(f"All dependencies for {instrument} present.")
            logger.info(
                f"For this downstream dependent: {dependent}, "
                "all these upstream dependencies data are available: "
                f"{upstream_dependencies}"
            )
        else:
            logger.info(f"Some dependencies for {instrument} are missing.")

    return instruments_to_process


def prepare_data(instrument, data_level, start_date, version, upstream_dependencies):
    """Prepare data for batch job.

    Parameters
    ----------
    instrument : str
        Instrument.
    data_level : str
        Data level.
    start_date : str
        Data start date.
    version : str
        version.
    upstream_dependencies : list of dict
        A list of dictionaries containing dependency instrument,
        data level, and version.

    Returns
    -------
    prepared_data : str
        Data to submit to batch job.
    """
    # Prepare batch job command
    # NOTE: Batch job expects command like this:
    # "Command": [
    #     "--instrument", "mag",
    #     "--data-level", "l1a",
    #     "--start-date", "20231212",
    #     "--version", "v001",
    #     "--dependency", """[
    #         {
    #             'instrument': 'swe',
    #             'data_level': 'l0',
    #             'descriptor': 'lveng-hk',
    #             'start_date': '20231212',
    #             'version': 'v001',
    #         },
    #         {
    #             'instrument': 'mag',
    #             'data_level': 'l0',
    #             'descriptor': 'lveng-hk',
    #             'start_date': '20231212',
    #             'version': 'v001',
    #         }]""",
    #    "--repointing", 1,
    #     "--upload-to-sdc"
    # ]
    prepared_data = [
        "--instrument",
        instrument,
        "--data-level",
        data_level,
        "--start-date",
        start_date,
        "--version",
        version,
        "--dependency",
        f"{upstream_dependencies}",
        "--upload-to-sdc",
    ]
    # TODO: Add repointing information to the command

    return prepared_data


def is_job_in_status_table(
    instrument: str, data_level: str, descriptor: str, start_date: str, version: str
):
    """Check if the job is already running.

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
    version : str
        Data version.

    Returns
    -------
    bool
        True if duplicate job is found, False otherwise.
    """
    # check in status tracking table if job is already in progress
    # for this instrument, data level, version, and descriptor
    with Session(db.get_engine()) as session:
        query = select(models.StatusTracking.__table__).where(
            models.StatusTracking.instrument == instrument,
            models.StatusTracking.data_level == data_level,
            models.StatusTracking.descriptor == descriptor,
            models.StatusTracking.start_date == datetime.strptime(start_date, "%Y%m%d"),
            models.StatusTracking.version == version,
            models.StatusTracking.status.in_(
                [models.Status.INPROGRESS.value, models.Status.SUCCEEDED.value]
            ),
        )

        results = session.execute(query).all()
        if results:
            return True
    return False


def send_lambda_put_event(instrument_to_process_data):
    r"""Send custom PutEvent to EventBridge.

    Example of what PutEvent looks like:

        event = {
            "DetailType": "Job Started",
            "Source": "imap.lambda",
            "Detail": {
            "detail": {
                "instrument": "swapi",
                "level": "l1",
                "descriptor": "sci",
                "start_date": "20230724",
                "version": "v001",
                "status": "INPROGRESS",
                "dependency": json.dumps([
                    {
                        "instrument": "swapi",
                        "level": "l0",
                        "descriptor": "sci",
                        "start_date": "20230724",
                        "version": "v001"
                    }]),
            }
        }

    Parameters
    ----------
    instrument_to_process_data : dict
        Example of input:
            {
            "instrument": "mag",
            "data_level": "l1a",
            "descriptor": "norm-mago",
            "start_date": "20231212",
            "version": "v001",
            "upstream_dependencies": [
                {
                    "instrument": "swe",
                    "data_level": "l0",
                    "descriptor": "lveng-hk",
                    "start_date": "20231212",
                    "version": "v001"
                },
                {
                    "instrument": "mag",
                    "data_level": "l0",
                    "descriptor": "lveng-hk",
                    "start_date": "20231212",
                    "version": "v001"
            }
        ]

    Returns
    -------
    dict
        EventBridge response
    """
    event_client = boto3.client("events")

    # Get event inputs ready
    instrument = instrument_to_process_data["instrument"]
    data_level = instrument_to_process_data["data_level"]
    descriptor = instrument_to_process_data["descriptor"]
    start_date = instrument_to_process_data["start_date"]
    version = instrument_to_process_data["version"]
    dependency = instrument_to_process_data["upstream_dependencies"]

    # Create event["detail"] information
    detail = {
        "status": models.Status.INPROGRESS.name,
        "instrument": instrument,
        "data_level": data_level,
        "descriptor": descriptor,
        "start_date": start_date,
        "version": version,
        "dependency": f"{dependency}",
    }

    # create PutEvent dictionary
    event = IMAPLambdaPutEvent(detail_type="Job Started", detail=detail)
    event_data = event.to_event()

    logger.info(f"Sending event to EventBridge - {event_data}")
    # Send event to EventBridge
    response = event_client.put_events(Entries=[event_data])
    return response


def lambda_handler(event: dict, context):
    """Lambda handler."""
    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    # Event details:
    filename = event["detail"]["object"]["key"]
    components = ScienceFilePath.extract_filename_components(filename)
    logger.info(f"Parsed filename - {components}")
    instrument = components["instrument"]

    # Get information for the batch job.
    region = os.getenv("REGION")
    account = os.getenv("ACCOUNT")
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
        # Downstream dependents are the instruments that
        # depend on the current instrument.
        downstream_dependents = query_downstream_dependencies(session, components)

        # Check if every downstream dependents
        # have all upstream dependencies. This helps to determine if
        # we can start the batch job.
        downstream_instruments_to_process = query_upstream_dependencies(
            session, downstream_dependents
        )

        # No instruments to process
        if not downstream_instruments_to_process:
            logger.info("No instruments_to_process. Skipping further processing.")
            return

        # Start Batch Job execution for those that has all dependencies
        for downstream_data in downstream_instruments_to_process:
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
                downstream_data["instrument"],
                "--data-level",
                downstream_data["data_level"],
                "--descriptor",
                downstream_data["descriptor"],
                "--start-date",
                downstream_data["start_date"],
                "--version",
                downstream_data["version"],
                "--dependency",
                f"{downstream_data['upstream_dependencies']}",
                "--upload-to-sdc",
            ]
            logger.info(f"Submitting job with this command - {batch_command}")
            # NOTE: The batch job name should contain only
            # alphanumeric characters and hyphens.
            level_to_process = downstream_data["data_level"]
            # Eg. "codice-l1a-job"
            job_name = (
                f"{downstream_data['instrument']}-{level_to_process}-"
                f"{downstream_data['descriptor']}-job"
            )
            logger.info("Job name: %s", job_name)
            response = batch_client.submit_job(
                jobName=job_name,
                jobQueue=job_queue,
                jobDefinition=job_definition,
                containerOverrides={
                    "command": batch_command,
                },
            )
            logger.info(f"Submitted job - {response}")
            # Send EventBridge event to indexer lambda
            logger.info("Sending EventBridge event to indexer lambda.")
            send_lambda_put_event(downstream_data)
            logger.info("EventBridge event sent.")
