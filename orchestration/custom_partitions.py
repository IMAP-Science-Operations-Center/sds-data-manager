"""
This file contains information for working with our custom partitions.

"""
from dagster import (
    StaticPartitionsDefinition,
    DynamicPartitionsDefinition,
    SensorEvaluationContext,
    SensorResult,
    sensor,
    DefaultSensorStatus,
    RunRequest
)
import datetime

import pandas as pd
from sds_data_manager.lambda_code.SDSCode.database import database as db, models

IDEX_10_DAY_RANGES_PATH = "sds_data_manager/lambda_code/SDSCode/utils/idex_10_day_CDF_names.csv"
IDEX_30_DAY_RANGES_PATH = "sds_data_manager/lambda_code/SDSCode/utils/idex_30_day_CDF_names.csv"

MISSION_START_TIME = "2025-09-17T00:00:00"

##### THIS TELLS DAGSTER THAT SOME FILES ARE DIVIDED UP BY POINTING NUMBER
repoint_partitions = DynamicPartitionsDefinition(name="repoint_partitions")
@sensor(default_status=DefaultSensorStatus.RUNNING)
def add_repoint_partitions(context: SensorEvaluationContext):
    '''
    Periodically polls the PointingTable, and tells dagster that new repoint numbers exist.
    '''
    with db.Session() as session:
        pointing_records = session.query(models.PointingTable).all()
    
    if not pointing_records:
        return SensorResult()

    existing_partitions = context.instance.get_dynamic_partitions("repoint_partitions")

    pointing_partition_names = []
    for repoint in pointing_records:
        if not repoint.pointing_start_utc or not repoint.pointing_end_utc:
            continue
        partition_name = "repoint" + str(repoint.pointing_id) + "_" +repoint.pointing_start_utc.strftime("%Y-%m-%dT%H:%M:%S") + "_to_" + repoint.pointing_end_utc.strftime("%Y-%m-%dT%H:%M:%S") 
        if partition_name in existing_partitions:
            continue
        pointing_partition_names.append(partition_name)
    partition_requests = []
    if pointing_partition_names:
        partition_requests.append(repoint_partitions.build_add_request(pointing_partition_names))
        context.log.info(f"Registered new dynamic partitions: {pointing_partition_names}")

    return SensorResult(
        dynamic_partitions_requests=partition_requests
    )

##### THIS TELLS DAGSTER THAT SOME FILES ARE DIVIDED UP BY DAY
daily_partitions = DynamicPartitionsDefinition(name="daily_partitions")
@sensor(default_status=DefaultSensorStatus.RUNNING,
        minimum_interval_seconds=86400)
