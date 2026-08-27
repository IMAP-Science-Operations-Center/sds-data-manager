"""Override behavior for IDEX processing."""

import datetime
import os

import pandas as pd
from dagster import (
    AssetExecutionContext,
    AssetKey,
    AssetSelection,
    Failure,
    RunRequest,
    SensorResult,
    asset,
    sensor,
)
from imap_data_access.file_validation import Version
from sqlalchemy import select

from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.orchestration import config, custom_partitions, imap_file
from sds_data_manager.orchestration.dagster_utilities import (
    get_affected_partitions,
    get_materialization_result,
)
from sds_data_manager.orchestration.file_handler_registry import FileBuilderRegistry
from sds_data_manager.orchestration.types import DependencyNode


@FileBuilderRegistry.register("idex", "l0", "raw")
class IDEXL0FileHandler(imap_file.IMAPScienceFileHandler):
    """Handle IMAP files that have no associated jobs."""

    # This handler materializes itself from IDEXL0Files via its own asset/sensor,
    # so it is excluded from the consolidated science file materialization sensor.
    USE_COMMON_SENSOR = False

    def __init__(self, node: DependencyNode, partition):
        """Initialize the IDEXL0FileHandler class."""
        self.job_config = node
        self.partitions_def = partition

    def build_asset(self):
        """Build the unique IDEX L0 Dagster Asset."""

        @asset(partitions_def=self.partitions_def, output_required=False)
        def idex_l0_raw(context: AssetExecutionContext):
            """Represent the asset needed for 10 days of IDEX data.

            This gets all L0 files on the SDC that match this partition, and attempts
            to materialize the asset. If the files are the same as before,
            nothing happens.
            """
            current_partition = context.partition_key
            date_range = current_partition.split("_", 1)[1]
            if "_to_" in date_range:
                p_start_str, _ = date_range.split("_to_")
                p_start = datetime.datetime.strptime(
                    p_start_str, "%Y-%m-%dT%H:%M:%S"
                ).replace(tzinfo=datetime.timezone.utc)

            stmt = select(models.IDEXL0Files).filter(
                models.IDEXL0Files.start_date == p_start
            )
            files = []
            versions = []
            with db.Session() as session:
                records = session.scalars(stmt).all()

                if records:
                    # Dedup: keep highest version per file base path
                    best: dict[str, models.IDEXL0Files] = {}
                    for rec in records:
                        # Get the basename from the filepath
                        filename = os.path.basename(rec.file_path)
                        base = filename.rsplit("_", 1)[0]  # strip "_v001.pkts"
                        # "imap_idex_l0_raw_20260408_v001.pkts" ->
                        # "imap_idex_l0_raw_20260408"
                        current_version = Version(rec.major_version, rec.minor_version)
                        if base not in best or current_version > Version(
                            best[base].major_version, best[base].minor_version
                        ):
                            best[base] = rec

                    for rec in best.values():
                        filename = os.path.basename(rec.file_path)
                        files.append(filename)
                        versions.append(Version(rec.major_version, rec.minor_version))

                    materialization = get_materialization_result(
                        context,
                        AssetKey("idex_l0_raw"),
                        current_partition,
                        files,
                        max(versions),
                        "science",
                    )
                    if materialization:
                        yield materialization
                else:
                    raise Failure(description="Processing failed: No data found")

        return idex_l0_raw

    def build_sensor(self):
        """Build a sensor for the IDEX L0 table."""
        sensor_name = f"{self.job_config.to_dagster_name()}_sensor"

        @sensor(
            name=sensor_name,
            asset_selection=AssetSelection.all(),
            minimum_interval_seconds=300,
        )
        def _file_sensor(context):
            """Poll the IDEX L0 table for updates.

            For every partition with an update, we kick off a run of the
            "idex_l0_raw" asset.
            """
            start_date = context.cursor or config.MISSION_START_TIME
            start_dt = datetime.datetime.fromisoformat(start_date).replace(
                tzinfo=datetime.timezone.utc
            )
            now_dt = datetime.datetime.now(datetime.timezone.utc)
            now_iso = now_dt.isoformat()
            idex_10_day_ranges = pd.read_csv(
                custom_partitions.IDEX_10_DAY_RANGES_PATH, header=0, dtype=str
            )
            # Get recent partitions that have had new files ingested since the last
            # time we checked.
            # Deduplicate partitions so we only try and trigger one job if there
            # have been multiple files ingested corresponding to the same partition.
            # In the IDEXL0Files table, the start date corresponds to the start date
            # of a 10 day window and therefore, are the same as partitions.
            stmt = (
                select(models.IDEXL0Files.start_date)
                .filter(models.IDEXL0Files.ingestion_date > start_dt)
                .distinct()
                .order_by(models.IDEXL0Files.start_date)
            )
            with db.Session() as session:
                recent_db_partitions = session.scalars(stmt).all()

                run_requests = []
                run_suffix = now_dt.timestamp()
                for date in recent_db_partitions:
                    # Query the end_date of the partition.
                    # Find the row where the input start date is equal to
                    # the start date in the df.
                    matching_row = idex_10_day_ranges[
                        idex_10_day_ranges["start_date"] == date.strftime("%Y%m%d")
                    ]
                    if matching_row.empty:
                        context.log.info(f"No window with start date: {date}")
                        continue
                    window_end_dt = datetime.datetime.strptime(
                        matching_row["end_date"].iloc[0], "%Y%m%d"
                    ).replace(tzinfo=datetime.timezone.utc)

                    # TODO use get_10_day_window_end_date from imap_processing when that
                    # is merged
                    partition_keys = get_affected_partitions(
                        context,
                        custom_partitions.idex10_partitions,
                        date,
                        window_end_dt,
                    )

                    for key in partition_keys:
                        # Trigger the job as soon as there is a file in the window.
                        asset_name = "idex_l0_raw"
                        run_requests.append(
                            RunRequest(
                                run_key=f"idex_{key}_{run_suffix}",
                                partition_key=key,
                                asset_selection=[AssetKey(asset_name)],
                            )
                        )

            return SensorResult(run_requests=run_requests, cursor=now_iso)

        return _file_sensor
