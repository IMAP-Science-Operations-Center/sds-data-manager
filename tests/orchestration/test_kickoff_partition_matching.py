"""Test which partitions a kickoff sensor triggers from new upstream materializations.

Consecutive repoint partitions share a boundary (one ends at the instant the next
begins), and a new repoint's partition is registered in Dagster by
add_repoint_partitions a few minutes after the science file sensor can already
report a materialization for it. Neither may cause the neighbouring repoint to be
triggered in place of the right one.
"""

import datetime
import json

import pytest
from dagster import (
    AssetKey,
    AssetMaterialization,
    DagsterInstance,
    build_sensor_context,
)

from sds_data_manager.lambda_code.SDSCode.database.models import PointingTable
from sds_data_manager.orchestration import config
from sds_data_manager.orchestration.imap_dagster import defs, job_handlers

LO_L1A_SENSOR = "lo_l1a_all_kickoff_sensor"

# Three consecutive repoints. Each one starts at the instant the previous one ends.
REPOINT_416 = "repoint416_2026-10-29T10:03:26_to_2026-10-30T10:03:28"
REPOINT_417 = "repoint417_2026-10-30T10:03:28_to_2026-10-31T10:03:22"
REPOINT_418 = "repoint418_2026-10-31T10:03:22_to_2026-11-01T10:03:30"


def _utc(*args):
    return datetime.datetime(*args, tzinfo=datetime.timezone.utc)


def _get_job_handler(job_name):
    return next(job for job in job_handlers if job.dagster_job_name == job_name)


def _report_materialization(instance, asset_name, partition):
    """Report an upstream materialization, as the science file sensors do."""
    instance.report_runless_asset_event(
        asset_event=AssetMaterialization(
            asset_key=AssetKey([asset_name]),
            partition=partition,
            metadata={
                "file_names": [f"{asset_name}_{partition}.pkts"],
                "input_type": "science",
                "version": "v000.0001",
                "start_date": "",
            },
        )
    )


def _run_kickoff_sensor(instance, sensor_name, cursor=None):
    """Run a kickoff sensor for one tick, returning its run requests and cursor."""
    context = build_sensor_context(instance=instance, cursor=cursor)
    run_requests = list(defs.get_sensor_def(sensor_name)(context))
    return run_requests, context.cursor


def _register_repoint_partitions(instance):
    """Run add_repoint_partitions once and apply its requests to the instance."""
    context = build_sensor_context(instance=instance)
    result = defs.get_sensor_def("add_repoint_partitions")(context)
    for request in result.dynamic_partitions_requests:
        instance.add_dynamic_partitions(
            partitions_def_name=request.partitions_def_name,
            partition_keys=request.partition_keys,
        )


def _add_consecutive_pointings(session):
    """Add repoints 416 and 417, where 417 does not have an end time yet."""
    boundary = _utc(2026, 10, 30, 10, 3, 28)
    session.add_all(
        [
            PointingTable(
                pointing_id=416,
                pointing_start_utc=_utc(2026, 10, 29, 10, 3, 26),
                pointing_end_utc=boundary,
                repoint_start_utc=boundary,
                repoint_end_utc=boundary,
            ),
            PointingTable(pointing_id=417, pointing_start_utc=boundary),
        ]
    )
    session.commit()


def _set_pointing_417_end(session):
    """Give repoint 417 its end time, so add_repoint_partitions can register it."""
    pointing = session.get(PointingTable, 417)
    pointing.pointing_end_utc = _utc(2026, 10, 31, 10, 3, 22)
    session.commit()


@pytest.fixture
def lo_l1a_handler():
    return _get_job_handler("lo_l1a_all_processing_job")


