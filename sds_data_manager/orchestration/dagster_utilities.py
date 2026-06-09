import datetime

from dagster import (
    AssetKey,
    AssetMaterialization,
    DagsterEventType,
    DynamicPartitionsDefinition,
    EventRecordsFilter,
    MaterializeResult,
    RunRequest,
)


def _existing_asset(
    context,
    asset_key: AssetKey,
    partition: str,
    file_names: list[str],
    current_version: int,
):
    """This checks the most recent materialization of an asset, if it exists.

    If an asset already exists and the file_names in the metadata are the
    same between the two, then we return True.

    Otherwise we return False.
    """
    records = context.instance.get_event_records(
        EventRecordsFilter(
            asset_key=asset_key,
            asset_partitions=[partition],
            event_type=DagsterEventType.ASSET_MATERIALIZATION,
        ),
        limit=1,
    )

    if records:
        # Extract the previous file list from the metadata
        last_metadata = records[0].asset_materialization.metadata
        last_files_used = last_metadata.get("file_names").value
        last_version = int(last_metadata.get("version").value)
        if current_version < last_version:
            # We are trying to add an older version, a better one already exists
            return True
        # Compare lists
        if set(last_files_used) == set(file_names):
            return True

    return False


def get_materialization(
    context,
    asset_key: AssetKey,
    partition: str,
    file_names: list[str],
    version: str,
    data_type: str,
    start_date: str = None,
):

    if _existing_asset(context, asset_key, partition, file_names, int(version)):
        return

    return AssetMaterialization(
        asset_key=asset_key,
        partition=str(partition),
        metadata={
            "file_names": file_names,
            "input_type": data_type,
            "version": version,
            "start_date": start_date,
        },
    )


def get_materialization_result(
    context,
    asset_key: AssetKey,
    partition: str | None,
    file_names: list[str],
    version: str,
    data_type: str,
    inputs: dict | None = None,
) -> MaterializeResult | None:
    """Return a MaterializeResult object, if metadata is unique.

    We first check if an asset already exists. If it does, we return nothing.

    data_type must be one of "science", "ancillary", "spice", "spin", or "repoint".
    """
    if _existing_asset(context, asset_key, partition, file_names, int(version)):
        return

    return MaterializeResult(
        asset_key=asset_key,
        metadata={
            "file_names": file_names,
            "input_type": data_type,
            "version": version,
            "inputs": inputs,
        },
    )


def run_all_affected_partitions(
    context,
    asset_key_phrase: str,
    min_dt: datetime.datetime,
    max_dt: datetime.datetime,
    suffix: str,
):
    """This function loops through all assets, looking for the "asset_key_phrase". Then, it yields
    a run request for those assets at the partitions that exist between min_dt and max_dt.
    """
    _cache = {}
    asset_graph = context.repository_def.asset_graph
    for asset_key in asset_graph.get_all_asset_keys():
        context.log.info(f"Determining affected partitions from {asset_key}")
        if asset_key_phrase not in asset_key.to_user_string():
            continue  # This asset is not applicable
        context.log.info(f"Found spice partition: {asset_key}")
        partitions_def = asset_graph.get(asset_key).partitions_def

        if not partitions_def:
            continue

        # This serves to cache the partitions so we don't need to calculate them twice.
        if partitions_def.name not in _cache:
            affected_partitions = get_affected_partitions(
                context, partitions_def, min_dt, max_dt
            )
            _cache[partitions_def.name] = affected_partitions
        else:
            affected_partitions = _cache[partitions_def.name]

        for partition in affected_partitions:
            yield RunRequest(
                run_key=f"{asset_key.to_user_string()}_{partition}_{suffix}",
                partition_key=partition,
                asset_selection=[asset_key],
            )


def get_affected_partitions(
    context,
    partitions_def: DynamicPartitionsDefinition,
    min_dt: datetime.datetime,
    max_dt: datetime.datetime,
):
    """This is a helper function that returns a set of the repoint partitions that between within
    two datetime objects.
    """
    context.log.info(
        f"Checking for matching partitions in the time range of {min_dt} to {max_dt}"
    )
    keys = partitions_def.get_partition_keys(dynamic_partitions_store=context.instance)
    affected_keys = []
    for key in keys:
        date_range = key.split("_", 1)[1]
        if "_to_" in date_range:
            p_start_str, p_end_str = date_range.split("_to_")
            p_start = datetime.datetime.strptime(
                p_start_str, "%Y-%m-%dT%H:%M:%S"
            ).replace(tzinfo=datetime.timezone.utc)
            p_end = datetime.datetime.strptime(p_end_str, "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=datetime.timezone.utc
            )

            # Check for time overlap logic
            if min_dt == max_dt:
                if (
                    min_dt.replace(tzinfo=datetime.timezone.utc) < p_end
                    and max_dt.replace(tzinfo=datetime.timezone.utc) >= p_start
                ):
                    context.log.info("It was a match! Adding to the affected keys.")
                    affected_keys.append(key)
            elif (
                min_dt.replace(tzinfo=datetime.timezone.utc) < p_end
                and max_dt.replace(tzinfo=datetime.timezone.utc) > p_start
            ):
                context.log.info("It was a match! Adding to the affected keys.")
                affected_keys.append(key)

    return affected_keys


def parse_dates_from_partition_key(
    partition_key: str,
) -> tuple[datetime.datetime, datetime.datetime]:
    """Extracts start and end datetimes from a string formatted like:
    '{name}_%Y-%m-%dT%H:%M:%S_to_%Y-%m-%dT%H:%M:%S'
    """
    if not partition_key:
        return None, None

    date_range = partition_key.split("_", 1)[1]
    if "_to_" in date_range:
        p_start_str, p_end_str = date_range.split("_to_")
        p_start = datetime.datetime.strptime(p_start_str, "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=datetime.timezone.utc
        )
        p_end = datetime.datetime.strptime(p_end_str, "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=datetime.timezone.utc
        )

    return p_start, p_end
