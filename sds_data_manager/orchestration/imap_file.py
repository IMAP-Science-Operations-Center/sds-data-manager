"""IMAP file handler for files on the SDS without associated jobs"""
import datetime
import os
import json
from dagster import (
    AssetExecutionContext,
    Failure,
    AssetSelection,
    AssetKey,
    SensorEvaluationContext,
    sensor,
    DefaultSensorStatus,
    SensorResult,
    AssetSpec,
    DynamicPartitionsDefinition,
)
from sqlalchemy import select
from sds_data_manager.orchestration import dagster_utilities
from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.orchestration.types import DependencyNode

from sqlalchemy import select, func
from sqlalchemy.orm import aliased

MISSION_START_TIME = "2026-04-01T00:00:00"

class IMAPScienceFileHandler:
    """Handle IMAP files that have no associated jobs."""

    def __init__(self, 
                 node: DependencyNode, 
                 partition):
        self.job_config = node
        self.partitions_def = partition
        
    def build_asset(self):
        return AssetSpec(key=self.job_config.to_dagster_asset(), partitions_def=self.partitions_def)
    
    def build_sensor(self):
        sensor_name = f"{self.job_config.to_dagster_asset().to_user_string()}_sensor"
        @sensor(name=sensor_name,
                asset_selection=AssetSelection.all(),
                default_status=DefaultSensorStatus.RUNNING,
                minimum_interval_seconds=300)
        def _file_sensor(context: SensorEvaluationContext):
            
            if context.cursor:
                start_dt = datetime.datetime.fromisoformat(context.cursor).replace(tzinfo=datetime.timezone.utc)
            else: 
                start_dt = datetime.datetime.fromisoformat(MISSION_START_TIME).replace(tzinfo=datetime.timezone.utc)

            stmt = (
                select(models.ScienceFiles)
                .filter(models.ScienceFiles.ingestion_date >= start_dt,
                        models.ScienceFiles.instrument==self.job_config.source,
                        models.ScienceFiles.data_level==self.job_config.data_type,
                        models.ScienceFiles.descriptor==self.job_config.descriptor)
                # Define the unique group
                .distinct(
                    models.ScienceFiles.instrument,
                    models.ScienceFiles.data_level,
                    models.ScienceFiles.descriptor,
                    models.ScienceFiles.repointing,
                )
                # Order by the group, then by version descending to put the highest at the top
                .order_by(
                    models.ScienceFiles.instrument,
                    models.ScienceFiles.data_level,
                    models.ScienceFiles.descriptor,
                    models.ScienceFiles.repointing,
                    models.ScienceFiles.version.desc()
                )
            )
            with db.Session() as session:
                recent_db_records = session.scalars(stmt).all()

                for record in recent_db_records:
                    asset_graph = context.repository_def.asset_graph
                    
                    partitions_def = asset_graph.get(self.job_config.to_dagster_asset()).partitions_def
                    
                    if not partitions_def:
                        continue
                    if partitions_def.name == 'repoint_partitions':
                        # We need to only materialize the repoint that this is a part of
                        with db.Session() as session:
                            repoint = session.query(models.PointingTable).filter(models.PointingTable.pointing_id==record.repointing).all()[0]
                            if not repoint.pointing_start_utc or not repoint.pointing_end_utc:
                                continue
                            affected_partitions = ["repoint" + str(repoint.pointing_id) + "_" +repoint.pointing_start_utc.strftime("%Y-%m-%dT%H:%M:%S") + "_to_" + repoint.pointing_end_utc.strftime("%Y-%m-%dT%H:%M:%S")]
                    else:
                        # For any other type of science file, we need to materialize the partition that contains the start_date
                        affected_partitions = dagster_utilities.get_affected_partitions(context, partitions_def, record.start_date, record.start_date)
                    
                    for partition in affected_partitions:
                        materialization = dagster_utilities.get_materialization(context,
                                                                                self.job_config.to_dagster_asset(),
                                                                                partition,
                                                                                [os.path.basename(record.file_path)],
                                                                                str(int(record.version[1:])),
                                                                                "science")
                        if materialization:
                            yield materialization

            context.update_cursor(start_dt.isoformat())

        return _file_sensor

class IMAPAncillaryFileHandler:
    """Handle IMAP job dependencies and submission."""

    def __init__(self, node: DependencyNode):
        self.job_config = node
        self.partition_type = self.job_config.to_dagster_asset().to_user_string().replace("_ancillary_", "") 
        self.partition_name = self.partition_type + "_partitions"
        self.partitions_def = DynamicPartitionsDefinition(name=self.partition_name)

    def build_asset(self):
        return AssetSpec(key=self.job_config.to_dagster_asset(), partitions_def=self.partitions_def)
    
    def build_sensor(self):
        sensor_name = f"{self.job_config.to_dagster_asset().to_user_string()}_sensor"
        @sensor(name=sensor_name,
                asset_selection=AssetSelection.all(),
                default_status=DefaultSensorStatus.RUNNING,
                minimum_interval_seconds=600)
        def _file_sensor(context: AssetExecutionContext):
            row_num = func.row_number().over(
                            partition_by=(models.AncillaryFiles.start_date, models.AncillaryFiles.end_date),
                            order_by=models.AncillaryFiles.version.desc()
                        ).label('rn')
            # 2. Create a subquery that applies your filters and appends the row number
            subq = select(models.AncillaryFiles, row_num).where(
                models.AncillaryFiles.instrument == self.job_config.source,
                models.AncillaryFiles.descriptor == self.job_config.descriptor
            ).subquery()

            # 3. Alias your model to the subquery so SQLAlchemy knows how to map it back to Python objects
            LatestFile = aliased(models.AncillaryFiles, subq)

            # 4. Execute the final query, grabbing only the rows where the row number is 1
            stmt = select(LatestFile).where(subq.c.rn == 1)

            materializations = []
            with db.Session() as session:
                ancillary_files = session.scalars(stmt).all()
                if not ancillary_files:
                    raise Failure(description="Processing failed: No data found")
                for file in ancillary_files:
                    context.log.info(f"Retrieving the following file: {file.file_path}")
                    start_date = file.start_date
                    end_date = file.end_date
                    if not start_date:
                        start_date_str = datetime.datetime(2025,9,17).replace(tzinfo=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
                    else:
                        start_date_str = start_date.strftime("%Y-%m-%dT%H:%M:%S")
                    if not end_date:
                        end_date_str = datetime.datetime(2045,9,17).replace(tzinfo=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
                    else:
                        end_date_str = end_date.strftime("%Y-%m-%dT%H:%M:%S")

                    partition_key = self.partition_type + '_' + start_date_str + '_to_' + end_date_str
                    existing_partitions = context.instance.get_dynamic_partitions(self.partition_name)
                    if partition_key not in existing_partitions:
                        context.log.info(f"Creating the following partition key: {partition_key}")
                        context.instance.add_dynamic_partitions(
                                                                partitions_def_name=self.partition_name,
                                                                partition_keys=[partition_key]
                                                            )

                    materialization = dagster_utilities.get_materialization(context,
                                                                            self.job_config.to_dagster_asset(),
                                                                            partition_key,
                                                                            [os.path.basename(file.file_path)],
                                                                            str(int(file.version[1:])),
                                                                            "ancillary")
                    if materialization:
                        materializations.append(materialization)
            
            return SensorResult(asset_events=materializations)
            
    
        return _file_sensor