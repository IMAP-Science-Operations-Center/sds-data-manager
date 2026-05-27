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

MISSION_START_TIME = "2025-09-17T00:00:00"

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
                start_dt = json.loads(context.cursor).get('last_start_date', None)
                ingest_dt = json.loads(context.cursor).get('last_ingest_date', None)
                start_dt = datetime.datetime.fromisoformat(start_dt).replace(tzinfo=datetime.timezone.utc)
                ingest_dt = datetime.datetime.fromisoformat(ingest_dt).replace(tzinfo=datetime.timezone.utc)
            else: 
                start_dt = datetime.datetime.fromisoformat(MISSION_START_TIME).replace(tzinfo=datetime.timezone.utc)
                ingest_dt = datetime.datetime.now((datetime.timezone.utc))
            if start_dt < ingest_dt:
                # We are still working through a backlog
                next_time_to_check = start_dt + datetime.timedelta(days=7)
                time_field_to_check = models.ScienceFiles.start_date
                new_cursor = json.dumps({'last_start_date': next_time_to_check.isoformat(), 'last_ingest_date': ingest_dt.isoformat()})                 
            else:
                # We've caught up to the backlog, now only looking for new files
                next_time_to_check = datetime.datetime.now((datetime.timezone.utc))
                time_field_to_check = models.ScienceFiles.ingestion_date
                start_dt = ingest_dt
                new_cursor = json.dumps({'last_start_date': next_time_to_check.isoformat(), 'last_ingest_date': next_time_to_check.isoformat()})

            stmt = (
                select(models.ScienceFiles)
                .filter(time_field_to_check >= start_dt,
                        time_field_to_check <= next_time_to_check,
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

                if not recent_db_records:
                    return SensorResult(cursor = new_cursor)

                materializations = []
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
                            materializations.append(materialization)

            return SensorResult(
                asset_events=materializations,
                cursor = new_cursor
            )
        
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

            stmt = (
                    select(models.AncillaryFiles)
                    .filter(models.AncillaryFiles.descriptor==self.job_config.descriptor,
                            models.AncillaryFiles.instrument==self.job_config.source)
                    .distinct(
                        models.AncillaryFiles.instrument,
                        models.AncillaryFiles.descriptor,
                    )
                    .order_by(
                        models.AncillaryFiles.instrument,
                        models.AncillaryFiles.descriptor,
                        models.AncillaryFiles.version.desc()
                    )
                )

            with db.Session() as session:
                ancillary_files = session.scalars(stmt).all()
                if not ancillary_files:
                    raise Failure(description="Processing failed: No data found")
                for file in ancillary_files:
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
                        return SensorResult(asset_events=[materialization])                    
            
    
        return _file_sensor