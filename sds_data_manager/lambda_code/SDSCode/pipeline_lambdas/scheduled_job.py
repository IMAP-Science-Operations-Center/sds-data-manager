"""Functions for triggering a processing job on a schedule."""

import datetime as dt
import logging

from imap_data_access import ProcessingInputCollection, RepointInput

from ..database import database as db
from . import (
    batch_starter,
    dependency,
)
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
        batch_starter.try_to_submit_job(
            session,
            job,
            "20000101",
            "v001",
            processing_input_collection.serialize(),
        )


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
