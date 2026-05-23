"""IMAP file handler for files on the SDS without associated jobs"""
import datetime
import os
from dagster import (
    AssetExecutionContext,
    Failure,
    AssetSelection,
    AssetKey,
    SensorEvaluationContext,
    sensor,
    DefaultSensorStatus,
    SensorResult,
    AssetSpec
)
from sqlalchemy import select
from orchestration import custom_partitions
from orchestration import dagster_utilities
from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models

MISSION_START_TIME = "2025-09-17T00:00:00"

_partition_map = {
            "daily":   custom_partitions.daily_partitions,
            "repoint": custom_partitions.repoint_partitions,
            "10d":     custom_partitions.idex10_partitions,
            # NOTE: Right now, IDEX is the only instrument who uses 1mo cadence job that
            # maps to exactly 30 days. If this changes, this logic will need update.
            "1mo":     custom_partitions.idex30_partitions,
            # TODO: add cadence custom partition definition and update to use those
            # later
            "3mo":     custom_partitions.idex30_partitions,
            "6mo":     custom_partitions.idex30_partitions,
            "1yr":     custom_partitions.whole_mission_partition,
        }

class IMAPScienceFileHandler:
    """Handle IMAP files that have no associated jobs."""

    def __init__(self, asset_name, partition):
        self.needs_spin = False
        self.needs_repoint_file = False
        self.needs_spice = False
        
        self.source, self.data_type, self.descriptor = asset_name.split("_")
        self.asset_name = asset_name.replace('-', '')
        self.partitions_def = _partition_map.get(partition)
        
    def build_asset(self):
        return AssetSpec(key=AssetKey([self.asset_name]), partitions_def=self.partitions_def)
    
    def build_sensor(self):
        sensor_name = f"{self.asset_name}_sensor"
        @sensor(name=sensor_name,
                asset_selection=AssetSelection.all(),
                default_status=DefaultSensorStatus.RUNNING,
                minimum_interval_seconds=600)
        def _file_sensor(context: SensorEvaluationContext):
            
            start_date = context.cursor or MISSION_START_TIME
            start_dt = datetime.datetime.fromisoformat(start_date).replace(tzinfo=datetime.timezone.utc)
            if (datetime.datetime.now((datetime.timezone.utc)) - start_dt) > datetime.timedelta(days=7):
                next_time_to_check = start_dt + datetime.timedelta(days=7)                           
            else:
                next_time_to_check = datetime.datetime.now((datetime.timezone.utc))

            stmt = (
                select(models.ScienceFiles)
                .filter(models.ScienceFiles.ingestion_date >= start_dt,
                        models.ScienceFiles.ingestion_date <= next_time_to_check,
                        models.ScienceFiles.instrument==self.source,
                        models.ScienceFiles.data_level==self.data_type)
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
                    return SensorResult(cursor = next_time_to_check.isoformat())

                materializations = []
                for record in recent_db_records:
                    type = record.instrument + '_' + record.data_level + '_' + record.descriptor

                    asset_graph = context.repository_def.asset_graph
                    
                    partitions_def = asset_graph.get(AssetKey(type)).partitions_def
                    
                    if not partitions_def:
                        continue
                    if partitions_def.name == 'repoint_partitions':
                        # We need to only materialize the repoint that this is a part of
                        with db.Session() as session:
                            repoint = session.query(models.PointingTable).filter(models.PointingTable.pointing_id==record.repointing).all()[0]
                            affected_partitions = ["repoint" + str(repoint.pointing_id) + "_" +repoint.pointing_start_utc.strftime("%Y-%m-%dT%H:%M:%S") + "_to_" + repoint.pointing_end_utc.strftime("%Y-%m-%dT%H:%M:%S")]
                    else:
                        # For any other type of science file, we need to materialize the partition that contains the start_date
                        affected_partitions = dagster_utilities.get_affected_partitions(context, partitions_def, record.start_date, record.start_date)
                    
                    for partition in affected_partitions:
                        materialization = dagster_utilities.get_materialization(context,
                                                                                type,
                                                                                partition,
                                                                                [os.path.basename(record.file_path)],
                                                                                str(int(record.version[1:])),
                                                                                "science")
                        if materialization:
                            materializations.append(materialization)

            return SensorResult(
                asset_events=materializations,
                cursor = next_time_to_check.isoformat()
            )
        
        return _file_sensor

class IMAPAncillaryFileHandler:
    """Handle IMAP job dependencies and submission."""

    def __init__(self, asset_name):
        self.needs_spin = False
        self.needs_repoint_file = False
        self.needs_spice = False

        self.source, self.data_type, self.descriptor = asset_name.split("_")
        self.asset_name = asset_name.replace('-', '')
        self.partitions_def = custom_partitions.whole_mission_partition
        
    def build_asset(self):
        return AssetSpec(key=AssetKey([self.asset_name]), partitions_def=self.partitions_def)
    
    def build_sensor(self):
        sensor_name = f"{self.asset_name}_sensor"
        @sensor(name=sensor_name,
                asset_selection=AssetSelection.all(),
                default_status=DefaultSensorStatus.RUNNING,
                minimum_interval_seconds=600)
        def _file_sensor(context: AssetExecutionContext):

            stmt = (
                    select(models.AncillaryFiles)
                    .filter(models.AncillaryFiles.descriptor==self.descriptor,
                            models.AncillaryFiles.instrument==self.source)
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
                ancillary_file = session.scalars(stmt).all()

                if ancillary_file:
                    materialization = dagster_utilities.get_materialization(context,
                                                                            self.asset_name,
                                                                            "wholemission_2025-09-17T00:00:00_to_2045-09-17T00:00:00",
                                                                            [os.path.basename(ancillary_file[0].file_path)],
                                                                            str(int(ancillary_file[0].version[1:])),
                                                                            "ancillary")
                    if materialization:
                        return SensorResult(asset_events=[materialization])
                else:
                    raise Failure(description="Processing failed: No data found")
            
    
        return _file_sensor