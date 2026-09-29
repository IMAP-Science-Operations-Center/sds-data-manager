"""Sensor for materializing any files missed by the Assets."""

import datetime
import json
import os

from dagster import (
    AssetKey,
    AssetSelection,
    DagsterEventType,
    EventRecordsFilter,
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


def _is_safe_to_backfill(context, asset_key, partition, file_name) -> bool:
    """Return True if a backup materialization can't clobber a wanted one.

    The latest materialization of a partition is what downstream jobs read their
    inputs from, so the backup sensor only writes one when the partition has never
    been materialized, or when the latest materialization is exactly one file with
    the same name (ignoring version) as ``file_name``. Partitions that hold several
    files (e.g. ``idex_l0_raw``) or a different file are left alone.

    Version ordering is still enforced afterwards by ``get_materialization``.
    """
    records = context.instance.get_event_records(
        EventRecordsFilter(
            asset_key=asset_key,
            asset_partitions=[partition],
            event_type=DagsterEventType.ASSET_MATERIALIZATION,
        ),
        limit=1,
    )
    if not records:
        return True
    last_files = records[0].asset_materialization.metadata.get("file_names")
    last_files = last_files.value if last_files else []
    if isinstance(last_files, str):
        last_files = [last_files]
    # "imap_glows_l1a_de_20260102_v001.0001.cdf" -> "imap_glows_l1a_de_20260102"
    base = file_name.rsplit("_", 1)[0]
    return len(last_files) == 1 and last_files[0].rsplit("_", 1)[0] == base


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

    Files older than ``max_age`` are never considered, so a reset cursor or
    Dagster instance does not re-materialize the whole history. The sensor also
    never overwrites a materialization unless it is an older version of the same
    single file (see ``_is_safe_to_backfill``).
    """
    from sds_data_manager.orchestration.imap_dagster import defs  # noqa: PLC0415

    materializations = []
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - config.BACKUP_MATERIALIZATION_MIN_AGE
    window_start = now - config.BACKUP_MATERIALIZATION_MAX_AGE
    last_ingestion_date, last_file_path = _parse_cursor(context.cursor)
    if last_ingestion_date < window_start:
        last_ingestion_date, last_file_path = window_start, ""

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

            file_name = os.path.basename(record.file_path)
            for partition in affected_partitions:
                context.log.info(
                    f"""The following partition was
                    identified as affected: {partition}"""
                )
                if not _is_safe_to_backfill(context, target, partition, file_name):
                    context.log.info(
                        f"Partition {partition} of {target.to_user_string()} holds "
                        f"other files; not overwriting it with {file_name}."
                    )
                    continue
                # If the job op materializes this file at the same moment,
                # both may pass this check and Dagster records two identical
                # materializations. That is harmless: downstream job
                # submission is deduplicated by the processing job table.
                materialization = dagster_utilities.get_materialization(
                    context,
                    target,
                    partition,
                    [file_name],
                    Version(record.major_version, record.minor_version),
                    "science",
                    extra_metadata={"materialized_by": "backup_sensor"},
                )
                if materialization:
                    context.log.warning(
                        f"{record.file_path} was not materialized by its "
                        "processing job or other sensor; "
                        "materializing it from the backup "
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
