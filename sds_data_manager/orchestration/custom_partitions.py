"""Contains information for working with our custom partitions."""

import datetime

import pandas as pd
from dagster import (
    DynamicPartitionsDefinition,
    SensorEvaluationContext,
    SensorResult,
    SkipReason,
    sensor,
)

from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.orchestration import config
from sds_data_manager.orchestration.maps_utils import (
    get_map_partition_names,
)

IDEX_10_DAY_RANGES_PATH = (
    "sds_data_manager/lambda_code/SDSCode/utils/idex_10_day_CDF_names.csv"
)
IDEX_30_DAY_RANGES_PATH = (
    "sds_data_manager/lambda_code/SDSCode/utils/idex_30_day_CDF_names.csv"
)

cadence_3mo_partitions = DynamicPartitionsDefinition(name="cadence_3mo_partitions")
cadence_6mo_partitions = DynamicPartitionsDefinition(name="cadence_6mo_partitions")
cadence_1yr_partitions = DynamicPartitionsDefinition(name="cadence_1yr_partitions")

CADENCE_PARTITION_DEFS = {
    "3mo": cadence_3mo_partitions,
    "6mo": cadence_6mo_partitions,
    "1yr": cadence_1yr_partitions,
}


##### THIS TELLS DAGSTER THAT SOME FILES ARE DIVIDED UP BY POINTING NUMBER
repoint_partitions = DynamicPartitionsDefinition(name="repoint_partitions")


@sensor(minimum_interval_seconds=600)
def add_repoint_partitions(context: SensorEvaluationContext):
    """Alert dagster when new repoint partitions should be made."""
    with db.Session() as session:
        pointing_records = session.query(models.PointingTable).all()

        if not pointing_records:
            return SensorResult()

        existing_partitions = context.instance.get_dynamic_partitions(
            "repoint_partitions"
        )

        pointing_partition_names = []
        for repoint in pointing_records:
            if not repoint.pointing_start_utc or not repoint.pointing_end_utc:
                continue
            if repoint.pointing_start_utc < datetime.datetime.fromisoformat(
                config.MISSION_START_TIME
            ).replace(tzinfo=datetime.timezone.utc):
                continue
            partition_name = (
                "repoint"
                + str(repoint.pointing_id)
                + "_"
                + repoint.pointing_start_utc.strftime("%Y-%m-%dT%H:%M:%S")
                + "_to_"
                + repoint.pointing_end_utc.strftime("%Y-%m-%dT%H:%M:%S")
            )
            if partition_name in existing_partitions:
                continue
            pointing_partition_names.append(partition_name)
        partition_requests = []
        if pointing_partition_names:
            partition_requests.append(
                repoint_partitions.build_add_request(pointing_partition_names)
            )
            context.log.info(
                f"Registered new dynamic partitions: {pointing_partition_names}"
            )

    return SensorResult(dynamic_partitions_requests=partition_requests)


##### THIS TELLS DAGSTER THAT SOME FILES ARE DIVIDED UP BY DAY
daily_partitions = DynamicPartitionsDefinition(name="daily_partitions")


@sensor(minimum_interval_seconds=86400)
def add_daily_partitions(context: SensorEvaluationContext):
    """Alert Dagster when new daily partitions should be made."""
    start_date = context.cursor or config.MISSION_START_TIME
    start_dt = (
        datetime.datetime.fromisoformat(start_date)
        .replace(tzinfo=datetime.timezone.utc)
        .replace(hour=0, minute=0, second=0)
    )
    end_dt = datetime.datetime.now(datetime.timezone.utc)

    existing_partitions = context.instance.get_dynamic_partitions("daily_partitions")

    # Materialize the days up to 10 days in advance.
    date_list = [
        start_dt + datetime.timedelta(days=x)
        for x in range((end_dt - start_dt).days + 10)
    ]

    daily_partition_names = []
    for date in date_list:
        partition_name = (
            "daily"
            + "_"
            + date.strftime("%Y-%m-%dT%H:%M:%S")
            + "_to_"
            + (date + datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
        )
        if partition_name in existing_partitions:
            continue
        daily_partition_names.append(partition_name)
    partition_requests = []
    if daily_partition_names:
        partition_requests.append(
            daily_partitions.build_add_request(daily_partition_names)
        )
        context.log.info(f"Registered new dynamic partitions: {daily_partition_names}")

    return SensorResult(
        dynamic_partitions_requests=partition_requests, cursor=end_dt.isoformat()
    )


##### THIS TELLS DAGSTER THAT SOME FILES ARE DIVIDED UP BY 10-day
idex10_partitions = DynamicPartitionsDefinition(name="idex_10_day_partitions")


@sensor(minimum_interval_seconds=86400)
def add_idex_10_day_partitions(context: SensorEvaluationContext):
    """Alert Dagster when new IDEX 10-day partitions should be made."""
    # These partitions come from a static cadence CSV, so we always evaluate from
    # mission start to avoid cursor drift shrinking the effective window.
    start_dt = datetime.datetime.fromisoformat(config.MISSION_START_TIME).replace(
        tzinfo=datetime.timezone.utc
    )
    end_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        days=40
    )  # We'll grab up to the next ~4 10 day periods

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
    mask = (idex_10_day_ranges["start_date"] >= start_bound) & (
        idex_10_day_ranges["start_date"] <= end_bound
    )

    # Apply the mask, sort, and extract the formatted strings
    filtered_df = (
        idex_10_day_ranges[mask].sort_values("start_date").reset_index(drop=True)
    )
    ten_day_keys = filtered_df["start_date"].dt.strftime("%Y-%m-%dT%H:%M:%S").tolist()

    existing_partitions = context.instance.get_dynamic_partitions(
        "idex_10_day_partitions"
    )
    partition_names = []
    for i in range(0, len(ten_day_keys) - 1):
        partition_name = "idex10_" + ten_day_keys[i] + "_to_" + ten_day_keys[i + 1]
        if partition_name in existing_partitions:
            continue
        partition_names.append(partition_name)
    partition_requests = []
    if partition_names:
        partition_requests.append(idex10_partitions.build_add_request(partition_names))
        context.log.info(f"Registered new dynamic partitions: {partition_names}")

    return SensorResult(dynamic_partitions_requests=partition_requests)


