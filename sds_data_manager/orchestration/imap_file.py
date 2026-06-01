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
    EventRecordsFilter,
    DagsterEventType,
    TextMetadataValue
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
                minimum_interval_seconds=300)
        def _file_sensor(context: SensorEvaluationContext):
            
            materializations = []

            if context.cursor:
                start_dt = datetime.datetime.fromisoformat(context.cursor).replace(tzinfo=datetime.timezone.utc)
            else: 
                start_dt = datetime.datetime.fromisoformat(MISSION_START_TIME).replace(tzinfo=datetime.timezone.utc)

            stmt = (
                select(models.ScienceFiles)
                .filter(#models.ScienceFiles.ingestion_date >= start_dt,
                        models.ScienceFiles.instrument==self.job_config.source,
                        models.ScienceFiles.data_level==self.job_config.data_type,
                        models.ScienceFiles.descriptor==self.job_config.descriptor)
                # Define the unique group
                .distinct(
                    models.ScienceFiles.start_date,
                    models.ScienceFiles.repointing,
                )
                # Order by the group, then by version descending to put the highest at the top
                .order_by(
                    models.ScienceFiles.start_date,
                    models.ScienceFiles.repointing,
                    models.ScienceFiles.version.desc()
                )
            )
            
            with db.Session() as session:
                recent_db_records = session.scalars(stmt).all()

                for record in recent_db_records:
                    context.log.info(f"Analyzing file: {record.file_path}")                
                    if self.partitions_def.name == 'repoint_partitions':
                        # We need to only materialize the repoint that this is a part of
                        with db.Session() as session:
                            repoint = session.query(models.PointingTable).filter(models.PointingTable.pointing_id==record.repointing).all()[0]
                            if not repoint.pointing_start_utc or not repoint.pointing_end_utc:
                                continue
                            affected_partitions = ["repoint" + str(repoint.pointing_id) + "_" +repoint.pointing_start_utc.strftime("%Y-%m-%dT%H:%M:%S") + "_to_" + repoint.pointing_end_utc.strftime("%Y-%m-%dT%H:%M:%S")]
                    else:
                        # For any other type of science file, we need to materialize the partition that contains the start_date
                        affected_partitions = dagster_utilities.get_affected_partitions(context, 
                                                                                        self.partitions_def, 
                                                                                        record.start_date, 
                                                                                        record.start_date)
                        
                    for partition in affected_partitions:
                        context.log.info(f"The following partition was identified as affected: {partition}")
                        materialization = dagster_utilities.get_materialization(context,
                                                                                self.job_config.to_dagster_asset(),
                                                                                partition,
                                                                                [os.path.basename(record.file_path)],
                                                                                str(int(record.version[1:])),
                                                                                "science")
                        if materialization:
                            context.log.info(f"{record.file_path} will be materialized.")
                            materializations.append(materialization)
            return SensorResult(asset_events=materializations,
                                cursor=start_dt.isoformat())

        return _file_sensor

class IMAPAncillaryFileHandler:
    """Handle IMAP job dependencies and submission."""

    def __init__(self, node: DependencyNode, 
                 partition):
        self.job_config = node
        self.partitions_def = partition

    def build_asset(self):
        return AssetSpec(key=self.job_config.to_dagster_asset(), partitions_def=self.partitions_def)
    
    def build_sensor(self):
        sensor_name = f"{self.job_config.to_dagster_asset().to_user_string()}_sensor"
        @sensor(name=sensor_name,
                asset_selection=AssetSelection.all(),
                minimum_interval_seconds=600)
        def _file_sensor(context: AssetExecutionContext):

            if context.cursor:
                start_dt = datetime.datetime.fromisoformat(context.cursor).replace(tzinfo=datetime.timezone.utc)
            else: 
                start_dt = datetime.datetime.fromisoformat(MISSION_START_TIME).replace(tzinfo=datetime.timezone.utc)

            # 1. Define the Window Function
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
                
                for record in ancillary_files:
                    file_start_date = record.start_date
                    file_end_date = record.end_date
                    if not file_start_date:
                        file_start_date = datetime.datetime.fromisoformat(MISSION_START_TIME).replace(tzinfo=datetime.timezone.utc)
                    if not file_end_date:
                        file_end_date = datetime.datetime(2045,9,17).replace(tzinfo=datetime.timezone.utc)

                    affected_partitions = dagster_utilities.get_affected_partitions(context, 
                                                                                    self.partitions_def, 
                                                                                    file_start_date.replace(tzinfo=datetime.timezone.utc), 
                                                                                    file_end_date.replace(tzinfo=datetime.timezone.utc))
                    
                    for partition in affected_partitions:
                        partition_start_date, partition_end_date = self._parse_dates_from_key(partition)

                        # We're going to do some complex logic here to determine which file we need to use. 
                        # Determine if we already have a file here, and if its start_date is closer or further away than
                        # the start date of the partition we're looking at. Honestly, this should probably be in an asset,
                        # since this might take a while. TODO.
                        records = context.instance.get_event_records(
                                                EventRecordsFilter(
                                                    asset_key=self.job_config.to_dagster_asset(), 
                                                    asset_partitions=[partition],
                                                    event_type=DagsterEventType.ASSET_MATERIALIZATION
                                                ),
                                                limit=1
                                            )
                        if records:
                            # Extract the previous file list from the metadata
                            last_metadata = records[0].asset_materialization.metadata
                            previous_file_start_date = datetime.datetime.strptime(last_metadata.get('start_date', TextMetadataValue("20250101")).value, '%Y%m%d').replace(tzinfo=datetime.timezone.utc)
                            distance_to_previous_file = previous_file_start_date.replace(tzinfo=datetime.timezone.utc) - partition_start_date.replace(tzinfo=datetime.timezone.utc)
                            distance_to_new_file = file_start_date.replace(tzinfo=datetime.timezone.utc) - partition_start_date.replace(tzinfo=datetime.timezone.utc)
                            if distance_to_previous_file < distance_to_new_file:
                                # If the file we're looking at is further away that the last file, we shouldn't do anything to this partition. 
                                continue

                        materialization = dagster_utilities.get_materialization(context,
                                                                                self.job_config.to_dagster_asset(),
                                                                                partition,
                                                                                [os.path.basename(record.file_path)],
                                                                                record.version[1:],
                                                                                "ancillary",
                                                                                start_date=record.start_date.strftime('%Y%m%d'))
                        if materialization:
                            materializations.append(materialization)

            return SensorResult(asset_events=materializations,
                                cursor=start_dt.isoformat())            
    
        return _file_sensor
    
    def _parse_dates_from_key(self, 
                              partition_key: str) -> tuple[datetime.datetime, datetime.datetime]:
        """
        Extracts start and end datetimes from a string formatted like:
        '{name}_%Y-%m-%dT%H:%M:%S_to_%Y-%m-%dT%H:%M:%S'
        """
        if not partition_key:
            return None, None
            
        date_range = partition_key.split('_', 1)[1]
        if "_to_" in date_range:
            p_start_str, p_end_str = date_range.split("_to_")
            p_start = datetime.datetime.strptime(p_start_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
            p_end = datetime.datetime.strptime(p_end_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
            
        return p_start, p_end