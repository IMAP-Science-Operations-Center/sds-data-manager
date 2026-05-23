import datetime 
from dagster import (
    AssetSelection,
    SensorEvaluationContext,
    SensorResult,
    sensor,
    DefaultSensorStatus
)
from sds_data_manager.lambda_code.SDSCode.database import database as db, models
from orchestration import custom_partitions, dagster_utilities

import logging
from contextlib import nullcontext

from os.path import basename
from sqlalchemy import desc

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
MISSION_START_TIME = "2025-09-17T00:00:00"

def get_latest_repoint_file(
    end_date: datetime,
    session: db.Session = None,
) -> str | None:
    """Get latest repoint file.

    Query for the latest repoint file for given end_date.

    Parameters
    ----------
    end_date : datetime
        End date to find dependent files with.
    session : db.Session, optional
        Database session. If not provided, a new session will be created.

    Returns
    -------
    str
        Latest repoint file name.
    """

    def _query_latest_repoint(sess):
        return (
            sess.query(models.RepointFiles)
            .order_by(desc(models.RepointFiles.file_path))
            .first()
        )

    if session is not None:
        latest_repoint_file = _query_latest_repoint(session)
    else:
        with db.Session() as new_session:
            latest_repoint_file = _query_latest_repoint(new_session)

    if not latest_repoint_file:
        raise ValueError("No Repoint file found in the database.")

    if latest_repoint_file.end_date.replace(tzinfo=datetime.timezone.utc) < end_date.replace(tzinfo=datetime.timezone.utc):
        logger.info(
            f"Latest repoint file end date {latest_repoint_file.end_date} "
            f"is before input end date {end_date}"
        )
        return None

    return basename(latest_repoint_file.file_path)

# ruff: noqa: PLR0915, PLR0912, PLR0911
def get_upstream_dependency_inputs_repoint(
    start_date: datetime,
    end_date: datetime,
    open_session: db.Session = None,
):
    """Construct a ProcessingInputCollection of dependency files.

    Parameters
    ----------
        dependency in the query parameters.
    start_date : datetime
        Start date to find dependent files with.
    end_date : datetime
        End date to find dependent files with.
    open_session : db.Session, optional
        Database session. If not provided, a new session will be created.

    Returns
    -------
    ProcessingInputCollection
        Dependency files that can include Ancillary, SPICE, or Science inputs.
    """
    # Use provided session or create a new one
    session_context = nullcontext(open_session) if open_session else db.Session()
    with session_context as session:
        latest_repoint_file = get_latest_repoint_file(end_date, session)
        if latest_repoint_file is None:
            logger.info(f"No repoint file found for {start_date} to {end_date}")
            return None
        logger.info(
            f"Found repoint file: {latest_repoint_file}."
        )

    return [latest_repoint_file]

@sensor(asset_selection=AssetSelection.all(),
        minimum_interval_seconds=600,
        default_status=DefaultSensorStatus.RUNNING)
def repoint_file_sensor(context: SensorEvaluationContext):

    # 1. Handle the Cursor
    cursor_str = context.cursor or MISSION_START_TIME
    cursor_date = datetime.datetime.fromisoformat(cursor_str).replace(tzinfo=datetime.timezone.utc)
    
    # Track the latest ingestion date to update the cursor at the end
    latest_ingestion_date = cursor_date
    
    # A unique suffix is added to the run_key so Dagster allows this partition 
    # to be run *again* if a different file updates the same timeframe next week.
    cursor_suffix = latest_ingestion_date.timestamp()

    # 2. Query for new files
    if (datetime.datetime.now((datetime.timezone.utc)) - cursor_date) > datetime.timedelta(days=7):
        min_dt = cursor_date
        max_dt = cursor_date + datetime.timedelta(days=7)                           
        latest_ingestion_date = max_dt
        for run in dagster_utilities.run_all_affected_partitions(context, "repoint_file_", min_dt, max_dt, cursor_suffix):
            yield run
    else:
        with db.Session() as session:
            # Get new repoint files
            new_files = session.query(models.RepointFiles).filter(models.RepointFiles.ingestion_date > cursor_date).all()
            
            if not new_files:
                yield SensorResult(skip_reason="No new repoint files ingested.")

            for file in new_files:
                # Advance cursor date marker
                if file.ingestion_date > latest_ingestion_date:
                    latest_ingestion_date = file.ingestion_date

                min_dt = datetime.datetime.fromisoformat(MISSION_START_TIME).replace(tzinfo=datetime.timezone.utc)
                max_dt = file.end_date.replace(tzinfo=datetime.timezone.utc)
                
                for run in dagster_utilities.run_all_affected_partitions(context, "repoint_file_", min_dt, max_dt, cursor_suffix):
                    yield run

    context.update_cursor(latest_ingestion_date.isoformat())

sensors = [repoint_file_sensor]