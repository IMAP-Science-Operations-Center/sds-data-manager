"""Sensor for materializing any files missed by the Assets."""

import datetime
import json
import os

from dagster import (
    AssetKey,
    AssetSelection,
    SensorEvaluationContext,
    SensorResult,
    sensor,
)
from imap_data_access.file_validation import Version
from sqlalchemy import and_, or_, select

from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.orchestration import config, dagster_utilities

MAX_RECORDS_PER_TICK = 500


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


@sensor(
    name="job_output_backup_materialization_sensor",
    asset_selection=AssetSelection.all(),
    minimum_interval_seconds=300,
)
def backup_sensor(context: SensorEvaluationContext):
    """Sensor that runlessly materializes IMAP assets from ScienceFiles.

    The sensor serves as a "backup" for the primary methods of materializing
    assets. It scans the ScienceFiles table for newly ingested files, maps each
    one back to its Dagster asset key and partitions definition, and reports a
    runless AssetMaterialization for it unless an equal or newer materialization
    already exists.

    To give the other sensors and assets time to materialize first, this sensor
    only considers files older than ``min_age``. If a file is older than ``min_age``
    but never materialized, it was very likely missed by the other assets, and
    should be materialized.
    """
    from sds_data_manager.orchestration.imap_dagster import defs  # noqa: PLC0415

    materializations = []
    last_ingestion_date, last_file_path = _parse_cursor(context.cursor)
    cutoff = (
        datetime.datetime.now(datetime.timezone.utc)
        - config.BACKUP_MATERIALIZATION_MIN_AGE
    )

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
        .limit(MAX_RECORDS_PER_TICK)
    )

    with db.Session() as session:
        recent_db_records = session.scalars(stmt).all()
        if recent_db_records:
            last_ingestion_date = recent_db_records[-1].ingestion_date
            last_file_path = recent_db_records[-1].file_path

        # Only materialize the highest version of each logical file in this
        # batch. Older versions seen in a later batch are rejected by
        # get_materialization's version check, so this saves time.
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
            target = AssetKey(
                (
                    record.instrument
                    + "_"
                    + record.data_level
                    + "_"
                    + record.descriptor
                ).replace("-", "")
            )
            asset_graph = defs.get_repository_def().asset_graph
            try:
                partitions_def = asset_graph.get(target).partitions_def
                context.log.info(f"Analyzing file: {record.file_path}")
            except KeyError:
                context.log.info(f"No suitable assets found for: {record.file_path}")
                continue

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
                    target,
                    partition,
                    [os.path.basename(record.file_path)],
                    Version(record.major_version, record.minor_version),
                    "science",
                    extra_metadata={"materialized_by": "backup_sensor"},
                )
                if materialization:
                    context.log.warning(
                        f"{record.file_path} was not materialized by its "
                        "processing job; materializing it from the backup "
                        "sensor without input metadata."
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


sensors = [backup_sensor]
