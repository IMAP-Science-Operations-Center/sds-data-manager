"""Class for handling science files on the SDS that are not created by AWS Batch."""

import datetime
import json
import os

from dagster import (
    AssetSelection,
    AssetSpec,
    DynamicPartitionsDefinition,
    SensorEvaluationContext,
    SensorResult,
    sensor,
)
from imap_data_access.file_validation import Version
from sqlalchemy import and_, or_, select

from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.orchestration import config, dagster_utilities
from sds_data_manager.orchestration.types import DependencyNode


class IMAPScienceFileHandler:
    """Handle IMAP files that have no associated jobs."""

    # Whether this node's materialization is handled by the shared science file
    # materialization sensor built by build_materialization_sensor().
    # Subclasses that materialize themselves (e.g. IDEXL0FileHandler) should set
    # this to False and provide their own build_sensor().
    USE_COMMON_SENSOR = True

    def __init__(self, node: DependencyNode, partition):
        """Initialize the Handler."""
        self.job_config = node
        self.partitions_def = partition

    def build_asset(self):
        """Return an AssetSpec representing the IMAP file."""
        return AssetSpec(
            key=self.job_config.to_dagster_asset(), partitions_def=self.partitions_def
        )


def _get_affected_partitions(context, session, record, partitions_def):
    """Return the partition keys affected by a newly ingested ScienceFiles record."""
    if partitions_def.name == "repoint_partitions":
        # We need to only materialize the repoint that this is in
        repoint = (
            session.query(models.PointingTable)
            .filter(models.PointingTable.pointing_id == record.repointing)
            .first()
        )
        if (
            repoint is None
            or not repoint.pointing_start_utc
            or not repoint.pointing_end_utc
        ):
            return []
        return [
            "repoint"
            + str(repoint.pointing_id)
            + "_"
            + repoint.pointing_start_utc.strftime("%Y-%m-%dT%H:%M:%S")
            + "_to_"
            + repoint.pointing_end_utc.strftime("%Y-%m-%dT%H:%M:%S")
        ]
    # For any other type of science file, we need to materialize the partition
    # that contains the start_date
    return dagster_utilities.get_affected_partitions(
        context, partitions_def, record.start_date, record.start_date
    )


def _parse_cursor(cursor: str | None) -> tuple[datetime.datetime, str]:
    """Return the (ingestion_date, file_path) of the last record a sensor processed.

    The cursor is a JSON object so that ties on ingestion_date (S3 LastModified
    only has one-second resolution) can be broken by file_path. A bare ISO
    timestamp is also accepted for cursors written by older versions of the sensor.
    """
    if not cursor:
        return (
            datetime.datetime.fromisoformat(config.MISSION_START_TIME).replace(
                tzinfo=datetime.timezone.utc
            ),
            "",
        )
    try:
        parsed = json.loads(cursor)
        ingestion_date, file_path = parsed["ingestion_date"], parsed["file_path"]
    except (json.JSONDecodeError, TypeError, KeyError):
        ingestion_date, file_path = cursor, ""
    ingestion_date = datetime.datetime.fromisoformat(ingestion_date)
    if ingestion_date.tzinfo is None:
        ingestion_date = ingestion_date.replace(tzinfo=datetime.timezone.utc)
    return ingestion_date, file_path


