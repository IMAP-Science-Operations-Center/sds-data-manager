"""Common functions used throughout the orchestration code."""

import datetime

from dagster import (
    AssetExecutionContext,
    AssetKey,
    AssetMaterialization,
    DagsterEventType,
    DynamicPartitionsDefinition,
    EventRecordsFilter,
    MaterializeResult,
)
from imap_data_access.file_validation import Version


def _existing_asset(
    context: AssetExecutionContext,
    asset_key: AssetKey,
    partition: str,
    file_names: list[str],
    current_version: Version,
):
    """Return True if an Asset should not be materialized.

    If an asset already exists and the file_names in the metadata are the
    same between the two, then we return True.

    We also return True if the version of the previous file is greater
    than the version we are trying to materialize.
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
        # Backwards-compatible fallback: pre-existing materializations may not
        # have major_version/minor_version (only the legacy `version` field).
        # Default to 0 so any valid version triggers re-materialization.
        major_meta = last_metadata.get("major_version")
        minor_meta = last_metadata.get("minor_version")
        last_major_version = int(getattr(major_meta, "value", None) or 0)
        last_minor_version = int(getattr(minor_meta, "value", None) or 0)
        if current_version < Version(last_major_version, last_minor_version):
            # We are trying to add an older version, a better one already exists
            return True
        # Compare lists
        if set(last_files_used) == set(file_names):
            return True

    return False


def get_materialization(
    context: AssetExecutionContext,
    asset_key: AssetKey,
    partition: str,
    file_names: list[str],
    version: Version,
    data_type: str,
    start_date: str = "",
):
    """Return AssetMaterialization only if different from previous materialization."""
    if _existing_asset(context, asset_key, partition, file_names, version):
        return

    return AssetMaterialization(
        asset_key=asset_key,
        partition=str(partition),
        metadata={
            "file_names": file_names,
            "input_type": data_type,
            "major_version": str(version.major),
            "minor_version": str(version.minor),
            "start_date": start_date,
        },
    )


def get_materialization_result(
    context: AssetExecutionContext,
    asset_key: AssetKey,
    partition: str | None,
    file_names: list[str],
    version: Version,
    data_type: str,
    inputs: dict | None = None,
) -> MaterializeResult | None:
    """Return a MaterializeResult object, if metadata is unique.

    We first check if an asset already exists. If it does, we return nothing.

    data_type must be one of "science", "ancillary", "spice", "spin", or "repoint".
    """
    if _existing_asset(context, asset_key, partition, file_names, version):
        return

    return MaterializeResult(
        asset_key=asset_key,
        metadata={
            "file_names": file_names,
            "input_type": data_type,
            "major_version": str(version.major),
            "minor_version": str(version.minor),
            "inputs": inputs,
        },
    )


def get_affected_partitions(
    context: AssetExecutionContext,
    partitions_def: DynamicPartitionsDefinition,
    min_dt: datetime.datetime,
    max_dt: datetime.datetime,
):
    """Return a set of partitions overlapping between two datetime objects."""
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
    """Extract start and end datetimes from partition names.

    The partition names should be formatted like:
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
