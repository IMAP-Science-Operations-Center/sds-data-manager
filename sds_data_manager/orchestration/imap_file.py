"""Class for handling science files on the SDS that are not created by AWS Batch."""

import datetime
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
from sqlalchemy import select

from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.orchestration import config, dagster_utilities
from sds_data_manager.orchestration.types import DependencyNode


class IMAPScienceFileHandler:
    """Handle IMAP files that have no associated jobs."""

    # Whether this node's materialization is handled by the single, consolidated
    # science file materialization sensor built by build_materialization_sensor().
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
            .all()[0]
        )
        if not repoint.pointing_start_utc or not repoint.pointing_end_utc:
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


def build_materialization_sensor(
    targets: list[tuple[DependencyNode, DynamicPartitionsDefinition]],
):
    """Build a single sensor that materializes all runless-materialized IMAP assets.

    This replaces having one sensor per science file node. It scans the
    ScienceFiles table for newly ingested files and maps each one back to its
    Dagster asset key and partitions definition, then performs a runless
    materialization for it. This covers both file-only assets (e.g. raw L0
    files) and Batch job outputs, since job ops no longer materialize their
    own outputs.

    Parameters
    ----------
    targets : list[tuple[DependencyNode, DynamicPartitionsDefinition]]
        Every (node, partitions_def) pair whose materialization should be
        handled by this sensor.
    """
    node_lookup = {
        (node.source, node.data_type, node.descriptor): (
            node.to_dagster_asset(),
            partitions_def,
        )
        for node, partitions_def in targets
    }

    @sensor(
        name="science_file_materialization_sensor",
        asset_selection=AssetSelection.all(),
        minimum_interval_seconds=300,
    )
    def _materialization_sensor(context: SensorEvaluationContext):
        materializations = []

        if context.cursor:
            latest_ingestion_date = datetime.datetime.fromisoformat(
                context.cursor
            ).replace(tzinfo=datetime.timezone.utc)
        else:
            latest_ingestion_date = datetime.datetime.fromisoformat(
                config.MISSION_START_TIME
            ).replace(tzinfo=datetime.timezone.utc)

        stmt = (
            select(models.ScienceFiles)
            .filter(models.ScienceFiles.ingestion_date >= latest_ingestion_date)
            # Define the unique group
            .distinct(
                models.ScienceFiles.instrument,
                models.ScienceFiles.data_level,
                models.ScienceFiles.descriptor,
                models.ScienceFiles.start_date,
                models.ScienceFiles.repointing,
            )
            # Order by the group, then by version descending to put the highest at
            # the top
            .order_by(
                models.ScienceFiles.instrument,
                models.ScienceFiles.data_level,
                models.ScienceFiles.descriptor,
                models.ScienceFiles.start_date,
                models.ScienceFiles.repointing,
                models.ScienceFiles.major_version.desc(),
                models.ScienceFiles.minor_version.desc(),
            )
        )

        with db.Session() as session:
            recent_db_records = session.scalars(stmt).all()

            for record in recent_db_records:
                latest_ingestion_date = max(
                    latest_ingestion_date, record.ingestion_date
                )

                target = node_lookup.get(
                    (record.instrument, record.data_level, record.descriptor)
                )
                if target is None:
                    # Not a Dagster-tracked asset (e.g. produced outside this sensor).
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
                    materialization = dagster_utilities.get_materialization(
                        context,
                        asset_key,
                        partition,
                        [os.path.basename(record.file_path)],
                        Version(record.major_version, record.minor_version),
                        "science",
                    )
                    if materialization:
                        context.log.info(f"{record.file_path} will be materialized.")
                        materializations.append(materialization)

        return SensorResult(
            asset_events=materializations, cursor=latest_ingestion_date.isoformat()
        )

    return _materialization_sensor