@pytest.mark.parametrize(
    ("up_start", "up_end", "expected"),
    [
        # A range equal to one partition only overlaps that partition, not the
        # neighbours it touches.
        (_utc(2026, 10, 30, 10, 3, 28), _utc(2026, 10, 31, 10, 3, 22), [REPOINT_417]),
        (_utc(2026, 10, 29, 10, 3, 26), _utc(2026, 10, 30, 10, 3, 28), [REPOINT_416]),
        (_utc(2026, 10, 31, 10, 3, 22), _utc(2026, 11, 1, 10, 3, 30), [REPOINT_418]),
        # A range inside a partition.
        (_utc(2026, 10, 30, 12), _utc(2026, 10, 30, 13), [REPOINT_417]),
        # A range that crosses a boundary overlaps both sides of it.
        (
            _utc(2026, 10, 30, 10, 3, 27),
            _utc(2026, 10, 30, 10, 3, 29),
            [REPOINT_416, REPOINT_417],
        ),
        (
            _utc(2026, 10, 30),
            _utc(2026, 11, 1),
            [REPOINT_416, REPOINT_417, REPOINT_418],
        ),
        # A zero-length range overlaps the partition that contains the instant.
        (_utc(2026, 10, 30, 12), _utc(2026, 10, 30, 12), [REPOINT_417]),
        # A shared boundary belongs to the partition that starts there.
        (
            _utc(2026, 10, 30, 10, 3, 28),
            _utc(2026, 10, 30, 10, 3, 28),
            [REPOINT_417],
        ),
        # A range outside all of the partitions.
        (_utc(2026, 11, 2), _utc(2026, 11, 3), []),
    ],
)
def test_overlapping_partitions_are_half_open(
    lo_l1a_handler, up_start, up_end, expected
):
    """Ranges that only touch a partition's boundary do not overlap it."""
    instance = DagsterInstance.ephemeral()
    instance.add_dynamic_partitions(
        "repoint_partitions", [REPOINT_416, REPOINT_417, REPOINT_418]
    )

    # The upstream key is not one of the registered partitions, so it is matched
    # by its dates.
    overlapping = lo_l1a_handler._get_overlapping_target_partitions(
        "daily_2026-10-30T00:00:00_to_2026-10-31T00:00:00", up_start, up_end, instance
    )

    assert sorted(overlapping) == expected


def test_same_partition_key_is_matched_directly(lo_l1a_handler):
    """An upstream partition that is registered for the job is triggered as-is."""
    instance = DagsterInstance.ephemeral()
    instance.add_dynamic_partitions("repoint_partitions", [REPOINT_416, REPOINT_417])

    overlapping = lo_l1a_handler._get_overlapping_target_partitions(
        REPOINT_417,
        _utc(2026, 10, 30, 10, 3, 28),
        _utc(2026, 10, 31, 10, 3, 22),
        instance,
    )

    assert overlapping == [REPOINT_417]


def test_kickoff_sensor_waits_for_unregistered_partition(
    mock_db_session, ephemeral_instance
):
    """An event on a partition that is not registered yet is retried later.

    Repoint 417 gets its end time, and its L0 file is indexed, in the same window
    before add_repoint_partitions registers its partition. The neighbouring
    repoint 416 shares a boundary with it and is the only partition that exists.
    """
    _add_consecutive_pointings(mock_db_session)
    _register_repoint_partitions(ephemeral_instance)
    registered = ephemeral_instance.get_dynamic_partitions("repoint_partitions")
    assert REPOINT_416 in registered
    assert REPOINT_417 not in registered

    _set_pointing_417_end(mock_db_session)
    _report_materialization(ephemeral_instance, "lo_l0_raw", REPOINT_417)

    # Tick 1: nothing is triggered, and the event is left for the next tick.
    run_requests, cursor = _run_kickoff_sensor(ephemeral_instance, LO_L1A_SENSOR)
    assert run_requests == []
    assert json.loads(cursor)["lo_l0_raw"] == 0

    # Without the partition, the next tick still waits for it.
    run_requests, cursor = _run_kickoff_sensor(
        ephemeral_instance, LO_L1A_SENSOR, cursor
    )
    assert run_requests == []
    assert json.loads(cursor)["lo_l0_raw"] == 0

    # Tick 3: once the partition exists, the right repoint is triggered, and only it.
    _register_repoint_partitions(ephemeral_instance)
    assert REPOINT_417 in ephemeral_instance.get_dynamic_partitions(
        "repoint_partitions"
    )
    run_requests, cursor = _run_kickoff_sensor(
        ephemeral_instance, LO_L1A_SENSOR, cursor
    )
    assert [request.partition_key for request in run_requests] == [REPOINT_417]
    assert json.loads(cursor)["lo_l0_raw"] > 0

    # The event is consumed now, so it does not trigger anything again.
    run_requests, _ = _run_kickoff_sensor(ephemeral_instance, LO_L1A_SENSOR, cursor)
    assert run_requests == []