def build_materialization_sensor(
    targets: list[tuple[DependencyNode, DynamicPartitionsDefinition]],
    name: str,
    materialized_by: str,
    min_age: datetime.timedelta = datetime.timedelta(0),
    max_records_per_tick: int = 500,
    is_backup: bool = False,
):
    """Build a sensor that runlessly materializes IMAP assets from ScienceFiles.

    The sensor scans the ScienceFiles table for newly ingested files, maps each
    one back to its Dagster asset key and partitions definition, and reports a
    runless AssetMaterialization for it unless an equal or newer materialization
    already exists.

    It is used in two ways (see imap_dagster.py):

    - As the primary materializer for file-only assets (e.g. raw L0 files) that
      have no processing job. A short ``min_age`` gives the indexer time to commit
      rows whose ingestion_date (the S3 LastModified time) is slightly earlier
      than a row already committed, so the cursor does not move past them.
    - As a backup for processing job outputs. The job op materializes its own
      outputs (with the ``inputs`` that produced them), so the backup sensor only
      considers files older than ``min_age`` to give the op time to do so first.
      Anything it does materialize was missed by the op, so it logs a warning.

    Parameters
    ----------
    targets : list[tuple[DependencyNode, DynamicPartitionsDefinition]]
        Every (node, partitions_def) pair whose materialization should be
        handled by this sensor.
    name : str
        Name of the sensor. Changing it resets the sensor's cursor.
    materialized_by : str
        Value of the ``materialized_by`` metadata on each materialization, so the
        source of a materialization can be identified in the Dagster UI.
    min_age : datetime.timedelta
        Only consider files whose ingestion_date is at least this old.
    max_records_per_tick : int
        Maximum number of ScienceFiles rows to process per evaluation. The cursor
        only advances past the rows that were processed, so a large backlog (e.g.
        the first tick with no cursor) is worked through over several ticks rather
        than timing out.
    is_backup : bool
        Whether this sensor is a backup for another materialization path. If so,
        every materialization it reports is logged as a warning.
    """
    node_lookup = {
        (node.source, node.data_type, node.descriptor): (
            node.to_dagster_asset(),
            partitions_def,
        )
        for node, partitions_def in targets
    }

    @sensor(
        name=name,
        asset_selection=AssetSelection.all(),
        minimum_interval_seconds=300,
    )
    def _materialization_sensor(context: SensorEvaluationContext):
        materializations = []
        last_ingestion_date, last_file_path = _parse_cursor(context.cursor)
        cutoff = datetime.datetime.now(datetime.timezone.utc) - min_age

        # Keyset pagination on (ingestion_date, file_path), so the cursor can
        # stop part way through rows that share an ingestion_date.
        stmt = (
            select(models.ScienceFiles)
            .filter(
                models.ScienceFiles.ingestion_date < cutoff,
                or_(
                    models.ScienceFiles.ingestion_date > last_ingestion_date,
                    and_(
                        models.ScienceFiles.ingestion_date == last_ingestion_date,
                        models.ScienceFiles.file_path > last_file_path,
                    ),
                ),
            )
            .order_by(models.ScienceFiles.ingestion_date, models.ScienceFiles.file_path)
            .limit(max_records_per_tick)
        )

        with db.Session() as session:
            recent_db_records = session.scalars(stmt).all()
            if recent_db_records:
                last_ingestion_date = recent_db_records[-1].ingestion_date
                last_file_path = recent_db_records[-1].file_path

            # Only materialize the highest version of each logical file in this
            # batch. Older versions seen in a later batch are rejected by
            # get_materialization's version check.
            latest_records = {}
            for record in recent_db_records:
                key = (
                    record.instrument,
                    record.data_level,
                    record.descriptor,
                    record.start_date,
                    record.repointing,
                )
                current = latest_records.get(key)
                if current is None or Version(
                    record.major_version, record.minor_version
                ) > Version(current.major_version, current.minor_version):
                    latest_records[key] = record

            for record in latest_records.values():
                target = node_lookup.get(
                    (record.instrument, record.data_level, record.descriptor)
                )
                if target is None:
                    # Not an asset this sensor is responsible for.
                    continue
                asset_key, partitions_def = target

                context.log.info(f"Analyzing file: {record.file_path}")
                affected_partitions = _get_affected_partitions(
                    context, session, record, partitions_def
                )

                for partition in affected_partitions:
                    context.log.info(
                        f"""The following partition was
                        identified as affected: {partition}"""
                    )
                    # If the job op materializes this file at the same moment,
                    # both may pass this check and Dagster records two identical
                    # materializations. That is harmless: downstream job
                    # submission is deduplicated by the processing job table.
                    materialization = dagster_utilities.get_materialization(
                        context,
                        asset_key,
                        partition,
                        [os.path.basename(record.file_path)],
                        Version(record.major_version, record.minor_version),
                        "science",
                        extra_metadata={"materialized_by": materialized_by},
                    )
                    if materialization:
                        if is_backup:
                            context.log.warning(
                                f"{record.file_path} was not materialized by its "
                                "processing job; materializing it from the backup "
                                "sensor without input metadata."
                            )
                        else:
                            context.log.info(
                                f"{record.file_path} will be materialized."
                            )
                        materializations.append(materialization)

        return SensorResult(
            asset_events=materializations,
            cursor=json.dumps(
                {
                    "ingestion_date": last_ingestion_date.isoformat(),
                    "file_path": last_file_path,
                }
            ),
        )

    return _materialization_sensor
