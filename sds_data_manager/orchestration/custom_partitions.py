"""Contains information for working with our custom partitions."""

import datetime

import pandas as pd
from dagster import (
    DynamicPartitionsDefinition,
    SensorEvaluationContext,
    SensorResult,
    sensor,
)

from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.orchestration import config

IDEX_10_DAY_RANGES_PATH = (
    "sds_data_manager/lambda_code/SDSCode/utils/idex_10_day_CDF_names.csv"
)
IDEX_30_DAY_RANGES_PATH = (
    "sds_data_manager/lambda_code/SDSCode/utils/idex_30_day_CDF_names.csv"
)

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
    start_date = context.cursor or config.MISSION_START_TIME
    start_dt = datetime.datetime.fromisoformat(start_date).replace(
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

    return SensorResult(
        dynamic_partitions_requests=partition_requests, cursor=end_dt.isoformat()
    )


##### THIS TELLS DAGSTER THAT SOME FILES ARE DIVIDED UP BY 30-day
idex30_partitions = DynamicPartitionsDefinition(name="idex_30_day_partitions")


@sensor(minimum_interval_seconds=86400)
def add_idex_30_day_partitions(context: SensorEvaluationContext):
    """Alert Dagster when new IDEX 30-day partitions should be made."""
    start_date = context.cursor or config.MISSION_START_TIME
    start_dt = datetime.datetime.fromisoformat(start_date).replace(
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

    return SensorResult(
        dynamic_partitions_requests=partition_requests, cursor=end_dt.isoformat()
    )


##### THIS TELLS DAGSTER ABOUT SPACECRAFT POINTING-ATTITUDE PROCESSING WINDOWS
# One partition per attitude_history SPICE kernel, keyed by pointing times.
# Partition start = pointing_start_utc of the first pointing with any overlap
# with the ah kernel. Partition end = pointing_end_utc of the last pointing
# completely covered by the ah kernel.
# Prefix is "pointingattitude" (no underscores) so parse_dates_from_partition_key can
# split on the first "_" to isolate the date range.
pointing_attitude_partitions = DynamicPartitionsDefinition(
    name="pointing_attitude_partitions"
)


@sensor(minimum_interval_seconds=600)
def add_pointing_attitude_partitions(context: SensorEvaluationContext):
    """Alert Dagster when new spacecraft pointing partitions should be made.

    One partition is maintained per ah kernel cycle. When a new ah kernel
    extends or replaces earlier coverage, any existing partitions whose full
    range is subsumed by the new partition are deleted first. This handles
    both the normal growing-append case (same start, later end) and the
    retroactive combined-file case (one large file that subsumes many small
    early-mission daily partitions).
    """
    with db.Session() as session:
        attitude_kernels = (
            session.query(models.SPICEFiles)
            .filter(models.SPICEFiles.kernel_type == "attitude_history")
            .all()
        )

        if not attitude_kernels:
            return SensorResult()

        existing_partitions = context.instance.get_dynamic_partitions(
            "pointing_attitude_partitions"
        )

        # Parse existing partitions into (start_str, end_str, key) for subsumption
        # checks. %Y-%m-%dT%H:%M:%S is fixed-width and zero-padded, so
        # lexicographic string comparison is equivalent to chronological order.
        existing_parsed = []
        for key in existing_partitions:
            date_range = key.split("_", 1)[1]
            if "_to_" in date_range:
                start_str, end_str = date_range.split("_to_")
                existing_parsed.append((start_str, end_str, key))

        partitions_to_add = []
        partitions_to_delete = []

        for kernel in attitude_kernels:
            if not kernel.min_date_datetime or not kernel.max_date_datetime:
                continue
            ah_min = kernel.min_date_datetime
            ah_max = kernel.max_date_datetime

            # First pointing with any overlap with the ah kernel coverage
            first_overlapping = (
                session.query(models.PointingTable)
                .filter(
                    models.PointingTable.pointing_start_utc < ah_max,
                    models.PointingTable.repoint_start_utc > ah_min,
                )
                .order_by(models.PointingTable.pointing_start_utc)
                .first()
            )

            # Last pointing completely contained within the ah kernel coverage
            last_covered = (
                session.query(models.PointingTable)
                .filter(
                    models.PointingTable.pointing_start_utc >= ah_min,
                    models.PointingTable.repoint_start_utc <= ah_max,
                )
                .order_by(models.PointingTable.pointing_end_utc.desc())
                .first()
            )

            # Skip if no pointings are completely covered yet
            if not first_overlapping or not last_covered:
                continue

            new_start_str = first_overlapping.pointing_start_utc.strftime(
                "%Y-%m-%dT%H:%M:%S"
            )
            new_end_str = last_covered.pointing_end_utc.strftime("%Y-%m-%dT%H:%M:%S")
            new_partition_name = f"pointingattitude_{new_start_str}_to_{new_end_str}"

            if new_partition_name in existing_partitions:
                continue  # Already up to date

            # Delete any existing partition whose range is fully contained within
            # the new range. Covers both the growing-append case (same start,
            # smaller end) and the retroactive combined-file case (many small
            # early-mission partitions all subsumed by one large new partition).
            subsumed = [
                key
                for start_str, end_str, key in existing_parsed
                if start_str >= new_start_str and end_str <= new_end_str
            ]
            partitions_to_delete.extend(subsumed)
            partitions_to_add.append(new_partition_name)

        partition_requests = []
        if partitions_to_delete:
            partition_requests.append(
                pointing_attitude_partitions.build_delete_request(partitions_to_delete)
            )
            context.log.info(f"Deleting subsumed partitions: {partitions_to_delete}")
        if partitions_to_add:
            partition_requests.append(
                pointing_attitude_partitions.build_add_request(partitions_to_add)
            )
            context.log.info(f"Registered new partitions: {partitions_to_add}")

    return SensorResult(dynamic_partitions_requests=partition_requests)


sensors = [
    add_repoint_partitions,
    add_daily_partitions,
    add_idex_10_day_partitions,
    add_idex_30_day_partitions,
    add_pointing_attitude_partitions,
]