def test_kickoff_sensor_processes_events_before_unregistered_partition(
    mock_db_session, ephemeral_instance
):
    """Events before the one waiting for its partition are still processed."""
    _add_consecutive_pointings(mock_db_session)
    _register_repoint_partitions(ephemeral_instance)
    _set_pointing_417_end(mock_db_session)

    _report_materialization(ephemeral_instance, "lo_l0_raw", REPOINT_416)
    _report_materialization(ephemeral_instance, "lo_l0_raw", REPOINT_417)

    # Tick 1: only repoint 416 can be triggered. The cursor stops after its event.
    run_requests, cursor = _run_kickoff_sensor(ephemeral_instance, LO_L1A_SENSOR)
    assert [request.partition_key for request in run_requests] == [REPOINT_416]
    cursor_after_416 = json.loads(cursor)["lo_l0_raw"]
    assert cursor_after_416 > 0

    # Tick 2: repoint 417 is still not registered, so nothing new happens.
    run_requests, cursor = _run_kickoff_sensor(
        ephemeral_instance, LO_L1A_SENSOR, cursor
    )
    assert run_requests == []
    assert json.loads(cursor)["lo_l0_raw"] == cursor_after_416

    # Tick 3: repoint 417 gets triggered once it is registered.
    _register_repoint_partitions(ephemeral_instance)
    run_requests, cursor = _run_kickoff_sensor(
        ephemeral_instance, LO_L1A_SENSOR, cursor
    )
    assert [request.partition_key for request in run_requests] == [REPOINT_417]
    assert json.loads(cursor)["lo_l0_raw"] > cursor_after_416


def test_kickoff_sensor_gives_up_on_partition_that_never_registers(
    mock_db_session, ephemeral_instance, monkeypatch
):
    """A partition that is never registered must not block the sensor forever.

    Once the event has waited too long it is matched by its dates instead, which
    must not select the neighbouring repoint that only touches its boundary.
    """
    _add_consecutive_pointings(mock_db_session)
    _register_repoint_partitions(ephemeral_instance)
    _report_materialization(ephemeral_instance, "lo_l0_raw", REPOINT_417)
    monkeypatch.setattr(
        config, "UNREGISTERED_PARTITION_MAX_WAIT", datetime.timedelta(0)
    )

    run_requests, cursor = _run_kickoff_sensor(ephemeral_instance, LO_L1A_SENSOR)

    assert run_requests == []
    assert json.loads(cursor)["lo_l0_raw"] > 0


def test_kickoff_sensor_matches_other_partitioning_by_date(ephemeral_instance):
    """An upstream asset with other partitions is matched to this job by date.

    The yearly map partition runs from 2026-01-01 to 2027-01-01. Daily upstream
    partitions that merely touch either end of it must not trigger it.
    """
    job_name = "ultra_l3_u45spxhhfspfullhae2deg1yr"
    upstream_name = "ultra_l3_u45enahhfspfullhae2deg1yr"
    year_partition = "cadence1yr_2026-01-01T00:00:00_to_2027-01-01T00:00:00"
    ephemeral_instance.add_dynamic_partitions(
        "cadence_1yr_partitions", [year_partition]
    )
    daily_before = "daily_2025-12-31T00:00:00_to_2026-01-01T00:00:00"
    daily_inside = "daily_2026-06-01T00:00:00_to_2026-06-02T00:00:00"
    daily_after = "daily_2027-01-01T00:00:00_to_2027-01-02T00:00:00"
    for partition in (daily_before, daily_after, daily_inside):
        _report_materialization(ephemeral_instance, upstream_name, partition)

    run_requests, cursor = _run_kickoff_sensor(
        ephemeral_instance, f"{job_name}_kickoff_sensor"
    )

    assert [request.partition_key for request in run_requests] == [year_partition]
    assert json.loads(cursor)[upstream_name] > 0