##### THIS TELLS DAGSTER THAT SOME FILES ARE DIVIDED UP BY 30-day
idex30_partitions = DynamicPartitionsDefinition(name="idex_30_day_partitions")


@sensor(minimum_interval_seconds=86400)
def add_idex_30_day_partitions(context: SensorEvaluationContext):
    """Alert Dagster when new IDEX 30-day partitions should be made."""
    # These partitions come from a static cadence CSV, so we always evaluate from
    # mission start to avoid cursor drift shrinking the effective window.
    start_dt = datetime.datetime.fromisoformat(config.MISSION_START_TIME).replace(
        tzinfo=datetime.timezone.utc
    )
    end_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        days=40
    )  # We'll make sure we're grabbing the next couple of 30-day periods

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
    mask = (idex_30_day_ranges["start_date"] >= start_bound) & (
        idex_30_day_ranges["start_date"] <= end_bound
    )

    # Apply the mask, sort, and extract the formatted strings
    filtered_df = (
        idex_30_day_ranges[mask].sort_values("start_date").reset_index(drop=True)
    )
    thirty_day_keys = (
        filtered_df["start_date"].dt.strftime("%Y-%m-%dT%H:%M:%S").tolist()
    )

    existing_partitions = context.instance.get_dynamic_partitions(
        "idex_30_day_partitions"
    )
    partition_names = []
    for i in range(0, len(thirty_day_keys) - 1):
        partition_name = (
            "idex30_" + thirty_day_keys[i] + "_to_" + thirty_day_keys[i + 1]
        )
        if partition_name in existing_partitions:
            continue
        partition_names.append(partition_name)
    partition_requests = []
    if partition_names:
        partition_requests.append(idex30_partitions.build_add_request(partition_names))
        context.log.info(f"Registered new dynamic partitions: {partition_names}")

    return SensorResult(dynamic_partitions_requests=partition_requests)


# Run daily (24 hours = 86400 seconds)
@sensor(minimum_interval_seconds=86400)
def add_cadence_map_partitions(context: SensorEvaluationContext):
    """Create missing cadence partitions daily.

    This sensor checks for new cadence partitions that need to be created
    and creates them in Dagster.
    """
    added_any = False

    # Compare against existing partitions in dagster.
    for cadence_str, partition_def in CADENCE_PARTITION_DEFS.items():
        existing_partitions = set(
            context.instance.get_dynamic_partitions(partition_def.name)
        )
        # Set include_open=True for progressive maps.
        progressive_partition_names = get_map_partition_names(
            cadence_str, include_open=True
        )

        context.log.info(f"Existing cadence partitions: {existing_partitions}")
        context.log.info(f"Partitions to create: {progressive_partition_names}")

        missing_partitions = [
            partition_name
            for partition_name in progressive_partition_names
            if partition_name not in existing_partitions
        ]
        if not missing_partitions:
            continue

        # Add missing partitions via instance API
        context.instance.add_dynamic_partitions(
            partitions_def_name=partition_def.name,
            partition_keys=missing_partitions,
        )
        context.log.info(
            f"Created new {cadence_str} cadence partitions: {missing_partitions}"
        )
        added_any = True

    if not added_any:
        return SkipReason("No new cadence partitions to create")

    return SensorResult()


sensors = [
    add_repoint_partitions,
    add_daily_partitions,
    add_idex_10_day_partitions,
    add_idex_30_day_partitions,
    add_cadence_map_partitions,
]
