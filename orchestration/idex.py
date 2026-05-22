import datetime
import pandas as pd
from dagster import (
    SensorEvaluationContext,
    SensorResult,
    sensor,
    RunRequest,
    asset,
    AssetExecutionContext,
    Failure,
    AssetKey,
    AssetSelection,
    DefaultSensorStatus
)
from orchestration import custom_partitions
from orchestration.dagster_utilities import get_materialization_result
from sds_data_manager.lambda_code.SDSCode.database import database as db, models
from sqlalchemy import select
from orchestration.imap_file import IMAPAncillaryFileHandler
from orchestration.imap_job import IMAPJobHandler

MISSION_START_TIME = "2025-09-24T00:00:00"

@sensor(asset_selection=AssetSelection.all(),
        minimum_interval_seconds=600,
        default_status=DefaultSensorStatus.RUNNING)
def watch_idex_l0_files(context):
    '''
    Polls the IDEX L0 table for updates. 

    For every partition with an update, we kick off a run of the "idex_l0_raw" asset.
    '''
    
    start_date = context.cursor or MISSION_START_TIME
    start_dt = datetime.datetime.fromisoformat(start_date).replace(tzinfo=datetime.timezone.utc)
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    now_iso = now_dt.isoformat()
    idex_10_day_ranges = pd.read_csv(custom_partitions.IDEX_10_DAY_RANGES_PATH, header=0, dtype=str)
    # Get recent partitions that have had new files ingested since the last time we
    # checked.
    # Deduplicate partitions so we only try and trigger one job if there have been
    # multiple files ingested corresponding to the same partition.
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
        # Find the row where the input start date is equal to the start date in the df.
        matching_row = idex_10_day_ranges[
            idex_10_day_ranges["start_date"] == date.strftime("%Y%m%d")]
        if matching_row.empty:
            context.log.info(f"No window with start date: {date}")
            continue
        window_end_dt = datetime.datetime.strptime(
            matching_row["end_date"].iloc[0], "%Y%m%d"
        ).replace(tzinfo=datetime.timezone.utc)

        # TODO use get_10_day_window_end_date from imap_processing when that is merged
        partition_key = "idex10_"+date.strftime("%Y-%m-%dT%H:%M:%S")+"_to_"+window_end_dt.strftime("%Y-%m-%dT%H:%M:%S") 

        # If the end of the window is in the past, then we can trigger the job to
        # process that partition. If today is the last day of the window (window end
        # dates are exclusive so the last day of data is window_end_date - 1 day) then
        # we can process.
        if now_dt >= (window_end_dt - datetime.timedelta(days=1)):
            asset_name = "idex_l0_raw"
            run_requests.append(RunRequest(
                                    run_key=f"idex_{partition_key}_{run_suffix}",
                                    partition_key=partition_key,
                                    asset_selection=[AssetKey(asset_name)]
                                ))

    return SensorResult(run_requests=run_requests,
                        cursor = now_iso)


@asset(partitions_def=custom_partitions.idex10_partitions,
        output_required=False)
def idex_l0_raw(context: AssetExecutionContext):
    '''
    This is kicked off by watch_idex_l0_files. 

    This gets all L0 files on the SDC that match this partition, and attempts to 
    materialize the asset. If the files are the same as before, nothing happens. 
    '''
    current_partition = context.partition_key
    partition_datetime = datetime.datetime.strptime(current_partition, "%Y-%m-%dT%H:%M:%S")

    stmt = (
            select(models.IDEXL0Files)
            .filter(models.IDEXL0Files.start_date==partition_datetime)
            )
    files = []
    versions = []
    with db.Session() as session:
        records = session.scalars(stmt).all()

    if records:
        # Dedup: keep highest version per file base path (strip version suffix)
        best: dict[str, models.IDEXL0Files] = {}
        for rec in records:
            # "imap_idex_l0_raw_20260408_v001.pkts" -> "imap_idex_l0_raw_20260408"
            base = rec.file_path.rsplit("_", 1)[0]  # strip "_v001.pkts"
            if base not in best or rec.version > best[base].version:
                best[base] = rec

        for rec in records:
            files.append(rec.file_path)
            versions.append(rec.version)

        materialization = get_materialization_result(context,
                                                    "idex_l0_raw",
                                                    current_partition,
                                                    files,
                                                    versions,
                                                    "science")
        if materialization:
            yield materialization
    else:
        raise Failure(description="Processing failed: No data found")


L0_sensor = [watch_idex_l0_files]
L0_asset = [idex_l0_raw]