def add_daily_partitions(context: SensorEvaluationContext):
    '''
    Periodically polls the PointingTable, and tells dagster that new repoint numbers exist.
    '''
    start_date = context.cursor or MISSION_START_TIME
    start_dt = datetime.datetime.fromisoformat(start_date).replace(tzinfo=datetime.timezone.utc)
    end_dt = datetime.datetime.now((datetime.timezone.utc))

    existing_partitions = context.instance.get_dynamic_partitions("daily_partitions")

    # Materialize the days up to 10 days in advance. 
    date_list = [start_dt + datetime.timedelta(days=x) for x in range((end_dt - start_dt).days + 10)]
    
    daily_partition_names = []
    for date in date_list:
        partition_name = "daily" + "_" + date.strftime("%Y-%m-%dT%H:%M:%S") + "_to_" + (date + datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S") 
        if partition_name in existing_partitions:
            continue
        daily_partition_names.append(partition_name)
    partition_requests = []
    if daily_partition_names:
        partition_requests.append(daily_partitions.build_add_request(daily_partition_names))
        context.log.info(f"Registered new dynamic partitions: {daily_partition_names}")

    return SensorResult(
        dynamic_partitions_requests=partition_requests,
        cursor=end_dt.isoformat()
    )

##### THIS TELLS DAGSTER THAT SOME FILES ARE DIVIDED UP BY 10-day
idex10_partitions = DynamicPartitionsDefinition(name="idex_10_day_partitions")
@sensor(default_status=DefaultSensorStatus.RUNNING,
        minimum_interval_seconds=86400)
def add_idex_10_day_partitions(context: SensorEvaluationContext):
    '''
    Periodically polls the PointingTable, and tells dagster that new repoint numbers exist.
    '''
    start_date = context.cursor or MISSION_START_TIME
    start_dt = datetime.datetime.fromisoformat(start_date).replace(tzinfo=datetime.timezone.utc)
    end_dt = datetime.datetime.now((datetime.timezone.utc))+datetime.timedelta(days=40) # We'll grab up to the next ~4 10 day periods 

    idex_10_day_ranges = pd.read_csv(
        IDEX_10_DAY_RANGES_PATH,
        usecols=["start_date", "end_date"],
        converters={
            "start_date": lambda s: pd.to_datetime(s, format="%Y%m%d", utc=True),
            "end_date": lambda s: pd.to_datetime(s, format="%Y%m%d", utc=True),
        },
    )

    if idex_10_day_ranges["start_date"].duplicated().any():
        raise ValueError("Duplicate IDEX 10-day start_date values were found")

    # Convert inputs to pandas datetime objects for safe comparison
    start_bound = pd.to_datetime(start_dt)
    end_bound = pd.to_datetime(end_dt)

    # Create a mask to filter rows where start_date falls within the bounds
    mask = (
        (idex_10_day_ranges["start_date"] >= start_bound) & 
        (idex_10_day_ranges["start_date"] <= end_bound)
    )
    
    # Apply the mask, sort, and extract the formatted strings
    filtered_df = idex_10_day_ranges[mask].sort_values("start_date").reset_index(drop=True)
    ten_day_keys = filtered_df["start_date"].dt.strftime("%Y-%m-%dT%H:%M:%S").tolist()
    
    existing_partitions = context.instance.get_dynamic_partitions("idex_10_day_partitions")
    partition_names = []
    for i in range(0,len(ten_day_keys)-1):
        partition_name = "idex10_" + ten_day_keys[i] + "_to_" + ten_day_keys[i+1]
        if partition_name in existing_partitions:
            continue
        partition_names.append(partition_name)
    partition_requests = []
    if partition_names:
        partition_requests.append(idex10_partitions.build_add_request(partition_names))
        context.log.info(f"Registered new dynamic partitions: {partition_names}")

    return SensorResult(
        dynamic_partitions_requests=partition_requests,
        cursor=end_dt.isoformat()
    )

##### THIS TELLS DAGSTER THAT SOME FILES ARE DIVIDED UP BY 30-day
idex30_partitions = DynamicPartitionsDefinition(name="idex_30_day_partitions")
@sensor(default_status=DefaultSensorStatus.RUNNING,
        minimum_interval_seconds=86400)
def add_idex_30_day_partitions(context: SensorEvaluationContext):
    '''
    Periodically polls the PointingTable, and tells dagster that new repoint numbers exist.
    '''
    start_date = context.cursor or MISSION_START_TIME
    start_dt = datetime.datetime.fromisoformat(start_date).replace(tzinfo=datetime.timezone.utc)
    end_dt = datetime.datetime.now((datetime.timezone.utc))+datetime.timedelta(days=40) # We'll make sure we're grabbing the next couple of 30-day periods

    idex_30_day_ranges = pd.read_csv(
        IDEX_30_DAY_RANGES_PATH,
        usecols=["start_date", "end_date"],
        converters={
            "start_date": lambda s: pd.to_datetime(s, format="%Y%m%d", utc=True),
            "end_date": lambda s: pd.to_datetime(s, format="%Y%m%d", utc=True),
        },
    )

    if idex_30_day_ranges["start_date"].duplicated().any():
        raise ValueError("Duplicate IDEX 30-day start_date values were found")

    # Convert inputs to pandas datetime objects for safe comparison
    start_bound = pd.to_datetime(start_dt)
    end_bound = pd.to_datetime(end_dt)

    # Create a mask to filter rows where start_date falls within the bounds
    mask = (
        (idex_30_day_ranges["start_date"] >= start_bound) & 
        (idex_30_day_ranges["start_date"] <= end_bound)
    )
    
    # Apply the mask, sort, and extract the formatted strings
    filtered_df = idex_30_day_ranges[mask].sort_values("start_date").reset_index(drop=True)
    thirty_day_keys = filtered_df["start_date"].dt.strftime("%Y-%m-%dT%H:%M:%S").tolist()
    
    existing_partitions = context.instance.get_dynamic_partitions("idex_30_day_partitions")
    partition_names = []
    for i in range(0,len(thirty_day_keys)-1):
        partition_name = "idex30_" + thirty_day_keys[i] + "_to_" + thirty_day_keys[i+1]
        if partition_name in existing_partitions:
            continue
        partition_names.append(partition_name)
    partition_requests = []
    if partition_names:
        partition_requests.append(idex30_partitions.build_add_request(partition_names))
        context.log.info(f"Registered new dynamic partitions: {partition_names}")

    return SensorResult(
        dynamic_partitions_requests=partition_requests,
        cursor=end_dt.isoformat()
    )

def run_all_affected_partitions(context, 
                                asset_key_phrase,
                                min_dt,
                                max_dt,
                                suffix):
    '''
    This function loops through all assets, looking for the "asset_key_phrase". Then, it yields 
    a run request for those assets at the partitions that exist between min_dt and max_dt. 
    '''
    _cache = {}
    asset_graph = context.repository_def.asset_graph
    for asset_key in asset_graph.get_all_asset_keys():
        context.log.info(f"Determining affected partitions from {asset_key}")
        if asset_key_phrase not in asset_key.to_user_string():
            continue # This asset is not applicable
        context.log.info(f"Found spice partition: {asset_key}")
        partitions_def = asset_graph.get(asset_key).partitions_def
        
        if not partitions_def:
            continue

        # This serves to cache the partitions so we don't need to calculate them twice. 
        if partitions_def.name not in _cache:
            affected_partitions = get_affected_partitions(context, partitions_def, min_dt, max_dt)
            _cache[partitions_def.name] = affected_partitions
        else:
            affected_partitions = _cache[partitions_def.name]

        for partition in affected_partitions:
            yield RunRequest(
                run_key=f"{asset_key.to_user_string()}_{partition}_{suffix}",
                partition_key=partition,
                asset_selection=[asset_key]
            )

def get_affected_partitions(context, 
                            partitions_def, 
                            min_dt, 
                            max_dt):
    '''
    This is a helper function that returns a set of the repoint partitions that between within 
    two datetime objects. 
    '''
    context.log.info(f"Checking for matching partitions in the time range of {min_dt} to {max_dt}")
    keys = partitions_def.get_partition_keys(dynamic_partitions_store=context.instance)
    affected_keys = []
    for key in keys:
        context.log.info(f"Checking this partition key: {key}")
        date_range = key.split('_', 1)[1]
        if "_to_" in date_range:
            p_start_str, p_end_str = date_range.split("_to_")
            p_start = datetime.datetime.strptime(p_start_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
            p_end = datetime.datetime.strptime(p_end_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
            
            # Check for time overlap logic
            if min_dt <= p_end and max_dt >= p_start:
                context.log.info(f"It was a match! Adding to the affected keys.")
                affected_keys.append(key)

    return affected_keys

whole_mission_partition = StaticPartitionsDefinition(["wholemission_2025-09-17T00:00:00_to_2045-09-17T00:00:00"])

sensors = [add_repoint_partitions,
           add_daily_partitions,
           add_idex_10_day_partitions,
           add_idex_30_day_partitions]

