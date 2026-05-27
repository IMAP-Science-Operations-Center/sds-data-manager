"""
This file contains information for working with our custom partitions.

"""
from dagster import (
    StaticPartitionsDefinition,
    DynamicPartitionsDefinition,
    SensorEvaluationContext,
    SensorResult,
    sensor,
    DefaultSensorStatus
)
import datetime

import pandas as pd
from sds_data_manager.lambda_code.SDSCode.database import database as db, models

IDEX_10_DAY_RANGES_PATH = "sds_data_manager/lambda_code/SDSCode/utils/idex_10_day_CDF_names.csv"
IDEX_30_DAY_RANGES_PATH = "sds_data_manager/lambda_code/SDSCode/utils/idex_30_day_CDF_names.csv"

MISSION_START_TIME = "2026-04-01T00:00:00"

##### THIS TELLS DAGSTER THAT SOME FILES ARE DIVIDED UP BY POINTING NUMBER
repoint_partitions = DynamicPartitionsDefinition(name="repoint_partitions")
@sensor(default_status=DefaultSensorStatus.RUNNING,
        minimum_interval_seconds=600)
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
            if repoint.pointing_start_utc < datetime.datetime.fromisoformat(MISSION_START_TIME).replace(tzinfo=datetime.timezone.utc):
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

whole_mission_partition = StaticPartitionsDefinition(["wholemission_2025-09-17T00:00:00_to_2045-09-17T00:00:00"])

sensors = [add_repoint_partitions,
           add_daily_partitions,
           add_idex_10_day_partitions,
           add_idex_30_day_partitions]

