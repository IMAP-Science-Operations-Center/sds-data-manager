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

from sds_data_manager.lambda_code.SDSCode import spice_utilities
from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.orchestration import config, spice
from sds_data_manager.orchestration.maps_utils import (
    get_map_partition_names,
)

IDEX_10_DAY_RANGES_PATH = (
    "sds_data_manager/lambda_code/SDSCode/utils/idex_10_day_CDF_names.csv"
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


##### THIS TELLS DAGSTER ABOUT SPACECRAFT POINTING-ATTITUDE PROCESSING WINDOWS
# One partition per contiguous stretch of attitude_history coverage that a
# single kernel is "in effect" for, keyed by pointing times.
# Partition start = pointing_start_utc of the first pointing with any overlap
# with that stretch. Partition end = pointing_end_utc of the last pointing
# completely covered by it. A stretch that completely covers no pointing is
# merged into the next contiguous stretch.
# Prefix is "pointingattitude" (no underscores) so parse_dates_from_partition_key can
# split on the first "_" to isolate the date range.
pointing_attitude_partitions = DynamicPartitionsDefinition(
    name="pointing_attitude_partitions"
)


def _get_effective_ah_coverage() -> list[
    tuple[str, datetime.datetime, datetime.datetime]
]:
    """Return the stretches of time each attitude_history kernel is in effect for.

    Kernels are selected by the same metakernel logic used to resolve SPICE
    dependencies for a processing run (latest version per file root, then
    most-recently-ingested first, each kernel only filling the gaps left by
    higher-priority kernels). This keeps the partitions consistent with the
    kernels a run will actually load, and means a superseded kernel stops
    driving partitions as soon as a newer delivery covers its time range,
    even when the newer kernels' coverage doesn't line up exactly with it.

    Each kernel's contribution is its coverage envelope (first segment start
    to last segment end) minus the envelopes of every higher-priority kernel.
    Envelopes, rather than individual segments, are used so a kernel's own
    internal data gaps never split it into several partitions; it is only
    split where a higher-priority kernel takes over part of its range.

    Returns
    -------
    list[tuple[str, datetime.datetime, datetime.datetime]]
        (file_name, start, end) for each contributed stretch, in metakernel
        priority order (highest first). Stretches never overlap, and
        stretches cut by a higher-priority kernel touch it exactly.
    """
    metakernel = spice_utilities.metakernel_builder(
        None, None, file_types={"ATTITUDE_HISTORY"}
    )
    selected = metakernel.spice_files[
        spice_utilities.SpacecraftAttitudeKernels.spice_category_name()
    ]

    claimed = []
    coverage = []
    for kernel in selected:
        intervals = spice.parse_interval_list(kernel.get("file_intervals_datetime"))
        if not intervals:
            continue
        envelope = [intervals[0][0], intervals[-1][1]]
        for start, end in spice.subtract_intervals([envelope], claimed):
            coverage.append((kernel["file_name"], start, end))
        claimed.append(envelope)
    return coverage


# A pointing's fixed attitude actually ends at repoint_start_utc (when the
# spacecraft starts slewing to the next pointing), not at pointing_end_utc.
# pointing_end_utc is recorded as when the *next* pointing begins, i.e. after
# that slew finishes -- so it's later than the true end of this pointing's
# stable attitude. That's why these queries check repoint_start_utc against
# the ah kernel's bounds instead of pointing_end_utc.
def _first_overlapping_pointing(session, ah_min, ah_max):
    """Return the first pointing with any overlap with [ah_min, ah_max]."""
    return (
        session.query(models.PointingTable)
        .filter(
            models.PointingTable.pointing_start_utc < ah_max,
            models.PointingTable.repoint_start_utc > ah_min,
        )
        .order_by(models.PointingTable.pointing_start_utc)
        .first()
    )


def _last_covered_pointing(session, ah_min, ah_max):
    """Return the last pointing completely contained within [ah_min, ah_max]."""
    return (
        session.query(models.PointingTable)
        .filter(
            models.PointingTable.pointing_start_utc >= ah_min,
            models.PointingTable.repoint_start_utc <= ah_max,
        )
        .order_by(models.PointingTable.pointing_end_utc.desc())
        .first()
    )


def _pointing_attitude_partition_key(first_pointing, last_pointing):
    """Build the partition key spanning first_pointing through last_pointing."""
    start_str = first_pointing.pointing_start_utc.strftime("%Y-%m-%dT%H:%M:%S")
    end_str = last_pointing.pointing_end_utc.strftime("%Y-%m-%dT%H:%M:%S")
    return f"pointingattitude_{start_str}_to_{end_str}"


@sensor(minimum_interval_seconds=600)
def add_pointing_attitude_partitions(context: SensorEvaluationContext):
    """Keep the pointing attitude partitions in sync with the current ah kernels.

    The desired set of partitions is rebuilt from scratch every tick from the
    attitude_history coverage currently in effect (see
    _get_effective_ah_coverage), then reconciled against the existing
    partitions: missing partitions are added and any partition that is no
    longer desired is deleted. This handles the normal growing-append case
    (same start, later end), the retroactive combined-file case (one large
    file replacing many small early-mission files), and reprocessed
    deliveries whose coverage boundaries don't match the kernels they replace.
    """
    effective_coverage = _get_effective_ah_coverage()
    if not effective_coverage:
        return SensorResult()

    desired_partitions = []
    # A stretch too short to fully cover any pointing would otherwise lose
    # the pointing it partially covers: the previous partition stops before
    # that pointing and the next one starts after it. Carry such a stretch
    # forward and merge it into the start of the next contiguous one.
    pending_start = pending_end = None
    # (ah_min, ah_max, first_overlapping) of the stretch behind the most
    # recently desired partition, so a trailing remainder can be merged back
    # into it after the loop.
    last_window = None
    with db.Session() as session:
        for file_name, stretch_start, ah_max in sorted(
            effective_coverage, key=lambda coverage: coverage[1]
        ):
            ah_min = stretch_start
            if pending_end is not None and pending_end >= stretch_start:
                ah_min = pending_start
            pending_start = pending_end = None

            first_overlapping = _first_overlapping_pointing(session, ah_min, ah_max)
            last_covered = _last_covered_pointing(session, ah_min, ah_max)

            # Skip if no pointings are completely covered yet
            if not first_overlapping or not last_covered:
                context.log.info(
                    f"No fully covered pointing for {file_name} between "
                    f"{ah_min} and {ah_max}; merging it into the next stretch."
                )
                pending_start, pending_end = ah_min, ah_max
                continue

            desired_partitions.append(
                _pointing_attitude_partition_key(first_overlapping, last_covered)
            )
            last_window = (ah_min, ah_max, first_overlapping)

        # No later stretch picked up the final remainder. If it continues
        # straight on from the previous partition's coverage, their union may
        # fully cover pointings neither covers alone, so extend that partition.
        if (
            pending_end is not None
            and last_window is not None
            and last_window[1] >= pending_start
        ):
            window_min, _, first_overlapping = last_window
            last_covered = _last_covered_pointing(session, window_min, pending_end)
            if last_covered:
                desired_partitions[-1] = _pointing_attitude_partition_key(
                    first_overlapping, last_covered
                )

    # Never wipe out every partition because nothing was resolvable this tick.
    if not desired_partitions:
        return SensorResult()

    desired_partitions = list(dict.fromkeys(desired_partitions))
    existing_partitions = context.instance.get_dynamic_partitions(
        "pointing_attitude_partitions"
    )
    partitions_to_delete = [
        key for key in existing_partitions if key not in desired_partitions
    ]
    partitions_to_add = [
        key for key in desired_partitions if key not in existing_partitions
    ]

    partition_requests = []
    if partitions_to_delete:
        partition_requests.append(
            pointing_attitude_partitions.build_delete_request(partitions_to_delete)
        )
        context.log.info(f"Deleting superseded partitions: {partitions_to_delete}")
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
    add_cadence_map_partitions,
    add_pointing_attitude_partitions,
]
