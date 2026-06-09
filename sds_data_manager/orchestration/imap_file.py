"""Class for handling science files on the SDS that are not created by AWS Batch."""

import datetime
import os

from dagster import (
    AssetSelection,
    AssetSpec,
    SensorEvaluationContext,
    SensorResult,
    sensor,
)
from sqlalchemy import select

from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.orchestration import config, dagster_utilities
from sds_data_manager.orchestration.types import DependencyNode


class IMAPScienceFileHandler:
    """Handle IMAP files that have no associated jobs."""

    def __init__(self, node: DependencyNode, partition):
        """Initialize the Handler."""
        self.job_config = node
        self.partitions_def = partition

    def build_asset(self):
        """Return an AssetSpec representing the IMAP file."""
        return AssetSpec(
            key=self.job_config.to_dagster_asset(), partitions_def=self.partitions_def
        )

    def build_sensor(self):
        """Create an asset representing an IMAP science file NOT created from Batch."""
        sensor_name = f"{self.job_config.to_dagster_name()}_sensor"

        @sensor(
            name=sensor_name,
            asset_selection=AssetSelection.all(),
            minimum_interval_seconds=300,
        )
        def _file_sensor(context: SensorEvaluationContext):

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
                .filter(
                    models.ScienceFiles.ingestion_date >= latest_ingestion_date,
                    models.ScienceFiles.instrument == self.job_config.source,
                    models.ScienceFiles.data_level == self.job_config.data_type,
                    models.ScienceFiles.descriptor == self.job_config.descriptor,
                )
                # Define the unique group
                .distinct(
                    models.ScienceFiles.start_date,
                    models.ScienceFiles.repointing,
                )
                # Order by the group, then by version descending to put the highest at
                # the top
                .order_by(
                    models.ScienceFiles.start_date,
                    models.ScienceFiles.repointing,
                    models.ScienceFiles.version.desc(),
                )
            )

            with db.Session() as session:
                recent_db_records = session.scalars(stmt).all()

                for record in recent_db_records:
                    latest_ingestion_date = max(
                        latest_ingestion_date, record.ingestion_date
                    )
                    context.log.info(f"Analyzing file: {record.file_path}")
                    if self.partitions_def.name == "repoint_partitions":
                        # We need to only materialize the repoint that this is in
                        repoint = (
                            session.query(models.PointingTable)
                            .filter(
                                models.PointingTable.pointing_id == record.repointing
                            )
                            .all()[0]
                        )
                        if (
                            not repoint.pointing_start_utc
                            or not repoint.pointing_end_utc
                        ):
                            continue
                        affected_partitions = [
                            "repoint"
                            + str(repoint.pointing_id)
                            + "_"
                            + repoint.pointing_start_utc.strftime("%Y-%m-%dT%H:%M:%S")
                            + "_to_"
                            + repoint.pointing_end_utc.strftime("%Y-%m-%dT%H:%M:%S")
                        ]
                    else:
                        # For any other type of science file,
                        # we need to materialize the partition
                        # that contains the start_date
                        affected_partitions = dagster_utilities.get_affected_partitions(
                            context,
                            self.partitions_def,
                            record.start_date,
                            record.start_date,
                        )

                    for partition in affected_partitions:
                        context.log.info(
                            f"""The following partition was
                            identified as affected: {partition}"""
                        )
                        materialization = dagster_utilities.get_materialization(
                            context,
                            self.job_config.to_dagster_asset(),
                            partition,
                            [os.path.basename(record.file_path)],
                            str(int(record.version[1:])),
                            "science",
                        )
                        if materialization:
                            context.log.info(
                                f"{record.file_path} will be materialized."
                            )
                            materializations.append(materialization)

            return SensorResult(
                asset_events=materializations, cursor=latest_ingestion_date.isoformat()
            )

        return _file_sensor
